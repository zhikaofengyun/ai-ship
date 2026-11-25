"""Bulk recipe replay, exact source placement and real multi-output encoding."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import batch_queue, batches
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from app.store import Store
from tests.test_production import ProductionBase


class BatchTests(ProductionBase):
    path = "/production/batches"

    def setUp(self):
        super().setUp()
        path = Path(self.temp.name) / "changes.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=128x96:d=2:r=25",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
        self.asset = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["asset"]
        self.choices = [
            dict(asset_id=self.asset["id"], version=1, source_in=i * 0.4, source_out=i * 0.4 + 0.4)
            for i in range(4)
        ]
        self.spec = {
            "project_id": self.pid,
            "name": "批次测试",
            "idempotency_key": "batch-key",
            "count": 2,
            "slots": [{"choices": self.choices}],
            "width": 128,
            "height": 96,
            "fps": 25,
        }

    def get(self, bid, query=""):
        res = self.client.get("/api/v1" + self.path + "/" + bid + query)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["data"]

    def test_ten_thousand_persisted_ordered_page_and_controls(self):
        spec = {**self.spec, "slots": [{"choices": self.choices}] * 7, "count": 10000}
        saved = self.post(self.path, spec)
        bid = saved["id"]
        self.assertEqual(saved["counts"]["queued"], 10000)
        self.assertEqual(self.post(self.path, spec)["id"], bid)
        self.post(self.path, {**spec, "name": "different"}, 409)
        self.assertEqual(self.get(bid, "?after=9990")["items"][0]["ordinal"], 9991)
        self.assertEqual(self.post(self.path + "/" + bid + "/pause", {})["status"], "paused")
        self.assertIsNone(batch_queue.claim(self.store))
        self.post(self.path + "/" + bid + "/resume", {})
        # Independent database connection sees the durable queue after a process restart.
        restarted = Store(self.store.path)
        batch_queue.initialize(restarted)
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _: batch_queue.claim(restarted), range(12)))
        self.assertEqual(len({r["ordinal"] for r in rows}), 12)
        # Expiring a worker makes its ordinal reclaimable with a new fence.
        r = rows[0]
        with self.store.transaction(), self.store.connection() as db:
            db.execute(
                "UPDATE production_items SET due=0 WHERE batch=? AND ordinal=?", (bid, r["ordinal"])
            )
        recovered = batch_queue.claim(restarted)
        self.assertEqual(recovered["ordinal"], r["ordinal"])
        self.assertGreater(recovered["token"], r["token"])
        with self.assertRaisesRegex(Exception, "工作项已经交由"):
            batch_queue.update(self.store, r, status="succeeded")
        self.assertEqual(
            self.post(self.path + "/" + bid + "/cancel", {})["counts"]["cancelled"], 9988
        )
        self.post(self.path + "/" + bid + "/resume", {}, 409)
        recipe = batches.Recipe.model_validate(spec)
        signatures = {
            tuple(c.source_in for c in batches.choices_for(recipe, i)) for i in range(1, 10001)
        }
        self.assertEqual(len(signatures), 10000)

    def test_legacy_queue_migrates_with_pending_work_and_concurrent_startup(self):
        bid = self.post(self.path, self.spec)["id"]
        with self.store.transaction(), self.store.connection() as db:
            db.execute("DROP INDEX batch_local_ready")
            db.execute("DROP INDEX batch_generation_active")
            db.execute("ALTER TABLE production_items DROP COLUMN needs_generation")
        connections = [Store(self.store.path) for _ in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(batch_queue.initialize, connections))
        with self.store.connection() as db:
            self.assertEqual(
                [
                    r[0]
                    for r in db.execute(
                        "SELECT needs_generation FROM production_items WHERE batch=?", (bid,)
                    )
                ],
                [1, 1],
            )
        for connection in connections[:2]:
            self.assertTrue(batches.work_once(connection))
        self.assertEqual(self.get(bid)["counts"]["succeeded"], 2)

    def test_two_real_renders_and_recover_after_receipt_interruption(self):
        bid = self.post(self.path, {**self.spec, "output": "render"})["id"]
        self.assertTrue(batches.work_once(self.store))
        self.assertTrue(batches.work_once(self.store))
        detail = self.get(bid)
        self.assertEqual(detail["status"], "completed", detail)
        art_ids = []
        for item in detail["items"]:
            artifact = self.store.get(item["artifact_id"], "local")
            self.assertFalse(artifact["data"]["publish_ready"])
            self.assertEqual(artifact["data"]["timeline_id"], item["timeline_id"])
            art_ids.append(artifact["id"])
            result = self.client.get("/api/v1/media/artifacts/" + artifact["id"] + "/file")
            self.assertEqual(
                result.status_code, 200, result.text[:100] if result.status_code != 200 else ""
            )
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "null", "-"],
                input=result.content,
                check=True,
            )
        self.assertEqual(len(set(art_ids)), 2)
        with self.store.transaction(), self.store.connection() as db:
            db.execute(
                "UPDATE production_items SET status='running',artifact_id=NULL,due=0 WHERE batch=? AND ordinal=1",
                (bid,),
            )
        with patch("app.batches.render_timeline", side_effect=AssertionError("repeat render")):
            self.assertTrue(batches.work_once(self.store))
        self.assertEqual(self.get(bid)["items"][0]["artifact_id"], art_ids[0])

    def test_template_replacement_preserves_hand_edits_and_checks_revision(self):
        original = self.create(
            "Timeline",
            {
                "name": "手剪",
                "status": "draft",
                "audio_tracks": [],
                "captions": [{"text": "固定字幕", "start": 0.1, "end": 0.3}],
                "clips": [
                    {
                        "id": "manual-cut",
                        "asset_id": self.asset["id"],
                        "track": 1,
                        "speed": 1,
                        "volume": 0.45,
                        "timeline_start": 0.2,
                        "source_in": 0.0,
                        "source_out": 0.4,
                    }
                ],
                "format": {"width": 128, "height": 96, "fps": 25},
            },
            refs=[{"id": self.asset["id"], "version": 1}],
        )
        spec = {**self.spec, "count": 1, "template": {"id": original["id"], "version": 1}}
        changed = deepcopy(spec)
        changed["slots"][0]["choices"][0]["source_out"] = 0.3
        self.post(self.path, changed, 422)
        bid = self.post(self.path, spec)["id"]
        self.assertTrue(batches.work_once(self.store))
        row = self.get(bid)["items"][0]
        self.assertEqual(row["status"], "succeeded", row)
        result = self.store.get(row["timeline_id"], "local")["data"]
        self.assertEqual(result["captions"], original["data"]["captions"])
        self.assertEqual(result["clips"][0]["volume"], 0.45)
        self.assertEqual(result["clips"][0]["timeline_start"], 0.2)
        self.assertFalse(result["locked"])
        self.assertEqual(self.store.get(original["id"], "local")["version"], 1)
        bid = self.post(self.path, {**spec, "idempotency_key": "retry-stale-key"})["id"]
        self.store.update(
            self.asset["id"],
            {**self.asset["data"], "name": "revised"},
            tenant_id="local",
            expected_revision=1,
        )
        batches.work_once(self.store)
        row = self.get(bid)["items"][0]
        self.assertEqual(row["status"], "failed", row)
        self.assertEqual(row["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.post(self.path + "/" + bid + "/retry", {})["counts"]["queued"], 1)

    def test_batch_dispatches_frozen_dag_input_and_records_child_run(self):
        wf = self.create(
            "Workflow",
            {
                "name": "接管复制",
                "DAG": {
                    "nodes": [
                        {
                            "id": "editor",
                            "feature_id": "E01",
                            "route_id": "E01-R1",
                            "parameters": {},
                            "operation_args": {},
                        }
                    ],
                    "edges": [],
                    "max_steps": 3,
                    "max_attempts": 1,
                    "budget": 0,
                },
            },
        )
        spec = {
            **self.spec,
            "workflow_tail": {
                "id": wf["id"],
                "version": 1,
                "node_id": "editor",
                "input_path": "data",
            },
        }
        wrong = deepcopy(spec)
        wrong["workflow_tail"]["input_path"] = "absent"
        self.post(self.path, wrong, 422)
        bid = self.post(self.path, spec)["id"]
        batches.work_once(self.store)
        detail = self.get(bid)["items"][0]
        self.assertEqual(detail["status"], "succeeded", detail)
        child = self.store.get(detail["workflow_run_id"], "local")
        self.assertEqual(child["data"]["status"], "completed")
        made = child["data"]["checkpoint"]["nodes"]["editor"]["outputs"]["timeline"]
        draft = self.store.get(made["id"], "local")
        self.assertEqual(
            draft["data"]["clips"], self.store.get(detail["timeline_id"], "local")["data"]["clips"]
        )
        self.assertIn(detail["timeline_id"], [r["id"] for r in draft["refs"]])


class BatchGenerationTransport(unittest.TestCase):
    """Use the existing HTTP ComfyUI protocol fixture; returned bytes are verified synthetic video."""

    def setUp(self):
        from tests.test_generation import GenerationTests

        self.transport = GenerationTests()
        self.transport.setUp()
        self.addCleanup(self.transport.doCleanups)
        self.addCleanup(self.transport.tearDown)
        self.store = self.transport.store
        self.path = "/api/v1/production/batches"

    def body(self, *slots):
        return {
            "project_id": self.transport.pid,
            "name": "合成与剪辑",
            "count": 1,
            "idempotency_key": "mixed-batch-key",
            "output": "timeline",
            "slots": [{"generate": s} for s in slots],
        }

    def cycle(self, bid):
        with self.store.transaction(), self.store.connection() as db:
            db.execute(
                "UPDATE production_items SET due=0 WHERE batch=? AND status='waiting'", (bid,)
            )
        self.assertTrue(batches.work_once(self.store))
        return self.transport.client.get(self.path + "/" + bid).json()["data"]

    def test_multiple_generated_slots_reference_receipt_budget_and_resume(self):
        import json
        import os

        t = self.transport
        plain = deepcopy(t.cfg)
        reference, _ = t.reference_setup()
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps({"reference": t.cfg, "plain": plain})
        spec = self.body(
            {"profile_id": "plain", "prompt": "Wide view", "duration": 0.4},
            {
                "profile_id": "reference",
                "prompt": "Close view",
                "duration": 0.6,
                "reference_asset_id": reference["id"],
                "reference_asset_version": 1,
            },
        )
        self.assertEqual(
            t.client.post(self.path, json={**spec, "max_generation_calls": 1}).status_code, 422
        )
        saved = t.client.post(self.path, json={**spec, "max_generation_calls": 2}).json()["data"]
        self.assertEqual(saved["generation_limit"], 2)
        bid = saved["id"]
        d = self.cycle(bid)
        self.assertEqual(d["counts"]["waiting"], 1, d)
        health = t.client.get(
            self.path + "/observability/health", params={"project_id": t.pid}
        ).json()["data"]
        self.assertEqual(health["receipt_items"], {"media_slots": 1, "workflow": 0})
        self.assertEqual(t.submits, 1)
        t.status = "done"
        d = self.cycle(bid)
        self.assertEqual([j["slot"] for j in d["items"][0]["generation_jobs"]], [1, 2], d)
        self.assertEqual(t.submits, 2)
        d = self.cycle(bid)
        self.assertEqual(d["status"], "completed", d)
        self.assertEqual(t.submits, 2)
        item = d["items"][0]
        jobs = item["generation_jobs"]
        draft = self.store.get(item["timeline_id"], "local")
        self.assertEqual([c["timeline_start"] for c in draft["data"]["clips"]], [0, 0.4])
        observed = []
        for j in jobs:
            self.assertEqual(j["status"], "ready")
            self.assertIn(j["generation_id"], [r["id"] for r in draft["refs"]])
            observed.append(self.store.get(j["generation_id"], "local")["data"]["seed"])
        self.assertEqual(len(set(observed)), 2)

    def test_cancel_waiting_second_slot_keeps_first_and_does_not_send_third(self):
        t = self.transport
        spec = self.body(
            *[{"profile_id": "demo", "prompt": f"Shot {i}", "duration": 0.4} for i in range(3)]
        )
        bid = t.client.post(self.path, json=spec).json()["data"]["id"]
        self.cycle(bid)
        t.status = "done"
        self.cycle(bid)
        t.status = "pending"
        t.client.post(self.path + "/" + bid + "/cancel")
        d = self.cycle(bid)
        self.assertEqual(d["status"], "cancelled", d)
        self.assertEqual(
            [j["status"] for j in d["items"][0]["generation_jobs"]], ["ready", "cancelled"]
        )
        self.assertEqual(t.submits, 2)

    def test_frozen_dag_generation_waits_for_original_after_restart(self):
        t = self.transport
        sample = self.transport.client.post(
            "/api/v1/media/import",
            params={"project_id": t.pid, "filename": "local-clip.mp4"},
            content=t.video,
        ).json()["data"]["asset"]
        flow = t.create(
            "Workflow",
            {
                "name": "生成与画面",
                "DAG": {
                    "nodes": [
                        {
                            "id": "editor",
                            "feature_id": "E01",
                            "route_id": "E01-R1",
                            "parameters": {},
                            "operation_args": {},
                        },
                        {
                            "id": "gen",
                            "feature_id": "G03",
                            "route_id": "G03-R2",
                            "parameters": {},
                            "operation_args": {
                                "profile_id": "demo",
                                "prompt": "test candidate",
                                "seed": 3,
                            },
                        },
                    ],
                    "edges": [{"source": "editor", "target": "gen", "type": "control"}],
                    "max_steps": 2,
                    "max_attempts": 1,
                    "budget": 0,
                },
            },
        )
        spec = {
            **self.body(),
            "slots": [
                {
                    "choices": [
                        {"asset_id": sample["id"], "version": 1, "source_in": 0, "source_out": 0.4}
                    ]
                }
            ],
            "workflow_tail": {
                "id": flow["id"],
                "version": 1,
                "node_id": "editor",
                "input_path": "data",
            },
        }
        bad = t.client.post(self.path, json={**spec, "max_generation_calls": 0})
        self.assertEqual(bad.status_code, 422, bad.text)
        saved = t.client.post(self.path, json={**spec, "max_generation_calls": 1}).json()["data"]
        bid = saved["id"]
        waiting = self.cycle(bid)
        self.assertEqual(waiting["counts"]["waiting"], 1, waiting)
        health = t.client.get(
            self.path + "/observability/health", params={"project_id": t.pid}
        ).json()["data"]
        self.assertEqual(health["receipt_items"], {"media_slots": 0, "workflow": 1})
        child = waiting["items"][0]["workflow_run_id"]
        self.assertEqual(t.submits, 1)
        self.assertEqual(self.store.get(child, "local")["data"]["status"], "waiting_receipt")
        t.status = "done"
        with self.store.transaction(), self.store.connection() as db:
            db.execute(
                "UPDATE production_items SET due=0 WHERE batch=? AND status='waiting'", (bid,)
            )
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.batches import work_once; from app.store import get_store; assert work_once(get_store())",
            ],
            check=True,
            timeout=60,
        )
        d = t.client.get(self.path + "/" + bid).json()["data"]
        self.assertEqual(d["counts"]["succeeded"], 1, d)
        self.assertEqual(d["items"][0]["workflow_run_id"], child)
        self.assertEqual(t.submits, 1)
        self.assertEqual(
            self.store.get(child, "local")["data"]["checkpoint"]["nodes"]["gen"]["attempts"], 1
        )

    def test_camera_frame_batch_slot_mixed_with_original_clip(self):
        t = self.transport
        _, _ = t.reference_setup()
        asset = t.client.post(
            "/api/v1/media/import",
            params={"project_id": t.pid, "filename": "camera.mp4"},
            content=t.video,
        ).json()["data"]["asset"]
        spec = self.body(
            {
                "profile_id": "demo",
                "prompt": "Reframe the shot",
                "duration": 0.4,
                "reference_asset_id": asset["id"],
                "reference_asset_version": 1,
                "reference_time_seconds": 0.31,
            }
        )
        spec["slots"].insert(
            0,
            {
                "choices": [
                    {"asset_id": asset["id"], "version": 1, "source_in": 0, "source_out": 0.2}
                ]
            },
        )
        bad = deepcopy(spec)
        bad["slots"][1]["generate"]["reference_time_seconds"] = 2
        self.assertEqual(t.client.post(self.path, json=bad).status_code, 422)
        response = t.client.post(self.path, json=spec)
        self.assertEqual(response.status_code, 200, response.text)
        bid = response.json()["data"]["id"]
        self.cycle(bid)
        t.status = "done"
        complete = self.cycle(bid)
        self.assertEqual(complete["status"], "completed", complete)
        item = complete["items"][0]
        trace = self.store.get(item["generation_jobs"][0]["generation_id"], "local")
        self.assertEqual(trace["data"]["reference_input"]["source_time_seconds"], 0.31)
        draft = self.store.get(item["timeline_id"], "local")
        self.assertEqual(len(draft["data"]["clips"]), 2)
        self.assertEqual(draft["data"]["clips"][0]["asset_id"], asset["id"])
        self.assertEqual(t.submits, 1)

    def test_one_shot_can_choose_camera_or_generation_and_bypass_a_full_window(self):
        t = self.transport
        source = t.client.post(
            "/api/v1/media/import",
            params={"project_id": t.pid, "filename": "camera.mp4"},
            content=t.video,
        ).json()["data"]["asset"]
        spec = self.body({"profile_id": "demo", "prompt": "product reveal", "duration": 0.4})
        spec.update(
            count=5, width=160, height=120, fps=25, output="render", generation_window=1, seed=2
        )
        spec["slots"][0]["choices"] = [
            {"asset_id": source["id"], "version": 1, "source_in": a, "source_out": a + 0.2}
            for a in (0, 0.4)
        ]
        estimate = t.client.post(self.path + "/estimate", json=spec)
        self.assertEqual(estimate.status_code, 200, estimate.text)
        self.assertEqual(
            estimate.json()["data"], {"slot_calls": [3], "workflow_calls": 0, "generation_calls": 3}
        )
        self.assertEqual(t.submits, 0)
        self.assertEqual(
            t.client.get(self.path, params={"project_id": t.pid}).json()["data"]["batches"], []
        )
        self.assertEqual(
            t.client.post(self.path, json={**spec, "max_generation_calls": 2}).status_code, 422
        )
        saved = t.client.post(self.path, json={**spec, "max_generation_calls": 3}).json()["data"]
        bid = saved["id"]
        self.assertEqual(saved["generation_limit"], 3)
        first = self.cycle(bid)
        self.assertEqual(first["items"][0]["status"], "waiting", first)
        self.assertEqual(t.submits, 1)
        # The pending provider receipt holds the sole permit. Both camera-only items
        # must still render while all remaining provider submissions stay parked.
        for _ in range(2):
            self.assertTrue(batches.work_once(self.store))
        self.assertFalse(batches.work_once(self.store))
        partial = t.client.get(self.path + "/" + bid).json()["data"]
        self.assertEqual(partial["counts"]["succeeded"], 2, partial)
        raw_cuts = [
            self.store.get(item["timeline_id"], "local")["data"]["clips"][0]
            for item in partial["items"]
            if item["status"] == "succeeded"
        ]
        self.assertEqual({clip["source_in"] for clip in raw_cuts}, {0, 0.4})
        self.assertTrue(all(clip["asset_id"] == source["id"] for clip in raw_cuts))
        self.assertEqual(t.submits, 1)
        t.status = "done"
        for _ in range(8):
            result = self.cycle(bid)
            if result["status"] == "completed":
                break
        self.assertEqual(result["counts"]["succeeded"], 5, result)
        self.assertEqual(t.submits, 3)
        seeds = set()
        for item in result["items"]:
            if item["generation_jobs"]:
                job = self.store.get(item["generation_id"], "local")
                seeds.add(job["data"]["seed"])
            artifact = self.store.get(item["artifact_id"], "local")
            self.assertTrue(artifact["data"]["qc"]["checks"]["full_decode"])
            res = t.client.get("/api/v1/media/artifacts/" + artifact["id"] + "/file")
            self.assertEqual(res.status_code, 200)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-xerror", "-i", "pipe:0", "-f", "null", "-"],
                input=res.content,
                check=True,
            )
        self.assertEqual(len(seeds), 3)

    def test_ten_thousand_mixed_ordinals_cost_and_recipe_replay(self):
        from app.batch_combinations import dimensions, generation_counts, item_generation_flags

        generate = {"profile_id": "demo", "prompt": "view", "duration": 0.4}
        spec = self.body(generate, generate)
        for i, slot in enumerate(spec["slots"]):
            slot["choices"] = [
                dict(asset_id=f"camera-{j}", version=1, source_out=1) for j in range(3 + i)
            ]
        recipe = batches.Recipe(**{**spec, "seed": 73, "count": 10000})
        hard_counts = [0, 0]
        sources = set()
        replayed = batches.Recipe(**recipe.model_dump())
        flags = item_generation_flags(recipe, 0)
        for ordinal in range(1, recipe.count + 1):
            selected = batches.choices_for(recipe, ordinal)
            self.assertEqual(selected, batches.choices_for(replayed, ordinal))
            self.assertEqual(next(flags), int(None in selected))
            for i, cut in enumerate(selected):
                hard_counts[i] += cut is None
            if all(selected):
                key = tuple(c.asset_id for c in selected)
                self.assertNotIn(key, sources)
                sources.add(key)
        self.assertEqual(len(sources), 12)
        self.assertEqual(
            list(generation_counts(dimensions(recipe.slots), recipe.seed, recipe.count)),
            hard_counts,
        )
