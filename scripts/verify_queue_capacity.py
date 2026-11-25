"""Verify the 100,000-item queue boundary, independent of media encoding throughput."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.batches import Recipe, choices_for
from app.store import Store
from tests.test_batches import BatchTests


def measure():
    os.environ["AI_SHIP_BATCH_WORKERS"] = "0"
    case = BatchTests()
    case.setUp()
    try:
        spec = {
            **case.spec,
            "slots": [{"choices": case.choices} for _ in range(9)],
            "count": 100000,
            "seed": 25,
        }
        before = time.monotonic()
        batch = case.post(case.path, spec)
        enqueue_seconds = time.monotonic() - before
        bid = batch["id"]
        assert batch["counts"]["queued"] == 100000
        assert case.post(case.path, spec)["id"] == bid
        assert case.get(bid, "?after=99998&limit=2")["items"][-1]["ordinal"] == 100000
        assert case.post(case.path + "/" + bid + "/pause", {})["status"] == "paused"
        with Store(case.store.path).connection() as db:
            durable = db.execute(
                "SELECT count(*) FROM production_items WHERE batch=? AND status='queued'", (bid,)
            ).fetchone()[0]
        recipe = Recipe(**spec)
        combinations = {
            tuple(round(c.source_in * 1000) for c in choices_for(recipe, ordinal))
            for ordinal in range(1, 100001)
        }
        assert durable == len(combinations) == 100000
        assert case.post(case.path + "/" + bid + "/resume", {})["status"] in {
            "queued",
            "active",
            "running",
        }
        cancelled = case.post(case.path + "/" + bid + "/cancel", {})
        assert cancelled["counts"]["cancelled"] == 100000
        profiles = json.loads(
            (
                Path(__file__).resolve().parents[1] / "contracts/examples/comfy-animatediff.json"
            ).read_text()
        )
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps(profiles)
        mixed_spec = {
            **spec,
            "idempotency_key": "mixed-queue-capacity",
            "slots": [
                *spec["slots"][:-1],
                {
                    "choices": case.choices,
                    "generate": {
                        "profile_id": "animatediff-local",
                        "prompt": "A moving close-up",
                        "duration": 0.4,
                    },
                },
            ],
        }
        estimate = case.post(case.path + "/estimate", mixed_spec)
        expected = estimate["slot_calls"][-1]
        assert 0 < expected < 100000
        before_mixed = time.monotonic()
        mixed = case.post(case.path, {**mixed_spec, "max_generation_calls": expected})
        mixed_seconds = time.monotonic() - before_mixed
        with Store(case.store.path).connection() as db:
            mixed_counts = dict(
                db.execute(
                    "SELECT needs_generation,count(*) FROM production_items WHERE batch=? GROUP BY needs_generation",
                    (mixed["id"],),
                )
            )
        assert mixed_counts == {0: 100000 - expected, 1: expected}
        assert mixed["generation_limit"] == expected
        assert case.get(mixed["id"], "?after=99998&limit=2")["items"][-1]["ordinal"] == 100000
        assert (
            case.post(case.path + "/" + mixed["id"] + "/cancel", {})["counts"]["cancelled"]
            == 100000
        )
        return {
            "mixed_slot": {
                "requested": 100000,
                "camera_only_items": mixed_counts[0],
                "model_items": mixed_counts[1],
                "estimated_model_calls": expected,
                "enqueue_seconds": round(mixed_seconds, 3),
                "inference_submissions": 0,
                "renders": 0,
            },
            "requested": 100000,
            "persisted_rows": durable,
            "distinct_generated_cut_combinations": len(combinations),
            "enqueue_seconds": round(enqueue_seconds, 3),
            "last_visible_ordinal": 100000,
            "idempotent_replay": True,
            "rows_cancelled": cancelled["counts"]["cancelled"],
            "scope": "Single-machine admission, durable queue, paging, combination enumeration and controls. Workers disabled. Zero rendered media files in this specific test.",
        }
    finally:
        case.tearDown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    report = parser.parse_args().report
    result = measure()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result))
