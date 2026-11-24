"""Run every discovered test and preserve per-case execution evidence."""

from datetime import datetime, timezone
import argparse
import json
import random
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self.active = None

    def startTest(self, test):
        self.active = {
            "id": test.id(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.active["seconds"] = round(time.monotonic() - self.started, 4)
        self.cases.append(self.active)
        self.active = None
        super().stopTest(test)

    def addSuccess(self, test):
        self.active["status"] = "passed"
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.active.update(status="failed", failure=self._exc_info_to_string(error, test))
        super().addFailure(test, error)

    def addError(self, test, error):
        if self.active is None:
            self.cases.append(
                {
                    "id": test.id(),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "seconds": 0,
                    "status": "setup_or_teardown_error",
                    "failure": self._exc_info_to_string(error, test),
                }
            )
        else:
            self.active.update(status="error", failure=self._exc_info_to_string(error, test))
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.active.update(status="skipped", reason=reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, error):
        if error:
            self.active.update(status="failed", failure=self._exc_info_to_string(error, test))
        super().addSubTest(test, subtest, error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--order-seed", type=int, default=0)
    args = parser.parse_args()
    start = datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)
    )

    def flatten(items):
        for item in items:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item

    cases = list(flatten(suite))
    random.Random(args.order_seed).shuffle(cases)
    suite = unittest.TestSuite(cases)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    unique = len({case["id"] for case in result.cases}) == result.testsRun
    report = {
        "started_at": start,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "tests_run": result.testsRun,
        "unique_test_ids": unique,
        "order_seed": args.order_seed,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful() and unique and not result.skipped,
        "cases": result.cases,
        "external_transport": "Model calls explicitly use injected responses; no live platform or provider account is contacted.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
