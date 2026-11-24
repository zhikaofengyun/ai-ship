from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.test_production import ProductionBase


class CommentFeedbackTests(ProductionBase):
    def observe(self, **kw):
        code = kw.pop("code", 200)
        row = {
            "request_id": uuid4().hex,
            "project_id": self.pid,
            "account_id": self.account["id"],
            "platform_post_id": "real-post-key",
            "platform_comment_id": "comment-321",
            "expected_revision": 0,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "provenance": "商家客服人工核对",
            "retention_basis": "获准保留去标识节选",
            "visibility": "visible",
            "excerpt": "opaque body marker <img onerror='not-executed'>",
            "retain_until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            **kw,
        }
        return self.post("/production/comment-feedback/observe", row, code)

    def classify(self, item, **kw):
        code = kw.pop("code", 200)
        return self.post(
            "/production/comment-feedback/classify",
            {
                "request_id": uuid4().hex,
                "project_id": self.pid,
                "comment": {"id": item["id"], "version": item["version"]},
                "intent": "question",
                "sentiment": "uncertain",
                "topic": "usage",
                "expected_classification_version": 0,
                **kw,
            },
            code,
        )

    def summary(self):
        return self.client.get(
            "/api/v1/production/comment-feedback/" + self.pid + "/summary"
        ).json()["data"]

    def body(self, c):
        return self.client.get(
            "/api/v1/production/comment-feedback/" + self.pid + "/" + c["id"] + "/body"
        ).json()["data"]

    def test_independent_body_and_classification_revisions(self):
        c = self.observe()
        self.assertNotIn("opaque body marker", str(c))
        self.assertIsNone(c["data"]["raw_ref"])
        self.assertIn("opaque body marker", self.body(c)["excerpt"])
        l = self.classify(c)
        self.assertEqual(l["data"]["body_version"], 1)
        self.assertEqual(self.summary()["sentiments"], {"uncertain": 1})
        self.classify(c, expected_classification_version=1, sentiment="neutral")
        self.assertEqual(self.summary()["sentiments"], {"neutral": 1})
        revised = self.observe(expected_revision=1, excerpt="经过更新的新节选")
        self.assertEqual(revised["version"], 2)
        self.assertEqual(self.summary()["unclassified"], 1)
        self.classify(c, expected_classification_version=2, code=409)
        self.classify(revised, expected_classification_version=2)
        self.assertEqual(self.summary()["classified"], 1)
        self.assertEqual(self.store.get(c["id"], "local")["version"], 2)

    def test_deletion_purges_excerpts_and_excludes_judgments(self):
        c = self.observe()
        self.classify(c, sentiment="negative")
        self.observe(
            expected_revision=1,
            visibility="deleted",
            excerpt=None,
            retain_until=None,
            unavailable_reason="原贴已删除",
        )
        self.assertIsNone(self.body(c)["excerpt"])
        self.assertEqual(self.summary()["unavailable"], 1)
        self.assertEqual(self.summary()["sentiments"], {})
        self.observe(expected_revision=2, code=409)
        with self.store.connection() as db:
            self.assertEqual(db.execute("select count(*) from comment_excerpts").fetchone()[0], 0)
            for table, column in (
                ("revisions", "data_json"),
                ("command_results", "response_json"),
                ("events", "payload_json"),
            ):
                self.assertEqual(
                    db.execute(
                        f"select count(*) from {table} where {column} like ?",
                        ("%opaque body marker%",),
                    ).fetchone()[0],
                    0,
                )
        self.assertEqual(len(self.store.history(c["id"], "local")), 2)

    def test_scope_expiry_and_repeat_conflicts(self):
        c = self.observe()
        self.observe(code=409)
        self.observe(platform_comment_id="another", retain_until="2000-01-01T00:00:00Z", code=422)
        other = self.create("Project", {"name": "Other"})
        self.classify(c, project_id=other["id"], code=409)
        generic = self.create("Comment", {"name": "unverified generic observation"})
        self.classify(generic, code=422)
        with self.store.connection() as db:
            db.execute("UPDATE comment_excerpts SET expires='2000-01-01T00:00:00+00:00'")
        self.assertFalse(self.body(c)["available"])
        self.classify(c, code=409)
        self.assertEqual(self.summary()["unavailable"], 1)

    def test_external_link_and_revoked_account(self):
        c = self.observe()
        wrong_post = self.create(
            "Post", {"platform_post_id": "other", "account_id": self.account["id"]}
        )
        self.observe(post_record_id=wrong_post["id"], expected_revision=1, code=422)
        self.store.update(
            self.account["id"],
            {**self.account["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=1,
        )
        self.assertEqual(self.summary()["unavailable"], 1)
        self.classify(c, code=409)
        self.observe(
            expected_revision=1,
            visibility="deleted",
            excerpt=None,
            retain_until=None,
            unavailable_reason="不再授权",
        )
