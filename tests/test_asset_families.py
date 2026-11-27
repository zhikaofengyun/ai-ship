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

    def test_complete_link_prevents_transitive_overmerge_and_breaks_ties_stably(self):
        from app.semantic_families import complete_link, suggestions

        edges = {("a", "b"): 0.9, ("b", "c"): 0.9, ("a", "c"): 0.55}
        self.assertEqual(complete_link(["c", "b", "a"], edges, 0.8), [("a", "b"), ("c",)])
        self.assertEqual(complete_link(["a", "b", "c"], edges, 0.55), [("a", "b", "c")])
        # A single matched shot among three unrelated shots cannot group the whole video.
        evidence = {
            a: {"frames": [{"pts_us": i} for i in range(n)]} for a, n in [("a", 1), ("b", 3)]
        }
        parts = suggestions(evidence, {"a": [(1, 0)], "b": [(1, 0), (0, 1), (0, 1)]}, 0.8)
        self.assertEqual(parts, {"groups": [], "unmatched": ["a", "b"]})

    def test_clip_suggest_confirm_revoke_and_revalidate_pinned_frame_evidence(self):
        import hashlib
        import struct
        from unittest.mock import patch

        a, b, c = [self.media(k) for k in ("red", "blue", "green")]
        pins = list(map(pin, (a, b, c)))
        space = "clip-b32/test-space"

        def seed(item, val):
            data = struct.pack("<512f", *val, *([0.0] * 510))
            with self.store.transaction(), self.store.connection() as db:
                db.execute(
                    "INSERT OR REPLACE INTO media_frame_vectors VALUES(?,?,?,?,?,?,?,?)",
                    (
                        "local",
                        self.pid,
                        space,
                        item["id"],
                        item["hash"],
                        0,
                        hashlib.sha256(item["id"].encode()).hexdigest(),
                        data,
                    ),
                )

        cmd = {"members": pins, "threshold": 0.8, "request_id": "suggest-same"}
        with patch("app.frame_vectors.local_model", return_value=None):
            self.assertEqual(self.action("suggest", 503, **cmd)["code"], "CLIP_UNAVAILABLE")
        with patch("app.frame_vectors.local_model", return_value=(space,)):
            seed(a, (1.0, 0.0))
            seed(b, (0.96, 0.28))
            self.assertEqual(self.action("suggest", 409, **cmd)["code"], "CLIP_INDEX_REQUIRED")
            seed(c, (0.0, 1.0))
            audit = self.action("suggest", **cmd)["audit"]
            self.assertEqual(audit["id"], self.action("suggest", **cmd)["audit"]["id"])
            report = audit["data"]
            self.assertEqual(report["unmatched"], [c["id"]])
            suggested = report["groups"][0]
            self.assertEqual(set(suggested["members"]), {a["id"], b["id"]})
            self.assertAlmostEqual(suggested["minimum_score"], 0.96, places=5)
            self.assertEqual(suggested["pairs"][0]["left_pts_us"], 0)
            confirm = {
                "mode": "review_clip",
                "members": pins[:2],
                "audit": pin(audit),
                "name": "复核过的语义类",
                "reason": "人工核对了采样帧",
                "request_id": "review-same",
            }
            self.assertEqual(
                self.action("group", 409, **{**confirm, "members": pins})["code"],
                "CLIP_GROUP_MISMATCH",
            )
            fake = self.create("Audit", {**report, "name": "复制"})
            self.assertEqual(
                self.action("group", 409, **{**confirm, "audit": pin(fake)})["code"],
                "CLIP_AUDIT_REQUIRED",
            )
            group = self.action("group", **confirm)["cluster"]
            self.assertEqual(group["data"]["space"], space)
            self.assertEqual(group["data"]["evidence_id"], audit["id"])
            self.assertEqual(group["id"], self.action("group", **confirm)["cluster"]["id"])
            self.assertEqual(
                self.action(
                    "group", mode="retire", group=pin(group), name="拆开", reason="人工撤销"
                )["cluster"]["data"]["status"],
                "retired",
            )
            with patch("app.frame_vectors.local_model", return_value=("clip-b32/changed",)):
                self.assertEqual(self.action("group", 409, **confirm)["code"], "CLIP_SPACE_CHANGED")
            seed(b, (0.8, 0.6))
            self.assertEqual(self.action("group", 409, **confirm)["code"], "CLIP_EVIDENCE_CHANGED")
            self.assertEqual(self.action("suggest", 409, **cmd)["code"], "CLIP_EVIDENCE_CHANGED")
            seed(b, (float("nan"), 0))
            self.assertEqual(self.action("suggest", 409, **cmd)["code"], "CLIP_INDEX_INVALID")
            seed(b, (0.96, 0.28))
            self.assertEqual(
                self.action(
                    "suggest", 409, **{**cmd, "members": [{**pins[0], "version": 999}, pins[1]]}
                )["code"],
                "STALE_ASSET_INPUT",
            )
            self.assertEqual(
                self.action("suggest", 422, **{**cmd, "members": [pins[0], pins[0]]})["code"],
                "FAMILY_MEMBERS",
            )
            other = self.create("Project", {"name": "另一个项目"})
            self.assertEqual(
                self.action("suggest", 409, **{**cmd, "project_id": other["id"]})["code"],
                "PROJECT_MISMATCH",
            )
            self.assertEqual(
                self.action("suggest", 422, **{**cmd, "members": pins * 11})["code"],
                "REQUEST_VALIDATION",
            )
            src = next(r for r in b["refs"] if r["kind"] == "Source")
            src = self.store.get(src["id"], "local")
            self.store.update(
                src["id"],
                {**src["data"], "status": "revoked"},
                expected_revision=src["version"],
                tenant_id="local",
                actor_id="reviewer",
                refs=src["refs"],
            )
            self.assertEqual(self.action("suggest", 403, **cmd)["code"], "MEDIA_REVOKED")
            self.assertEqual(self.action("group", 403, **confirm)["code"], "MEDIA_REVOKED")
