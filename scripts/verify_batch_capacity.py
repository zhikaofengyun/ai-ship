"""Cold local capacity measurement with real FFmpeg media and durable per-item results."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.auth import LOCAL_ACTOR
from app.main import app
from app.media import import_saved_file
from app.store import get_store


def measure(client, store, project, asset, count, output):
    choices = [
        {
            "asset_id": asset["id"],
            "version": asset["version"],
            "source_in": i / 10,
            "source_out": (i + 2) / 10,
        }
        for i in range(32)
    ]
    spec = {
        "project_id": project,
        "name": "Capacity " + output,
        "idempotency_key": "capacity-" + output,
        "slots": [{"choices": choices} for _ in range(3)],
        "count": count,
        "output": output,
        "width": 160,
        "height": 90,
        "fps": 25,
        "seed": 20261125,
    }
    base = "/api/v1/production/batches"
    start = time.monotonic()
    response = client.post(base, json=spec)
    response.raise_for_status()
    batch = response.json()["data"]
    enqueue = time.monotonic() - start
    prior = None
    progress_at = start
    while True:
        response = client.get(base + "/" + batch["id"], params={"limit": 1})
        response.raise_for_status()
        batch = response.json()["data"]
        if batch["counts"] != prior:
            progress_at = time.monotonic()
            prior = batch["counts"]
        if batch["counts"]["failed"] or batch["status"] in {
            "cancelled",
            "completed",
            "completed_with_errors",
        }:
            break
        if time.monotonic() - progress_at > 90:
            raise RuntimeError("No progress in 90 seconds: " + repr(batch))
        time.sleep(0.5)
    elapsed = time.monotonic() - start
    if batch["status"] != "completed" or batch["counts"]["succeeded"] != count:
        raise RuntimeError(
            repr(
                client.get(base + "/" + batch["id"], params={"state": "failed", "limit": 2}).json()
            )
        )
    with store.connection() as db:
        counts = db.execute(
            "SELECT COUNT(DISTINCT timeline_id),COUNT(DISTINCT artifact_id) FROM production_items WHERE batch=?",
            (batch["id"],),
        ).fetchone()
    assert counts[0] == count
    decoded = 0
    hashes = set()
    if output == "render":
        assert counts[1] == count
        after = 0
        while True:
            page = client.get(
                base + "/" + batch["id"], params={"limit": 100, "after": after}
            ).json()["data"]
            for item in page["items"]:
                media = client.get("/api/v1/media/artifacts/" + item["artifact_id"] + "/file")
                media.raise_for_status()
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        "-i",
                        "pipe:0",
                        "-f",
                        "null",
                        "-",
                    ],
                    input=media.content,
                    check=True,
                    timeout=30,
                )
                decoded += 1
                hashes.add(hashlib.sha256(media.content).hexdigest())
            after = page["next_after"]
            if after is None:
                break
        assert decoded == count
    return {
        "requested": count,
        "completed": count,
        "enqueue_seconds": round(enqueue, 3),
        "time_to_completion_seconds": round(elapsed, 3),
        "distinct_timelines": counts[0],
        "distinct_artifact_records": counts[1],
        "downloaded_and_decoded": decoded,
        "unique_download_sha256": len(hashes),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drafts", type=int, default=10000)
    parser.add_argument("--renders", type=int, default=120)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if any(v < 0 or v > 32768 for v in (args.drafts, args.renders)) or not 1 <= args.workers <= 16:
        parser.error("counts must fit the 32³ distinct combinations and workers must be 1–16")
    report = {
        "workers": args.workers,
        "media": "Synthetic FFmpeg testsrc2; 3 x 0.20 s per output, 160 x 90, 25 FPS. Single-machine SQLite; no provider or platform traffic.",
        "runs": {},
    }
    with tempfile.TemporaryDirectory(prefix="ai-ship-capacity-") as directory:
        os.environ.update(
            AI_SHIP_MODE="local",
            AI_SHIP_DB=directory + "/db.sqlite3",
            AI_SHIP_DATA_DIR=directory,
            AI_SHIP_BATCH_WORKERS=str(args.workers),
        )
        fixture = Path(directory) / "testsrc.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=25:duration=4",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(fixture),
            ],
            check=True,
        )
        with TestClient(app) as client:
            store = get_store()
            pid = store.create(
                "Project", {"name": "Local capacity verification"}, tenant_id="local"
            )["id"]
            asset = import_saved_file(
                store, LOCAL_ACTOR, pid, fixture.name, fixture, "Synthetic capacity fixture"
            )["asset"]
            for kind, n in (("render", args.renders), ("timeline", args.drafts)):
                if n:
                    report["runs"][kind] = measure(client, store, pid, asset, n, kind)
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(report, indent=2) + "\n")
                    print(kind, report["runs"][kind], flush=True)


if __name__ == "__main__":
    main()
