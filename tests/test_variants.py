import hashlib
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class StoryboardVariantTests(ProductionBase):
    @classmethod
    def setUpClass(cls):
        cls.files = TemporaryDirectory()
        for word, hz, x in (("LEAF", 125, 5), ("RIVER", 500, 120), ("MOUNTAIN", 2000, 240)):
            f = Path(cls.files.name) / (word + ".mp4")
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=white:s=480x240:r=25:d=1.2",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=f={hz}:d=1.2",
                    "-vf",
                    f"drawbox=x={x}:y=140:w=100:h=90:color=black:t=fill,drawtext=fontcolor=black:fontsize=54:text={word}:x=(w-tw)/2:y=28",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(f),
                ],
                check=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.files.cleanup()

    def setUp(self):
        super().setUp()
        self.assets = []
        self.versions = []
        for name in ("LEAF", "RIVER", "MOUNTAIN"):
            path = Path(self.temp.name) / (name + ".mp4")
            shutil.copyfile(Path(self.files.name) / path.name, path)
            result = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)
            self.assets.append(result["asset"])
            self.versions.append(result["version"])
        self.base = self.post(
            "/production/auto-edits",
            {
                "project_id": self.pid,
                "asset_ids": [a["id"] for a in self.assets[:2]],
                "goal": "制作两段自然短片",
                "mode": "local_vision",
                "target_seconds": 2,
                "width": 320,
                "height": 180,
                "fps": 25,
                "idempotency_key": uuid4().hex,
            },
        )
        self.sess = self.base["session"]
        self.path = "/production/variants/" + self.sess["id"]
        self.body = {
            "expected_revision": self.sess["version"],
            "request_id": "variant-test-0001",
            "count": 3,
            "pool": [self.pin(a) for a in self.assets],
            "seed": 6,
        }
        self.assertEqual(self.sess["data"]["stage"], "review")

    @staticmethod
    def pin(r):
        return {"id": r["id"], "version": r["version"]}

    def select(self, family, candidate=0, expected=200):
        return self.post(
            self.path + "/" + family["id"] + "/select",
            {
                "expected_revision": family["version"],
                "plan": self.pin(family["candidates"][candidate]["plan"]),
                "review_note": "逐镜核对商品画面和节奏。",
            },
            expected,
        )

    def test_actual_picture_text_audio_structure_family_choose_render_and_decode(self):
        f = self.post(self.path, self.body)
        self.assertGreaterEqual(f["actual"], 2)
        self.assertEqual(f["audience_overlap"], "unknown")
        self.assertIsNone(f["platform_duplicate_verdict"])
        stored = self.store.get(f["id"], "local")["data"]
        self.assertTrue(
            all(fp["sound"]["status"] == "measured" for fp in stored["fingerprints"].values())
        )
        self.assertTrue(all(len(fp["frames"]) == 3 for fp in stored["fingerprints"].values()))
        c = f["candidates"][0]
        self.assertGreater(c["vs_base"]["picture"], 0.08)
        self.assertGreater(c["vs_base"]["sound"], 0.1)
        self.assertGreater(c["vs_base"]["structure"], 0)
        if shutil.which("tesseract"):
            self.assertGreater(c["vs_base"]["text"], 0.2)
        adopted = self.select(f)
        self.assertEqual(adopted["plan"]["data"]["base_plan_ref"]["id"], self.base["plan"]["id"])
        self.assertEqual(self.select(f)["session"]["id"], adopted["session"]["id"])
        aid = adopted["session"]["id"]
        d = self.post(
            "/production/auto-edits/" + aid + "/approve",
            {"expected_revision": adopted["session"]["version"]},
        )
        self.assertEqual(
            [s["asset_id"] for s in c["shots"]],
            [s["asset_id"] for s in d["timeline"]["data"]["clips"]],
        )
        out = self.post(
            "/production/auto-edits/" + aid + "/render",
            {
                "expected_revision": d["session"]["version"],
                "timeline_revision": d["timeline"]["version"],
            },
        )
        movie = self.client.get(
            "/api/v1/media/artifacts/" + out["artifact"]["id"] + "/file"
        ).content
        frames = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-map",
                "0:v",
                "-f",
                "framemd5",
                "-",
            ],
            input=movie,
            capture_output=True,
            check=True,
        )
        self.assertGreater(sum(not s.startswith(b"#") for s in frames.stdout.splitlines()), 40)
        self.assertEqual(
            self.store.get(self.base["plan"]["id"], "local")["version"],
            self.base["plan"]["version"],
        )

    def test_replay_conflict_family_reload_and_explicit_zero(self):
        f = self.post(self.path, self.body)
        self.assertEqual(self.post(self.path, self.body)["id"], f["id"])
        self.post(self.path, {**self.body, "seed": 7}, 409)
        listed = self.client.get("/api/v1" + self.path).json()["data"]["families"]
        self.assertEqual([x["id"] for x in listed], [f["id"]])
        z = self.post(self.path, {**self.body, "request_id": "zero", "locked_slots": [0, 1]})
        self.assertEqual(z["actual"], 0)
        self.assertEqual(z["status"], "insufficient")
        self.assertEqual(z["candidates"], [])

    def test_locked_slot_manual_rewrite_and_repeat_history(self):
        f = self.post(self.path, {**self.body, "locked_slots": [0], "count": 1})
        expected = self.base["plan"]["data"]["shots"][0]
        first = f["candidates"][0]["shots"][0]
        self.assertEqual(
            (expected["asset_id"], expected["source_in"], expected["source_out"]),
            (first["asset_id"], first["source_in"], first["source_out"]),
        )
        manual = [
            {
                **self.pin(next(x for x in self.assets if x["id"] == s["asset_id"])),
                "source_in": s["source_in"],
                "source_out": s["source_out"],
            }
            for s in f["candidates"][0]["shots"]
        ]
        b = {
            **self.body,
            "route": "N04-R3",
            "count": 1,
            "request_id": "manual-history",
            "manual_shots": manual,
            "locked_slots": [0],
            "manual_reason": "保留品牌开场，更换后段论据。",
        }
        dupe = self.post(self.path, b)
        self.assertEqual(dupe["actual"], 0)
        self.assertEqual(dupe["rejected"]["duplicates_history"], 1)
        self.assertEqual(
            self.post(self.path, {**b, "request_id": "manual-new", "history_days": 0})["actual"], 1
        )
        self.post(
            self.path,
            {
                **b,
                "request_id": "lock-conflict",
                "manual_shots": [self.pin(self.assets[2]), manual[1]],
            },
            409,
        )

    def test_quota_committed_vs_reserved_and_policy_approval(self):
        sid = self.assets[2]["id"]
        self.create("Usage", {"asset_id": sid, "status": "reserved"})
        self.create(
            "Usage",
            {
                "asset_id": sid,
                "status": "rendered",
                "artifact_id": self.create("Artifact", {"name": "Local test deliverable"})["id"],
            },
        )
        p = self.create("Policy", {"release_state": "draft", "typed_config": {"max_uses": 1}})
        b = {**self.body, "route": "N04-R2", "policy": self.pin(p), "locked_slots": [0]}
        self.post(self.path, b, 409)
        p = self.store.update(
            p["id"],
            {**p["data"], "release_state": "approved"},
            tenant_id="local",
            expected_revision=p["version"],
            actor_id="test",
        )
        f = self.post(self.path, {**b, "policy": self.pin(p)})
        self.assertEqual(f["history_usage"][sid], 1)
        self.assertEqual(f["max_uses"], 1)
        self.assertEqual(f["actual"], 0)
        self.assertGreater(f["rejected"].get("reuse_quota", 0), 0)

    def test_product_guard_locked_interval_and_changed_fact(self):
        src = self.create(
            "Source", {"name": "SKU supplier", "provenance": "reviewed product sheet"}
        )
        product = self.post(
            "/production/products/import",
            {
                "project_id": self.pid,
                "source_id": src["id"],
                "csv_text": "SKU,price,currency,valid_until,name,evidence\nLEAF,10,USD,2099-01-01T00:00:00Z,Product,paper",
            },
        )["records"][0]
        b = {**self.body, "products": [self.pin(product)]}
        self.assertEqual(self.post(self.path, b)["actual"], 0)
        for version in self.versions:
            self.post(
                "/production/tags",
                {
                    "project_id": self.pid,
                    "asset_version_id": version["id"],
                    "start": 0,
                    "end": 1.2,
                    "label": "LEAF",
                    "category": "SKU",
                    "evidence": "Reviewed reference image and shelf SKU",
                    "locked": True,
                },
            )
        f = self.post(self.path, {**b, "request_id": "tagged"})
        self.assertGreater(f["actual"], 0)
        self.store.update(
            product["id"],
            {**product["data"], "price": "11"},
            tenant_id="local",
            expected_revision=product["version"],
            actor_id="test",
        )
        self.select(f, expected=409)

    def test_revision_scope_and_usage_revocation(self):
        f = self.post(self.path, self.body)
        self.post(self.path, {**self.body, "request_id": "stale", "expected_revision": 1}, 409)
        other = self.create("Project", {"name": "Other project"})
        file = Path(self.temp.name) / "other.mp4"
        shutil.copyfile(Path(self.files.name) / "LEAF.mp4", file)
        outsider = import_saved_file(self.store, LOCAL_ACTOR, other["id"], file.name, file)["asset"]
        self.post(
            self.path, {**self.body, "request_id": "other", "pool": [self.pin(outsider)]}, 403
        )
        s = f["candidates"][0]["shots"][0]["asset_id"]
        a = self.store.get(s, "local")
        self.store.update(
            s,
            {**a["data"], "rights_status": "revoked"},
            tenant_id="local",
            expected_revision=a["version"],
            actor_id="test",
        )
        self.select(f, expected=403)
