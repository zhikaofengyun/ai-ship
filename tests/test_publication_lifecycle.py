"""Manual observations preserve producer intent and the original post metric chain."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from tests.test_production import ProductionBase


class PublicationLifecycleTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.artifact = self.create(
            "Artifact", {"name": "排程测试样片", "status": "technical_passed"}
        )
        for kind in ("rights_check", "content_check"):
            self.post(
                "/production/approvals",
                {
                    "subject_id": self.artifact["id"],
                    "subject_version": 1,
                    "kind": kind,
                    "decision": "approved",
                    "evidence": "本机回归核对",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        self.pub = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "account_id": self.account["id"],
                "artifact_id": self.artifact["id"],
                "title": "首发",
                "privacy": "private",
            },
        )

    def call(self, pub, action, expected=200, key=None, **fields):
        return self.post(
            "/production/publication-lifecycle/" + pub["id"] + "/" + action,
            {
                "expected_revision": pub["version"],
                "request_id": key or uuid4().hex,
                "reason": "核对账号后台与制作排期",
                **fields,
            },
            expected,
        )

    def observed(self, pub, kind, expected=200, **fields):
        return self.call(
            pub,
            "observe",
            expected,
            observation=kind,
            observed_at=datetime.now(timezone.utc).isoformat(),
            evidence="运营人员在原账号后台对照工单 280，记录核对结论",
            **fields,
        )

    def pre(self, pub):
        return self.client.get(
            "/api/v1/production/publications/" + pub["id"] + "/preflight"
        ).json()["data"]

    def receipt(self, pub, post_id):
        return self.post(
            "/production/publications/" + pub["id"] + "/receipt",
            {
                "expected_revision": pub["version"],
                "platform_post_id": post_id,
                "post_url": "https://example.org/posts/" + post_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "evidence": "实际后台人工登记测试",
                "visibility": "public",
            },
        )

    def test_cancel_unsent_releases_quota_and_preserves_late_post(self):
        pub = self.pub
        self.post(
            "/production/account-quota/settings",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "account_id": self.account["id"],
                "expected_revision": 0,
                "daily_slots": 4,
                "concurrent_slots": 1,
                "hold_seconds": 120,
                "reason": "测试配额",
            },
        )
        hold = self.post(
            "/production/account-quota/act",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "publication": {"id": pub["id"], "version": 1},
                "expected_ticket_version": 0,
                "action": "reserve",
                "reason": "本地排程",
            },
        )
        key = uuid4().hex
        cancelled = self.call(pub, "cancel", key=key)
        closed = cancelled["publication"]
        self.assertEqual(self.store.get(hold["id"], "local")["data"]["status"], "released")
        self.assertFalse(self.pre(closed)["can_handoff"])
        self.assertTrue(self.call(pub, "cancel", key=key)["idempotent_replay"])
        self.call(closed, "change", 409, title="无效重排")
        late = self.receipt(closed, "late-1")
        self.assertEqual(late["publication"]["data"]["lifecycle_state"], "cancelled_before_handoff")
        self.assertEqual(late["post"]["data"]["publication_id"], pub["id"])

    def test_changed_schedule_races_and_predecessor_late_evidence_blocks_successor(self):
        p = self.pub

        def move(title):
            body = {
                "expected_revision": 1,
                "request_id": uuid4().hex,
                "reason": "安排另一时段",
                "title": title,
                "local_datetime": "2030-11-03T01:30:00",
                "timezone": "America/New_York",
                "fold": 1,
            }
            return self.client.post(
                "/api/v1/production/publication-lifecycle/" + p["id"] + "/change", json=body
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(move, ["方案一", "方案二"]))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        changed = next(r for r in results if r.status_code == 200).json()["data"]
        successor, closed = changed["successor"], changed["publication"]
        self.assertEqual(successor["data"]["scheduled_at"], "2030-11-03T06:30:00+00:00")
        self.assertEqual(successor["data"]["predecessor_intent"]["version"], 1)
        self.assertFalse(self.pre(closed)["can_handoff"])
        self.receipt(closed, "late-1")
        self.assertIn("PREDECESSOR_RECEIPT", [i["code"] for i in self.pre(successor)["problems"]])
        self.call(successor, "change", 409, title="不能未经核对重复迁移")

    def test_unknown_effect_actual_receipt_and_per_post_removal(self):
        pending = self.observed(self.pub, "outcome_unknown")["publication"]
        self.assertFalse(self.pre(pending)["can_handoff"])
        self.call(pending, "cancel", 409)
        cleared = self.observed(pending, "confirmed_not_sent")["publication"]
        self.assertTrue(self.pre(cleared)["can_handoff"])
        a = self.receipt(cleared, "p-1")
        b = self.receipt(a["publication"], "p-2")
        metric = self.post(
            "/production/metrics/import",
            {
                "project_id": self.pid,
                "rows": [self.metric(post_record_id=a["post"]["id"], value=723)],
            },
        )["records"][0]
        self.observed(b["publication"], "confirmed_not_sent", 409)
        removed = self.observed(
            b["publication"], "post_removed", post={"id": a["post"]["id"], "version": 1}
        )
        self.assertIs(removed["external_request_sent"], False)
        self.assertEqual(removed["post"]["id"], a["post"]["id"])
        self.assertEqual(removed["post"]["data"]["visibility"], "removed")
        self.assertFalse(removed["event"]["data"]["independently_verified"])
        self.assertEqual(
            self.store.get(a["post"]["id"], "local", 1)["data"]["visibility"], "public"
        )
        self.assertEqual(self.store.get(b["post"]["id"], "local")["data"]["visibility"], "public")
        self.assertEqual(
            removed["publication"]["data"]["post_refs"], [a["post"]["id"], b["post"]["id"]]
        )
        retained = self.store.get(metric["id"], "local")
        self.assertEqual(retained["data"]["value_or_null"], 723)
        self.assertEqual(
            {(r["id"], r["version"]) for r in retained["refs"] if r["kind"] == "Post"},
            {(a["post"]["id"], 1)},
        )
