from pathlib import Path
import subprocess
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase
from tests.test_selection import pin


class AssetFamilyTests(ProductionBase):
    def media(self, color, variant=0):
        path = Path(self.temp.name) / f"{color}-{variant}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=128x90:r=12",
                "-t",
                "1",
                "-metadata",
                f"comment={variant}",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                str(path),
            ],
            check=True,
        )
        return import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["version"]

    def action(self, which, expected=200, **body):
        r = self.client.post(
            "/api/v1/production/asset-families/" + which,
            json={"request_id": uuid4().hex, "project_id": self.pid, **body},
        )
        self.assertEqual(r.status_code, expected, r.text)
        return r.json().get("data") or r.json()["error"]

    def test_decoded_frames_near_review_and_retirement_recompute_family_pressure(self):
        a, b, c = self.media("red"), self.media("red", 1), self.media("blue")
        results = self.action("compare", left=pin(a), right=pin(b))
        self.assertIsNone(results["cluster"])
        audit = results["audit"]
        self.assertEqual(audit["data"]["classification"], "near_candidate")
        self.assertEqual(len(audit["data"]["left"]["sampled_frames"]), 3)
        self.assertIsNone(audit["data"]["measurements"]["sound"])
        self.assertEqual(
            self.action("compare", left=pin(a), right=pin(c))["audit"]["data"]["classification"],
            "distinct_samples",
        )
        self.create(
            "Usage", {"asset_version_ref": pin(b), "status": "rendered", "clip_use_id": "c"}
        )

        def pressure():
            response = self.post(
                "/production/assets/cooldown",
                {
                    "project_id": self.pid,
                    "request_id": uuid4().hex,
                    "asset": pin(a),
                    "rule": {"reason": "核对压力"},
                },
            )
            return response["score"]["data"]["pressure"]

        self.assertEqual(pressure(), 0)
        group = self.action(
            "group", mode="review_near", audit=pin(audit), name="近似红色", reason="逐段核对"
        )["cluster"]
        self.assertEqual(pressure(), 1)
        retired = self.action(
            "group", mode="retire", group=pin(group), name="拆组", reason="复核后拆开"
        )["cluster"]
        self.assertEqual(retired["version"], 2)
        self.assertEqual(pressure(), 0)
        self.assertEqual(
            self.action(
                "group", 409, mode="retire", group=pin(group), name="旧请求", reason="过期"
            )["code"],
            "STALE_ASSET_INPUT",
        )

    def test_same_bytes_and_same_source_distinct_from_semantic_and_partial_ranges(self):
        a, b = self.media("red"), self.media("red", 1)
        d = self.create("AssetVersion", {**a["data"], "variant": "derivative"}, [pin(a)])
        exact = self.action("compare", left=pin(a), right=pin(d))
        self.assertEqual(exact["cluster"]["data"]["cluster_level"], "exact")
        self.assertEqual(
            exact["audit"]["data"]["left"]["file_sha256"],
            exact["audit"]["data"]["right"]["file_sha256"],
        )
        self.action(
            "group",
            409,
            mode="provenance",
            members=[pin(a), pin(b)],
            name="无法证明",
            reason="笼统说法",
        )
        origin = self.action(
            "group", mode="provenance", members=[pin(a), pin(d)], name="同源版", reason="原始引用"
        )["cluster"]
        self.assertEqual(origin["data"]["source_asset_ids"], [a["data"]["asset_id"]])
        self.action(
            "group", 409, mode="retire", group=pin(origin), name="撤销", reason="尝试覆盖事实"
        )
        partial = self.action("compare", left={**pin(a), "end": 0.4}, right={**pin(b), "end": 0.4})[
            "audit"
        ]
        pg = self.action(
            "group", mode="review_near", audit=pin(partial), name="局部", reason="选段相似"
        )["cluster"]
        self.assertEqual(pg["data"]["comparison_scope"], "segments")
        semantic = self.action(
            "group", mode="semantic", members=[pin(a), pin(b)], name="红色", reason="背景为红色"
        )
        self.assertEqual(semantic["cluster"]["data"]["cluster_level"], "semantic")
        self.create(
            "Usage", {"asset_version_ref": pin(b), "status": "rendered", "clip_use_id": "c"}
        )
        watch = self.post(
            "/production/assets/cooldown",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "asset": pin(a),
                "rule": {"reason": "核对压力"},
            },
        )
        self.assertEqual(watch["score"]["data"]["pressure"], 0)
