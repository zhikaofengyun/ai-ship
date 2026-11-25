"""Replay a real pinned temporal input through ComfyUI and audit every input frame pixel."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from verify_mixed_generation import ROOT, inspect_video
from tests.test_production import ProductionBase

SOURCE = ROOT / "evidence/batch-production/real-footage/coffee-excerpt.mp4"
PTS = [1.0, 1.12, 1.24, 1.36]


def decode_rgb(path, filters):
    return subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            str(path),
            "-filter_threads",
            "1",
            "-vf",
            filters,
            "-fps_mode",
            "vfr",
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
    )


def audit(folder):
    report = json.loads((folder / "inference.json").read_bytes())
    proof = report["evidence"]
    uploaded = folder / "reference.mp4"
    output = folder / "candidate.webm"
    assert proof["source_frame_pts_seconds"] == PTS
    assert (
        proof["segment_sha256"]
        == report["verified_sha256"]
        == hashlib.sha256(uploaded.read_bytes()).hexdigest()
    )
    assert proof["original_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert (
        inspect_video(uploaded) == report["reference"]
        and inspect_video(output) == report["candidate"]
    )
    source_rgb = decode_rgb(
        SOURCE, "select='eq(n,25)+eq(n,28)+eq(n,31)+eq(n,34)',format=rgba,scale=288:512,setsar=1"
    )
    sent_rgb = decode_rgb(uploaded, "null")
    assert len(sent_rgb) == 4 * 288 * 512 * 3 and source_rgb == sent_rgb
    frame_size = 288 * 512 * 3
    hashes = [
        hashlib.sha256(sent_rgb[i : i + frame_size]).hexdigest()
        for i in range(0, len(sent_rgb), frame_size)
    ]
    assert len(set(hashes)) == 4
    return {
        "all_decoded_source_pixels_equal": True,
        "source_indices": [25, 28, 31, 34],
        "source_pts_seconds": PTS,
        "decoded_rgb_sha256_by_frame": hashes,
        "number_of_compared_rgb_channels": len(sent_rgb),
    }


def run(url, folder, tiny):
    folder.mkdir(parents=True, exist_ok=False)
    profiles = json.loads((ROOT / "contracts/examples/comfy-video-segment.json").read_bytes())
    config = profiles["animatediff-video-segment"]
    config["base_url"] = url
    if tiny:
        config["workflow"]["13"] = {"class_type": "VAELoader", "inputs": {"vae_name": "taesd"}}
        for node in ("7", "11"):
            config["workflow"][node]["inputs"]["vae"] = ["13", 0]
    fixture = ProductionBase()
    fixture.setUp()
    try:
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps(profiles)
        imported = fixture.client.post(
            "/api/v1/media/import",
            params={"project_id": fixture.pid, "filename": "coffee-camera.mp4"},
            content=SOURCE.read_bytes(),
        )
        imported.raise_for_status()
        asset = imported.json()["data"]["asset"]
        body = {
            "project_id": fixture.pid,
            "profile_id": "animatediff-video-segment",
            "prompt": "The existing coffee ceremony table with tan clay cups and a dark dish on white cloth, patterned backdrop. Preserve the arrangement, natural colours and outlines. Slight natural camera movement.",
            "seed": 48185,
            "idempotency_key": uuid4().hex,
            "reference_asset_id": asset["id"],
            "reference_asset_version": asset["version"],
            "reference_time_seconds": 1.0,
            "reference_end_seconds": 1.5,
        }
        api = "/production/generation"
        start = time.monotonic()
        job = fixture.post(api, body)
        jid = job["job"]["id"]
        assert fixture.post(api, body)["job"]["id"] == jid
        events = []
        while time.monotonic() - start < 600:
            stage = job["job"]["data"]["stage"]
            if not events or stage != events[-1]["stage"]:
                events.append({"stage": stage, "seconds": round(time.monotonic() - start, 3)})
            if stage in {"ready", "failed", "outcome_unknown", "cancelled"}:
                break
            time.sleep(2)
            job = fixture.post(api + "/" + jid + "/refresh", {})
        details = job["job"]["data"]
        assert details["stage"] == "ready", details.get("issue")
        proof = details["reference_input"]
        report = {
            "profile": config,
            "evidence": proof,
            "verified_sha256": details["reference_upload"]["verified_sha256"],
            "provider_id": details["remote_id"],
            "events": events,
            "input": body,
        }
        for key, record, suffix in (
            ("reference", proof["segment_ref"], ".mp4"),
            ("candidate", job["asset"], ".webm"),
        ):
            response = fixture.client.get("/api/v1/media/" + record["id"] + "/file")
            response.raise_for_status()
            path = folder / (key + suffix)
            path.write_bytes(response.content)
            report[key] = inspect_video(path)
            assert report[key]["decoded_frames"] == 4
        (folder / "inference.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base-url", help="Registered local ComfyUI URL for a fresh run")
    mode.add_argument("--audit", action="store_true", help="Audit a saved run without a model")
    cli.add_argument("--folder", type=Path, required=True)
    cli.add_argument("--tiny-vae", action="store_true")
    args = cli.parse_args()
    if args.base_url:
        run(args.base_url, args.folder, args.tiny_vae)
    print(json.dumps(audit(args.folder), indent=2))
