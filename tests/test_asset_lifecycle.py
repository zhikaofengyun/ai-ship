from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import os
from unittest.mock import patch
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.errors import DomainError
from app.media import blob_path, import_saved_file, resolve_media
from tests.test_production import ProductionBase
from tests.test_selection import pin


class AssetLifecycleTests(ProductionBase):
    def media(self, color, *, quality=0.9, proof=True):
        path = Path(self.temp.name) / (color + ".mp4")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=160x90:r=12",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-y",
                str(path),
            ],
            check=True,
            timeout=25,
        )
        result = import_saved_file(
            self.store, LOCAL_ACTOR, self.pid, path.name, path, "Local synthetic test"
        )
        asset = result["asset"]
        version = result["version"]
        source = self.store.get(asset["data"]["source_id"], "local")
        if proof:
            self.call(
                "processing-evidence",
                source=pin(source),
                evidence="Test owner controls the synthetic video",
            )
        if quality is not None:
            self.post(
                "/production/assets/score",
                {
                    "project_id": self.pid,
                    "asset_version_id": version["id"],
                    "dimensions": {"quality": quality},
                    "weights": {"quality": 1},
                },
            )
        return version

    def call(self, op, status=200, **payload):
        res = self.client.post(
            "/api/v1/production/assets/" + op,
            json={"project_id": self.pid, "request_id": uuid4().hex, **payload},
        )
        self.assertEqual(res.status_code, status, res.text)
        return res.json().get("data") or res.json()["error"]

    def usage(self, v, artifact=None, status="rendered"):
        data = {"asset_version_ref": pin(v), "clip_use_id": uuid4().hex, "status": status}
        if artifact:
            data["artifact_id"] = artifact["id"]
        return self.create("Usage", data, [pin(v)])

    def test_family_pressure_distinguishes_reservations_previews_and_accounts(self):
        a, b = self.media("red"), self.media("blue")
        self.create(
            "Cluster",
            {
                "members": [dict(kind="AssetVersion", **pin(x)) for x in (a, b)],
                "cluster_level": "near",
            },
        )
        artifact = self.create("Artifact", {"name": "project export"})
        self.usage(b, artifact)
        self.usage(b, status="preview")
        self.create(
            "Usage",
            {
                "asset_version_id": a["id"],
                "reservation_status": "reserved",
                "expires_at": "2099-01-01T00:00:00Z",
            },
            [pin(a)],
        )
        account2 = self.create("Account", {"name": "second"})
        obs = self.create("Audit", {"name": "Checked final media"})
        stamp = datetime.now(timezone.utc)
        for acct, expected in [(self.account, True), (account2, True), (self.account, False)]:
            post = self.create(
                "Post",
                {
                    "account_id": acct["id"],
                    "artifact_id": artifact["id"],
                    "audience_scope": "campus",
                    "actual_published_at": stamp.isoformat(),
                    "final_media_status": "verified_same" if expected else "unknown",
                    "media_mapping_evidence_refs": [pin(obs)],
                },
            )
            if expected:
                for i, count in [(-2, 5), (-1, 8)]:
                    self.create(
                        "Metric",
                        {
                            "post_record_id": post["id"],
                            "metric_name": "views",
                            "value_or_null": count,
                            "unit": "views",
                            "counting_mode": "cumulative",
                            "observed_at": (stamp + timedelta(seconds=i)).isoformat(),
                        },
                    )
        rules = {
            "reason": "每周复用",
            "audience": "campus",
            "account_id": self.account["id"],
            "window_days": 7,
            "threshold": 2,
        }
        result = self.call("cooldown", asset=pin(a), rule=rules)
        stats = result["policy"]["data"]["evaluation"]
        self.assertEqual(
            (
                stats["window_rendered"],
                stats["active_reservations"],
                stats["confirmed_posts"],
                stats["unmapped_posts"],
            ),
            (1, 1, 2, 1),
        )
        self.assertEqual(result["score"]["data"]["fatigue"], 1)
        exposure = self.call("cooldown", asset=pin(a), rule={**rules, "mode": "post_views"})
        self.assertEqual(exposure["score"]["data"]["pressure"], 16)

    def test_exploration_preserves_floor_and_scoped_time_limited_exceptions(self):
        new, used, denied, weak = (
            self.media("red"),
            self.media("blue"),
            self.media("green", proof=False),
            self.media("yellow", quality=0.1),
        )
        self.usage(used)
        payload = {
            "assets": [pin(a) for a in (new, used, denied, weak)],
            "slots": 3,
            "exploration_share": 0.34,
            "quality_floor": 0.6,
            "name": "Shortlist",
        }
        selected = self.call("exploration", **payload)["plan"]["data"]
        self.assertEqual([s["pool"] for s in selected["candidates"]], ["exploration", "retained"])
        self.assertEqual(selected["unfilled"], 1)
        self.assertEqual(
            {o["code"] for o in selected["omissions"]},
            {"PROCESSING_SCOPE_UNVERIFIED", "QUALITY_FLOOR"},
        )
        self.call("cooldown", 422, asset=pin(used), rule={"mode": "exclude", "reason": "例外"})
        policy = self.call(
            "cooldown",
            asset=pin(used),
            rule={
                "mode": "exclude",
                "account_id": self.account["id"],
                "reason": "例外",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )["policy"]
        self.call("exploration", 409, **payload, policies=[pin(policy)])
        forced = self.call(
            "exploration", **payload, policies=[pin(policy)], account_id=self.account["id"]
        )["plan"]["data"]
        self.assertEqual(len(forced["candidates"]), 1)
        self.assertIn("ASSET_COOLDOWN", [r["code"] for r in forced["omissions"]])

    def test_cold_restore_preserves_refs_alias_and_index_and_rechecks_bytes_rights(self):
        a = self.media("red")
        pid = self.pid
        req = lambda op, **payload: self.post(
            "/production/asset-index/" + op, {"project_id": pid, **payload}
        )
        build = req("build", mode="shadow", request_key="a", expected_revision=0)
        req("switch", snapshot=build["snapshot"], expected_revision=0, action="activate")
        stored = self.call("tier", asset=pin(a), tier="local_cold", reason="季末归档")
        current = stored["asset"]
        self.assertEqual(
            self.store.get(a["id"], "local", version=1)["data"].get("storage_tier"), None
        )
        for target in (a["id"], a["data"]["asset_id"]):
            with self.assertRaises(DomainError) as caught:
                resolve_media(self.store, LOCAL_ACTOR, target)
            self.assertEqual(caught.exception.code, "ASSET_OFFLINE")
        for channel in ("indexed", "source"):
            self.assertEqual(req("query", channel=channel)["count"], 0)
        self.assertEqual(
            self.call("tier", 409, asset=pin(a), tier="online", reason="过期请求")["code"],
            "STALE_ASSET_INPUT",
        )
        cold_location = self.store.get(current["data"]["cold_location_id"], "local")
        cold_path = blob_path(cold_location, "local")
        expected = cold_path.read_bytes()
        alias = self.store.get(a["data"]["asset_id"], "local")
        source = self.store.get(alias["data"]["source_id"], "local")
        self.store.update(
            source["id"],
            {**source["data"], "status": "revoked"},
            tenant_id="local",
            actor_id="test",
            expected_revision=source["version"],
        )
        self.assertEqual(
            self.call("tier", 403, asset=pin(current), tier="online", reason="复苏")["code"],
            "MEDIA_REVOKED",
        )
        self.store.update(
            source["id"],
            source["data"],
            tenant_id="local",
            actor_id="test",
            expected_revision=source["version"] + 1,
        )
        cold_path.write_bytes(b"damaged")
        self.assertEqual(
            self.call("tier", 409, asset=pin(current), tier="online", reason="复苏")["code"],
            "OBJECT_CORRUPTION",
        )
        cold_path.write_bytes(expected)
        hot = blob_path(self.store.get(a["data"]["blob_location_id"], "local"), "local")
        hot.unlink()
        recovered = self.call("tier", asset=pin(current), tier="online", reason="复苏")
        self.assertEqual(recovered["event"]["data"]["verified_checksum"], a["data"]["content_hash"])
        self.assertEqual(hot.read_bytes(), expected)
        self.assertEqual(req("query", channel="indexed")["count"], 1)

    def test_post_mapping_rejects_different_bytes_and_pins_human_observation(self):
        a, b = self.media("red"), self.media("blue")
        artifact = self.create(
            "Artifact",
            {
                "name": "published cut",
                "blob_location_id": a["data"]["blob_location_id"],
                "content_hash": a["data"]["content_hash"],
            },
        )
        pub = self.create(
            "Publication",
            {
                "artifact_id": artifact["id"],
                "artifact_version": artifact["version"],
                "account_id": self.account["id"],
            },
        )
        post = self.create(
            "Post",
            {
                "publication_id": pub["id"],
                "submitted_artifact_id": artifact["id"],
                "final_media_status": "unknown",
                "account_id": self.account["id"],
            },
        )
        kwargs = {
            "post": pin(post),
            "published_at": datetime.now(timezone.utc).isoformat(),
            "evidence": "下载自所核对的帖子",
            "audience": "campus",
        }
        self.assertEqual(
            self.call("post-mapping", 409, **kwargs, observed_file=pin(b))["code"],
            "FINAL_MEDIA_DIFFERS",
        )
        self.assertEqual(
            self.store.get(post["id"], "local")["data"]["final_media_status"], "unknown"
        )
        result = self.call("post-mapping", **kwargs, observed_file=pin(a))
        self.assertEqual(result["post"]["data"]["final_media_status"], "verified_same")
        self.assertEqual(
            result["post"]["data"]["media_mapping_evidence_refs"][0]["id"], result["audit"]["id"]
        )

    def test_plan_used_by_director_rechecks_version_purpose_and_live_pressure(self):
        a = self.media("red")
        policy = self.call(
            "cooldown",
            asset=pin(a),
            rule={"reason": "保留本周使用额度", "threshold": 1, "hard_limit": True},
        )["policy"]
        payload = {
            "assets": [pin(a)],
            "policies": [pin(policy)],
            "slots": 1,
            "exploration_share": 1,
            "quality_floor": 0.5,
            "name": "导演候选",
        }
        plan = self.call("exploration", **payload)["plan"]
        edit = {
            "project_id": self.pid,
            "goal": "展示红色画面",
            "mode": "local_vision",
            "target_seconds": 1,
            "asset_ids": [a["data"]["asset_id"]],
            "exploration_plan": pin(plan),
            "idempotency_key": uuid4().hex,
        }
        answer = self.post("/production/auto-edits", edit)
        self.assertEqual(answer["session"]["data"]["exploration_plan"], pin(plan))
        self.assertIn(ref_key(plan), {(p["id"], p["version"]) for p in answer["session"]["refs"]})
        self.usage(a)
        error = self.post("/production/auto-edits", {**edit, "idempotency_key": uuid4().hex}, 409)
        self.assertEqual(error["error"]["code"], "ASSET_COOLDOWN")
        self.call("tier", asset=pin(a), tier="quarantine", reason="重新核权")
        req = self.client.post(
            "/api/v1/production/assets/exploration",
            json={"project_id": self.pid, "request_id": uuid4().hex, **payload},
        )
        self.assertEqual(req.status_code, 409)
        self.assertEqual(req.json()["error"]["code"], "STALE_ASSET_INPUT")

    def test_backup_with_only_cold_bytes_restores_and_still_checks_permission(self):
        from scripts.restore import restore
        from app.store import get_store

        a = self.media("purple")
        cooled = self.call("tier", asset=pin(a), tier="local_cold", reason="cold-only backup")[
            "asset"
        ]
        blob = self.store.get(a["data"]["blob_location_id"], "local")
        main_path = blob_path(blob, "local")
        main_path.unlink()
        archive = Path(self.temp.name) / "archived.zip"
        subprocess.run(
            [sys.executable, "scripts/backup.py", "--output", str(archive)],
            check=True,
            capture_output=True,
        )
        target = Path(self.temp.name) / "new-workspace"
        restore(archive, target)
        with patch.dict(
            os.environ,
            {"AI_SHIP_DATA_DIR": str(target), "AI_SHIP_DB": str(target / "ai-ship.sqlite3")},
        ):
            recovered = get_store()
            with self.assertRaises(DomainError) as caught:
                resolve_media(recovered, LOCAL_ACTOR, a["id"])
            self.assertEqual(caught.exception.code, "ASSET_OFFLINE")
            source_id = recovered.get(a["data"]["asset_id"], "local")["data"]["source_id"]
            src = recovered.get(source_id, "local")
            restricted = recovered.update(
                src["id"],
                {**src["data"], "processing_scope": {"allowed": False}},
                tenant_id="local",
                actor_id="test",
                expected_revision=src["version"],
            )
            self.assertEqual(
                self.call("tier", 403, asset=pin(cooled), tier="online", reason="denied")["code"],
                "MEDIA_PROCESSING_DENIED",
            )
            recovered.update(
                src["id"],
                src["data"],
                tenant_id="local",
                actor_id="test",
                expected_revision=restricted["version"],
            )
            self.call("tier", asset=pin(cooled), tier="online", reason="permitted")
            self.assertTrue(resolve_media(recovered, LOCAL_ACTOR, a["id"])[2].is_file())
        # A backup containing the registered cold location without its bytes must fail.
        cold = self.store.get(cooled["data"]["cold_location_id"], "local")
        blob_path(cold, "local").unlink()
        missing = Path(self.temp.name) / "missing.zip"
        rejected = subprocess.run(
            [sys.executable, "scripts/backup.py", "--output", str(missing)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("冷藏副本缺失", rejected.stderr)
        self.assertFalse(missing.exists())


def ref_key(record):
    return record["id"], record["version"]
