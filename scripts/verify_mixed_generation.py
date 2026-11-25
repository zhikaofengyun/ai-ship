"""Run the mixed camera/diffusion recipe against a configured local ComfyUI worker."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import batches
from tests.test_production import ProductionBase


def inspect_video(path):
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)]
    )
    stream = next(s for s in json.loads(raw)["streams"] if s["codec_type"] == "video")
    hashes = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
        text=True,
    )
    frames = [row.split(",")[-1].strip() for row in hashes.splitlines() if not row.startswith("#")]
    assert len(frames) == int(stream["nb_read_frames"]) and len(set(frames)) > 1
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "video_stream": {
            k: stream[k] for k in ("codec_name", "width", "height", "r_frame_rate", "time_base")
        },
        "decoded_frames": len(frames),
        "distinct_decoded_frames": len(set(frames)),
    }


def run(base, output, *, tiny_vae=False):
    output.mkdir(parents=True, exist_ok=False)
    footage = ROOT / "evidence/batch-production/real-footage/coffee-excerpt.mp4"
    credit = json.loads(footage.with_name("coffee-evidence.json").read_bytes())
    source_hash = hashlib.sha256(footage.read_bytes()).hexdigest()
    assert source_hash == credit["attributed_download_sha256"]
    report = {"source": credit["source"], "source_sha256": source_hash, "items": []}
    cfg = json.loads((ROOT / "contracts/examples/comfy-animatediff.json").read_bytes())[
        "animatediff-reference"
    ]
    cfg["base_url"] = base
    # Keep the CPU smoke test bounded while running the full trained model and motion module.
    cfg["workflow"]["10"]["inputs"].update(width=160, height=160)
    cfg["workflow"]["12"]["inputs"]["amount"] = 4
    if tiny_vae:
        cfg["workflow"]["13"] = {"class_type": "VAELoader", "inputs": {"vae_name": "taesd"}}
        for node in ("7", "11"):
            cfg["workflow"][node]["inputs"]["vae"] = ["13", 0]
    with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
        stats = client.get("/system_stats")
        stats.raise_for_status()
        report["worker"] = {
            k: v
            for k, v in stats.json()["system"].items()
            if k in {"comfyui_version", "pytorch_version", "python_version", "os"}
        }
        for node in ("ADE_AnimateDiffLoaderGen1", "SaveWEBM", "CheckpointLoaderSimple"):
            assert node in client.get("/object_info/" + node).json(), node
    fixture = ProductionBase()
    fixture.setUp()
    try:
        os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps({"camera-diffusion": cfg})
        api = fixture.client
        imported = api.post(
            "/api/v1/media/import",
            params={"project_id": fixture.pid, "filename": "coffee-camera.mp4"},
            content=footage.read_bytes(),
        )
        imported.raise_for_status()
        asset = imported.json()["data"]["asset"]
        spec = {
            "project_id": fixture.pid,
            "name": "Keria T · coffee roasting · CC BY-SA 4.0",
            "idempotency_key": uuid4().hex,
            "count": 2,
            "seed": 48185,
            "output": "render",
            "width": 256,
            "height": 256,
            "fps": 24,
            "generation_window": 1,
            "max_generation_calls": 1,
            "slots": [
                {
                    "choices": [
                        {
                            "asset_id": asset["id"],
                            "version": asset["version"],
                            "source_in": 1,
                            "source_out": 1.5,
                        }
                    ],
                    "generate": {
                        "profile_id": "camera-diffusion",
                        "prompt": "Coffee beans roasting in a pan. Preserve the original roasting scene and create gentle natural movement.",
                        "duration": 0.5,
                        "reference_asset_id": asset["id"],
                        "reference_asset_version": asset["version"],
                        "reference_time_seconds": 1.239,
                    },
                }
            ],
        }
        path = "/production/batches"
        report["estimate"] = fixture.post(path + "/estimate", spec)
        assert report["estimate"] == {"generation_calls": 1, "workflow_calls": 0, "slot_calls": [1]}
        initial = fixture.post(path, spec)
        assert fixture.post(path, spec)["id"] == initial["id"]
        start = time.monotonic()
        observed = []
        while time.monotonic() - start < 900:
            worked = batches.work_once(fixture.store)
            current = api.get("/api/v1" + path + "/" + initial["id"]).json()["data"]
            states = [item["status"] for item in current["items"]]
            if not observed or observed[-1]["states"] != states:
                observed.append({"seconds": round(time.monotonic() - start, 3), "states": states})
                print(observed[-1], flush=True)
            if current["counts"]["failed"]:
                errors = [
                    (
                        item["error"],
                        (
                            fixture.store.get(item["generation_id"], "local")["data"]["issue"]
                            if item["generation_id"]
                            else None
                        ),
                    )
                    for item in current["items"]
                    if item["status"] == "failed"
                ]
                raise RuntimeError(f"Batch failed: {errors}")
            if current["status"] == "completed":
                break
            if not worked:
                time.sleep(0.5)
        assert current["counts"]["succeeded"] == 2, current
        report.update(
            batch_id=initial["id"],
            states=observed,
            elapsed_seconds=round(time.monotonic() - start, 3),
            profile=cfg,
        )
        assert sum(len(x["generation_jobs"]) for x in current["items"]) == 1
        for item in current["items"]:
            route = "generated" if item["generation_jobs"] else "camera"
            encoded = api.get("/api/v1/media/artifacts/" + item["artifact_id"] + "/file")
            encoded.raise_for_status()
            path_out = output / (route + ".mp4")
            path_out.write_bytes(encoded.content)
            summary = {
                "ordinal": item["ordinal"],
                "route": route,
                "artifact_id": item["artifact_id"],
                "timeline_id": item["timeline_id"],
                **inspect_video(path_out),
            }
            if item["generation_jobs"]:
                job = fixture.store.get(item["generation_id"], "local")
                proof = job["data"]["reference_input"]
                assert (
                    proof["source_time_seconds"] == 1.239
                    and proof["original_sha256"] == source_hash
                )
                assert job["data"]["reference_upload"]["verified_sha256"] == proof["png_sha256"]
                assert proof["asset_ref"] == {
                    "id": asset["id"],
                    "version": asset["version"],
                    "kind": asset["kind"],
                }
                generated = api.get(
                    "/api/v1/media/" + item["generation_jobs"][0]["asset_id"] + "/file"
                )
                generated.raise_for_status()
                raw = output / "model-output.webm"
                raw.write_bytes(generated.content)
                summary.update(
                    generation_id=job["id"],
                    seed=job["data"]["seed"],
                    reference_input=proof,
                    uploaded_reference_sha256=job["data"]["reference_upload"]["verified_sha256"],
                    model_output=inspect_video(raw),
                )
                assert summary["model_output"]["decoded_frames"] == 4
            report["items"].append(summary)
        assert len({item["sha256"] for item in report["items"]}) == 2
    finally:
        fixture.tearDown()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--tiny-vae",
        action="store_true",
        help="Use locally installed TAESD encoder/decoder weights",
    )
    args = parser.parse_args()
    run(args.base_url, args.output, tiny_vae=args.tiny_vae)
