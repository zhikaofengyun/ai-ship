"""Use real adapters and isolated formal-report fixtures; fixture decisions never enter the ledger."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.feature_run_evidence import LEGACY, criteria, runtime_fingerprint, validate_index

ROOT = Path(__file__).resolve().parents[1]
ATLAS = json.loads((ROOT / "contracts/atlas.json").read_text())
INDEX = json.loads((ROOT / "contracts/feature-evidence.json").read_text())
ATLAS_SHA = INDEX["atlas_sha256"]


class FeatureRunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = {
            "format": "atlas-evidence-index-1",
            "atlas_sha256": ATLAS_SHA,
            "runs": [],
            "reviews": [],
        }
        self.feature = next(f for f in ATLAS["features"] if f["id"] == "V04")
        (self.root / "app").mkdir()
        (self.root / "app/runtime.py").write_text("# isolated promotion fixture\n")
        self.runtime = runtime_fingerprint(self.root)

    def put(self, path, data):
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False).encode()
        full.write_bytes(raw)
        return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}

    def run_fixture(self, env="local", feature=None, missing=None):
        feature = feature or self.feature
        run_id = env + "-" + feature["id"]
        observed = {k: True for k in criteria(feature) if k != missing}
        if not observed:
            raise ValueError("empty fixture")
        raw = {
            "passed": True,
            "run_id": run_id,
            "feature_id": feature["id"],
            "environment": env,
            "target": "isolated-test-" + env,
            "runtime_sha256": self.runtime,
            "observations": {k: {"actual": v, "extent": "complete"} for k, v in observed.items()},
            "deployment_id": "test-instance",
            "service": "test-service",
            "external_receipt_id": "test-fixture-only",
        }
        observation = self.put("evidence/" + run_id + "-raw.json", raw)
        run = {
            "format": "atlas-criterion-run-1",
            "passed": True,
            "id": run_id,
            "feature_id": feature["id"],
            "environment": env,
            "target": "isolated-test-" + env,
            "runtime_sha256": self.runtime,
            "atlas_sha256": ATLAS_SHA,
            "executor": "fixture-worker",
            "command": "fixture-run",
            "finished_at": "2026-01-01T01:02:03Z",
            "measurement": observation,
            "checks": [
                {
                    "criterion": k,
                    "passed": True,
                    "extent": "complete",
                    "observation": "synthetic fixture: " + k,
                    "expected": True,
                }
                for k in sorted(observed)
            ],
        }
        pin = self.put("evidence/" + run_id + ".json", run)
        self.ledger["runs"].append({"id": run_id, "profile": "criterion-run-v1", **pin})
        return run, {"id": run_id, "sha256": pin["sha256"]}

    def review_fixture(self, basis, flag="full_business_acceptance", reviewer="fixture-reviewer"):
        report = {
            "format": "atlas-acceptance-review-1",
            "id": "review-" + flag,
            "accepted": True,
            "feature_id": "V04",
            "decision": flag,
            "target": "isolated-test-"
            + {"production_verified": "production", "external_live_verified": "external_live"}.get(
                flag, "local"
            ),
            "runtime_sha256": self.runtime,
            "atlas_sha256": ATLAS_SHA,
            "reviewer": reviewer,
            "reviewed_at": "2026-01-01T02:02:03Z",
            "rationale": "independent synthetic coverage review",
            "basis": basis,
        }
        self.ledger["reviews"].append(
            {"id": report["id"], **self.put("evidence/" + report["id"] + ".json", report)}
        )

    def validate(self):
        return validate_index(self.root, ATLAS, self.ledger, ATLAS_SHA)

    def test_actual_report_pins_bind_partial_measurements_without_whole_feature_promotion(self):
        historical = {
            **INDEX,
            "runs": [e for e in INDEX["runs"] if e["profile"] in LEGACY],
            "reviews": [],
        }
        runs, accepted = validate_index(ROOT, ATLAS, historical, ATLAS_SHA)
        self.assertIn("V04", runs)
        self.assertFalse(accepted)
        self.assertTrue(
            all(
                c["extent"] == "partial"
                for group in runs.values()
                for r in group
                for c in r["checks"]
            )
        )

    def test_matched_placement_browser_reports_do_not_accept_rehashed_changed_counts(self):
        for browser in ("chromium", "webkit"):
            entry = next(e for e in INDEX["runs"] if e["id"] == f"matched-placement-{browser}-v1")
            report = json.loads((ROOT / entry["path"]).read_text())
            self.ledger["runs"] = [dict(entry)]
            self.ledger["runs"][0].update(self.put(entry["path"], report))
            runs, reviews = self.validate()
            self.assertEqual(set(runs), {"M09"})
            self.assertFalse(reviews)
            self.assertEqual(
                {(c["criterion"], c["extent"]) for c in runs["M09"][0]["checks"]},
                {("Q02", "partial"), ("Q03", "partial"), ("Q06", "partial")},
            )
            report["matched_comparison"]["baseline"]["entrants"] = 101
            self.ledger["runs"][0].update(self.put(entry["path"], report))
            with self.assertRaisesRegex(ValueError, "Report check failed"):
                self.validate()

    def test_reject_scope_override_path_escape_symlink_false_and_modified_report(self):
        entry = copy.deepcopy(next(e for e in INDEX["runs"] if e["profile"] == "frame-search-v1"))
        dest = self.root / entry["path"]
        dest.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / entry["path"], dest)
        self.ledger["runs"] = [entry]
        self.validate()
        entry["feature_id"] = "V04"
        with self.assertRaisesRegex(ValueError, "bindings"):
            self.validate()
        del entry["feature_id"]
        original = entry["path"]
        entry["path"] = "../outside.json"
        with self.assertRaisesRegex(ValueError, "relative"):
            self.validate()
        entry["path"] = original
        dest.unlink()
        dest.symlink_to(ROOT / original)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.validate()
        dest.unlink()
        data = json.loads((ROOT / original).read_text())
        data["passed"] = False
        pin = self.put(original, data)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.validate()
        entry.update(pin)
        with self.assertRaisesRegex(ValueError, "Report check failed"):
            self.validate()
        data["passed"] = 1
        entry.update(self.put(original, data))
        with self.assertRaisesRegex(ValueError, "Report check failed"):
            self.validate()

    def test_complete_scoped_run_needs_separate_review_and_runtime_change_invalidates_promotion(
        self,
    ):
        _, basis = self.run_fixture()
        self.assertFalse(self.validate()[1])
        self.review_fixture([basis])
        decisions = self.validate()[1]["V04"]
        self.assertTrue(decisions["full_business_acceptance"][0]["current"])
        self.assertNotIn("production_verified", decisions)
        (self.root / "app/runtime.py").write_text("# revised runtime\n")
        self.assertFalse(self.validate()[1]["V04"]["full_business_acceptance"][0]["current"])

    def test_missing_condition_and_partial_extent_each_block_full_acceptance(self):
        missing = next(k for k in criteria(self.feature) if k.startswith("F"))
        _, basis = self.run_fixture(missing=missing)
        self.review_fixture([basis])
        with self.assertRaisesRegex(ValueError, "every route"):
            self.validate()
        self.ledger.update(runs=[], reviews=[])
        run, basis = self.run_fixture()
        run["checks"][0]["extent"] = "partial"
        raw_path = run["measurement"]["path"]
        observed = json.loads((self.root / raw_path).read_text())
        observed["observations"][run["checks"][0]["criterion"]]["extent"] = "partial"
        run["measurement"] = self.put(raw_path, observed)
        pin = self.put(self.ledger["runs"][0]["path"], run)
        self.ledger["runs"][0].update(pin)
        self.review_fixture([{**basis, "sha256": pin["sha256"]}])
        with self.assertRaisesRegex(ValueError, "every route"):
            self.validate()

    def test_other_feature_local_model_or_self_review_cannot_prove_production(self):
        _, basis = self.run_fixture()
        self.review_fixture([basis], "production_verified")
        with self.assertRaisesRegex(ValueError, "independent matching"):
            self.validate()
        self.ledger["reviews"] = []
        self.review_fixture([basis], reviewer="fixture-worker")
        with self.assertRaisesRegex(ValueError, "independent matching"):
            self.validate()
        self.ledger["reviews"] = []
        _, other = self.run_fixture(feature=next(f for f in ATLAS["features"] if f["id"] == "S02"))
        self.review_fixture([other])
        with self.assertRaisesRegex(ValueError, "independent matching"):
            self.validate()

    def test_production_and_external_references_require_their_own_pinned_measurements(self):
        _, local = self.run_fixture()
        self.review_fixture([local])
        _, production = self.run_fixture("production")
        _, external = self.run_fixture("external_live")
        self.review_fixture([production], "production_verified")
        self.review_fixture([external], "external_live_verified")
        self.assertTrue(all(v[0]["current"] for v in self.validate()[1]["V04"].values()))
        raw = self.root / "evidence/external_live-V04-raw.json"
        raw.write_bytes(raw.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.validate()

    def test_rehashed_false_observation_or_wrong_pinned_identity_still_fails(self):
        run, _ = self.run_fixture()
        bank = run["measurement"]["path"]
        raw = json.loads((self.root / bank).read_text())
        raw["observations"][run["checks"][0]["criterion"]]["actual"] = False
        run["measurement"] = self.put(bank, raw)
        self.ledger["runs"][0].update(self.put(self.ledger["runs"][0]["path"], run))
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.validate()
        raw["feature_id"] = "S02"
        run["measurement"] = self.put(bank, raw)
        self.ledger["runs"][0].update(self.put(self.ledger["runs"][0]["path"], run))
        with self.assertRaisesRegex(ValueError, "measurement"):
            self.validate()

    def test_relabelled_local_or_partially_measured_fixture_cannot_promote(self):
        run, _ = self.run_fixture()
        entry = self.ledger["runs"][0]
        run["environment"] = "production"
        entry.update(self.put(entry["path"], run))
        with self.assertRaisesRegex(ValueError, "measurement"):
            self.validate()
        run["environment"] = "local"
        raw_file = run["measurement"]["path"]
        observed = json.loads((self.root / raw_file).read_text())
        observed["observations"][run["checks"][0]["criterion"]]["extent"] = "partial"
        run["measurement"] = self.put(raw_file, observed)
        entry.update(self.put(entry["path"], run))
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.validate()
