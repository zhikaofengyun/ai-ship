"""Account handoff leases and commercial intent share one immutable publication version."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.production import now
from tests import test_commercial_handoff as commerce


class QuotaCommercialIntegration(unittest.TestCase):
    def setUp(self):
        self.flow = commerce.CommercialHandoffTests()
        self.flow.setUp()
        self.addCleanup(self.flow.tearDown)
        self.f = self.flow
        self.pub = self.f.pub()
        self.pkg = self.f.package()

    def quota(self, route, **body):
        return self.f.client.post(
            "/api/v1/production/account-quota/" + route,
            json={"request_id": uuid4().hex, "project_id": self.f.pid, **body},
        )

    def configure(self):
        response = self.quota(
            "settings",
            account_id=self.f.creator["id"],
            expected_revision=0,
            daily_slots=2,
            concurrent_slots=1,
            hold_seconds=300,
            reason="现场验收安排",
        )
        self.assertEqual(response.status_code, 200, response.text)

    def slot(self, pub, action, version=0):
        return self.quota(
            "act",
            publication=commerce.pin(pub),
            action=action,
            expected_ticket_version=version,
            reason="核实交接状态",
        )

    def bind(self, pub, code=200):
        return self.f.command("bindings", code, **self.f.binding(self.pkg, pub))

    def test_active_hold_cannot_rebind_even_via_cosmetic_revision_then_releases(self):
        self.configure()
        held = self.slot(self.pub, "reserve").json()["data"]
        self.assertEqual(held["data"]["status"], "held")
        self.assertEqual(self.bind(self.pub, 409)["code"], "PUBLICATION_QUOTA_PINNED")
        draft = {**self.pub["data"], "title": "只改名称也会改变已预留意图的版本"}
        raw = self.f.client.patch(
            "/api/v1/records/" + self.pub["id"],
            json={"expected_revision": 1, "data": draft, "reason": "修改名称"},
        )
        self.assertEqual(raw.status_code, 409, raw.text)
        self.assertEqual(self.f.store.get(self.pub["id"], "local")["version"], 1)
        self.assertEqual(self.f.download(self.pkg).status_code, 200)
        released = self.slot(self.pub, "release", 1)
        self.assertEqual(released.status_code, 200, released.text)
        linked = self.bind(self.pub)["publication"]
        self.assertEqual(linked["version"], 2)
        # The replacement intent needs its own grant and retains the original account.
        held2 = self.slot(linked, "reserve", 2)
        self.assertEqual(held2.status_code, 200, held2.text)
        self.assertEqual(held2.json()["data"]["data"]["publication_version"], 2)

    def test_inflight_receipt_and_late_product_observation_survive_sale_expiry(self):
        linked = self.bind(self.pub)["publication"]
        self.configure()
        self.assertEqual(self.slot(linked, "reserve").status_code, 200)
        self.assertEqual(self.slot(linked, "handoff", 1).status_code, 200)
        self.assertEqual(self.bind(linked, 409)["code"], "PUBLICATION_QUOTA_PINNED")
        self.f.revision(self.f.product, quantity=0)
        self.assertFalse(
            self.f.client.get(
                "/api/v1/production/publications/" + linked["id"] + "/preflight"
            ).json()["data"]["can_handoff"]
        )
        receipt = self.f.post(
            "/production/publications/" + linked["id"] + "/receipt",
            {
                "expected_revision": linked["version"],
                "platform_post_id": "account-quota-fixture",
                "post_url": "https://example.test/quota-post",
                "observed_at": now(),
                "evidence": "真人回执测试夹具",
            },
        )
        state = self.f.client.get(
            "/api/v1/production/account-quota/" + self.f.pid + "/" + self.f.creator["id"],
            params={"publication_id": linked["id"]},
        ).json()["data"]
        self.assertEqual((state["effective_state"], state["receipt_count_24h"]), ("settled", 1))
        observation = self.f.command(
            "observations",
            publication=commerce.pin(receipt["publication"]),
            post=commerce.pin(receipt["post"]),
            evidence_source=commerce.pin(self.f.creator_source),
            observed_shop_product_ids=[],
            observed_disclosure_kind="absent",
            evidence="复看实际帖子未见商品卡或商业披露",
            observed_at=now(),
        )["post"]
        self.assertEqual(observation["data"]["commercial_observation"]["status"], "mismatch")
        self.assertEqual(self.f.store.get(self.f.product["id"], "local")["data"]["quantity"], 0)

    def test_binding_and_reservation_race_cannot_both_target_the_same_version(self):
        self.configure()

        def do_bind():
            return self.f.client.post(
                "/api/v1/production/commerce/bindings",
                json={
                    "project_id": self.f.pid,
                    "request_id": uuid4().hex,
                    **self.f.binding(self.pkg, self.pub),
                },
            )

        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(do_bind), pool.submit(self.slot, self.pub, "reserve")]
            results = [t.result() for t in tasks]
        self.assertEqual(
            sorted(r.status_code for r in results), [200, 409], [r.text for r in results]
        )
        failure = next(r for r in results if r.status_code == 409)
        self.assertIn(
            failure.json()["error"]["code"], {"PUBLICATION_QUOTA_PINNED", "REVISION_CONFLICT"}
        )

    def test_queued_expired_and_unconfigured_intents_can_still_bind(self):
        unconfigured = self.bind(self.pub)["publication"]
        self.assertTrue(unconfigured["data"]["commerce_binding"])
        self.configure()
        first = self.f.pub()
        self.assertEqual(self.slot(first, "reserve").status_code, 200)
        waiting = self.f.pub()
        self.assertEqual(self.slot(waiting, "reserve").json()["data"]["data"]["status"], "queued")
        self.assertEqual(self.bind(waiting)["publication"]["version"], 2)
        with patch(
            "app.account_quota.clock",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=10),
        ):
            self.assertEqual(self.bind(first)["publication"]["version"], 2)
