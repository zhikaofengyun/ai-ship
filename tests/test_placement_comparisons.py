"""Focused SQLite/API checks for conditional retention and frozen creative feedback."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.edit_feedback import feedback_context
from app.errors import DomainError
from app.selection import ChosenVersion
from tests.test_production import ProductionBase


class PlacementComparisonTests(ProductionBase):
    base = "/production/post-reviews"

    def setUp(self):
        super().setUp()
        self.sources = [self.create("Asset", {"name": name}) for name in ("Baseline", "Candidate")]
        self.query = {
            "project_id": self.pid,
            "cohort": {
                "account_id": self.account["id"],
                "start_seconds": 0,
                "end_seconds": 1,
                "traffic": "organic",
                "cohort_after_hours": 0,
                "cohort_hours": 24,
            },
            "baseline": self.pin(self.sources[0]),
            "candidate": self.pin(self.sources[1]),
            "role": "video",
        }

    @staticmethod
    def pin(record):
        return {"id": record["id"], "version": record["version"]}

    def observe(self, day, arms, start, stop, post=None, hours=50, coverage=1, traffic="organic"):
        published = datetime(2026, 1, day, 0, tzinfo=timezone.utc)
        if post is None:
            post = self.create(
                "Post",
                {
                    "platform_post_id": uuid4().hex,
                    "account_id": self.account["id"],
                    "actual_published_at": published.isoformat(),
                    "final_media_status": "verified_same",
                },
            )
        clock = {"num": 1, "den": 1000000}
        layers = [
            {
                "source_ref": {**self.pin(self.sources[i]), "kind": "Asset"},
                "clip_use_id": f"clip-{i}",
                "role": "video",
                "source_interval": [0, round(coverage * 1e6)],
                "mapping_curve": {"kind": "linear"},
            }
            for i in arms
        ]
        spans = [{"output_interval": [0, round(coverage * 1e6)], "contributions": layers}]
        if coverage < 1:
            spans.append({"output_interval": [round(coverage * 1e6), 1000000], "contributions": []})
        mapping = {"data": {"target_timebase": clock, "segments": spans}}
        # The media verification gate has a real FFmpeg/download test in test_auto_edits.
        # Here only its already-verified time map is substituted; retention is written via the API.
        with patch("app.retention.final_mapping", return_value=(mapping, [post])):
            response = self.post(
                self.base + "/retention-mapping",
                {
                    "project_id": self.pid,
                    "request_id": uuid4().hex,
                    "post": self.pin(post),
                    "source": "Synthetic cohort from a frozen test report",
                    "denominator": start,
                    "traffic": traffic,
                    "window_start": published.isoformat(),
                    "window_end": (published + timedelta(days=1)).isoformat(),
                    "observed_at": (published + timedelta(hours=hours)).isoformat(),
                    "points": [
                        {"at_seconds": 0, "retained": start},
                        {"at_seconds": 1, "retained": stop},
                    ],
                },
            )
        return post, response["insight"]

    def test_matching_denominators_mixed_sources_exclusions_and_recheck(self):
        baseline, _ = self.observe(1, [0], 100, 30)
        self.observe(1, [0], 200, 140)
        self.observe(1, [1], 50, 40)
        self.observe(2, [0], 20, 12)
        self.observe(2, [1], 1000, 500)
        self.observe(3, [0], 10, 10)
        self.observe(1, [0, 1], 13, 1)
        self.observe(1, [1], 15, 1, coverage=0.5)
        self.observe(2, [1], 10, 10, traffic="paid")
        result = self.post(self.base + "/placement-comparisons?limit=1", self.query)
        self.assertEqual(result["matched_days"], 2)
        self.assertEqual(len(result["days"]), 1)
        self.assertEqual(
            result["matched_baseline"],
            {
                "entrants": 320,
                "retained": 182,
                "posts": 3,
                "other_source_posts": 0,
            },
        )
        self.assertEqual(result["matched_candidate"]["entrants"], 1050)
        self.assertAlmostEqual(result["difference_pp"], 10)
        for got, want in zip(result["leave_one_day_out_range_pp"], [-10, 30]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(result["unmatched_baseline_posts"], 1)
        self.assertEqual(result["co_occurring_posts_excluded"], 1)
        shared = result["co_occurring_observations"]
        self.assertEqual((shared["posts"], shared["entrants"], shared["retained"]), (1, 13, 1))
        self.assertEqual(len(shared["example_receipts"]), 1)
        self.assertEqual(result["partial_interval_posts_excluded"], 1)
        self.assertFalse(result["causal_claim"])
        second = self.post(self.base + "/placement-comparisons?offset=1", self.query)
        self.assertEqual(second["days"][0]["published_day_utc"], "2026-01-02")
        self.assertEqual(second["evidence_hash"], result["evidence_hash"])

        body = {
            **self.query,
            "evidence_hash": result["evidence_hash"],
            "request_id": uuid4().hex,
            "hypothesis": "Hypothesis on changing first shot",
            "counterevidence": "Audience may differ",
            "next_edit": "Hold copy fixed, replace shot",
            "stop_rule": "Stop if improvement fails",
        }
        saved = self.post(self.base + "/placement-comparison-hypotheses", body)["insight"]
        again = self.post(self.base + "/placement-comparison-hypotheses", body)["insight"]
        self.assertEqual(saved["id"], again["id"])
        self.assertEqual(len([r for r in saved["refs"] if r["kind"] == "Post"]), 5)
        pin = ChosenVersion(**self.pin(saved))
        self.assertIn(
            '"matched_days":2', feedback_context(LOCAL_ACTOR, self.pid, [pin])[0]["data_excerpt"]
        )
        self.observe(1, [0], 100, 98, post=baseline, hours=72)
        with self.assertRaises(DomainError) as error:
            feedback_context(LOCAL_ACTOR, self.pid, [pin])
        self.assertEqual(error.exception.code, "PLACEMENT_COMPARISON_CHANGED")

    def test_insufficient_days_and_conflicting_corrections(self):
        self.observe(1, [0], 100, 90)
        candidate, _ = self.observe(2, [1], 100, 40)
        corrected, _ = self.observe(1, [1], 100, 60)
        self.observe(
            1, [1], 100, 99, post=corrected
        )  # Same observation instant, contradictory counts.
        result = self.post(self.base + "/placement-comparisons", self.query)
        self.assertEqual(result["cohort_diagnostics"]["conflicting_latest_posts"], 1)
        self.assertEqual(result["matched_days"], 0)
        self.assertIsNone(result["difference_pp"])
        self.observe(1, [1], 100, 0, post=corrected, hours=63)
        scoped = {**self.query, "published_from": "2026-01-01", "published_through": "2026-01-01"}
        result = self.post(self.base + "/placement-comparisons", scoped)
        self.assertEqual(result["matched_days"], 1)
        self.assertEqual(result["matched_candidate"]["retained"], 0)
        self.assertIsNone(result["leave_one_day_out_range_pp"])
        self.assertEqual(result["difference_pp"], -90)
        self.assertEqual(result["cohort_diagnostics"]["conflicting_latest_posts"], 0)
        self.post(
            self.base + "/placement-comparisons",
            {**scoped, "candidate": self.query["baseline"]},
            422,
        )
        self.post(
            self.base + "/placement-comparisons", {**scoped, "published_from": "2026-01-07"}, 422
        )
        # Another project must not consume this cohort.
        other = self.create("Project", {"name": "second project"})
        self.post(self.base + "/placement-comparisons", {**scoped, "project_id": other["id"]}, 409)

    def test_total_views_and_edited_receipts_do_not_fill_missing_curves(self):
        aggregate_only = self.create(
            "Post",
            {
                "platform_post_id": "synthetic-whole-post",
                "account_id": self.account["id"],
                "actual_published_at": "2026-01-01T00:00:00Z",
                "final_media_status": "verified_same",
            },
        )
        self.post(
            "/production/metrics/import",
            {
                "project_id": self.pid,
                "rows": [self.metric(post_record_id=aggregate_only["id"], value=10000)],
            },
        )

        def reports():
            response = self.client.get(
                "/api/v1" + self.base + "/placement-performance",
                params={
                    **self.query["cohort"],
                    "project_id": self.pid,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["data"]

        report = reports()
        self.assertFalse(report["causal_claim"])
        self.assertEqual(report["items"], [])
        self.assertEqual(report["matching_posts"], 0)
        self.post(self.base + "/placement-comparisons", self.query, 409)
        observed, _ = self.observe(1, [0], 100, 30)
        _, latest = self.observe(1, [0], 100, 22, post=observed, hours=74)
        self.store.update(
            latest["id"],
            {**latest["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=latest["version"],
            reason="Fixture withdrawal",
        )
        report = reports()
        self.assertEqual(report["revised_latest_receipts"], 1)
        self.assertEqual(report["items"], [])  # No resurrection of the earlier 30% result.
        self.observe(1, [0], 100, 25, post=observed, hours=78)
        self.assertEqual(reports()["items"][0]["mean_post_retention"], 0.25)
        self.store.update(
            observed["id"],
            {**observed["data"], "final_media_status": "unknown"},
            tenant_id="local",
            expected_revision=observed["version"],
            reason="Platform re-edit",
        )
        self.assertEqual(reports()["items"], [])
