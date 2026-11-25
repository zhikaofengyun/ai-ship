"""Check immutable output contracts against actual timeline and FFmpeg receipts."""

from pathlib import Path
import subprocess
from uuid import uuid4

from app import batches
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class BatchAcceptanceTests(ProductionBase):
    base = "/production/batches"

    def setUp(self):
        super().setUp()
        path = Path(self.temp.name) / "output-contract.mp4"
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
        result = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)
        self.asset = result["asset"]
        self.version = self.store.list("local", kind="AssetVersion", project_id=self.pid)[0]
        self.recipe = {
            "project_id": self.pid,
            "name": "合同验收",
            "count": 1,
            "output": "render",
            "width": 128,
            "height": 96,
            "fps": 25,
            "slots": [
                {"choices": [{"asset_id": self.asset["id"], "version": 1, "source_out": 0.4}]}
            ],
        }

    def contract(self, requirements, previous=None):
        body = {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "name": "验收",
            "requirements": requirements,
        }
        if previous:
            body["previous"] = previous
        result = self.post(self.base + "/contracts", body)["contract"]
        return {"id": result["id"], "version": result["version"]}

    def submit(self, contract, expected=200):
        return self.post(
            self.base,
            {**self.recipe, "idempotency_key": uuid4().hex, "acceptance_contract": contract},
            expected,
        )

    def run_one(self, bid):
        self.assertTrue(batches.work_once(self.store))
        return self.client.get("/api/v1" + self.base + "/" + bid).json()["data"]

    def audit(self, row):
        return {
            a["stage"]: self.store.get(a["audit_id"], "local")["data"] for a in row["acceptance"]
        }

    def test_versioned_preflight_and_actual_decoded_output(self):
        c = self.contract(
            {
                "width": 128,
                "height": 96,
                "fps": 25,
                "duration_seconds": 0.4,
                "duration_tolerance_seconds": 0.04,
                "min_clip_seconds": 0.3,
                "max_clip_seconds": 0.5,
                "min_distinct_sources": 1,
                "max_generation_calls": 0,
                "require_full_decode": True,
                "require_reference_review": True,
            }
        )
        bid = self.submit(c)["id"]
        changed = self.contract({"width": 720}, c)
        self.submit(changed, 422)
        result = self.run_one(bid)
        self.assertEqual(result["counts"]["succeeded"], 1, result)
        reports = self.audit(result["items"][0])
        for phase, full in [("timeline", "deferred"), ("encoded", "passed")]:
            self.assertEqual(reports[phase]["contract"]["version"], 1)
            checks = {v["name"]: v for v in reports[phase]["checks"]}
            self.assertEqual(checks["require_full_decode"]["status"], full)
            self.assertEqual(checks["width"]["observed"], 128)
            self.assertEqual(checks["require_reference_review"]["observed"], [])
        self.assertEqual(
            self.client.get(
                "/api/v1" + self.base + "/contracts", params={"project_id": self.pid}
            ).json()["data"]["contracts"][0]["version"],
            2,
        )

    def test_missing_role_blocks_before_encode_then_new_evidence_only_retry(self):
        c = self.contract({"min_editorial_roles": 1})
        bid = self.submit(c)["id"]
        result = self.run_one(bid)
        row = result["items"][0]
        self.assertEqual(row["status"], "failed", row)
        self.assertIsNone(row["artifact_id"])
        self.assertEqual(self.audit(row)["timeline"]["checks"][0]["status"], "unknown")
        self.post(
            "/production/tags",
            {
                "project_id": self.pid,
                "asset_version_id": self.version["id"],
                "start": 0,
                "end": 0.6,
                "label": "商品证明",
                "category": "editorial_role",
                "evidence": "实拍样片开头展示外包装细节",
                "locked": True,
            },
        )
        self.post(self.base + "/" + bid + "/retry", {})
        result = self.run_one(bid)
        row = result["items"][0]
        self.assertEqual(row["status"], "succeeded", row)
        self.assertEqual(self.audit(row)["encoded"]["checks"][0]["observed"], ["商品证明"])
        self.assertGreaterEqual(
            len(
                [
                    r
                    for r in self.store.list("local", kind="Audit", project_id=self.pid)
                    if r["data"].get("batch_id") == bid
                ]
            ),
            3,
        )

    def test_encoded_duration_gate_blocks_artifact_that_passed_timeline(self):
        self.recipe["slots"][0]["choices"][0]["source_out"] = 0.20537
        c = self.contract({"duration_seconds": 0.20537, "duration_tolerance_seconds": 0.00001})
        bid = self.submit(c)["id"]
        row = self.run_one(bid)["items"][0]
        self.assertEqual(row["error"]["code"], "BATCH_ACCEPTANCE_FAILED", row)
        self.assertIsNotNone(row["artifact_id"])
        reports = self.audit(row)
        self.assertEqual(reports["timeline"]["status"], "passed")
        self.assertEqual(reports["encoded"]["status"], "blocked")
        self.assertNotEqual(reports["encoded"]["checks"][0]["observed"], 0.20537)
