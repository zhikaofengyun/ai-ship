from unittest.mock import patch
import json
import os

from tests.test_production import ProductionBase


class TrendFeedTests(ProductionBase):
    def sync(self, provider="google_trends", status=200):
        return self.post(
            "/production/trends/sync",
            {"project_id": self.pid, "provider": provider, "region": "US"},
            status,
        )

    def test_rss_with_pinned_evidence_and_duplicate_sync(self):
        feed = b"""<rss xmlns:ht="https://trends.google.com/trending/rss"><channel>
        <item><title>A</title><pubDate>Wed, 25 Nov 2026 01:00:00 GMT</pubDate><ht:approx_traffic>500+</ht:approx_traffic>
        <ht:news_item><ht:news_item_url>https://news.example/a</ht:news_item_url></ht:news_item></item>
        <item><title>Missing date</title></item></channel></rss>"""
        with patch(
            "app.trend_feeds.fetch_feed",
            return_value=("https://trends.google.com/trending/rss?geo=US", feed),
        ):
            first, second = self.sync(), self.sync()
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["skipped"], 1)
        self.assertEqual(first["items"][0]["id"], second["items"][0]["id"])
        trend = self.store.get(first["items"][0]["id"], "local")
        source = self.store.get(trend["refs"][0]["id"], "local")
        self.assertEqual(source["data"]["provenance"]["feed_sha256"], first["feed_sha256"])
        self.assertEqual(trend["data"]["observations"]["search_traffic_label"], "500+")
        self.assertEqual(trend["data"]["verification"], "single_provider_observation")

    def test_chart_preserves_missing_counts_and_requires_key(self):
        with patch.dict(os.environ, {"AI_SHIP_YOUTUBE_DATA_KEY": ""}):
            self.assertEqual(
                self.sync("youtube_most_popular", 503)["error"]["code"], "TREND_CREDENTIAL_REQUIRED"
            )
        item = {
            "id": "abcdefghijk",
            "snippet": {"title": "A", "publishedAt": "2026-01-02T00:00:00Z"},
            "statistics": {"viewCount": "0", "likeCount": "23"},
        }
        with patch(
            "app.trend_feeds.fetch_feed",
            return_value=(
                "https://www.googleapis.com/youtube/v3/videos",
                json.dumps({"items": [item]}).encode(),
            ),
        ):
            observations = self.sync("youtube_most_popular")["items"][0]["observations"]
        self.assertEqual(observations["video_totals"], {"viewCount": 0, "likeCount": 23})
        self.assertEqual(observations["chart_position"], 1)

    def test_invalid_provider_document_is_atomic(self):
        with patch("app.trend_feeds.fetch_feed", return_value=("feed", b"<html/>")):
            self.assertEqual(self.sync(status=502)["error"]["code"], "TREND_FEED_FORMAT")
        self.assertEqual(self.store.list("local", "Trend", self.pid), [])

    def test_instance_charts_preserve_federated_origin_and_cannot_claim_a_region(self):
        uid = "be6d7258-5699-4715-ab3b-1b6bc8c2fb83"
        clip = {
            "uuid": uid,
            "name": "Blender demo",
            "account": {"host": "video.blender.org"},
            "url": "https://video.blender.org/videos/watch/" + uid,
            "publishedAt": "2026-01-02T10:00:00Z",
            "nsfw": False,
            "views": 0,
            "likes": 3,
            "comments": None,
        }
        body = {
            "project_id": self.pid,
            "provider": "peertube_trending",
            "instance": "framatube.org",
        }
        with patch(
            "app.trend_feeds.fetch_feed",
            return_value=(
                "https://framatube.org/api/v1/videos",
                json.dumps(
                    {
                        "data": [
                            clip,
                            {**clip, "nsfw": True},
                            {**clip, "url": "https://unrelated.test/" + uid},
                        ]
                    }
                ).encode(),
            ),
        ):
            result = self.post("/production/trends/sync", body)
            other = self.post("/production/trends/sync", {**body, "instance": "peertube.tv"})
            self.assertEqual(result["skipped"], 2)
            one = result["items"][0]["observations"]
            self.assertEqual(
                one["federated_video_identity"],
                other["items"][0]["observations"]["federated_video_identity"],
            )
            self.assertEqual(one["video_totals"], {"views": 0, "likes": 3})
            self.assertIsNone(result["region"])
            self.post("/production/trends/sync", {**body, "region": "US"}, 422)
            self.post("/production/trends/sync", {**body, "instance": "unrelated.test"}, 422)
