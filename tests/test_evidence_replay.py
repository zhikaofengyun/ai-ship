import os
from unittest.mock import patch

from tests.replay_transport import capture_provider
from app.production import digest
from tests.test_production import ProductionBase


class EvidenceReplayTests(ProductionBase):
    def setUp(self):
        super().setUp()
        config, self.wires = self.enterContext(capture_provider())
        os.environ["AI_SHIP_PROVIDERS"] = config
        self.model = self.create(
            "ModelProfile", {"provider": "local", "model_id": "fixture", "structured_output": True}
        )

    def chat(self, text, **extra):
        return self.post(
            "/providers/chat",
            {
                "project_id": self.pid,
                "idempotency_key": "fixture-key-" + text,
                "max_cost": 0.1,
                "model_profile_id": self.model["id"],
                "messages": [{"role": "user", "content": text}],
                "json_output": True,
                **extra,
            },
        )["call"]

    def get(self, record, **filters):
        r = self.client.get(
            "/api/v1/harness/replay-evidence/" + record["id"],
            params={"project_id": self.pid, "version": record["version"], **filters},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def counts(self):
        with self.store.connection() as db:
            return [
                db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("events", "revisions", "records", "command_results", "leases")
            ]

    def test_frozen_real_http_messages_and_response_comparison_never_dispatch(self):
        first = self.chat("detail")
        second = self.chat("wide")
        before = self.counts()
        with patch("app.providers.httpx.Client", side_effect=AssertionError("external dispatch")):
            result = self.get(
                first,
                compare_id=second["id"],
                compare_version=second["version"],
                expected_text="detail",
                json_key="shot",
            )
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.wires), 2)
        self.assertEqual(result["left"]["messages"], self.wires[0]["messages"])
        self.assertEqual(result["left"]["messages"][0]["role"], "system")
        self.assertEqual(result["left"]["fingerprints"]["wire_request"], digest(self.wires[0]))
        self.assertEqual(result["left"]["fingerprints"]["provider_system"], "test-system-build")
        self.assertEqual(result["left"]["checks"], {"contains_text": True, "has_json_key": True})
        self.assertFalse(result["right"]["checks"]["contains_text"])
        self.assertFalse(result["comparison"]["same_messages"])
        self.assertTrue(result["comparison"]["same_model_profile"])
        self.assertEqual(
            (result["mode"], result["external_writes"], result["live_calls"]),
            ("recorded_evidence", 0, 0),
        )
        pending = self.get(first, version=1)["left"]
        self.assertIsNone(pending["response"])
        self.assertEqual(pending["status"], "masked")
        legacy = dict(first["data"])
        legacy.pop("wire_snapshot")
        old = self.create("ToolCall", legacy)
        self.assertEqual(self.get(old)["left"]["messages"], self.wires[0]["messages"])
        corrupt = self.create("ToolCall", {**legacy, "request_payload_hash": "unverified"})
        self.assertIsNone(self.get(corrupt)["left"]["messages"])

    def test_incomplete_revoked_and_external_publish_receipts_cannot_reissue(self):
        partial = self.chat("partial")
        right = self.create("Source", {"status": "active"})
        completed = self.chat(
            "private-source-detail",
            evidence_version_refs=[{"id": right["id"], "version": right["version"]}],
        )
        artifact = self.create(
            "Artifact", {"status": "technical_passed", "name": "Synthetic finished cut"}
        )
        pub = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "artifact_id": artifact["id"],
                "account_id": self.account["id"],
                "title": "Synthetic publication fixture",
            },
        )
        self.post(
            "/production/publications/" + pub["id"] + "/receipt",
            {
                "expected_revision": pub["version"],
                "platform_post_id": "fixture-published",
                "post_url": "https://example.test/posts/fixture",
                "observed_at": "2026-01-01T00:00:00Z",
                "evidence": "Manual receipt supplied to the ordinary typed endpoint",
            },
        )
        receipt = self.store.list("local", kind="PlatformEvent", project_id=self.pid)[0]
        external = self.create(
            "ToolCall",
            {
                "operation_kind": "publication.submit",
                "side_effect": "external_publish",
                "outcome": "outcome_unknown",
                "response": {"content": "must not be replayed"},
            },
        )
        self.store.update(
            right["id"], {"status": "revoked"}, expected_revision=1, tenant_id="local"
        )
        before = self.counts()
        masked = self.get(partial, expected_text="partial")["left"]
        self.assertIsNone(masked["response"])
        self.assertFalse(masked["checks"])
        withdrawn = self.get(completed)["left"]
        self.assertEqual(withdrawn["status"], "masked")
        self.assertIsNone(withdrawn["messages"])
        self.assertIsNone(withdrawn["response"])
        unknown = self.get(external)["left"]
        self.assertEqual(unknown["status"], "metadata_only")
        self.assertIsNone(unknown["response"])
        self.assertEqual(unknown["receipt"]["side_effect"], "external_publish")
        result = self.get(receipt, compare_id=partial["id"], compare_version=partial["version"])
        self.assertEqual(result["left"]["status"], "recorded_receipt")
        self.assertEqual(result["left"]["receipt"]["publication_id"], pub["id"])
        self.assertEqual(result["comparison"]["status"], "incomplete")
        target = "/api/v1/harness/replay-evidence/" + receipt["id"]
        self.assertEqual(self.client.post(target, json={"mode": "publish"}).status_code, 405)
        self.assertEqual(
            self.client.get(target, params={"project_id": "other", "version": 1}).status_code, 409
        )
        self.assertEqual(self.counts(), before)
