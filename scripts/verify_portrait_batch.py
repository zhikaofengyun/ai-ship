"""Measure a three-output portrait job through the live local HTTP API and verify its downloads."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, FPS = 1080, 1920, 25
WORKERS = 2
ITEM_COUNT = 3


def command(*args, timeout=180):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def fixture(directory):
    path = directory / "portrait-source.mp4"
    command(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1080x1920:rate=25:duration=15",
        "-f",
        "lavfi",
        "-i",
        "aevalsrc=0.12*sin(2*PI*(320*t+4*t*t)):s=48000:d=15",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(path),
    )
    return path


@contextmanager
def server(directory):
    # Inherit an already-bound socket so another local process cannot claim its port.
    env = {
        **os.environ,
        "AI_SHIP_MODE": "local",
        "AI_SHIP_DB": str(directory / "db.sqlite3"),
        "AI_SHIP_DATA_DIR": str(directory),
        "AI_SHIP_BATCH_WORKERS": str(WORKERS),
    }
    with socket.socket() as sock, (directory / "server.log").open("w") as log:
        sock.bind(("127.0.0.1", 0))
        sock.listen(32)
        port = sock.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--fd",
                str(sock.fileno()),
                "--log-level",
                "warning",
            ],
            env=env,
            cwd=ROOT,
            pass_fds=(sock.fileno(),),
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}/api/v1", timeout=45, trust_env=False
            ) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError((directory / "server.log").read_text())
                    try:
                        if client.get("health").is_success:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.15)
                else:
                    raise TimeoutError("Server startup timed out")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def api(response):
    response.raise_for_status()
    return response.json()["data"]


def verify_download(client, row, directory):
    artifact = api(client.get("records/" + row["artifact_id"]))
    response = client.get(
        "media/artifacts/" + row["artifact_id"] + "/file", params={"download": "true"}
    )
    response.raise_for_status()
    path = directory / f"output-{row['ordinal']}.mp4"
    path.write_bytes(response.content)
    sha = hashlib.sha256(response.content).hexdigest()
    if sha != artifact["data"]["content_hash"]:
        raise RuntimeError("Downloaded bytes do not match the stored artifact digest")
    streams = json.loads(
        command(
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
        ).stdout
    )
    video = next(s for s in streams["streams"] if s["codec_type"] == "video")
    audio = next(s for s in streams["streams"] if s["codec_type"] == "audio")
    if (
        video["codec_name"],
        video["width"],
        video["height"],
        video["avg_frame_rate"],
        audio["codec_name"],
    ) != ("h264", WIDTH, HEIGHT, "25/1", "aac"):
        raise RuntimeError("Unexpected streams: " + str(streams))
    duration = float(streams["format"]["duration"])
    if not 15 <= duration <= 15.1:
        raise RuntimeError("Unexpected muxed duration: " + str(duration))
    decoded = command(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "2",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "rawvideo",
        "-threads:v",
        "1",
        "-c:a",
        "pcm_s16le",
        "-f",
        "framehash",
        "-hash",
        "sha256",
        "-",
    ).stdout
    frames = {0: [], 1: []}
    for line in decoded.splitlines():
        if line and not line.startswith("#"):
            fields = line.split(",")
            frames[int(fields[0])].append(fields[-1].strip())
    if len(frames[0]) != FPS * 15 or not frames[1]:
        raise RuntimeError(f"Decoded video frames={len(frames[0])}, audio frames={len(frames[1])}")
    volume = command(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        str(path),
        "-vn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ).stderr
    mean = float(re.search(r"mean_volume: ([-.0-9]+) dB", volume).group(1))
    if mean < -60:
        raise RuntimeError("Audio was silent")
    return {
        "ordinal": row["ordinal"],
        "artifact_id": row["artifact_id"],
        "timeline_id": row["timeline_id"],
        "bytes": path.stat().st_size,
        "download_sha256": sha,
        "decoded_video_frame_sha256": hashlib.sha256("\n".join(frames[0]).encode()).hexdigest(),
        "decoded_audio_frame_sha256": hashlib.sha256("\n".join(frames[1]).encode()).hexdigest(),
        "video_frames_decoded": len(frames[0]),
        "audio_frames_decoded": len(frames[1]),
        "width": video["width"],
        "height": video["height"],
        "fps": video["avg_frame_rate"],
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "audio_hz": int(audio["sample_rate"]),
        "mean_audio_dbfs": mean,
        "duration_seconds": duration,
    }


def run(directory):
    source = fixture(directory)
    with server(directory) as client:
        project = api(
            client.post("projects", json={"name": "Portrait batch benchmark", "duration": 15})
        )
        asset = api(
            client.post(
                "media/import",
                params={"project_id": project["id"], "filename": source.name},
                content=source.read_bytes(),
                headers={"content-type": "video/mp4"},
            )
        )["asset"]
        choices = [
            {
                "asset_id": asset["id"],
                "version": asset["version"],
                "source_in": i,
                "source_out": i + 5,
            }
            for i in (0, 5, 10)
        ]
        specification = {
            "project_id": project["id"],
            "name": "Three vertical 15-second edits",
            "idempotency_key": "portrait-capacity",
            "slots": [{"choices": choices}, {"choices": [choices[1]]}, {"choices": [choices[2]]}],
            "count": ITEM_COUNT,
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "output": "render",
            "seed": 20261125,
        }
        start = time.monotonic()
        batch = api(client.post("production/batches", json=specification))
        enqueued = time.monotonic() - start
        while time.monotonic() - start < 600:
            batch = api(
                client.get("production/batches/" + batch["id"], params={"limit": ITEM_COUNT})
            )
            if batch["status"] in {"completed", "completed_with_errors", "cancelled"}:
                break
            time.sleep(0.5)
        elapsed = time.monotonic() - start
        if batch["counts"]["succeeded"] != ITEM_COUNT or batch["status"] != "completed":
            raise RuntimeError(json.dumps(batch, ensure_ascii=False))
        items = [verify_download(client, row, directory) for row in batch["items"]]
        for field in (
            "timeline_id",
            "artifact_id",
            "download_sha256",
            "decoded_video_frame_sha256",
            "decoded_audio_frame_sha256",
        ):
            if len({item[field] for item in items}) != ITEM_COUNT:
                raise RuntimeError("Duplicate output " + field)
        cpuinfo = Path("/proc/cpuinfo").read_text()
        return {
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "execution": "Isolated SQLite and real Uvicorn loopback HTTP with automatic background workers",
            "fixture": "Synthetic full-size FFmpeg testsrc2 with a frequency-swept sine tone; three five-second cuts per output. No external provider or platform call.",
            "scope": "One local run with three 15-second outputs. Timings describe only this fixture and host.",
            "environment": {
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "kernel": platform.release(),
                "cpu": re.search(r"model name\s*:\s*(.+)", cpuinfo).group(1),
                "logical_cpus_in_affinity": len(os.sched_getaffinity(0)),
                "ffmpeg": command("ffmpeg", "-version").stdout.splitlines()[0],
                "workers": WORKERS,
            },
            "batch_id": batch["id"],
            "source_sha256": asset["data"]["content_hash"],
            "requested": ITEM_COUNT,
            "succeeded": len(items),
            "enqueue_seconds": round(enqueued, 3),
            "time_to_completion_seconds": round(elapsed, 3),
            "time_to_completion_includes": "HTTP enqueue, background planning, rendering, built-in QC, and terminal-state poll; excludes fixture creation, upload and independent download verification.",
            "unique_downloads": len(items),
            "download_verification_seconds": round(time.monotonic() - start - elapsed, 3),
            "items": items,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=ROOT / "evidence/batch-production/portrait-capacity.json"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ai-ship-portrait-") as temp:
        report = run(Path(temp))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Verified {report['succeeded']} portrait outputs; {report['time_to_completion_seconds']} s",
        flush=True,
    )


if __name__ == "__main__":
    main()
