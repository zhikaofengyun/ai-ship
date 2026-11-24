"""Control-plane regression tests: persistence, authorization, state and contracts."""

import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from fastapi.testclient import TestClient
from app.auth import Actor
from app.catalog import ATLAS, COUNTS, FEATURES, MACHINES
from app.commands import dispatch
from app.errors import DomainError
from app.schemas import SCHEMAS, validate_record
from app.store import Store, get_store
from app.workflow import validate_workflow, resolve_parameters, validate_controls


class CoreStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.sqlite3")
        self.actor = Actor("alpha", "owner-a", "owner")
        self.project = self.store.create(
            "Project",
            {"name": "测试项目", "goals": "产品视频", "language": "zh-CN", "market": "CN"},
            tenant_id="alpha",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, kind="Script", data=None, **kwargs):
        return self.store.create(
            kind,
            data or {"text": "真实脚本"},
            tenant_id="alpha",
            project_id=self.project["id"],
            **kwargs,
        )

    def command(self, typ, parameters=None, target=None, revision=None):
        return {
            "command_id": uuid.uuid4().hex,
            "command_type": typ,
            "project_id": self.project["id"],
            "actor_id": "owner-a",
            "actor_role": "owner",
            "target_id": target,
            "expected_revision": revision,
            "input_version_refs": [],
            "parameters": parameters or {},
            "policy_version": None,
            "idempotency_key": uuid.uuid4().hex,
            "trace_id": uuid.uuid4().hex,
            "reason": "自动回归验证",
        }

    def test_catalog_exact_counts_and_schema_for_every_entity(self):
        self.assertEqual(
            COUNTS,
            {
                "modules": 23,
                "features": 178,
                "entities": 64,
                "machines": 12,
                "routes": 534,
                "controls": 742,
                "edges": 571,
                "requirements": 42,
                "scenarios": 14,
            },
        )
        self.assertEqual(set(SCHEMAS), {e["id"] for e in ATLAS["entities"]})
        for f in FEATURES.values():
            self.assertEqual(len(f["routes"]), 3)
            self.assertIn("operation", f)

    def test_immutable_history_and_cas_conflict(self):
        original = self.record()
        updated = self.store.update(
            original["id"], {"text": "新的脚本"}, tenant_id="alpha", expected_revision=1
        )
        self.assertEqual(updated["version"], 2)
        self.assertEqual(self.store.get(original["id"], "alpha", 1)["data"]["text"], "真实脚本")
        with self.assertRaises(DomainError) as ctx:
            self.store.update(
                original["id"], {"text": "覆盖"}, tenant_id="alpha", expected_revision=1
            )
        self.assertEqual(ctx.exception.code, "REVISION_CONFLICT")
        self.assertEqual(len(self.store.history(original["id"], "alpha")), 2)

    def test_database_triggers_prevent_rewriting_facts_and_events(self):
        rec = self.record()
        with self.store.connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE revisions SET reason=? WHERE record_id=?", ("overwrite", rec["id"])
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM events")
        self.assertEqual(len(self.store.events("alpha")), 2)

    def test_cross_tenant_references_never_resolve(self):
        foreign = self.store.create("Source", {"owner": "other"}, tenant_id="beta")
        with self.assertRaises(DomainError) as ctx:
            self.record("Asset", {"source_id": foreign["id"]})
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.store.list("alpha", "Asset"), [])
        with self.assertRaises(DomainError):
            self.store.get(self.project["id"], "beta")

    def test_same_project_transaction_references(self):
        other = self.store.create("Project", {"name": "另一个"}, tenant_id="alpha")
        other_script = self.store.create(
            "Script", {"text": "另外内容"}, tenant_id="alpha", project_id=other["id"]
        )
        with self.assertRaises(DomainError) as ctx:
            self.record("Plan", {"slots": []}, refs=[{"id": other_script["id"], "version": 1}])
        self.assertEqual(ctx.exception.code, "PROJECT_REFERENCE")

    def test_union_reference_type_and_pinned_version(self):
        source = self.record("Source", {"owner": "本人"})
        asset = self.record("Asset", {"source_id": source["id"], "bytes_hash": "a" * 64})
        mapping = self.record(
            "TimeMap",
            {"input_refs": [{"id": asset["id"], "kind": "Asset", "version": 1}], "segments": []},
        )
        self.assertEqual(mapping["refs"][0]["kind"], "Asset")
        self.assertEqual(mapping["refs"][0]["version"], 1)
        with self.assertRaises(DomainError):
            self.record(
                "TimeMap", {"input_refs": [{"id": asset["id"], "kind": "Audio", "version": 1}]}
            )

    def test_batch_failure_rolls_back_all_records_and_events(self):
        before = len(self.store.events("alpha"))
        with self.assertRaises(DomainError):
            with self.store.transaction():
                self.record("Script", {"text": "不会残留"})
                self.record("Asset", {"source_id": "missing"})
        self.assertEqual(len(self.store.events("alpha")), before)
        self.assertEqual(self.store.list("alpha", "Script"), [])

    def test_nested_failure_savepoint_does_not_commit_partial_operation(self):
        with self.store.transaction():
            self.record("Script", {"text": "保留"})
            try:
                with self.store.transaction():
                    self.record("Script", {"text": "回滚"})
                    raise ValueError("test failure")
            except ValueError:
                pass
        self.assertEqual([r["data"]["text"] for r in self.store.list("alpha", "Script")], ["保留"])

    def test_concurrent_cas_allows_only_one_winner(self):
        rec = self.record()
        barrier = threading.Barrier(2)

        def writer(n):
            barrier.wait()
            try:
                return self.store.update(
                    rec["id"], {"text": str(n)}, tenant_id="alpha", expected_revision=1
                )["version"]
            except DomainError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, [1, 2]))
        self.assertCountEqual(results, [2, "REVISION_CONFLICT"])
        self.assertEqual(len(self.store.history(rec["id"], "alpha")), 2)

    def test_manual_field_lock_blocks_changes_and_removal(self):
        rec = self.record(
            "Script", {"text": "受保护", "locks": [{"path": "text", "owner": "owner-a"}]}
        )
        with self.assertRaises(DomainError) as ctx:
            self.store.update(
                rec["id"],
                {"text": "改变", "locks": rec["data"]["locks"]},
                tenant_id="alpha",
                expected_revision=1,
            )
        self.assertEqual(ctx.exception.code, "LOCK_CONFLICT")
        with self.assertRaises(DomainError):
            self.store.update(rec["id"], {"text": "受保护"}, tenant_id="alpha", expected_revision=1)

    def test_missing_metric_differs_from_zero(self):
        with self.assertRaises(DomainError):
            validate_record("Metric", {"metric_name": "views", "value_or_null": None})
        validate_record(
            "Metric", {"metric_name": "views", "value_or_null": None, "missing_reason": "权限不足"}
        )
        validate_record("Metric", {"metric_name": "views", "value_or_null": 0})
        with self.assertRaises(DomainError):
            validate_record("Metric", {"value_or_null": 0, "missing_reason": "missing"})

    def test_secret_values_rejected_but_voice_token_allowed(self):
        with self.assertRaises(DomainError):
            self.record(
                "ModelProfile",
                {"provider": "example", "model_id": "model", "api_key": "do-not-store"},
            )
        validate_record("VoiceMap", {"token": "商品", "timing_status": "untimed"})
        validate_record(
            "ModelProfile",
            {"provider": "example", "model_id": "model", "credential_ref": "env:PROVIDER_KEY"},
        )
        with self.assertRaises(DomainError):
            validate_record("ModelProfile", {"credential_ref": "sk-secret-value"})

    def test_money_exact_decimal_and_currency(self):
        validate_record("Product", {"price": "12.30", "currency": "CNY"})
        with self.assertRaises(DomainError):
            validate_record("Product", {"price": "-1", "currency": "CNY"})
        with self.assertRaises(DomainError):
            validate_record("Product", {"price": 12.3})

    def test_timeline_integrity_and_untimed_anchor(self):
        with self.assertRaises(DomainError):
            validate_record("Timeline", {"tracks": [{"id": "t"}, {"id": "t"}]})
        with self.assertRaises(DomainError):
            validate_record(
                "Timeline", {"tracks": [], "clip_uses": [{"id": "c", "track_ref": "missing"}]}
            )
        with self.assertRaises(DomainError):
            validate_record("VoiceMap", {"timing_status": "verified"})
        with self.assertRaises(DomainError):
            validate_record(
                "Timeline",
                {
                    "keyframe_curves": [
                        {
                            "parameter_path": "x",
                            "clock_domain": "source_pts",
                            "anchor_mode": "fixed_sequence",
                        }
                    ]
                },
            )

    def test_clips_lock_and_edit_clear_locked_flag(self):
        timeline = self.record(
            "Timeline", {"clips": [{"id": "clip", "duration": 1}], "name": "待锁版"}
        )
        locked = dispatch(
            self.store,
            self.actor,
            self.command("LockTimelineRevision", target=timeline["id"], revision=1),
        )["record"]
        self.assertTrue(locked["data"]["locked"])
        edited = dispatch(
            self.store,
            self.actor,
            self.command(
                "ApplyChangeSet",
                {"operations": [{"op": "replace", "path": "/name", "value": "修改后草稿"}]},
                timeline["id"],
                2,
            ),
        )["record"]
        self.assertFalse(edited["data"]["locked"])
        self.assertEqual(edited["data"]["status"], "draft")

    def test_event_recovery_reads_ascending_page_without_skipping(self):
        for i in range(125):
            self.store.append_event(
                tenant_id="alpha",
                project_id=self.project["id"],
                event_type="test.check",
                payload={"index": i},
            )
        first = self.store.events("alpha", limit=100, after=0, ascending=True)
        second = self.store.events("alpha", limit=100, after=first[-1]["sequence"], ascending=True)
        sequence = [e["sequence"] for e in first + second]
        self.assertEqual(sequence, list(range(1, 127)))

    def test_dependency_invalidation_uses_fields_and_half_open_ranges(self):
        product = self.record("Product", {"SKU": "A", "price": "10", "currency": "CNY"})
        price = self.record(
            "Script",
            {"text": "十元"},
            refs=[{"id": product["id"], "version": 1, "fields": ["price"], "range": [0, 10]}],
        )
        color = self.record(
            "Script",
            {"text": "蓝色"},
            refs=[
                {
                    "id": product["id"],
                    "version": 1,
                    "fields": ["appearance.color"],
                    "range": [10, 20],
                }
            ],
        )
        report = self.store.impact(
            "alpha", [{"id": product["id"], "version": 1, "fields": ["price"], "range": [0, 10]}]
        )
        self.assertEqual([x["id"] for x in report["affected"]], [price["id"]])
        self.assertEqual([x["id"] for x in report["retained"]], [color["id"]])
        report = self.store.impact(
            "alpha", [{"id": product["id"], "fields": ["price"], "range": [10, 20]}]
        )
        self.assertEqual(report["affected"], [])

    def test_command_idempotent_replay_without_duplicate_writes(self):
        command = self.command("CreateFactBoundScript", {"data": {"text": "一个脚本"}})
        first = dispatch(self.store, self.actor, command)
        second = dispatch(self.store, self.actor, command)
        self.assertEqual(first["record"]["id"], second["record"]["id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(self.store.list("alpha", "Script")), 1)
        changed = copy.deepcopy(command)
        changed["parameters"]["data"]["text"] = "不同请求"
        with self.assertRaises(DomainError) as ctx:
            dispatch(self.store, self.actor, changed)
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_configure_feature_never_fakes_domain_completion(self):
        result = dispatch(
            self.store,
            self.actor,
            self.command(
                "ConfigureFeature", {"feature_id": "G01", "route_id": "G01-R1", "controls": {}}
            ),
        )
        self.assertEqual(result["status"], "configured")
        self.assertEqual(result["run"]["data"]["state"], "需补充")
        self.assertEqual(result["run"]["data"]["output_refs"], [])
        self.assertTrue(result["unresolved"])

    def test_unknown_side_effect_is_not_cancelled_or_retried(self):
        run = self.record("Run", {"state": "执行中", "outcome_unknown": True, "status": "running"})
        result = dispatch(
            self.store, self.actor, self.command("RequestCancel", target=run["id"], revision=1)
        )
        self.assertEqual(result["run"]["data"]["state"], "待核对")
        self.assertTrue(result["run"]["data"]["outcome_unknown"])
        with self.assertRaises(DomainError) as ctx:
            dispatch(
                self.store,
                self.actor,
                self.command("ResolveUnknownOutcome", {"resolved": True}, run["id"], 2),
            )
        self.assertEqual(ctx.exception.code, "RECONCILIATION_REQUIRED")

    def test_unavailable_command_and_spoofed_actor_are_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            dispatch(self.store, self.actor, self.command("PublishToAnyAccount"))
        self.assertEqual(ctx.exception.status, 501)
        cmd = self.command("CreateFactBoundScript", {"text": "a"})
        cmd["actor_id"] = "another"
        with self.assertRaises(DomainError) as ctx:
            dispatch(self.store, self.actor, cmd)
        self.assertEqual(ctx.exception.status, 403)

    def test_budget_reservation_and_actual_cost_not_released(self):
        r = dispatch(
            self.store,
            self.actor,
            self.command("ReserveBudget", {"amount": 7, "limit": 10, "currency": "CNY"}),
        )
        with self.assertRaises(DomainError) as ctx:
            dispatch(
                self.store,
                self.actor,
                self.command("ReserveBudget", {"amount": 4, "limit": 10, "currency": "CNY"}),
            )
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        self.assertEqual(len(self.store.list("alpha", "Cost")), 1)
        cost = r["record"]
        self.store.update(
            cost["id"], {**cost["data"], "actual": 7}, tenant_id="alpha", expected_revision=1
        )
        with self.assertRaises(DomainError):
            dispatch(
                self.store, self.actor, self.command("ReleaseBudget", target=cost["id"], revision=2)
            )

    def test_fencing_token_prevents_old_worker_release(self):
        r = dispatch(
            self.store,
            self.actor,
            self.command("AcquireLease", {"resource_id": self.project["id"], "ttl_seconds": 5}),
        )
        self.assertEqual(r["fencing_token"], 1)
        with self.assertRaises(DomainError):
            dispatch(
                self.store,
                self.actor,
                self.command("AcquireLease", {"resource_id": self.project["id"]}),
            )
        with self.assertRaises(DomainError):
            dispatch(
                self.store,
                self.actor,
                self.command(
                    "ReleaseLease", {"resource_id": self.project["id"], "fencing_token": 0}
                ),
            )
        dispatch(
            self.store,
            self.actor,
            self.command("ReleaseLease", {"resource_id": self.project["id"], "fencing_token": 1}),
        )
        r = dispatch(
            self.store,
            self.actor,
            self.command("AcquireLease", {"resource_id": self.project["id"]}),
        )
        self.assertEqual(r["fencing_token"], 2)

    def test_patch_preserves_unrelated_fields_and_rejects_stale_revision(self):
        rec = self.record(
            "Timeline",
            {
                "tracks": [{"id": "video"}],
                "clip_uses": [
                    {
                        "id": "c",
                        "track_ref": "video",
                        "source_range": [0, 4],
                        "output_range": [0, 4],
                    }
                ],
                "name": "不能变",
            },
        )
        result = dispatch(
            self.store,
            self.actor,
            self.command(
                "ApplyChangeSet",
                {
                    "operations": [
                        {"op": "replace", "path": "/clip_uses/0/output_range", "value": [0, 3]}
                    ]
                },
                rec["id"],
                1,
            ),
        )
        self.assertEqual(result["record"]["data"]["name"], "不能变")
        self.assertEqual(
            self.store.get(rec["id"], "alpha", 1)["data"]["clip_uses"][0]["output_range"], [0, 4]
        )
        self.assertEqual(result["change_set"]["data"]["commit_status"], "committed")


class GraphAndParameterTests(unittest.TestCase):
    def spec(self):
        return {
            "nodes": [
                {"id": "a", "feature_id": "P01", "route_id": "P01-R1", "parameters": {}},
                {"id": "b", "feature_id": "P02", "route_id": "P02-R3", "parameters": {}},
            ],
            "edges": [{"source": "a", "target": "b", "type": "control"}],
            "max_steps": 10,
            "max_attempts": 2,
            "budget": 0,
        }

    def test_valid_typed_workflow_and_bounds(self):
        report = validate_workflow(self.spec())
        self.assertTrue(report["valid"])
        self.assertEqual(report["order"], ["a", "b"])
        s = self.spec()
        del s["max_steps"]
        self.assertFalse(validate_workflow(s)["valid"])

    def test_cycle_dangling_and_wrong_route(self):
        s = self.spec()
        s["edges"].append({"source": "b", "target": "a", "type": "control"})
        self.assertIn("CYCLE", [e["code"] for e in validate_workflow(s)["errors"]])
        s = self.spec()
        s["nodes"][1]["route_id"] = "P01-R1"
        self.assertFalse(validate_workflow(s)["valid"])
        s = self.spec()
        s["edges"][0]["target"] = "ghost"
        self.assertFalse(validate_workflow(s)["valid"])

    def test_navigation_edges_and_unbounded_loops_not_execution(self):
        s = self.spec()
        s["edges"][0]["type"] = "feedback"
        self.assertFalse(validate_workflow(s)["valid"])
        s = self.spec()
        s["loops"] = [{"forever": True}]
        self.assertFalse(validate_workflow(s)["valid"])

    def test_controls_validate_declared_enum_types_and_unknown_fields(self):
        self.assertEqual(validate_controls("P01", {"route_choice": "结构化简报"}), [])
        self.assertTrue(validate_controls("P01", {"route_choice": "not-declared"}))
        self.assertTrue(validate_controls("P01", {"made_up": 4}))
        for fid, f in FEATURES.items():
            for ctrl in f["operation"]["controls"]:
                if ctrl["type"] == "enum" and ctrl.get("options"):
                    self.assertEqual(validate_controls(fid, {ctrl["key"]: ctrl["options"][0]}), [])

    def test_six_layers_orientation_and_explicit_null(self):
        result = resolve_parameters(
            {
                "orientation": "portrait",
                "layers": {
                    "project": {"values": {"x": 1, "color": "blue"}},
                    "sequence": {
                        "values": {"x": 2},
                        "orientation_overrides": {"portrait": {"x": 3}},
                    },
                    "clip": {"values": {"color": {"mode": "override", "value": None}}},
                    "keyframe": {"values": {"x": 4}},
                },
            }
        )
        self.assertEqual(result["effective"], {"x": 4, "color": None})
        self.assertEqual(result["origins"]["x"]["scope"], "keyframe")

    def test_ancestor_lock_yields_proposal_and_keeps_captured_value(self):
        r = resolve_parameters(
            {
                "layers": {
                    "project": {
                        "values": {"x": 1},
                        "locks": [{"path": "x", "owner": "human", "descendants": True}],
                    },
                    "clip": {"values": {"x": 2}},
                }
            }
        )
        self.assertEqual(r["effective"]["x"], 1)
        self.assertFalse(r["valid"])
        self.assertEqual(r["proposals"][0]["value"], 2)

    def test_delete_override_restores_parent_and_missing_value_is_rejected(self):
        self.assertEqual(
            resolve_parameters(
                {
                    "layers": {
                        "project": {"values": {"x": 1}},
                        "clip": {"values": {"x": {"mode": "delete_override"}}},
                    }
                }
            )["effective"]["x"],
            1,
        )
        with self.assertRaises(DomainError):
            resolve_parameters({"layers": {"clip": {"values": {"x": {"mode": "override"}}}}})


class CoreApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AI_SHIP_DATA_DIR": self.tmp.name,
                "AI_SHIP_DB": str(Path(self.tmp.name) / "api.sqlite3"),
                "AI_SHIP_MODE": "local",
            },
        )
        self.env.start()
        from app.main import app

        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def test_health_catalog_and_openapi_are_real(self):
        r = self.client.get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["counts"]["features"], 178)
        r = self.client.get("/api/v1/catalog")
        self.assertEqual(r.status_code, 200)
        self.assertIn("v6", r.json()["data"])
        r = self.client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/v1/commands", r.json()["paths"])

    def test_create_edit_reload_history_and_error_envelope(self):
        r = self.client.post("/api/v1/projects", json={"name": "真实创建", "goals": "测试"})
        self.assertEqual(r.status_code, 201)
        p = r.json()["data"]
        r = self.client.patch(
            "/api/v1/records/" + p["id"],
            json={"expected_revision": 1, "data": {**p["data"], "name": "修订"}},
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/v1/records/" + p["id"] + "/history")
        self.assertEqual(len(r.json()["data"]), 2)
        r = self.client.patch(
            "/api/v1/records/" + p["id"], json={"expected_revision": 1, "data": p["data"]}
        )
        self.assertEqual(r.status_code, 409)
        self.assertIn("trace_id", r.json())

    def test_invalid_project_and_forged_tenant_body_rejected(self):
        for payload in (
            {"name": "", "width": 1},
            {"name": "a", "tenant_id": "beta"},
            {"name": "a", "budget": -2},
        ):
            self.assertEqual(self.client.post("/api/v1/projects", json=payload).status_code, 422)

    def test_cross_origin_and_dns_rebinding_blocked(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/projects", json={"name": "a"}, headers={"Origin": "https://evil.example"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/v1/projects", headers={"Host": "evil.example"}).status_code, 403
        )

    def test_cannot_forge_artifacts_post_receipts_or_model_probe(self):
        for kind in ("Artifact", "Post", "Run", "Cost"):
            r = self.client.post(
                "/api/v1/records", json={"kind": kind, "data": {"status": "completed"}}
            )
            self.assertEqual(r.status_code, 403)
        r = self.client.post(
            "/api/v1/records",
            json={
                "kind": "ModelProfile",
                "data": {
                    "provider": "test",
                    "model_id": "not-a-real-model",
                    "health_status": "healthy",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_team_identity_isolation_and_read_only_role(self):
        keys = {
            "test-secret-a": {"actor_id": "a", "tenant_id": "alpha", "role": "owner"},
            "test-secret-b": {"actor_id": "b", "tenant_id": "beta", "role": "viewer"},
        }
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            self.assertEqual(self.client.get("/api/v1/projects").status_code, 401)
            r = self.client.post(
                "/api/v1/projects",
                headers={"Authorization": "Bearer test-secret-a"},
                json={"name": "A"},
            )
            self.assertEqual(r.status_code, 201)
            rid = r.json()["data"]["id"]
            r = self.client.get(
                "/api/v1/records/" + rid, headers={"Authorization": "Bearer test-secret-b"}
            )
            self.assertEqual(r.status_code, 404)
            r = self.client.post(
                "/api/v1/projects",
                headers={"Authorization": "Bearer test-secret-b"},
                json={"name": "B"},
            )
            self.assertEqual(r.status_code, 403)

    def test_http_only_session_supports_media_identity_and_revocation(self):
        keys = {"test-session-key": {"actor_id": "a", "tenant_id": "alpha", "role": "owner"}}
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            r = self.client.post(
                "/api/v1/session", headers={"Authorization": "Bearer test-session-key"}
            )
            self.assertEqual(r.status_code, 200, r.text)
            cookie = r.headers["set-cookie"].lower()
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=strict", cookie)
            self.assertNotIn("test-session-key", r.text)
            self.assertEqual(self.client.get("/api/v1/projects").status_code, 200)
            with patch.dict(os.environ, {"AI_SHIP_API_KEYS": "{}"}):
                self.assertEqual(self.client.get("/api/v1/projects").status_code, 401)
            self.assertEqual(self.client.delete("/api/v1/session").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/projects").status_code, 401)

    def test_cookie_session_does_not_bypass_origin_guard(self):
        keys = {"test-session-key": {"actor_id": "a", "tenant_id": "alpha", "role": "owner"}}
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            self.client.post(
                "/api/v1/session", headers={"Authorization": "Bearer test-session-key"}
            )
            r = self.client.post(
                "/api/v1/projects",
                json={"name": "must reject"},
                headers={"Origin": "https://evil.example"},
            )
            self.assertEqual(r.status_code, 403)

    def test_exact_four_mib_upload_chunk_and_one_byte_over_limit(self):
        p = self.client.post("/api/v1/projects", json={"name": "分块接口边界"}).json()["data"]
        r = self.client.post(
            "/api/v1/media/uploads",
            json={
                "project_id": p["id"],
                "filename": "chunk-test.bin",
                "size_bytes": 8 * 1024 * 1024,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        entry = r.json()["data"]["entry"]
        block = b"x" * (4 * 1024 * 1024)
        r = self.client.put(
            "/api/v1/media/uploads/" + entry["id"] + "?offset=0",
            content=block,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put(
            "/api/v1/media/uploads/" + entry["id"] + "?offset=" + str(len(block)),
            content=block + b"x",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(r.status_code, 413)
        current = get_store().get(entry["id"], "local")
        self.assertEqual(current["data"]["transferred_bytes"], len(block))

    def test_streamed_json_without_content_length_is_still_limited(self):
        def chunks():
            yield b'{"name":"'
            for _ in range(41):
                yield b"x" * 100000
            yield b'"}'

        r = self.client.post(
            "/api/v1/projects", content=chunks(), headers={"Content-Type": "application/json"}
        )
        self.assertEqual(r.status_code, 413)
        self.assertEqual(get_store().list("local", "Project"), [])

    def test_remote_session_requires_https_and_sets_secure_cookie(self):
        from app.main import app

        keys = {"remote-key": {"actor_id": "a", "tenant_id": "alpha", "role": "owner"}}
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            with TestClient(app, base_url="http://remote.example") as client:
                self.assertEqual(
                    client.post(
                        "/api/v1/session", headers={"Authorization": "Bearer remote-key"}
                    ).status_code,
                    403,
                )
            with TestClient(app, base_url="https://remote.example") as client:
                r = client.post("/api/v1/session", headers={"Authorization": "Bearer remote-key"})
                self.assertEqual(r.status_code, 200)
                self.assertIn("Secure", r.headers["set-cookie"])

    def test_schema_errors_do_not_echo_supplied_secrets(self):
        r = self.client.post("/api/v1/projects", json={"name": "x", "api_key": "never-return-this"})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("never-return-this", r.text)


if __name__ == "__main__":
    unittest.main()
