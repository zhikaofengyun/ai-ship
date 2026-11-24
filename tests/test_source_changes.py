from uuid import uuid4
from tests.test_production import ProductionBase


def pin(item):
    return {"id": item["id"], "version": item["version"]}


class SourceChangeTests(ProductionBase):
    def signal(self, source, change, when, **overrides):
        return {
            "event_id": uuid4().hex,
            "source": pin(source),
            "change": change,
            "occurred_at": when,
            "confirmed": change != "missing",
            "evidence": "项目内人工核对时保存的回执",
            **overrides,
        }

    def sync(self, *events):
        return self.post(
            "/production/acquisition/changes", {"project_id": self.pid, "events": events}
        )

    def release(self, source):
        artifact = self.create(
            "Artifact",
            {"name": "测试成片", "checksum": "fixture", "status": "technical_passed"},
            [pin(source)],
        )
        pub = self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "artifact_id": artifact["id"],
                "account_id": self.account["id"],
                "title": "本地待发",
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
                    "evidence": "测试审阅",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        return pub

    def ready(self, pub):
        return self.client.get(f"/api/v1/production/publications/{pub['id']}/preflight").json()[
            "data"
        ]

    def test_scoped_manifest_separates_origin_removal_rights_and_historical_posts(self):
        src = self.create("Source", {"status": "active"})
        other = self.create("Source", {"status": "active"})
        pub = self.release(src)
        unaffected = self.release(other)
        old = self.create("Post", {"platform_post_id": "published-once"}, [pin(pub)])
        self.assertTrue(self.ready(pub)["can_handoff"])
        absent = self.signal(src, "missing", "2025-02-05T00:00:00Z")
        self.assertEqual(self.sync(absent)["results"][0]["disposition"], "observed_only")
        self.assertEqual(self.store.get(src["id"], "local")["version"], 1)
        deleted = self.signal(src, "origin_deleted", "2025-02-04T00:00:00Z")
        r = self.sync(deleted)["results"][0]
        self.assertEqual(r["disposition"], "applied")
        self.assertEqual([x["id"] for x in r["impact"]["release_review"]], [pub["id"]])
        self.assertEqual([x["id"] for x in r["impact"]["historical"]], [old["id"]])
        self.assertTrue(self.ready(pub)["can_handoff"])
        newest = self.store.get(src["id"], "local")
        signal = self.signal(newest, "rights_revoked", "2025-02-03T12:00:00Z")
        result = self.sync(signal)["results"][0]
        self.assertEqual(result["disposition"], "applied")
        self.assertFalse(self.ready(pub)["can_handoff"])
        self.assertTrue(self.ready(unaffected)["can_handoff"])
        self.assertEqual(self.store.get(old["id"], "local")["hash"], old["hash"])
        self.assertEqual(self.sync(signal)["results"][0]["event"], result["event"])

    def test_revision_review_dedup_late_conflict_and_per_entry_failure(self):
        src = self.create("Source", {"status": "active"})
        pub = self.release(src)
        revised = self.signal(src, "revised", "2025-02-04T09:00:00Z", origin_revision="2")
        r = self.sync(revised)["results"][0]
        self.assertEqual(r["disposition"], "applied")
        pre = self.ready(pub)
        self.assertFalse(pre["can_handoff"])
        self.assertIn("SOURCE_CONTENT_CHANGED", [p["code"] for p in pre["problems"]])
        late = self.signal(src, "origin_deleted", "2025-02-04T08:00:00Z")
        conflict = self.signal(src, "origin_deleted", "2025-02-04T09:00:00Z")
        missing = self.signal(src, "missing", "2025-02-06T00:00:00Z")
        report = self.sync({**revised, "origin_revision": "wrong"}, late, conflict, missing)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(
            [x["disposition"] for x in report["results"]],
            ["failed", "late_ignored", "needs_reconciliation", "observed_only"],
        )
        self.assertEqual(report["results"][0]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.store.get(src["id"], "local")["version"], 2)
        self.assertEqual(len(self.store.list("local", kind="Event")), 4)
