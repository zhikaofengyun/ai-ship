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
        "frames_sha256": hashlib.sha256("\n".join(frames).encode()).hexdigest(),
        "distinct_decoded_frames": len(set(frames)),
    }


def run(base, output, *, tiny_vae=False, directions=(), registered_profile=None):
    output.mkdir(parents=True, exist_ok=False)
    footage = ROOT / "evidence/batch-production/real-footage/coffee-excerpt.mp4"
    credit = json.loads(footage.with_name("coffee-evidence.json").read_bytes())
    source_hash = hashlib.sha256(footage.read_bytes()).hexdigest()
    assert source_hash == credit["attributed_download_sha256"]
    report = {"source": credit["source"], "source_sha256": source_hash, "items": []}
    if registered_profile is not None:
        cfg = registered_profile
    else:
        cfg = json.loads((ROOT / "contracts/examples/comfy-animatediff.json").read_bytes())[
            "animatediff-reference"
        ]
        # Keep the CPU smoke test bounded while running the full trained model and motion module.
        cfg["workflow"]["10"]["inputs"].update(width=160, height=160)
        cfg["workflow"]["12"]["inputs"]["amount"] = 4
        if directions:
            # Portrait source, two-step motion weights and mild denoising match the approved
            # recipe from the earlier visual comparison. Only direction and seed vary.
            cfg["workflow"]["10"]["inputs"].update(width=96, height=160)
            cfg["workflow"]["6"]["inputs"].update(steps=2, denoise=0.12)
        if tiny_vae:
            cfg["workflow"]["13"] = {"class_type": "VAELoader", "inputs": {"vae_name": "taesd"}}
            for node in ("7", "11"):
                cfg["workflow"][node]["inputs"]["vae"] = ["13", 0]
    cfg["base_url"] = base
    with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
        stats = client.get("/system_stats")
        stats.raise_for_status()
        report["worker"] = {
            k: v
            for k, v in stats.json()["system"].items()
            if k in {"comfyui_version", "pytorch_version", "python_version", "os"}
        }
        for node in {definition["class_type"] for definition in cfg["workflow"].values()}:
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
        expected_calls = len(directions) or 1
        reference = asset
        reference_hash = source_hash
        if directions:
            frame = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "1.239",
                    "-i",
                    str(footage),
                    "-frames:v",
                    "1",
                    "-c:v",
                    "png",
                    "-threads",
                    "1",
                    "-f",
                    "image2pipe",
                    "pipe:1",
                ]
            )
            (output / "reference.png").write_bytes(frame)
            still = api.post(
                "/api/v1/media/import",
                params={"project_id": fixture.pid, "filename": "coffee-table.png"},
                content=frame,
            )
            still.raise_for_status()
            reference = still.json()["data"]["asset"]
            reference_hash = hashlib.sha256(frame).hexdigest()
            slot = spec["slots"][0]["generate"]
            slot.update(
                prompt="Keep the cup, saucer, tablecloth and table from the reference photograph.",
                prompt_variants=list(directions),
                reference_asset_id=reference["id"],
                reference_asset_version=reference["version"],
            )
            slot.pop("reference_time_seconds")
            spec.update(
                count=1 + expected_calls, max_generation_calls=expected_calls, width=96, height=160
            )
        path = "/production/batches"
        report["estimate"] = fixture.post(path + "/estimate", spec)
        assert report["estimate"] == {
            "generation_calls": expected_calls,
            "workflow_calls": 0,
            "slot_calls": [expected_calls],
        }
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
        assert current["counts"]["succeeded"] == spec["count"], current
        report.update(
            batch_id=initial["id"],
            states=observed,
            elapsed_seconds=round(time.monotonic() - start, 3),
            profile=cfg,
        )
        assert sum(len(x["generation_jobs"]) for x in current["items"]) == expected_calls
        for item in current["items"]:
            route = "generated" if item["generation_jobs"] else "camera"
            encoded = api.get("/api/v1/media/artifacts/" + item["artifact_id"] + "/file")
            encoded.raise_for_status()
            name = f"{route}-{item['ordinal']}" if directions else route
            path_out = output / (name + ".mp4")
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
                assert proof["original_sha256"] == reference_hash
                if not directions:
                    assert proof["source_time_seconds"] == 1.239
                assert job["data"]["reference_upload"]["verified_sha256"] == proof["png_sha256"]
                assert proof["asset_ref"] == {
                    "id": reference["id"],
                    "version": reference["version"],
                    "kind": reference["kind"],
                }
                generated = api.get(
                    "/api/v1/media/" + item["generation_jobs"][0]["asset_id"] + "/file"
                )
                generated.raise_for_status()
                raw = output / (
                    f"model-output-{item['ordinal']}.webm" if directions else "model-output.webm"
                )
                raw.write_bytes(generated.content)
                summary.update(
                    generation_id=job["id"],
                    seed=job["data"]["seed"],
                    prompt=job["data"]["prompt"],
                    reference_input=proof,
                    uploaded_reference_sha256=job["data"]["reference_upload"]["verified_sha256"],
                    model_output=inspect_video(raw),
                )
                assert summary["model_output"]["decoded_frames"] == 4
            report["items"].append(summary)
        assert len({item["sha256"] for item in report["items"]}) == spec["count"]
        if directions:
            generated = [item for item in report["items"] if item["route"] == "generated"]
            assert {item["prompt"] for item in generated} == {
                slot["prompt"] + "\n" + d for d in directions
            }
            assert len({item["uploaded_reference_sha256"] for item in generated}) == 1
            assert (
                len({item["model_output"]["frames_sha256"] for item in generated}) == expected_calls
            )
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
    parser.add_argument(
        "--prompt-direction",
        action="append",
        default=[],
        help="Generate one clip per direction from the same project still, plus a camera cut.",
    )
    parser.add_argument(
        "--profile-file",
        type=Path,
        help="Registered single ComfyUI video profile for a four-frame, eight-fps smoke run",
    )
    args = parser.parse_args()
    custom = json.loads(args.profile_file.read_text()) if args.profile_file else None
    run(
        args.base_url,
        args.output,
        tiny_vae=args.tiny_vae,
        directions=args.prompt_direction,
        registered_profile=custom,
    )
