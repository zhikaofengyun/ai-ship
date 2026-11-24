"""Real bounded workflow integration: FFmpeg nodes and durable control boundaries."""

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
import uuid
from fastapi.testclient import TestClient
from app.auth import Actor
from app.commands import dispatch
from app.errors import DomainError
from app.execution import start_workflow, execute_run, _call_handler
from app.store import Store, get_store
from app import media


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.video = Path(cls.fixture.name) / "synthetic-test.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=160x120:r=24:d=1.2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1.2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(cls.video),
            ],
            check=True,
            timeout=30,
        )

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AI_SHIP_DATA_DIR": self.tmp.name,
                "AI_SHIP_DB": str(Path(self.tmp.name) / "test.sqlite3"),
                "AI_SHIP_MODE": "local",
            },
        )
        self.env.start()
        self.store = get_store()
        self.actor = Actor("local", "local-owner", "owner")
        self.project = self.store.create(
            "Project", {"name": "合成测试素材工作流", "goals": "仅回归验证"}, tenant_id="local"
        )
        self.asset = media.import_saved_file(
            self.store, self.actor, self.project["id"], "synthetic-test.mp4", self.video
        )["asset"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def workflow(self, nodes, edges):
        spec = {
            "nodes": nodes,
            "edges": edges,
            "max_steps": 10,
            "max_attempts": 2,
            "budget": 0,
            "max_seconds": 120,
        }
        return self.store.create(
            "Workflow",
            {"name": "测试真实执行", "DAG": spec},
            tenant_id="local",
            project_id=self.project["id"],
        )

    def transform_node(self):
        return {
            "id": "transform",
            "feature_id": "I07",
            "route_id": "I07-R2",
            "parameters": {},
            "operation_args": {"recipe": {"trim_start": 0, "trim_end": 1, "scale": 1.016}},
            "input_bindings": {"asset_id": {"record_id": self.asset["id"], "version": 1}},
        }

    def script_node(self, nid="script"):
        return {
            "id": nid,
            "feature_id": "V01",
            "route_id": "V01-R3",
            "parameters": {},
            "operation_args": {"data": {"text": "人工脚本，自动测试"}},
        }

    def start(self, workflow, key=None):
        return start_workflow(
            self.store,
            self.actor,
            {
                "workflow_id": workflow["id"],
                "expected_revision": workflow["version"],
                "idempotency_key": key or uuid.uuid4().hex,
            },
        )

    def control(self, typ, run):
        c = {
            "command_id": uuid.uuid4().hex,
            "command_type": typ,
            "project_id": self.project["id"],
            "actor_id": self.actor.actor_id,
            "actor_role": "owner",
            "target_id": run["id"],
            "expected_revision": run["version"],
            "input_version_refs": [],
            "parameters": {},
            "policy_version": None,
            "idempotency_key": uuid.uuid4().hex,
            "trace_id": uuid.uuid4().hex,
            "reason": "测试安全检查点控制",
        }
        return dispatch(self.store, self.actor, c)

    def test_real_transform_then_extract_audio_and_idempotent_replay(self):
        second = {
            "id": "audio",
            "feature_id": "L07",
            "route_id": "L07-R1",
            "parameters": {},
            "operation_args": {},
            "input_bindings": {"asset_id": {"node_id": "transform", "output": "version"}},
        }
        w = self.workflow(
            [self.transform_node(), second],
            [{"source": "transform", "target": "audio", "type": "control"}],
        )
        key = uuid.uuid4().hex
        result = self.start(w, key)
        self.assertEqual(result["status"], "completed", result)
        cp = result["run"]["data"]["checkpoint"]
        self.assertEqual(cp["completed_nodes"], ["transform", "audio"])
        audio_ref = cp["nodes"]["audio"]["outputs"]["audio"]
        audio = self.store.get(audio_ref["id"], "local")
        self.assertEqual(audio["kind"], "Audio")
        self.assertEqual(audio["data"]["execution_context"]["node_id"], "audio")
        _, _, path = media.resolve_media(self.store, self.actor, audio["id"])
        self.assertGreater(path.stat().st_size, 100)
        count = len(self.store.list("local", "TransformRun"))
        again = self.start(w, key)
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.store.list("local", "TransformRun")), count)
        self.assertEqual(again["run"]["id"], result["run"]["id"])

    def test_unsupported_route_blocks_only_its_dependants(self):
        unsupported = {"id": "model", "feature_id": "G01", "route_id": "G01-R1", "parameters": {}}
        w = self.workflow([unsupported, self.script_node()], [])
        result = self.start(w)
        self.assertEqual(result["status"], "blocked")
        states = result["run"]["data"]["checkpoint"]["nodes"]
        self.assertEqual(states["model"]["status"], "blocked")
        self.assertEqual(states["script"]["status"], "completed")
        count = len(self.store.list("local", "Script"))
        again = execute_run(self.store, self.actor, result["run"]["id"])
        self.assertEqual(len(self.store.list("local", "Script")), count)
        self.assertEqual(again["status"], "blocked")

    def test_node_binding_cannot_read_nonancestor_layout_node(self):
        node = self.script_node()
        node["input_bindings"] = {
            "data.text": {"node_id": "transform", "output": "version", "value": "data"}
        }
        w = self.workflow([self.transform_node(), node], [])
        with self.assertRaises(DomainError) as ctx:
            self.start(w)
        self.assertEqual(ctx.exception.code, "NONDEPENDENCY_BINDING")

    def test_step_budget_stops_without_false_completion(self):
        w = self.workflow([self.script_node("a"), self.script_node("b")], [])
        data = copy.deepcopy(w["data"])
        data["DAG"]["max_steps"] = 1
        w = self.store.update(w["id"], data, tenant_id="local", expected_revision=1)
        result = self.start(w)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["run"]["data"]["checkpoint"]["steps_started"], 1)
        self.assertEqual(len(self.store.list("local", "Script")), 1)

    def test_cancel_finishes_current_node_and_preserves_checkpoint(self):
        w = self.workflow(
            [self.script_node("a"), self.script_node("b")],
            [{"source": "a", "target": "b", "type": "control"}],
        )
        entered = threading.Event()
        release = threading.Event()
        original = _call_handler

        def slow(*args):
            entered.set()
            release.wait(timeout=5)
            return original(*args)

        with patch("app.execution._call_handler", side_effect=slow):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.start, w)
                self.assertTrue(entered.wait(timeout=5))
                run = self.store.list("local", "Run")[0]
                requested = self.control("RequestCancel", run)
                self.assertEqual(requested["run"]["data"]["state"], "停止中")
                release.set()
                result = future.result(timeout=10)
        self.assertEqual(result["status"], "cancelled")
        cp = result["run"]["data"]["checkpoint"]
        self.assertEqual(cp["nodes"]["a"]["status"], "completed")
        self.assertEqual(cp["nodes"]["b"]["status"], "pending")
        self.assertFalse(result["run"]["data"]["worker_active"])

    def test_paused_run_resumes_without_replaying_completed_node(self):
        w = self.workflow(
            [self.script_node("a"), self.script_node("b")],
            [{"source": "a", "target": "b", "type": "control"}],
        )
        entered = threading.Event()
        release = threading.Event()
        original = _call_handler

        def slow(*args):
            entered.set()
            release.wait(timeout=5)
            return original(*args)

        with patch("app.execution._call_handler", side_effect=slow):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.start, w)
                self.assertTrue(entered.wait(timeout=5))
                self.control("SuspendTask", self.store.list("local", "Run")[0])
                release.set()
                result = future.result(timeout=10)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(len(self.store.list("local", "Script")), 1)
        result = execute_run(self.store, self.actor, result["run"]["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.store.list("local", "Script")), 2)
        self.assertEqual(result["run"]["data"]["checkpoint"]["nodes"]["a"]["attempts"], 1)

    def test_worker_unknown_cannot_be_retried_without_reconciliation(self):
        w = self.workflow([self.script_node()], [])
        with patch("app.execution._call_handler", side_effect=RuntimeError("injected crash")):
            result = self.start(w)
        self.assertEqual(result["status"], "awaiting_reconciliation")
        with self.assertRaises(DomainError) as ctx:
            execute_run(self.store, self.actor, result["run"]["id"])
        self.assertEqual(ctx.exception.code, "RECONCILIATION_REQUIRED")
        self.assertEqual(len(self.store.list("local", "Script")), 0)

    def test_resume_rejects_changed_input_version_and_keeps_completed_nodes(self):
        source = self.store.create(
            "Script", {"text": "原始人工输入"}, tenant_id="local", project_id=self.project["id"]
        )
        second = self.script_node("b")
        second["operation_args"] = {}
        second["input_bindings"] = {
            "data": {"record_id": source["id"], "version": 1, "value": "data"}
        }
        w = self.workflow(
            [self.script_node("a"), second], [{"source": "a", "target": "b", "type": "control"}]
        )
        entered = threading.Event()
        release = threading.Event()
        original = _call_handler

        def slow(*args):
            entered.set()
            release.wait(timeout=5)
            return original(*args)

        with patch("app.execution._call_handler", side_effect=slow):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.start, w)
                self.assertTrue(entered.wait(timeout=5))
                self.control("SuspendTask", self.store.list("local", "Run")[0])
                release.set()
                result = future.result(timeout=10)
        self.store.update(
            source["id"], {"text": "后来修订输入"}, tenant_id="local", expected_revision=1
        )
        result = execute_run(self.store, self.actor, result["run"]["id"])
        self.assertEqual(result["status"], "blocked")
        cp = result["run"]["data"]["checkpoint"]
        self.assertEqual(cp["nodes"]["a"]["attempts"], 1)
        self.assertEqual(cp["nodes"]["b"]["error"]["code"], "INPUT_VERSION_CHANGED")

    def test_revoked_worker_credential_cannot_commit_new_output(self):
        w = self.workflow([self.script_node("a"), self.script_node("b")], [])
        entered = threading.Event()
        release = threading.Event()
        original = _call_handler
        self.actor = Actor(
            "local", "local-owner", "owner", hashlib.sha256(b"worker-key").hexdigest()
        )
        keys = {"worker-key": {"tenant_id": "local", "actor_id": "local-owner", "role": "owner"}}

        def slow(*args):
            entered.set()
            release.wait(timeout=5)
            return original(*args)

        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            with patch("app.execution._call_handler", side_effect=slow):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.start, w)
                    self.assertTrue(entered.wait(timeout=5))
                    os.environ["AI_SHIP_API_KEYS"] = "{}"
                    release.set()
                    result = future.result(timeout=10)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.store.list("local", "Script"), [])
        self.assertEqual(
            result["run"]["data"]["checkpoint"]["nodes"]["a"]["error"]["code"],
            "AUTHORIZATION_REVOKED",
        )

    def test_expired_fence_rejects_output_and_orphan_attempt_requires_reconciliation(self):
        w = self.workflow([self.script_node()], [])
        original = _call_handler

        def expire(store, actor, run, node, args, refs):
            with store.transaction():
                store._local.conn.execute(
                    "UPDATE leases SET expires_at='2000-01-01T00:00:00Z' WHERE resource_id=?",
                    (run["id"],),
                )
            return original(store, actor, run, node, args, refs)

        with patch("app.execution._call_handler", side_effect=expire):
            with self.assertRaises(DomainError) as ctx:
                self.start(w)
        self.assertEqual(ctx.exception.code, "STALE_FENCE")
        self.assertEqual(self.store.list("local", "Script"), [])
        run = self.store.list("local", "Run")[0]
        result = execute_run(self.store, self.actor, run["id"])
        self.assertEqual(result["status"], "awaiting_reconciliation")
        self.assertTrue(result["run"]["data"]["outcome_unknown"])

    def test_committed_orphan_output_can_be_reconciled_without_reexecution(self):
        from app.main import app

        w = self.workflow([self.script_node()], [])
        original = _call_handler

        def orphan(*args):
            original(*args)
            raise RuntimeError("crash after commit before checkpoint")

        with patch("app.execution._call_handler", side_effect=orphan):
            result = self.start(w)
        self.assertEqual(result["status"], "awaiting_reconciliation")
        script = self.store.list("local", "Script")[0]
        run = result["run"]
        with TestClient(app) as client:
            candidates = client.get("/api/v1/execution/" + run["id"] + "/recovery").json()["data"][
                "candidates"
            ]
            self.assertEqual([r["id"] for r in candidates], [script["id"]])
            r = client.post(
                "/api/v1/execution/" + run["id"] + "/reconcile",
                json={
                    "expected_revision": run["version"],
                    "node_id": "script",
                    "outputs": {"script": {"id": script["id"], "version": script["version"]}},
                    "reason": "核对真实提交的人工脚本",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            run = r.json()["data"]["run"]
        final = execute_run(self.store, self.actor, run["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(self.store.list("local", "Script")), 1)

    def orphan_delivery_package(self):
        timeline = self.store.create(
            "Timeline",
            {
                "name": "真实交付恢复测试",
                "locked": True,
                "clips": [
                    {
                        "id": "c1",
                        "asset_id": self.asset["id"],
                        "source_in": 0,
                        "source_out": 1,
                        "timeline_start": 0,
                        "track": 0,
                    }
                ],
                "format": {"width": 160, "height": 120, "fps": 24},
            },
            tenant_id="local",
            project_id=self.project["id"],
            refs=[{"id": self.asset["id"], "version": 1}],
        )
        rendered = media.render_timeline(
            self.store,
            self.actor,
            {
                "project_id": self.project["id"],
                "timeline_id": timeline["id"],
                "expected_revision": 1,
                "profile": {"width": 160, "height": 120, "fps": 24},
            },
        )
        artifact = rendered["artifact"]
        workflow = self.workflow(
            [
                {
                    "id": "package",
                    "feature_id": "Y06",
                    "route_id": "Y06-R2",
                    "parameters": {},
                    "operation_args": {},
                    "input_bindings": {
                        "artifact_id": {"record_id": artifact["id"], "version": artifact["version"]}
                    },
                }
            ],
            [],
        )
        original = _call_handler

        def after_commit(*args):
            original(*args)
            raise RuntimeError("synthetic crash after real ZIP commit before checkpoint")

        with patch("app.execution._call_handler", side_effect=after_commit):
            result = self.start(workflow)
        self.assertEqual(result["status"], "awaiting_reconciliation", result)
        packages = self.store.list("local", "DeliveryPackage", self.project["id"])
        self.assertEqual(len(packages), 1)
        package = packages[0]
        blob = self.store.get(package["data"]["blob_location_id"], "local")
        path = media.blob_path(blob, "local")
        self.assertGreater(path.stat().st_size, 100)
        return result["run"], package, path

    def package_reconciliation(self, client, run, package):
        return client.post(
            "/api/v1/execution/" + run["id"] + "/reconcile",
            json={
                "expected_revision": run["version"],
                "node_id": "package",
                "outputs": {"package": {"id": package["id"], "version": package["version"]}},
                "reason": "核对真实ZIP、冻结清单与当前来源权限",
            },
        )

    def test_real_orphan_delivery_zip_recovers_without_repackaging(self):
        from app.main import app

        run, package, path = self.orphan_delivery_package()
        original_hash = media.hash_file(path)
        with TestClient(app) as client:
            response = self.package_reconciliation(client, run, package)
            self.assertEqual(response.status_code, 200, response.text)
            recovered = response.json()["data"]["run"]
        self.assertEqual(recovered["data"]["checkpoint"]["nodes"]["package"]["status"], "completed")
        final = execute_run(self.store, self.actor, run["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(self.store.list("local", "DeliveryPackage")), 1)
        self.assertEqual(media.hash_file(path), original_hash)

    def test_corrupt_missing_or_unavailable_orphan_delivery_zip_stays_unknown(self):
        from app.main import app

        run, package, path = self.orphan_delivery_package()
        original = path.read_bytes()
        with TestClient(app) as client:
            for corruption in (bytes([original[0] ^ 1]) + original[1:], original[:-1]):
                path.write_bytes(corruption)
                response = self.package_reconciliation(client, run, package)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["error"]["code"], "PACKAGE_CORRUPTION")
                self.assertEqual(self.store.get(run["id"], "local")["version"], run["version"])
            path.unlink()
            response = self.package_reconciliation(client, run, package)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["error"]["code"], "OBJECT_UNAVAILABLE")
            path.write_bytes(original)
            self.store.update(
                package["id"],
                {**package["data"], "status": "unavailable"},
                tenant_id="local",
                expected_revision=package["version"],
            )
            response = self.package_reconciliation(client, run, package)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "PACKAGE_NOT_AVAILABLE")
        current = self.store.get(run["id"], "local")
        self.assertTrue(current["data"]["outcome_unknown"])
        self.assertEqual(current["data"]["status"], "awaiting_reconciliation")
        self.assertEqual(current["data"]["checkpoint"]["nodes"]["package"]["status"], "unknown")

    def test_orphan_delivery_zip_recovery_rechecks_current_artifact_rights(self):
        from app.main import app

        run, package, path = self.orphan_delivery_package()
        source = self.store.get(self.asset["data"]["source_id"], "local")
        self.store.update(
            source["id"],
            {**source["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=source["version"],
        )
        with TestClient(app) as client:
            response = self.package_reconciliation(client, run, package)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["error"]["code"], "MEDIA_REVOKED")
        self.assertTrue(path.exists())
        self.assertEqual(self.store.get(run["id"], "local")["version"], run["version"])
        self.assertTrue(self.store.get(run["id"], "local")["data"]["outcome_unknown"])

    def test_empty_array_reconciliation_and_forged_provenance_are_rejected(self):
        from app.main import app

        w = self.workflow([self.script_node()], [])
        with patch("app.execution._call_handler", side_effect=RuntimeError("injected crash")):
            result = self.start(w)
        run = result["run"]
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/execution/" + run["id"] + "/reconcile",
                json={
                    "expected_revision": run["version"],
                    "node_id": "script",
                    "outputs": {"script": []},
                    "reason": "必须拒绝空产物",
                },
            )
            self.assertEqual(r.status_code, 422)
            self.assertEqual(r.json()["error"]["code"], "OUTPUT_CARDINALITY")
            r = client.post(
                "/api/v1/records",
                json={
                    "kind": "Script",
                    "project_id": self.project["id"],
                    "data": {
                        "text": "不能伪造",
                        "execution_context": {
                            "run_id": run["id"],
                            "node_id": "script",
                            "fencing_token": 1,
                        },
                    },
                },
            )
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()["error"]["code"], "RESERVED_PROVENANCE")
        self.assertTrue(self.store.get(run["id"], "local")["data"]["outcome_unknown"])

    def test_concurrent_resume_does_not_mark_live_worker_unknown(self):
        w = self.workflow([self.script_node()], [])
        entered = threading.Event()
        release = threading.Event()
        original = _call_handler

        def slow(*args):
            entered.set()
            release.wait(timeout=5)
            return original(*args)

        with patch("app.execution._call_handler", side_effect=slow):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.start, w)
                self.assertTrue(entered.wait(timeout=5))
                run = self.store.list("local", "Run")[0]
                with self.assertRaises(DomainError) as ctx:
                    execute_run(self.store, self.actor, run["id"])
                self.assertEqual(ctx.exception.code, "EXECUTION_BUSY")
                self.assertFalse(self.store.get(run["id"], "local")["data"]["outcome_unknown"])
                release.set()
                self.assertEqual(future.result(timeout=10)["status"], "completed")

    def test_single_node_cannot_commit_after_deadline(self):
        w = self.workflow([self.script_node()], [])
        data = copy.deepcopy(w["data"])
        data["DAG"]["max_seconds"] = 1
        w = self.store.update(w["id"], data, tenant_id="local", expected_revision=1)
        original = _call_handler

        def slow(*args):
            time.sleep(1.15)
            return original(*args)

        with patch("app.execution._call_handler", side_effect=slow):
            result = self.start(w)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.store.list("local", "Script"), [])
        self.assertEqual(
            result["run"]["data"]["checkpoint"]["nodes"]["script"]["error"]["code"],
            "EXECUTION_DEADLINE",
        )

    def test_team_viewer_cannot_start_or_forge_owner_role(self):
        from app.main import app

        w = self.workflow([self.script_node()], [])
        keys = {"viewer-key": {"tenant_id": "local", "actor_id": "viewer", "role": "viewer"}}
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            with TestClient(app) as client:
                body = {
                    "workflow_id": w["id"],
                    "expected_revision": 1,
                    "idempotency_key": "viewer-denied",
                }
                r = client.post(
                    "/api/v1/execution/start",
                    json=body,
                    headers={"Authorization": "Bearer viewer-key"},
                )
                self.assertEqual(r.status_code, 403)
                r = client.post(
                    "/api/v1/execution/start",
                    json={**body, "actor_role": "owner"},
                    headers={"Authorization": "Bearer viewer-key"},
                )
                self.assertEqual(r.status_code, 422)
        self.assertEqual(self.store.list("local", "Run"), [])

    def test_http_start_and_resume_contracts(self):
        from app.main import app

        w = self.workflow([self.script_node()], [])
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/execution/start",
                json={
                    "workflow_id": w["id"],
                    "expected_revision": 1,
                    "idempotency_key": "http-start",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            run = r.json()["data"]["run"]
            self.assertEqual(run["data"]["status"], "completed")
            r = client.post(
                "/api/v1/execution/" + run["id"] + "/resume",
                json={"expected_revision": run["version"]},
            )
            self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
