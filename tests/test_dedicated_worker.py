"""A dedicated process may drain independently while the API and remaining queue stay usable."""

from pathlib import Path
import hashlib
import os
import subprocess
import sys
import time
import unittest

from tests import test_batches

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "POSIX SIGTERM has graceful drain semantics")
class DedicatedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.job = test_batches.BatchTests()
        self.job.setUp()
        self.addCleanup(self.job.tearDown)

    def launch(self):
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/run.py"),
                "--batch-worker-only",
                "--batch-workers",
                "1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        self.addCleanup(stop)
        return process

    def state_after(self, bid, predicate):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = self.job.get(bid, "?limit=1")
            if predicate(result["counts"]):
                return result
            if result["counts"]["failed"]:
                self.fail(str(self.job.get(bid, "?state=failed&limit=1")))
            time.sleep(0.1)
        self.fail("Timed out waiting for the dedicated process")

    def test_worker_drain_restart_and_unique_completed_outputs(self):
        n = 20
        batch = self.job.post(
            self.job.path,
            {
                **self.job.spec,
                "slots": [{"choices": self.job.choices}] * 3,
                "count": n,
                "output": "render",
            },
        )
        first = self.launch()
        self.state_after(batch["id"], lambda c: c["succeeded"] >= 1)
        first.terminate()
        self.assertEqual(first.wait(timeout=30), 0)
        pending = self.job.get(batch["id"])
        self.assertGreater(pending["counts"]["queued"], 0)
        self.assertEqual(pending["counts"]["running"], 0)
        self.launch()
        self.launch()
        self.state_after(batch["id"], lambda c: c["succeeded"] == n)
        items = self.job.get(batch["id"], "?limit=30")["items"]
        self.assertEqual(len({i["artifact_id"] for i in items}), n)
        self.assertEqual(len({i["timeline_id"] for i in items}), n)
        digests = set()
        for item in items:
            r = self.job.client.get("/api/v1/media/artifacts/" + item["artifact_id"] + "/file")
            r.raise_for_status()
            digests.add(hashlib.sha256(r.content).hexdigest())
        self.assertEqual(len(digests), n)
