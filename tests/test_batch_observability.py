"""Read-only operations projections over real batch and render receipts."""

from pathlib import Path
import subprocess

from app import batch_queue, batches
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class BatchObservabilityTests(ProductionBase):
    path = "/production/batches"

    def setUp(self):
        super().setUp()
        path = Path(self.temp.name) / "observation.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=128x96:d=1:r=25",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                str(path),
            ],
            check=True,
        )
        self.asset = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["asset"]
        self.spec = {
            "project_id": self.pid,
            "name": "监测样本",
            "count": 2,
            "output": "render",
            "width": 128,
            "height": 96,
            "fps": 25,
            "slots": [
                {
                    "choices": [
                        {
                            "asset_id": self.asset["id"],
                            "version": 1,
                            "source_in": 0,
                            "source_out": 0.4,
                        },
                        {
                            "asset_id": self.asset["id"],
                            "version": 1,
                            "source_in": 0.4,
                            "source_out": 0.8,
                        },
                    ]
                }
            ],
        }

    def batch(self, key, **changes):
        return self.post(self.path, {**self.spec, "idempotency_key": "obs-test-" + key, **changes})[
            "id"
        ]

    def get(self, kind, expected=200, **args):
        r = self.client.get(
            "/api/v1" + self.path + "/observability/" + kind,
            params={"project_id": self.pid, **args},
        )
        self.assertEqual(r.status_code, expected, r.text)
        return r.json().get("data", {})

    def test_health_counts_expired_worker_failed_code_and_enqueue_age(self):
        bid = self.batch("age", output="timeline")
        health = self.get("health")
        self.assertEqual(health["counts"]["queued"], 2)
        self.assertGreaterEqual(health["queued_oldest_seconds"], 0)
        first = batch_queue.claim(self.store)
        with self.store.transaction(), self.store.connection() as db:
            db.execute(
                "UPDATE production_items SET due=0 WHERE batch=? AND ordinal=?",
                (bid, first["ordinal"]),
            )
        self.assertEqual(self.get("health")["expired_leases"], 1)
        # Claim the expired ordinal under a fresh fence, fail it, then pause the remaining item.
        recovered = batch_queue.claim(self.store)
        batch_queue.update(
            self.store,
            recovered,
            status="failed",
            error={"code": "TEST_QUOTA", "message": "合成故障"},
        )
        self.post(self.path + "/" + bid + "/pause", {})
        health = self.get("health")
        self.assertIsNone(health["queued_oldest_seconds"])
        self.assertEqual(health["paused_batches"], 1)
        self.assertEqual(health["failures"], [{"code": "TEST_QUOTA", "count": 1}])
        self.assertEqual(health["expired_leases"], 0)
        self.assertEqual(health["waiting_receipts"], 0)

    def test_comparison_same_outputs_with_fixed_sample_and_reject_mismatched_scope(self):
        a = self.batch("before")
        b = self.batch("after")
        in_progress = self.get("compare", left=a, right=b)
        self.assertFalse(in_progress["comparison"]["comparable"])
        for _ in range(4):
            self.assertTrue(batches.work_once(self.store))
        before = self.store.list("local", "Artifact", self.pid)
        data = self.get("compare", left=a, right=b)
        self.assertTrue(data["comparison"]["comparable"], data)
        self.assertGreater(data["comparison"]["throughput_ratio"], 0)
        self.assertEqual((data["additional_inference_calls"], data["additional_renders"]), (0, 0))
        self.assertIsNone(data["left"]["cost"]["actual_gpu_cost"])
        self.assertEqual(data["sample"], self.get("compare", left=a, right=b)["sample"])
        self.assertEqual([p["ordinal"] for p in data["sample"]], [1, 2])
        self.assertTrue(
            all(
                p["same_clip_sequence"] and p["same_file"] and p["left"]["full_decode"]
                for p in data["sample"]
            )
        )
        self.assertEqual(len(before), len(self.store.list("local", "Artifact", self.pid)))
        other = self.batch("timeline", output="timeline", height=128, width=96)
        mixed = self.get("compare", left=a, right=other)
        self.assertIsNone(mixed["comparison"]["throughput_ratio"])
        self.assertIn("output_kind", mixed["comparison"]["reasons"])
        self.assertIn("frame_format", mixed["comparison"]["reasons"])
        self.get("compare", 422, left=a, right=a)
        elsewhere = self.create("Project", {"name": "独立项目"})["id"]
        self.get("compare", 409, left=a, right=b, project_id=elsewhere)
