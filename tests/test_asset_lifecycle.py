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

    def test_source_admission_expiry_reconciles_real_derivative_and_preserves_shared_bytes(self):
        version = self.media("green", quality=None, proof=False)
        asset = self.store.get(version["data"]["asset_id"], "local")
        source = self.store.get(asset["data"]["source_id"], "local")
        base = {"source": pin(source), "reason": "核对入库单与使用范围"}
        file_endpoint = "/api/v1/media/" + asset["id"] + "/file"

        self.call("admission", **base, action="reject", expected_revision=0)
        self.assertEqual(self.client.get(file_endpoint).status_code, 200)
        self.assertEqual(self.client.get(file_endpoint + "?download=true").status_code, 403)
        self.call(
            "admission",
            409,
            **base,
            action="approve",
            allowed_purposes=["process"],
            expected_revision=1,
        )
        self.call("admission", **base, action="review", expected_revision=1)
        self.call(
            "admission",
            **base,
            action="restrict",
            allowed_purposes=["process", "inspect"],
            expected_revision=2,
        )
        from app.media import transform_asset

        version = self.store.get(version["id"], "local")
        self.assertEqual(version["data"]["admission_projection"]["scope_status"], "restrict")
        child = transform_asset(
            self.store,
            LOCAL_ACTOR,
            version["id"],
            {
                "project_id": self.pid,
                "expected_revision": version["version"],
                "recipe": {"width": 80, "height": 44},
            },
        )["version"]
        self.assertEqual(
            self.client.get("/api/v1/media/" + child["id"] + "/file?download=true").status_code, 403
        )
        self.call("retention-reconcile", 409, **base, expected_revision=3)

        project2 = self.create("Project", {"name": "Independent rights for same verified bytes"})
        path = Path(self.temp.name) / "green.mp4"
        unrelated = import_saved_file(
            self.store, LOCAL_ACTOR, project2["id"], path.name, path, "Independent upload"
        )
        self.assertEqual(unrelated["asset"]["data"]["content_hash"], asset["data"]["content_hash"])
        self.create("Post", {"status": "published"}, [pin(child)])
        self.call("admission", 409, **base, action="quarantine", expected_revision=2)
        expired = self.call(
            "admission",
            **base,
            action="approve",
            allowed_purposes=["inspect", "process", "export"],
            retain_until="2020-01-01T00:00:00Z",
            expected_revision=3,
        )
        self.assertEqual(expired["scope_status"], "expired")
        for item in [asset, child]:
            self.assertEqual(
                self.client.get("/api/v1/media/" + item["id"] + "/file").status_code, 403
            )
        body = {"project_id": self.pid, "request_id": uuid4().hex, **base, "expected_revision": 4}
        result = self.post("/production/assets/retention-reconcile", body)
        again = self.post("/production/assets/retention-reconcile", body)
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(result["audit"]["id"], again["audit"]["id"])
        summary = result["summary"]
        self.assertGreaterEqual(summary["counts_by_kind"]["AssetVersion"], 2)
        self.assertEqual(summary["shared_objects_retained"], 1)
        self.assertEqual(summary["physical_objects_removed"], 0)
        self.assertEqual(summary["external_verification_pending"], 1)
        self.assertEqual(
            self.client.get("/api/v1/media/" + unrelated["asset"]["id"] + "/file").status_code, 200
        )
        url = "/api/v1/production/assets/retention-reconcile/" + result["audit"]["id"]
        first = self.client.get(url, params={"project_id": self.pid, "limit": 1}).json()["data"]
        self.assertEqual((len(first["items"]), first["next_offset"]), (1, 1))
        details = self.client.get(url, params={"project_id": self.pid}).json()["data"]["items"]
        self.assertIn(child["id"], [i["id"] for i in details])
        self.assertEqual(len(details), summary["total_versions"])
        self.call("admission", 409, **base, action="review", expected_revision=5)
        head = self.client.get(
            "/api/v1/production/assets/admission",
            params={"project_id": self.pid, "source_id": source["id"]},
        )
        self.assertEqual(head.json()["data"]["scope_status"], "reconciled")

    def test_restore_reconciled_tombstone_and_fence_preexisting_worker(self):
        from scripts.restore import restore
        from app.store import Store

        version = self.media("gold", quality=None, proof=False)
        asset = self.store.get(version["data"]["asset_id"], "local")
        source = self.store.get(asset["data"]["source_id"], "local")
        base = {"source": pin(source), "reason": "Retire permanently"}
        self.call(
            "admission",
            **base,
            action="approve",
            allowed_purposes=["inspect", "process"],
            expected_revision=0,
        )
        with self.store.transaction(), self.store.connection() as db:
            db.execute(
                "INSERT INTO leases VALUES(?,?,?,?,?)",
                ("local", "run-historical", "worker", 3, "2099-01-01T00:00:00Z"),
            )
        archive = Path(self.temp.name) / "before-retirement.zip"
        subprocess.run(
            [sys.executable, "scripts/backup.py", "--output", str(archive)],
            check=True,
            capture_output=True,
        )
        self.call("admission", **base, action="quarantine", expected_revision=1)
        original = self.call("retention-reconcile", **base, expected_revision=2)
        with self.store.transaction(), self.store.connection() as db:
            db.execute("UPDATE leases SET fencing_token=8")
        target = Path(self.temp.name) / "after-retirement"
        result = restore(archive, target, current_db=self.store.path)
        self.assertEqual(
            result["governance"],
            {
                "sources_checked": 1,
                "media_or_grants_held": 0,
                "sources_isolated": 1,
                "leases_fenced": 1,
                "authority_available": True,
            },
        )
        restored = Store(target / "ai-ship.sqlite3")
        with self.assertRaises(DomainError) as exc:
            resolve_media(restored, LOCAL_ACTOR, version["id"], purpose="inspect")
        self.assertEqual(exc.exception.code, "MEDIA_RETENTION_BLOCK")
        with restored.connection() as db:
            row = db.execute(
                "SELECT fencing_token,expires_at FROM leases WHERE resource_id='run-historical'"
            ).fetchone()
            self.assertGreater(row[0], 8)
            self.assertLess(row[1], "2000")
            decision_id = db.execute(
                "SELECT decision_id FROM media_admission_log ORDER BY revision DESC LIMIT 1"
            ).fetchone()[0]
        decision = restored.get(decision_id, "local")["data"]
        self.assertEqual(decision["action"], "reconciled")
        self.assertEqual(
            decision["current_decision_id"], original["audit"]["data"]["decision_ref"]["id"]
        )

    def test_restore_blocks_changed_grant_through_unchanged_source_and_dedup_resolves_owner(self):
        from scripts.restore import restore
        from app.store import Store

        a = self.media("cyan", quality=None, proof=False)
        b = self.media("blue", quality=None, proof=False)
        asset = self.store.get(a["data"]["asset_id"], "local")
        src = self.store.get(asset["data"]["source_id"], "local")
        grant = self.create("Grant", {"status": "active"})
        source = self.store.update(
            src["id"],
            {**src["data"], "grant_ids": [grant["id"]]},
            tenant_id="local",
            expected_revision=1,
            refs=[pin(grant)],
        )
        path = Path(self.temp.name) / "cyan.mp4"
        duplicate = import_saved_file(
            self.store, LOCAL_ACTOR, self.pid, path.name, path, "Repeated import"
        )
        alias = duplicate["import_entry"]["data"]["source_id"]
        state = self.client.get(
            "/api/v1/production/assets/admission",
            params={"project_id": self.pid, "source_id": alias},
        ).json()["data"]
        self.assertEqual(state["requested_source"]["id"], alias)
        self.assertEqual(state["source"]["id"], source["id"])
        self.assertEqual(
            self.call(
                "admission",
                409,
                source={"id": alias, "version": 1},
                expected_revision=0,
                action="review",
                reason="Check alias",
            )["code"],
            "ADMISSION_CANONICAL_SOURCE",
        )
        archive = Path(self.temp.name) / "pre-grant-change.zip"
        subprocess.run(
            [sys.executable, "scripts/backup.py", "--output", str(archive)],
            check=True,
            capture_output=True,
        )
        self.store.update(
            grant["id"], {"status": "revoked"}, tenant_id="local", expected_revision=1
        )
        target = Path(self.temp.name) / "restore-grant"
        result = restore(archive, target, current_db=self.store.path)
        self.assertEqual(result["governance"]["media_or_grants_held"], 1)
        restored = Store(target / "ai-ship.sqlite3")
        with self.assertRaises(DomainError) as exc:
            resolve_media(restored, LOCAL_ACTOR, a["id"], purpose="export")
        self.assertEqual(exc.exception.code, "MEDIA_RESTORE_HOLD")
        self.assertEqual(exc.exception.details["record_id"], grant["id"])
        with patch.dict(os.environ, {"AI_SHIP_DATA_DIR": str(target)}):
            self.assertEqual(resolve_media(restored, LOCAL_ACTOR, b["id"])[0]["id"], b["id"])

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

    def test_generic_edits_preserve_archive_and_cannot_mint_computed_selection(self):
        a = self.media("orange")
        policy = self.call("cooldown", asset=pin(a), rule={"reason": "控制使用"})["policy"]
        payload = {
            "assets": [pin(a)],
            "policies": [pin(policy)],
            "slots": 1,
            "exploration_share": 1,
            "quality_floor": 0.6,
            "name": "本机选材",
        }
        plan = self.call("exploration", **payload)["plan"]

        def patch_record(r, data, code=200):
            result = self.client.patch(
                "/api/v1/records/" + r["id"], json={"data": data, "expected_revision": r["version"]}
            )
            self.assertEqual(result.status_code, code, result.text)
            return result.json().get("data")

        for item in (policy, plan):
            result = self.client.post(
                "/api/v1/records",
                json={"kind": item["kind"], "project_id": self.pid, "data": item["data"]},
            )
            self.assertEqual(result.status_code, 403, result.text)
        patch_record(policy, {**policy["data"], "typed_config": {"mode": "retain"}}, 403)
        patch_record(plan, {**plan["data"], "candidates": []}, 403)
        renamed = patch_record(plan, {"name": "编辑显示名称"})
        self.assertEqual(renamed["data"]["candidates"], plan["data"]["candidates"])
        from app.asset_lifecycle import verify_exploration_selection

        self.assertEqual(
            verify_exploration_selection(LOCAL_ACTOR, self.pid, pin(renamed), [a["id"]])["id"],
            plan["id"],
        )
        # An old manually constructed draft has no action receipt, even with copied fields.
        draft = self.create("Plan", plan["data"], plan["refs"])
        with self.assertRaises(DomainError) as denied:
            verify_exploration_selection(LOCAL_ACTOR, self.pid, pin(draft), [a["id"]])
        self.assertEqual(denied.exception.code, "UNVERIFIED_ASSET_PROJECTION")
        archived = self.call("tier", asset=pin(a), tier="local_cold", reason="季末存档")["asset"]
        original = self.store.get(a["data"]["asset_id"], "local")
        for item in (archived, original):
            patch_record(item, {**item["data"], "storage_tier": "online"}, 403)
            modified = patch_record(item, {"name": "档案显示名称"})
            self.assertEqual(modified["data"]["storage_tier"], "local_cold")
            self.assertEqual(modified["data"]["blob_location_id"], a["data"]["blob_location_id"])
            with self.assertRaises(DomainError) as caught:
                resolve_media(self.store, LOCAL_ACTOR, modified["id"])
            self.assertEqual(caught.exception.code, "ASSET_OFFLINE")
        older_alias = self.create("AssetVersion", a["data"], a["refs"])
        with self.assertRaises(DomainError) as cannot_use:
            resolve_media(self.store, LOCAL_ACTOR, older_alias["id"])
        self.assertEqual(cannot_use.exception.code, "ASSET_OFFLINE")
        ordinary = self.create("Plan", {"feature_id": "U05", "name": "自由编排草稿"})
        self.assertEqual(patch_record(ordinary, {"name": "普通修改"})["data"]["name"], "普通修改")


def ref_key(record):
    return record["id"], record["version"]
