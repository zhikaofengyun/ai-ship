import csv
from io import StringIO
from uuid import uuid4

from tests.test_production import ProductionBase


class PostReviewTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.posts = []
        for key in ("one", "two"):
            pub = self.create("Publication", {"name": "来源", "status": "draft"})
            event = self.create(
                "PlatformEvent",
                {
                    "provider": "manual",
                    "event_type": "manual_receipt",
                    "publication_id": pub["id"],
                    "evidence": "人工核对的来源条目",
                },
            )
            self.posts.append(
                self.create(
                    "Post",
                    {
                        "provider": "manual",
                        "account_id": self.account["id"],
                        "platform_post_id": key,
                        "publication_id": pub["id"],
                        "correlation_evidence": event["id"],
                        "final_media_status": "unknown",
                    },
                    [{"id": event["id"], "version": 1}, {"id": self.account["id"], "version": 1}],
                )
            )

    def metricrow(self, i, value, **kw):
        return self.metric(
            post_record_id=self.posts[i]["id"],
            value=value,
            data_mature_after="2026-01-02T12:00:00Z",
            counting_mode="cumulative",
            **kw,
        )

    def archive(self, rows, code=200):
        text = StringIO()
        w = csv.DictWriter(text, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        w.writeheader()
        w.writerows(rows)
        return self.post(
            "/production/post-reviews/archive",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "filename": "归档.csv",
                "csv_text": "\ufeff" + text.getvalue(),
            },
            code,
        )

    def compare(self, a, b, code=200):
        return self.post(
            "/production/post-reviews/compare",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "baseline": {"id": a["id"], "version": a["version"]},
                "comparison": {"id": b["id"], "version": b["version"]},
                "hypothesis": "开头顺序可能有影响",
                "counterevidence": "也可能由展示位置造成",
                "proposed_change": "对调首二镜头",
                "experiment_method": "在同类账号和同窗口复验",
                "stop_rule": "发生事实错误即停",
            },
            code,
        )

    def test_late_backfill_pins_new_observation_and_keeps_old_report(self):
        initial = self.archive([self.metricrow(0, 5), self.metricrow(1, 15)])
        a, b = initial["records"]
        self.assertEqual(a["data"]["archive_ref"]["id"], initial["archive"]["id"])
        first = self.compare(a, b)
        self.assertEqual(first["insight"]["data"]["difference"], 10)
        self.assertFalse(first["hypothesis"]["data"]["tested"])
        new = self.archive([self.metricrow(0, 11, observed_at="2026-01-04T00:00:00Z")])["records"][
            0
        ]
        compare = self.compare(a, b)["insight"]["data"]
        self.assertEqual(compare["difference"], 4)
        self.assertEqual(compare["observations"][0]["selected"]["id"], a["id"])
        self.assertEqual(compare["observations"][0]["latest"]["id"], new["id"])
        self.assertEqual(compare["scope"]["clip_attribution"], "unavailable")
        self.assertEqual(self.store.get(first["insight"]["id"], "local")["data"]["difference"], 10)
        duplicate = self.archive([self.metricrow(0, 5)])
        self.assertEqual((duplicate["count"], len(duplicate["duplicates"])), (0, 1))

    def test_missing_latest_ambiguous_time_and_incomparable_rejected(self):
        a, b = self.archive([self.metricrow(0, 5), self.metricrow(1, 7)])["records"]
        self.archive(
            [self.metricrow(0, None, observed_at="2026-01-04T00:00:00Z", missing_reason="等待补传")]
        )
        self.assertEqual(self.compare(a, b, 409)["error"]["code"], "METRIC_NOT_MATURE")
        self.archive([self.metricrow(0, 8, observed_at="2026-01-04T00:00:00Z")])
        self.assertEqual(self.compare(a, b, 409)["error"]["code"], "CONFLICTING_SNAPSHOTS")
        other = self.archive([{**self.metricrow(0, 2), "counting_mode": "interval"}])["records"][0]
        self.assertEqual(self.compare(other, b, 422)["error"]["code"], "INCOMPARABLE_POSTS")
        self.assertEqual(self.store.list("local", kind="Hypothesis", project_id=self.pid), [])

    def test_pre_maturity_observation_cannot_age_into_final_evidence(self):
        a, b = self.archive(
            [
                {**self.metricrow(0, 8), "data_mature_after": "2026-01-05T00:00:00Z"},
                self.metricrow(1, 12),
            ]
        )["records"]
        self.assertEqual(self.compare(a, b, 409)["error"]["code"], "METRIC_NOT_MATURE")

    def test_archive_rolls_back_on_bad_row_and_account_metrics_cannot_be_used(self):
        orig = self.store.list("local", kind="Source", project_id=self.pid)
        self.archive([self.metricrow(0, 5), self.metricrow(1, 6, observed_at="not ISO")], 422)
        self.assertEqual(self.store.list("local", kind="Source", project_id=self.pid), orig)
        self.assertEqual(self.store.list("local", kind="Metric", project_id=self.pid), [])
        a, b = self.archive(
            [{**self.metricrow(0, 5), "post_record_id": None}, self.metricrow(1, 7)]
        )["records"]
        self.assertEqual(self.compare(a, b, 422)["error"]["code"], "POST_METRIC_REQUIRED")
