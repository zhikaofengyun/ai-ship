"""Cross-domain regression with isolated real SQLite; all fixtures are synthetic."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.auth import LOCAL_ACTOR
from app.auth import Actor
from app.errors import DomainError
from app.main import app
from app.production import resolve_schedule
from app.providers import ChatInput, chat
from app.store import get_store


class ProductionBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AI_SHIP_MODE": "local",
                "AI_SHIP_DATA_DIR": self.temp.name,
                "AI_SHIP_DB": self.temp.name + "/test.db",
                "AI_SHIP_PROVIDERS": "{}",
            },
        )
        self.env.start()
        self.client = TestClient(app)
        self.store = get_store()
        self.project = self.create(
            "Project", {"name": "Synthetic regression project", "budget": 10}
        )
        self.pid = self.project["id"]
        self.account = self.create(
            "Account",
            {
                "provider": "manual",
                "identity_status": "manual_reference",
                "timezone": "Asia/Shanghai",
            },
        )

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def create(self, kind, data, refs=None):
        return self.store.create(
            kind,
            data,
            tenant_id="local",
            project_id=getattr(self, "pid", None),
            actor_id="test",
            refs=refs or [],
            reason="Synthetic test fixture",
        )

    def post(self, route, data, expected=200):
        result = self.client.post("/api/v1" + route, json=data)
        self.assertEqual(result.status_code, expected, result.text)
        return result.json().get("data", result.json())

    def metric(self, **changes):
        return {
            "account_id": self.account["id"],
            "metric_name": "views",
            "value": 0,
            "unit": "count",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-02T00:00:00Z",
            "observed_at": "2026-01-03T00:00:00Z",
            "source": "synthetic fixture",
            "paid_or_organic": "organic",
            **changes,
        }


class ProductionIntegrationTests(ProductionBase):
    def test_trend_source_window_and_atomicity(self):
        request = {
            "project_id": self.pid,
            "title": "Recorded observation",
            "canonical_url": "https://example.test/source",
            "platform": "manual",
            "observed_at": "2026-01-01T00:00:00Z",
            "expires_at": "2026-01-02T00:00:00Z",
            "evidence": "Synthetic observation, not real trend data",
        }
        trend = self.post("/production/trends", request)
        self.assertEqual(trend["data"]["status"], "expired")
        self.assertEqual(len(trend["refs"]), 1)
        self.post("/production/trends", {**request, "expires_at": "2025-12-31T00:00:00Z"}, 422)
        self.assertEqual(len(self.store.list("local", "Trend", self.pid)), 1)
        self.post("/production/trends", {**request, "canonical_url": "file:///etc/passwd"}, 422)

    def test_product_csv_money_exact_and_bad_rows_isolated(self):
        source = self.create("Source", {"provenance": "Synthetic price list"})
        result = self.post(
            "/production/products/import",
            {
                "project_id": self.pid,
                "source_id": source["id"],
                "csv_text": "SKU,price,currency,valid_until,quantity\nSKU01,19.9900,CNY,2099-01-01T00:00:00Z,2\nSKU02,-1,CNY,2099-01-01T00:00:00Z,1\nSKU01,20,CNY,2099-01-01T00:00:00Z,1\n",
            },
        )
        self.assertEqual((result["imported"], result["rejected"]), (1, 2))
        self.assertEqual(result["records"][0]["data"]["price"], "19.9900")

    def test_tag_range_and_versioned_manual_evidence(self):
        asset = self.create("Asset", {"name": "Synthetic media"})
        version = self.create(
            "AssetVersion", {"asset_id": asset["id"], "duration": 2, "status": "available"}
        )
        body = {
            "project_id": self.pid,
            "asset_version_id": version["id"],
            "start": 0.2,
            "end": 1.5,
            "label": "SKU-ONE",
            "category": "SKU",
            "evidence": "Human marked test segment",
        }
        tagged = self.post("/production/tags", body)
        self.assertTrue(tagged["tag"]["data"]["reviewer_lock"])
        self.assertIsNone(tagged["tag"]["data"]["confidence"])
        self.post("/production/tags", {**body, "end": 2.2}, 422)
        result = self.client.get(
            "/api/v1/production/search", params={"project_id": self.pid, "q": "SKU-ONE"}
        ).json()["data"]
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["semantic_search"], "not_connected")

    def test_search_excludes_current_revoked_source(self):
        source = self.create("Source", {"status": "active"})
        asset = self.create("Asset", {"source_id": source["id"], "name": "Synthetic media"})
        self.create("AssetVersion", {"asset_id": asset["id"], "status": "available"})
        self.store.update(
            source["id"], {"status": "revoked"}, tenant_id="local", expected_revision=1
        )
        result = self.client.get(
            "/api/v1/production/search", params={"project_id": self.pid}
        ).json()["data"]
        self.assertEqual(result["items"], [])

    def test_usage_reservation_conflict_release_and_history(self):
        shot = self.create("Shot", {"source_in": 0, "source_out": 1})
        owner1 = self.create("Plan", {"name": "First plan"})
        owner2 = self.create("Plan", {"name": "Second plan"})
        body = {
            "project_id": self.pid,
            "shot_id": shot["id"],
            "owner_ref": owner1["id"],
            "ttl_seconds": 60,
        }
        lease = self.post("/production/usage/reserve", body)
        with self.store.transaction():
            for i in range(501):
                self.create("Usage", {"clip_use_id": f"unrelated-{i}", "status": "rendered"})
        duplicate = self.post("/production/usage/reserve", body)
        self.assertEqual(duplicate["id"], lease["id"])
        self.post("/production/usage/reserve", {**body, "owner_ref": owner2["id"]}, 409)
        self.post(f"/production/usage/{lease['id']}/release", {"expected_revision": 1})
        self.post("/production/usage/reserve", {**body, "owner_ref": owner2["id"]})
        self.assertEqual(len(self.store.history(lease["id"], "local")), 2)

    def test_score_missing_dimensions_not_zero(self):
        version = self.create("AssetVersion", {"name": "Synthetic version"})
        score = self.post(
            "/production/assets/score",
            {
                "project_id": self.pid,
                "asset_version_id": version["id"],
                "dimensions": {"quality": 0.8, "performance": None},
                "weights": {"quality": 1, "performance": 1},
            },
        )
        self.assertEqual(score["data"]["observed_score"], 0.8)
        self.assertEqual(score["data"]["coverage"], 0.5)

    def test_voice_alignment_untimed_range_and_script_versions(self):
        script = self.create("Script", {"text": "两个杯子十九元"})
        audio = self.create("Audio", {"duration": 2, "bytes_hash": "synthetic"})
        body = {
            "project_id": self.pid,
            "script_id": script["id"],
            "audio_id": audio["id"],
            "anchors": [
                {
                    "token": "两个杯子",
                    "start": 0,
                    "end": 1,
                    "timing_status": "verified",
                    "evidence": "Human checked synthetic clip",
                },
                {"token": "十九元", "timing_status": "untimed"},
            ],
        }
        aligned = self.post("/production/voice/align", body)
        self.assertIsNone(aligned["data"]["anchors"][1]["time_range"])
        self.assertEqual(aligned["data"]["script_version"]["version"], 1)
        body["anchors"][0]["token"] = "三百元"
        self.post("/production/voice/align", body, 422)

    def test_half_open_multi_contributor_reverse_time_map(self):
        mapping = self.create(
            "TimeMap",
            {
                "segments": [
                    {"output_range": [0, 2], "source_range": [1, 5], "source_ref": "synthetic-a"},
                    {"output_range": [1, 2], "source_range": [8, 7], "source_ref": "synthetic-b"},
                ]
            },
        )
        result = self.post(
            "/production/time-map/trace", {"time_map_id": mapping["id"], "output_seconds": 1.5}
        )
        self.assertEqual([x["source_seconds"] for x in result["contributors"]], [4, 7.5])
        result = self.post(
            "/production/time-map/trace", {"time_map_id": mapping["id"], "output_seconds": 2}
        )
        self.assertEqual(result["status"], "unmapped")

    def test_schedule_dst_gap_ambiguity_and_utc(self):
        with self.assertRaises(DomainError) as gap:
            resolve_schedule("2026-03-08T02:30:00", "America/Los_Angeles", None)
        self.assertEqual(gap.exception.code, "NONEXISTENT_LOCAL_TIME")
        with self.assertRaises(DomainError) as duplicate:
            resolve_schedule("2026-11-01T01:30:00", "America/Los_Angeles", None)
        self.assertEqual(duplicate.exception.code, "AMBIGUOUS_LOCAL_TIME")
        a = resolve_schedule("2026-11-01T01:30:00", "America/Los_Angeles", 0)
        b = resolve_schedule("2026-11-01T01:30:00", "America/Los_Angeles", 1)
        self.assertNotEqual(a["scheduled_at"], b["scheduled_at"])
        self.assertEqual(
            resolve_schedule("2026-01-01T08:00:00", "Asia/Shanghai", None)["scheduled_at"],
            "2026-01-01T00:00:00+00:00",
        )

    def test_publication_reviews_current_rights_and_manual_receipt(self):
        source = self.create("Source", {"status": "active"})
        artifact = self.create(
            "Artifact",
            {
                "name": "Synthetic artifact",
                "checksum": "synthetic-checksum",
                "status": "technical_passed",
            },
            [{"id": source["id"], "version": 1}],
        )
        publication = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "artifact_id": artifact["id"],
                "account_id": self.account["id"],
                "title": "Synthetic title",
            },
        )
        path = f"/api/v1/production/publications/{publication['id']}/preflight"
        self.assertFalse(self.client.get(path).json()["data"]["can_handoff"])
        for kind in ("content_check", "rights_check"):
            self.post(
                "/production/approvals",
                {
                    "subject_id": artifact["id"],
                    "subject_version": 1,
                    "kind": kind,
                    "decision": "approved",
                    "evidence": "Synthetic test review",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        result = self.client.get(path).json()["data"]
        self.assertTrue(result["can_handoff"])
        self.assertFalse(result["can_send"])
        self.store.update(
            source["id"], {"status": "revoked"}, tenant_id="local", expected_revision=1
        )
        result = self.client.get(path).json()["data"]
        self.assertIn("DEPENDENCY_NOT_CURRENT", [p["code"] for p in result["problems"]])
        receipt = {
            "expected_revision": 1,
            "platform_post_id": "synthetic-post",
            "post_url": "https://example.test/post/1",
            "observed_at": "2026-01-01T00:00:00Z",
            "evidence": "Manual test report",
            "visibility": "public",
        }
        result = self.post(f"/production/publications/{publication['id']}/receipt", receipt)
        self.assertFalse(result["post"]["data"]["independently_verified"])
        self.assertEqual(result["publication"]["data"]["outcome"], "manual_reported")
        repeat = self.post(
            f"/production/publications/{publication['id']}/receipt",
            {**receipt, "expected_revision": 2},
        )
        self.assertTrue(repeat["duplicate"])

    def test_preflight_rechecks_grant_attached_after_artifact_was_frozen(self):
        source = self.create("Source", {"status": "active"})
        artifact = self.create(
            "Artifact", {"status": "technical_passed"}, [{"id": source["id"], "version": 1}]
        )
        pub = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "artifact_id": artifact["id"],
                "account_id": self.account["id"],
                "title": "Synthetic later grant",
            },
        )
        for kind in ("content_check", "rights_check"):
            self.post(
                "/production/approvals",
                {
                    "subject_id": artifact["id"],
                    "subject_version": 1,
                    "kind": kind,
                    "decision": "approved",
                    "evidence": "Synthetic review",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        grant = self.create("Grant", {"status": "active", "valid_from": "2099-01-01T00:00:00Z"})
        self.store.update(
            source["id"],
            {**source["data"], "grant_id": grant["id"]},
            tenant_id="local",
            expected_revision=1,
            refs=[{"id": grant["id"], "version": 1}],
        )
        result = self.client.get(f"/api/v1/production/publications/{pub['id']}/preflight").json()[
            "data"
        ]
        self.assertFalse(result["can_handoff"])
        self.assertIn(
            "MEDIA_RIGHTS_NOT_ACTIVE", [problem["code"] for problem in result["problems"]]
        )

    def test_metric_zero_null_duplicates_and_scope_groups(self):
        rows = [
            self.metric(),
            self.metric(metric_name="orders", value=None, missing_reason="scope_unavailable"),
        ]
        first = self.post("/production/metrics/import", {"project_id": self.pid, "rows": rows})
        self.assertEqual(first["count"], 2)
        again = self.post("/production/metrics/import", {"project_id": self.pid, "rows": rows})
        self.assertEqual(again["count"], 0)
        result = self.client.get(
            "/api/v1/production/analytics", params={"project_id": self.pid}
        ).json()["data"]
        groups = {g["metric_name"]: g for g in result["groups"]}
        self.assertEqual(groups["views"]["mean"], 0)
        self.assertIsNone(groups["orders"]["mean"])
        self.assertEqual(groups["orders"]["missing"], 1)
        self.post(
            "/production/metrics/import",
            {"project_id": self.pid, "rows": [self.metric(value=None)]},
            422,
        )

    def test_metric_timezones_choose_actual_latest_and_canonical_windows(self):
        rows = [
            self.metric(value=5, observed_at="2026-01-03T10:00:00+08:00"),
            self.metric(
                value=9,
                observed_at="2026-01-03T03:00:00Z",
                window_start="2026-01-01T08:00:00+08:00",
                window_end="2026-01-02T08:00:00+08:00",
            ),
        ]
        self.post("/production/metrics/import", {"project_id": self.pid, "rows": rows})
        result = self.client.get(
            "/api/v1/production/analytics", params={"project_id": self.pid}
        ).json()["data"]
        self.assertEqual(result["deduplicated_observations"], 1)
        self.assertEqual(result["groups"][0]["mean"], 9)

    def test_unstructured_validity_is_a_reported_blocker_not_a_server_error(self):
        product = self.create("Product", {"SKU": "SYNTHETIC", "validity": "需要业务负责人确认"})
        artifact = self.create(
            "Artifact", {"status": "technical_passed"}, [{"id": product["id"], "version": 1}]
        )
        publication = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "artifact_id": artifact["id"],
                "account_id": self.account["id"],
                "title": "Synthetic",
            },
        )
        result = self.client.get(f"/api/v1/production/publications/{publication['id']}/preflight")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn(
            "DEPENDENCY_VALIDITY_UNVERIFIABLE",
            [p["code"] for p in result.json()["data"]["problems"]],
        )

    def test_experiment_unit_data_and_honest_effect_boundary(self):
        accounts = [
            self.account,
            *[
                self.create("Account", {"provider": "manual", "name": f"Synthetic {i}"})
                for i in range(3)
            ],
        ]
        start, end = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"
        exp = self.post(
            "/production/experiments",
            {
                "project_id": self.pid,
                "name": "Synthetic experiment",
                "hypothesis": "Test only",
                "unit_ids": [a["id"] for a in accounts],
                "seed": "test-seed",
                "metric_name": "views",
                "stop_rule": "fixed window",
                "window_start": start,
                "window_end": end,
            },
        )
        metrics = self.post(
            "/production/metrics/import",
            {
                "project_id": self.pid,
                "rows": [self.metric(account_id=a["id"], value=i) for i, a in enumerate(accounts)],
            },
        )
        result = self.post(
            f"/production/experiments/{exp['id']}/evaluate",
            {"expected_revision": 1, "metric_ids": [m["id"] for m in metrics["records"]]},
        )
        self.assertFalse(result["data"]["result"]["causal_claim"])
        self.assertEqual(result["data"]["result"]["n"], {"baseline": 2, "candidate": 2})

    def test_experiment_latest_missing_or_immature_does_not_reuse_old_values(self):
        accounts = [
            self.account,
            *[
                self.create("Account", {"provider": "manual", "name": f"Maturity fixture {i}"})
                for i in range(3)
            ],
        ]
        exp = self.post(
            "/production/experiments",
            {
                "project_id": self.pid,
                "name": "Maturity test",
                "hypothesis": "Test only",
                "unit_ids": [a["id"] for a in accounts],
                "seed": "test-seed",
                "metric_name": "views",
                "stop_rule": "fixed window",
                "window_start": "2026-01-01T00:00:00Z",
                "window_end": "2026-01-02T00:00:00Z",
            },
        )
        older = [self.metric(account_id=a["id"], value=i) for i, a in enumerate(accounts)]
        latest = [
            self.metric(
                account_id=a["id"],
                value=i + 10,
                observed_at="2026-01-04T00:00:00Z",
                data_mature_after="2099-01-01T00:00:00Z",
            )
            for i, a in enumerate(accounts)
        ]
        metrics = self.post(
            "/production/metrics/import", {"project_id": self.pid, "rows": [*older, *latest]}
        )
        result = self.post(
            f"/production/experiments/{exp['id']}/evaluate",
            {"expected_revision": 1, "metric_ids": [m["id"] for m in metrics["records"]]},
        )
        self.assertEqual(result["data"]["result"]["status"], "insufficient_evidence")
        self.assertEqual(result["data"]["result"]["n"], {"baseline": 0, "candidate": 0})
        self.assertEqual(len(result["data"]["result"]["exclusions"]), 4)

    def test_metric_duplicate_detection_covers_more_than_recent_500(self):
        original = self.post(
            "/production/metrics/import", {"project_id": self.pid, "rows": [self.metric()]}
        )
        with self.store.transaction():
            for index in range(501):
                self.create(
                    "Metric",
                    {
                        "metric_name": f"Synthetic unrelated {index}",
                        "value_or_null": index,
                        "intake_fingerprint": f"fixture-{index}",
                    },
                )
        repeated = self.post(
            "/production/metrics/import", {"project_id": self.pid, "rows": [self.metric()]}
        )
        self.assertEqual(repeated["count"], 0)
        self.assertEqual(len(repeated["duplicates"]), 1)

    def test_analytics_keeps_old_snapshots_and_separates_measurement_modes(self):
        first = self.post(
            "/production/metrics/import",
            {
                "project_id": self.pid,
                "rows": [
                    self.metric(value=10, counting_mode="interval"),
                    self.metric(value=90, counting_mode="cumulative"),
                ],
            },
        )
        with self.store.transaction():
            for _ in range(503):
                self.create("Metric", {"metric_name": "Unverified draft", "value_or_null": 1})
        result = self.client.get(
            "/api/v1/production/analytics", params={"project_id": self.pid}
        ).json()["data"]
        self.assertEqual(result["snapshot_count"], 505)
        self.assertEqual(result["incomplete_snapshots"], 503)
        self.assertEqual(result["deduplicated_observations"], 2)
        self.assertFalse(result["may_be_truncated"])
        self.assertEqual(
            {g["counting_mode"]: g["mean"] for g in result["groups"]},
            {"interval": 10, "cumulative": 90},
        )
        self.assertEqual(
            {a for g in result["groups"] for a in g["metric_refs"]},
            {r["id"] for r in first["records"]},
        )

    def test_replay_and_tenant_scope_are_not_fabricated(self):
        self.post(
            "/production/replay/prepare",
            {"project_id": self.pid, "root_ids": [self.project["id"]]},
            422,
        )
        manifest = self.create(
            "RenderManifest", {"name": "Synthetic manifest", "status": "recorded"}
        )
        replay = self.post(
            "/production/replay/prepare", {"project_id": self.pid, "root_ids": [manifest["id"]]}
        )
        self.assertEqual(replay["data"]["status"], "blocked")
        self.assertEqual(
            replay["data"]["missing_dependencies"][0]["code"], "EXECUTION_EVIDENCE_MISSING"
        )
        outsider = self.store.create("Project", {"name": "Other tenant"}, tenant_id="other")
        self.post(
            "/production/replay/prepare",
            {"project_id": outsider["id"], "root_ids": [manifest["id"]]},
            409,
        )

    def test_replay_checks_bytes_and_current_rights_and_bidirectional_lineage(self):
        from app.media import persist_blob, blob_path

        source = self.create("Source", {"status": "active"})
        fixture = Path(self.temp.name) / "synthetic.bin"
        fixture.write_bytes(b"synthetic blob for hash verification, not video")
        blob = self.create(
            "BlobLocation", persist_blob(fixture, "local"), [{"id": source["id"], "version": 1}]
        )
        artifact = self.create(
            "Artifact", {"blob_location_id": blob["id"]}, [{"id": blob["id"], "version": 1}]
        )
        timeline = self.create("Timeline", {"clips": []})
        manifest = self.create(
            "RenderManifest",
            {
                "artifact_id": artifact["id"],
                "timeline_ref": {"id": timeline["id"], "version": 1},
                "output_hash": blob["data"]["checksum"],
                "tool_versions": {"fixture": "not a real render"},
                "finished_at": "2026-01-01T00:00:00Z",
            },
            [{"id": artifact["id"], "version": 1}, {"id": timeline["id"], "version": 1}],
        )
        body = {"project_id": self.pid, "root_ids": [manifest["id"]]}
        prepared = self.post("/production/replay/prepare", body)
        self.assertEqual(prepared["data"]["status"], "prepared_not_executed")
        self.assertTrue(prepared["data"]["byte_checks"][0]["passed"])
        self.assertFalse(prepared["data"]["execution_enabled"])
        trace = self.client.get(
            f"/api/v1/production/trace/{blob['id']}", params={"direction": "both"}
        ).json()["data"]
        self.assertTrue(
            {source["id"], artifact["id"], manifest["id"]}.issubset(
                {n["id"] for n in trace["nodes"]}
            )
        )
        blob_path(blob, "local").write_bytes(b"corrupted")
        self.store.update(
            source["id"], {"status": "revoked"}, tenant_id="local", expected_revision=1
        )
        failed = self.post("/production/replay/prepare", body)
        self.assertEqual(failed["data"]["status"], "blocked")
        self.assertTrue(
            {"OBJECT_CORRUPTION", "DEPENDENCY_NOT_CURRENT"}.issubset(
                {m["code"] for m in failed["data"]["missing_dependencies"]}
            )
        )


class ProviderLedgerTests(ProductionBase):
    """Transport is explicitly injected; these tests never contact model services."""

    def setUp(self):
        super().setUp()
        os.environ["TEST_PROVIDER_KEY"] = "synthetic-test-secret"
        os.environ["AI_SHIP_PROVIDERS"] = json.dumps(
            {
                "test": {
                    "base_url": "https://provider.invalid/v1",
                    "api_key_env": "TEST_PROVIDER_KEY",
                    "models": ["model-a"],
                    "input_price_per_million": 1,
                    "output_price_per_million": 2,
                    "currency": "USD",
                    "json_output": True,
                }
            }
        )
        self.profile = self.create(
            "ModelProfile",
            {
                "provider": "test",
                "endpoint_ref": "test",
                "model_id": "model-a",
                "credential_ref": "env:TEST_PROVIDER_KEY",
                "context_limit": 100000,
                "structured_output": True,
            },
        )

    def tearDown(self):
        os.environ.pop("TEST_PROVIDER_KEY", None)
        super().tearDown()

    def body(self, **changes):
        return ChatInput(
            project_id=self.pid,
            model_profile_id=self.profile["id"],
            messages=[{"role": "user", "content": "Synthetic JSON test"}],
            max_tokens=128,
            json_output=True,
            max_cost=1,
            **{"idempotency_key": "synthetic-request-1", **changes},
        )

    def factory(
        self,
        failure=None,
        content='{"proposal":"synthetic","evidence_refs":[]}',
        reported_model=None,
        after_post=None,
    ):
        parent = self

        class FakeClient:
            def __init__(self, **kwargs):
                parent.assertFalse(kwargs["follow_redirects"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, **kwargs):
                parent.calls += 1
                if failure:
                    raise failure
                if after_post:
                    after_post()
                payload = content(kwargs["json"]) if callable(content) else content
                return httpx.Response(
                    200,
                    json={
                        "id": "synthetic-provider-id",
                        "model": reported_model or kwargs["json"]["model"],
                        "choices": [{"message": {"content": payload}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                    },
                )

        return FakeClient

    def test_model_success_idempotency_and_config_price_not_bill(self):
        self.calls = 0
        response = chat(self.body(), LOCAL_ACTOR, self.factory())
        self.assertEqual(response["status"], "succeeded")
        duplicate = chat(self.body(), LOCAL_ACTOR, self.factory())
        self.assertTrue(duplicate["replayed"])
        self.assertEqual(self.calls, 1)
        cost = self.store.list("local", "Cost", self.pid)[0]["data"]
        self.assertIsNone(cost["actual"])
        self.assertAlmostEqual(cost["calculated_charge"], 0.00004)
        data = json.dumps(self.store.list("local", project_id=self.pid), ensure_ascii=False)
        self.assertNotIn("synthetic-test-secret", data)
        with self.assertRaises(DomainError) as conflict:
            chat(self.body(temperature=0.5), LOCAL_ACTOR, self.factory())
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_three_role_team_persists_checkpoints_and_independent_proposals(self):
        configuration = json.loads(os.environ["AI_SHIP_PROVIDERS"])
        configuration["test"]["models"].append("model-b")
        os.environ["AI_SHIP_PROVIDERS"] = json.dumps(configuration)
        second = self.create("ModelProfile", {**self.profile["data"], "model_id": "model-b"})
        evidence = self.create(
            "Brief", {"name": "Synthetic frozen evidence", "goals": "No real external actions"}
        )
        body = {
            "project_id": self.pid,
            "goal": "Produce a bounded proposal from test evidence",
            "roles": [
                {
                    "role": "planner",
                    "model_profile_id": self.profile["id"],
                    "independence_group": "planning",
                },
                {
                    "role": "reviewer",
                    "model_profile_id": self.profile["id"],
                    "independence_group": "review",
                },
                {
                    "role": "judge",
                    "model_profile_id": second["id"],
                    "independence_group": "decision",
                },
            ],
            "evidence_refs": [evidence["id"]],
            "idempotency_key": "synthetic-team-run",
            "max_tokens_per_role": 128,
            "max_cost": 3,
        }
        self.calls = 0
        with patch(
            "app.providers.httpx.Client",
            self.factory(
                content=lambda payload: json.dumps(
                    {
                        "proposal": "Synthetic rough cut",
                        "evidence_refs": [evidence["id"]],
                        "counter_evidence": [],
                        "unresolved": [],
                        "quality_checks": (
                            ["The proposal only uses information in the frozen test brief."]
                            if "the judge" in payload["messages"][0]["content"]
                            else []
                        ),
                    }
                )
            ),
        ):
            result = self.post("/providers/team", body)
            repeated = self.post("/providers/team", body)
        self.assertEqual(self.calls, 3)
        self.assertEqual(result["status"], "proposals_ready")
        self.assertEqual(result["run"]["data"]["state"], "待校验输出")
        self.assertEqual(result["run"]["data"]["checkpoint"]["next_role"], 3)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(len(result["outputs"]), 3)
        self.assertEqual(result["decision"]["data"]["status"], "proposed")
        self.assertEqual(len(self.store.list("local", "AgentRole", self.pid)), 3)

    def test_timeout_preserves_reservation_and_cannot_change_key(self):
        self.calls = 0
        response = chat(
            self.body(), LOCAL_ACTOR, self.factory(httpx.ReadTimeout("synthetic timeout"))
        )
        self.assertEqual(response["status"], "outcome_unknown")
        duplicate = chat(self.body(), LOCAL_ACTOR, self.factory())
        self.assertEqual(duplicate["status"], "outcome_unknown")
        self.assertEqual(self.calls, 1)
        with self.assertRaises(DomainError) as failure:
            chat(self.body(idempotency_key="different-key"), LOCAL_ACTOR, self.factory())
        self.assertEqual(failure.exception.code, "RECONCILE_FIRST")
        self.assertEqual(
            self.store.list("local", "Cost", self.pid)[0]["data"]["status"], "outcome_unknown"
        )

    def test_unknown_wire_request_cannot_retry_by_changing_non_wire_metadata(self):
        self.calls = 0
        original = self.body()
        # Exercise the automatic JSON system message as part of the real payload.
        original.messages[0].content = "Create a synthetic proposal."
        response = chat(original, LOCAL_ACTOR, self.factory(httpx.ReadTimeout("synthetic timeout")))
        self.assertEqual(response["status"], "outcome_unknown")
        with self.assertRaises(DomainError) as conflict:
            chat(original.model_copy(update={"max_cost": 2}), LOCAL_ACTOR, self.factory())
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")
        evidence = self.create("Brief", {"name": "Additional bookkeeping reference"})
        changes = [
            {"idempotency_key": "new-key-cost-ceiling", "max_cost": 2},
            {"idempotency_key": "new-key-evidence", "evidence_refs": [evidence["id"]]},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(DomainError) as blocked:
                chat(original.model_copy(update=change), LOCAL_ACTOR, self.factory())
            self.assertEqual(blocked.exception.code, "RECONCILE_FIRST")
        self.store.update(
            self.profile["id"],
            {**self.profile["data"], "name": "Renamed profile only"},
            tenant_id="local",
            expected_revision=self.profile["version"],
        )
        duplicate_profile = self.create(
            "ModelProfile", {**self.profile["data"], "provider": "different_display_label"}
        )
        for profile_id in (self.profile["id"], duplicate_profile["id"]):
            with self.subTest(profile_id=profile_id), self.assertRaises(DomainError) as blocked:
                chat(
                    original.model_copy(
                        update={
                            "idempotency_key": "new-key-" + profile_id,
                            "model_profile_id": profile_id,
                        }
                    ),
                    LOCAL_ACTOR,
                    self.factory(),
                )
            self.assertEqual(blocked.exception.code, "RECONCILE_FIRST")
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.store.list("local", "ToolCall", self.pid)), 1)
        self.assertEqual(len(self.store.list("local", "Cost", self.pid)), 1)

    def test_distinct_wire_payload_remains_an_explicit_new_request(self):
        self.calls = 0
        first = chat(self.body(), LOCAL_ACTOR, self.factory(httpx.ReadTimeout("synthetic timeout")))
        changed = self.body(idempotency_key="distinct-provider-payload")
        changed.messages[0].content = "A different synthetic JSON proposal request."
        second = chat(changed, LOCAL_ACTOR, self.factory())
        self.assertEqual((first["status"], second["status"]), ("outcome_unknown", "succeeded"))
        self.assertNotEqual(
            first["call"]["data"]["request_payload_hash"],
            second["call"]["data"]["request_payload_hash"],
        )
        self.assertEqual(second["call"]["data"]["request_semantic_hash_version"], 2)
        self.assertEqual(self.calls, 2)

    def test_legacy_unsettled_request_remains_blocked_after_hash_upgrade(self):
        self.calls = 0
        first = chat(self.body(), LOCAL_ACTOR, self.factory(httpx.ReadTimeout("synthetic timeout")))
        old = first["call"]
        legacy = dict(old["data"])
        legacy.pop("request_semantic_hash_version")
        legacy.pop("provider_identity_snapshot")
        legacy.pop("request_payload_hash")
        legacy["actual_provider_model"] = legacy.pop("requested_provider_model")
        legacy["request_semantic_hash"] = "a" * 64
        self.store.update(old["id"], legacy, tenant_id="local", expected_revision=old["version"])
        with self.assertRaises(DomainError) as blocked:
            chat(
                self.body(idempotency_key="upgraded-legacy-retry").model_copy(
                    update={"max_cost": 2}
                ),
                LOCAL_ACTOR,
                self.factory(),
            )
        self.assertEqual(blocked.exception.code, "RECONCILE_FIRST")
        self.assertEqual(self.calls, 1)

    def test_bad_json_does_not_complete_and_no_credentials_no_call(self):
        self.calls = 0
        response = chat(self.body(), LOCAL_ACTOR, self.factory(content="not json"))
        self.assertEqual(response["status"], "output_invalid")
        os.environ.pop("TEST_PROVIDER_KEY")
        with self.assertRaises(DomainError) as failure:
            chat(self.body(idempotency_key="missing-secret"), LOCAL_ACTOR, self.factory())
        self.assertEqual(failure.exception.code, "PROVIDER_CREDENTIAL_MISSING")
        self.assertEqual(self.calls, 1)

    def test_reported_model_mismatch_does_not_count_as_success(self):
        self.calls = 0
        result = chat(self.body(), LOCAL_ACTOR, self.factory(reported_model="unexpected-model"))
        self.assertEqual(result["status"], "output_invalid")
        self.assertEqual(result["result"]["model_identity_status"], "unverified_or_mismatch")
        self.assertEqual(
            self.store.list("local", "Cost", self.pid)[0]["data"]["status"], "usage_recorded"
        )

    def test_provider_display_labels_cannot_fake_heterogeneous_models(self):
        alias = self.create(
            "ModelProfile", {**self.profile["data"], "provider": "fake-independent-label"}
        )
        body = {
            "project_id": self.pid,
            "goal": "Synthetic identity boundary",
            "roles": [
                {
                    "role": "planner",
                    "model_profile_id": self.profile["id"],
                    "independence_group": "a",
                },
                {"role": "reviewer", "model_profile_id": alias["id"], "independence_group": "b"},
                {"role": "judge", "model_profile_id": alias["id"], "independence_group": "c"},
            ],
            "evidence_refs": [self.account["id"]],
            "idempotency_key": "fake-model-heterogeneity",
            "max_cost": 1,
        }
        self.post("/providers/team", body, 422)
        self.assertEqual(self.store.list("local", "ToolCall", self.pid), [])

    def test_revocation_during_first_role_prevents_later_paid_calls(self):
        configuration = json.loads(os.environ["AI_SHIP_PROVIDERS"])
        configuration["test"]["models"].append("model-b")
        os.environ["AI_SHIP_PROVIDERS"] = json.dumps(configuration)
        second = self.create("ModelProfile", {**self.profile["data"], "model_id": "model-b"})
        body = {
            "project_id": self.pid,
            "goal": "Synthetic revocation test",
            "roles": [
                {
                    "role": "planner",
                    "model_profile_id": self.profile["id"],
                    "independence_group": "a",
                },
                {
                    "role": "reviewer",
                    "model_profile_id": self.profile["id"],
                    "independence_group": "b",
                },
                {"role": "judge", "model_profile_id": second["id"], "independence_group": "c"},
            ],
            "evidence_refs": [self.account["id"]],
            "idempotency_key": "revoke-in-flight-test",
            "max_cost": 1,
            "max_tokens_per_role": 128,
        }
        self.calls = 0
        keys = {
            "synthetic-auth-key": {"tenant_id": "local", "actor_id": "local-owner", "role": "owner"}
        }
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            with patch(
                "app.providers.httpx.Client",
                self.factory(
                    content=json.dumps(
                        {
                            "proposal": "Synthetic rough cut",
                            "evidence_refs": [self.account["id"]],
                            "unresolved": [],
                            "counter_evidence": [],
                            "quality_checks": [],
                        }
                    ),
                    after_post=lambda: os.environ.__setitem__("AI_SHIP_API_KEYS", "{}"),
                ),
            ):
                result = self.client.post(
                    "/api/v1/providers/team",
                    json=body,
                    headers={"Authorization": "Bearer synthetic-auth-key"},
                )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["data"]["status"], "blocked")
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.store.list("local", "Cost", self.pid)), 1)

    def test_budget_and_homogeneous_team_fail_before_network(self):
        self.calls = 0
        with self.assertRaises(DomainError) as failure:
            body = self.body()
            body.max_cost = 0.00000001
            chat(body, LOCAL_ACTOR, self.factory())
        self.assertEqual(failure.exception.code, "COST_BUDGET")
        self.assertEqual(self.calls, 0)
        self.post(
            "/providers/team",
            {
                "project_id": self.pid,
                "goal": "Synthetic goal",
                "roles": [
                    {
                        "role": "planner",
                        "model_profile_id": self.profile["id"],
                        "independence_group": "a",
                    },
                    {
                        "role": "creator",
                        "model_profile_id": self.profile["id"],
                        "independence_group": "b",
                    },
                    {
                        "role": "judge",
                        "model_profile_id": self.profile["id"],
                        "independence_group": "c",
                    },
                ],
                "evidence_refs": [self.account["id"]],
                "idempotency_key": "synthetic-team",
                "max_cost": 1,
            },
            422,
        )


class RealMediaCrossDomainTests(ProductionBase):
    """Real media projections must agree with annotation, voice and lineage APIs."""

    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_dir.name) / "synthetic-cross-domain.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x120:rate=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "1.2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(cls.fixture),
            ],
            check=True,
            timeout=30,
        )

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def imported(self):
        from app.media import import_saved_file

        return import_saved_file(self.store, LOCAL_ACTOR, self.pid, self.fixture.name, self.fixture)

    def test_real_probe_bounds_tags_search_and_audio_anchors(self):
        imported = self.imported()
        asset, version = imported["asset"], imported["version"]
        tag = {
            "project_id": self.pid,
            "asset_version_id": version["id"],
            "start": 0.1,
            "end": 0.8,
            "label": "SYNTHETIC",
            "category": "subject",
            "evidence": "Synthetic chart clip",
        }
        self.post("/production/tags", tag)
        self.post("/production/tags", {**tag, "end": 999}, 422)
        result = self.client.get(
            "/api/v1/production/search", params={"project_id": self.pid, "min_duration": 2}
        ).json()["data"]
        self.assertEqual(result["items"], [])
        audio = self.post(
            f"/media/{asset['id']}/extract-audio", {"project_id": self.pid, "expected_revision": 1}
        )["audio"]
        script = self.create("Script", {"text": "合成音轨测试"})
        alignment = {
            "project_id": self.pid,
            "script_id": script["id"],
            "audio_id": audio["id"],
            "anchors": [
                {"token": "合成音轨测试", "start": 0, "end": 1, "timing_status": "estimated"}
            ],
        }
        mapped = self.post("/production/voice/align", alignment)
        self.assertEqual(mapped["data"]["audio_hash"], audio["data"]["content_hash"])
        alignment["anchors"][0]["end"] = 999
        self.post("/production/voice/align", alignment, 422)

    def test_actual_render_timemap_and_replay_resolve_immutable_bytes(self):
        imported = self.imported()
        asset, version = imported["asset"], imported["version"]
        timeline = self.create(
            "Timeline",
            {
                "name": "Synthetic cross domain render",
                "locked": True,
                "clips": [
                    {
                        "id": "clip-test",
                        "asset_id": asset["id"],
                        "asset_version_id": version["id"],
                        "source_in": 0.2,
                        "source_out": 1,
                        "timeline_start": 0.3,
                        "track": 0,
                        "volume": 1,
                    }
                ],
                "format": {"width": 160, "height": 120, "fps": 24},
            },
            [{"id": version["id"], "version": 1}],
        )
        rendered = self.post(
            "/media/render",
            {
                "project_id": self.pid,
                "timeline_id": timeline["id"],
                "expected_revision": 1,
                "profile": {"width": 160, "height": 120, "fps": 24},
            },
        )
        traced = self.post(
            "/production/time-map/trace",
            {"time_map_id": rendered["time_map"]["id"], "output_seconds": 0.5},
        )
        self.assertEqual(traced["status"], "mapped")
        self.assertEqual({c["contribution"] for c in traced["contributors"]}, {"visual", "audio"})
        for contribution in traced["contributors"]:
            self.assertEqual(contribution["source_ref"]["id"], version["id"])
            self.assertAlmostEqual(contribution["source_seconds"], 0.4)
            self.assertFalse(contribution["occlusion_evaluated"])
        end = self.post(
            "/production/time-map/trace",
            {"time_map_id": rendered["time_map"]["id"], "output_seconds": 1.1},
        )
        self.assertEqual(end["contributors"], [])
        replay = self.post(
            "/production/replay/prepare",
            {"project_id": self.pid, "root_ids": [rendered["manifest"]["id"]]},
        )
        self.assertEqual(replay["data"]["status"], "prepared_not_executed")
        self.assertGreaterEqual(len(replay["data"]["byte_checks"]), 2)
        self.assertTrue(all(c["passed"] for c in replay["data"]["byte_checks"]))


if __name__ == "__main__":
    unittest.main()
