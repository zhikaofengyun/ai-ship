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
