from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app.main import app
from app.auth import Actor, current_actor
from tests.test_production import ProductionBase


class AccountQuotaTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.artifact = self.create(
            "Artifact",
            {"name": "隔离测试", "checksum": "synthetic-sha", "status": "technical_passed"},
        )
        for k in ("rights_check", "content_check"):
            self.post(
                "/production/approvals",
                {
                    "subject_id": self.artifact["id"],
                    "subject_version": 1,
                    "kind": k,
                    "decision": "approved",
                    "evidence": "测试审阅",
                    "expires_at": "2099-11-24T00:00:00Z",
                },
            )
        self.pubs = [self.draft(str(i)) for i in range(4)]

    def draft(self, name):
        return self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "account_id": self.account["id"],
                "artifact_id": self.artifact["id"],
                "title": name,
            },
        )

    def req(self, path, body, code=200, key=None):
        return self.post(
            "/production/account-quota/" + path,
            {"project_id": self.pid, "request_id": key or uuid4().hex, **body},
            code,
        )

    def config(self, rev=0, cap=2, concurrent=1):
        return self.req(
            "settings",
            {
                "account_id": self.account["id"],
                "expected_revision": rev,
                "daily_slots": cap,
                "concurrent_slots": concurrent,
                "hold_seconds": 10,
                "reason": "现场安排",
            },
        )

    def act(self, pub, action="reserve", v=0, code=200, key=None, **kw):
        return self.req(
            "act",
            {
                "publication": {"id": pub["id"], "version": pub["version"]},
                "action": action,
                "expected_ticket_version": v,
                "reason": "测试时已核对",
                "not_before": None,
                **kw,
            },
            code,
            key,
        )

    def pre(self, pub):
        return self.client.get(
            "/api/v1/production/publications/" + pub["id"] + "/preflight"
        ).json()["data"]

    def read(self, pub):
        return self.client.get(
            "/api/v1/production/account-quota/" + self.pid + "/" + self.account["id"],
            params={"publication_id": pub["id"]},
        ).json()["data"]

    def receipt(self, pub):
        raw = {
            "expected_revision": pub["version"],
            "platform_post_id": uuid4().hex,
            "post_url": "https://example.test/delivery",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "evidence": "本地人工核对",
            "visibility": "public",
        }
        return self.post("/production/publications/" + pub["id"] + "/receipt", raw)

    def test_fifo_idempotent_release_handoff_and_receipt(self):
        p, q, r, _ = self.pubs
        self.assertTrue(self.pre(p)["can_handoff"])
        self.config()
        self.assertFalse(self.pre(p)["can_handoff"])
        k = uuid4().hex
        held = self.act(p, key=k)
        self.assertEqual(self.act(p, key=k)["id"], held["id"])
        self.assertTrue(self.pre(p)["can_handoff"])
        self.assertEqual(self.act(q)["data"]["status"], "queued")
        self.assertEqual(self.act(r)["data"]["status"], "queued")
        self.act(p, "release", 1)
        self.assertEqual(self.act(r, v=1)["data"]["wait_reason"], "earlier_request")
        second = self.act(q, v=1)
        self.assertEqual(second["data"]["status"], "held")
        hand = self.act(q, "handoff", 2)
        self.assertEqual(hand["data"]["status"], "in_handoff")
        self.assertFalse(self.pre(q)["can_handoff"])
        self.act(q, "reserve", 3, 409)
        posted = self.receipt(q)
        info = self.read(q)
        self.assertEqual(info["effective_state"], "settled")
        self.assertEqual(info["receipt_count_24h"], 1)
        self.assertEqual(info["held_or_unknown"], 0)
        self.assertEqual(info["ticket"]["data"]["receipt"]["id"], posted["post"]["id"])
        self.assertIsNone(info["ticket"]["data"].get("actual"))
        self.act(r, v=2)
        self.assertTrue(self.pre(r)["can_handoff"])

    def test_limits_apply_to_out_of_band_receipts_and_existing_holds(self):
        p, q, r, _ = self.pubs
        self.config(cap=2, concurrent=2)
        self.act(p)
        self.act(q)
        self.receipt(r)
        self.assertTrue(self.pre(p)["can_handoff"])
        self.assertFalse(self.pre(q)["can_handoff"])
        self.assertEqual(self.act(q, "handoff", 1, 409)["error"]["code"], "QUOTA_CAPACITY_CHANGED")
        self.config(rev=1, cap=1, concurrent=1)
        self.assertFalse(self.pre(p)["can_handoff"])
        with patch(
            "app.account_quota.clock", return_value=datetime.now(timezone.utc) + timedelta(hours=25)
        ):
            self.assertEqual(self.read(q)["receipt_count_24h"], 0)
            self.assertEqual(self.read(q)["effective_state"], "expired")

    def test_fair_race_expiry_and_unknown_handoff_never_auto_releases(self):
        self.config()
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(self.act, self.pubs[:2]))
        self.assertEqual(sorted(r["data"]["status"] for r in rows), ["held", "queued"])
        first = self.pubs[rows[1]["data"]["status"] == "held"]
        second = next(p for p in self.pubs[:2] if p != first)
        start = datetime.now(timezone.utc)
        with patch("app.account_quota.clock", return_value=start + timedelta(seconds=15)):
            self.assertEqual(self.read(first)["effective_state"], "expired")
            self.act(second, v=1)
            self.act(second, "handoff", 2)
        with patch("app.account_quota.clock", return_value=start + timedelta(days=2)):
            self.assertEqual(self.read(second)["effective_state"], "in_handoff")
            self.assertEqual(self.act(first, v=1)["data"]["status"], "queued")
            self.act(second, "release", 3)
            self.assertEqual(self.act(first, v=2)["data"]["status"], "held")

    def test_backoff_requires_fresh_evidence_and_prevents_early_reset(self):
        p = self.pubs[0]
        self.config()
        self.act(p)

        def feedback(outcome, revision, code=200):
            return self.req(
                "provider-observation",
                {
                    "account_id": self.account["id"],
                    "expected_revision": revision,
                    "outcome": outcome,
                    "retry_after_seconds": 30 if outcome == "rate_limited" else None,
                    "evidence": "账号负责人记录的限流响应",
                },
                code,
            )

        rate = feedback("rate_limited", 1)
        self.assertEqual(self.read(p)["block"], "RATE_LIMIT_BACKOFF")
        self.config(rev=rate["version"])
        self.act(p, "handoff", 1, 409)
        feedback("reviewed", 3, 409)
        with patch(
            "app.account_quota.clock",
            return_value=datetime.now(timezone.utc) + timedelta(seconds=31),
        ):
            self.assertEqual(self.read(p)["block"], "CAPABILITY_REFRESH_REQUIRED")
            resumed = feedback("reviewed", 3)
            self.assertFalse(resumed["data"]["refresh_required"])
            self.assertEqual(self.act(p, v=1)["data"]["status"], "held")

    def test_other_tenant_contract_and_failed_preflight_create_no_ticket(self):
        p = self.pubs[0]
        self.config()
        app.dependency_overrides[current_actor] = lambda: Actor("separate-team", "owner", "owner")
        try:
            self.act(p, code=404)
            url = "/api/v1/production/account-quota/" + self.pid + "/" + self.account["id"]
            self.assertEqual(self.client.get(url).status_code, 404)
        finally:
            del app.dependency_overrides[current_actor]
        self.store.update(
            self.artifact["id"],
            {**self.artifact["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=1,
        )
        # Scheduled intent bound to an unchecked new artifact has no qualifying approvals or QC.
        other = self.create("Artifact", {"name": "未审核", "checksum": "other"})
        pub = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "account_id": self.account["id"],
                "artifact_id": other["id"],
                "title": "未审",
            },
        )
        self.assertEqual(self.act(pub, code=409)["error"]["code"], "PREFLIGHT_REQUIRED")
        self.assertIsNone(self.read(pub)["ticket"])

    def test_inflight_intent_cannot_be_rebound_or_edited_and_expired_hold_is_reclaimable(self):
        p, q = self.pubs[:2]
        self.config()
        self.act(p)
        self.act(p, "handoff", 1)
        response = self.client.patch(
            "/api/v1/records/" + p["id"],
            json={
                "expected_revision": 1,
                "reason": "更改说明",
                "data": {**p["data"], "title": "改动"},
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.act(p, "release", 2)
        another = self.create("Account", {"name": "另一个账号", "provider": "manual"})
        changed = self.client.patch(
            "/api/v1/records/" + p["id"],
            json={
                "expected_revision": 1,
                "reason": "改绑",
                "data": {**p["data"], "account_id": another["id"]},
            },
        )
        self.assertEqual(changed.status_code, 409, changed.text)
        edit = self.client.patch(
            "/api/v1/records/" + p["id"],
            json={
                "expected_revision": 1,
                "reason": "更改说明",
                "data": {**p["data"], "title": "允许修改"},
            },
        )
        self.assertEqual(edit.status_code, 200, edit.text)
        self.act(q)
        with patch(
            "app.account_quota.clock",
            return_value=datetime.now(timezone.utc) + timedelta(seconds=15),
        ):
            reacquired = self.act(q, v=1)
            self.assertEqual(reacquired["data"]["status"], "held")
            self.assertNotIn("closed_at", reacquired["data"])

    def test_defer_scoping_roles_and_reservation_revisions(self):
        p = self.pubs[0]
        self.config()
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        delayed = self.act(p, "defer", not_before=future.isoformat())
        self.assertEqual(delayed["data"]["not_before"], future.isoformat())
        self.assertEqual(self.read(p)["ready_queue"], 0)
        self.assertEqual(self.act(p, v=1)["data"]["wait_reason"], "scheduled")
        self.act(p, v=1, code=409)
        extra = self.create("Project", {"name": "其他"})
        self.req(
            "act",
            {
                "project_id": extra["id"],
                "publication": {"id": p["id"], "version": 1},
                "action": "reserve",
                "expected_ticket_version": 2,
                "reason": "错项目",
            },
            409,
        )
        app.dependency_overrides[current_actor] = lambda: Actor("local", "test-editor", "editor")
        try:
            self.req(
                "settings",
                {
                    "account_id": self.account["id"],
                    "expected_revision": 1,
                    "daily_slots": 99,
                    "concurrent_slots": 1,
                    "hold_seconds": 10,
                    "reason": "越权",
                },
                403,
            )
            self.act(p, "handoff", 2, 403)
        finally:
            del app.dependency_overrides[current_actor]
        with patch("app.account_quota.clock", return_value=future + timedelta(seconds=10)):
            self.assertEqual(self.act(p, v=2)["data"]["status"], "held")
