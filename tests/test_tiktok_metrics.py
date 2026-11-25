import json
import os
from unittest.mock import patch
from app import tiktok_metrics
from tests.test_production import ProductionBase


class TikTokMetricsTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.target = self.create("Account", {"provider": "tiktok"})
        self.first = self.create(
            "Post", {"platform_post_id": "100", "account_id": self.target["id"]}
        )
        self.second = self.create(
            "Post", {"platform_post_id": "101", "account_id": self.target["id"]}
        )
        self.body = {
            "project_id": self.pid,
            "request_id": "metric-sync",
            "post_ids": [self.first["id"], self.second["id"]],
        }
        self.env2 = patch.dict(
            os.environ,
            {
                "AI_SHIP_TIKTOK_ACCOUNTS": json.dumps(
                    [
                        {
                            "tenant_id": "local",
                            "project_id": self.pid,
                            "account_id": self.target["id"],
                            "open_id": "fixture-user",
                            "token_env": "AI_SHIP_TIKTOK_TOKEN_TEST",
                        }
                    ]
                ),
                "AI_SHIP_TIKTOK_TOKEN_TEST": "fixture",
            },
        )
        self.env2.start()
        self.addCleanup(self.env2.stop)

    def test_missing_post_zero_field_and_retry(self):
        returned = (
            [
                {
                    "id": "100",
                    "create_time": 1662100000,
                    "view_count": 120,
                    "like_count": 0,
                    "comment_count": 2,
                }
            ],
            "log-test",
        )
        with patch.object(tiktok_metrics, "observation", return_value=returned) as fetch:
            first = self.post("/production/tiktok/metrics", self.body)
            second = self.post("/production/tiktok/metrics", self.body)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["count"], 4)
        self.assertEqual(first["missing_posts"], [self.second["id"]])
        self.assertEqual(first["source_id"], second["source_id"])
        metrics = {
            m["data"]["metric_name"]: m["data"]
            for m in self.store.list("local", "Metric", self.pid)
        }
        self.assertEqual(metrics["likes"]["value"], 0)
        self.assertIsNone(metrics["shares"]["value"])
        self.assertEqual(metrics["views"]["archive_ref"]["id"], first["source_id"])
        self.assertEqual(metrics["views"]["counting_mode"], "cumulative")

    def test_unrequested_video_rejected_and_scope_required(self):
        with patch.object(tiktok_metrics, "observation", return_value=([{"id": "wrong"}], None)):
            error = self.post("/production/tiktok/metrics", self.body, 502)
        self.assertEqual(error["error"]["code"], "TIKTOK_VIDEO_EVIDENCE")
        self.assertFalse(self.store.list("local", "Metric", self.pid))
        with patch.dict(os.environ, {"AI_SHIP_TIKTOK_ACCOUNTS": "[]"}):
            self.post("/production/tiktok/metrics", self.body, 503)

    def test_identity_capability_probe_and_suspend(self):
        import httpx

        path = f"/production/tiktok/accounts/{self.target['id']}/connection"

        def respond(request):
            self.assertEqual(request.headers.get("authorization"), "Bearer fixture")
            if request.url.path.endswith("/user/info/"):
                result = {"user": {"open_id": "fixture-user"}}
            else:
                self.assertEqual(request.url.path, "/v2/video/list/")
                result = {"videos": []}
            return httpx.Response(
                200, json={"data": result, "error": {"code": "ok", "log_id": "fixture"}}
            )

        with patch.object(
            tiktok_metrics,
            "tiktok_client",
            side_effect=lambda token: httpx.Client(
                base_url=tiktok_metrics.ROOT,
                headers={"Authorization": "Bearer " + token},
                transport=httpx.MockTransport(respond),
            ),
        ):
            ok = self.post(
                path, {"project_id": self.pid, "action": "verify", "expected_revision": 0}
            )
        self.assertEqual(ok["state"], "verified_read_only")
        self.assertEqual(ok["capabilities"], ["user.info.basic", "video.list"])
        self.post(path, {"project_id": self.pid, "action": "suspend", "expected_revision": 0}, 409)
        self.post(path, {"project_id": self.pid, "action": "suspend", "expected_revision": 1})
        with patch.object(tiktok_metrics, "observation", side_effect=AssertionError("suspended")):
            self.assertEqual(
                self.post("/production/tiktok/metrics", self.body, 409)["error"]["code"],
                "TIKTOK_CONNECTION_SUSPENDED",
            )
