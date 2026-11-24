"""Run a real registered ComfyUI video model on CPU and follow its artifact into an approved edit."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_production import ProductionBase


def run(args):
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    report = {
        "passed": False,
        "checks": [],
        "profile_file": "contracts/examples/comfy-animatediff.json",
        "events": [],
    }
    fixture = ProductionBase()
    with tempfile.TemporaryDirectory() as td, (folder / "worker.log").open("w") as log:
        tmp = Path(td)
        precision = os.environ.get("AI_SHIP_COMFY_CPU_DTYPE", "fp16")
        flags = {
            "fp16": ["--force-fp16", "--fp16-unet"],
            "bf16": ["--bf16-unet", "--bf16-text-enc", "--force-channels-last"],
        }[precision]
        report["cpu_dtype"] = precision
        cmd = [
            str(args.python.absolute()),
            str(args.main.resolve()),
            "--listen",
            "127.0.0.1",
            "--port",
            str(port),
            "--cpu",
            "--cpu-vae",
            *flags,
            "--preview-method",
            "none",
            "--output-directory",
            str(tmp / "output"),
            "--user-directory",
            str(tmp / "user"),
            "--disable-auto-launch",
        ]
        (tmp / "user").mkdir()
        env = {**os.environ, "OMP_NUM_THREADS": "3", "MKL_NUM_THREADS": "3", "HF_HUB_OFFLINE": "1"}
        worker = subprocess.Popen(
            cmd, cwd=args.main.resolve().parent, env=env, stderr=log, stdout=log
        )
        try:
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=5) as inspect:
                for _ in range(250):
                    if worker.poll() is not None:
                        raise RuntimeError("ComfyUI worker exited")
                    try:
                        if inspect.get(base + "/system_stats").is_success:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.4)
                else:
                    raise RuntimeError("ComfyUI not ready")
                report["worker"] = inspect.get(base + "/system_stats").json().get("system", {})
                for node in ("CheckpointLoaderSimple", "ADE_AnimateDiffLoaderGen1", "SaveWEBM"):
                    assert node in inspect.get(base + "/object_info/" + node).json(), node
            config = json.loads((ROOT / report["profile_file"]).read_text())
            for profile in config.values():
                profile["base_url"] = base
            fixture.setUp()
            os.environ["AI_SHIP_COMFY_PROFILES"] = json.dumps(config)
            api = fixture.client
            root = "/api/v1/production/generation"
            prompt = "A close-up film shot of a blue ceramic coffee cup on a light wooden table, morning light, gentle steam rising, a slow camera push forward, realistic studio photography."
            body = {
                "project_id": fixture.pid,
                "profile_id": "animatediff-local",
                "slot": "咖啡杯产品镜头",
                "prompt": prompt,
                "seed": args.seed,
                "idempotency_key": uuid4().hex,
            }

            def post(path, b=None):
                resp = api.post(path, json=b) if b is not None else api.post(path)
                resp.raise_for_status()
                return resp.json()["data"]

            still_request = {
                **body,
                "profile_id": "sd15-still",
                "slot": "商品静帧候选",
                "idempotency_key": uuid4().hex,
            }

            def complete(request, name):
                job = post(root, request)
                jid = job["job"]["id"]
                start = time.monotonic()
                observed = []
                while time.monotonic() - start < 840:
                    state = job["job"]["data"]["stage"]
                    if not observed or observed[-1]["stage"] != state:
                        observed.append(
                            {"stage": state, "seconds": round(time.monotonic() - start, 2)}
                        )
                    if state in {"ready", "failed", "cancelled"} or worker.poll() is not None:
                        break
                    time.sleep(2)
                    job = post(f"{root}/{jid}/refresh")
                assert job["asset"], job["job"]["data"].get("issue") or state
                assert post(root, request)["job"]["id"] == jid
                report.setdefault("task_events", {})[name] = observed
                return job

            still = complete(still_request, "text-to-image")
            still_id = still["job"]["id"]
            img = api.get("/api/v1/media/" + still["asset"]["id"] + "/file")
            img.raise_for_status()
            assert img.content.startswith(bytes.fromhex("89504e470d0a1a0a"))
            (folder / "generated-still.png").write_bytes(img.content)
            held = post(f"{root}/{still_id}/hold", {"duration": 1, "fps": 24})
            assert (
                held["asset"]["id"] != still["asset"]["id"]
                and held["job"]["data"]["hold_params"]["frames"] == 24
            )
            report["generated_still"] = {
                "sha256": hashlib.sha256(img.content).hexdigest(),
                "job": still["job"]["data"],
                "hold": held["job"]["data"]["hold_params"],
            }
            report["checks"].append(
                "SD1.5 text-to-image inference / PNG original / human-specified hold into a separate 24-frame asset"
            )

            def save_video(state, filename):
                response = api.get("/api/v1/media/" + state["asset"]["id"] + "/file")
                response.raise_for_status()
                origin = folder / filename
                origin.write_bytes(response.content)
                hashes = subprocess.check_output(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(origin),
                        "-map",
                        "0:v",
                        "-f",
                        "framemd5",
                        "-",
                    ],
                    text=True,
                )
                rows = [line for line in hashes.splitlines() if line and not line.startswith("#")]
                assert len(rows) == 8 and len({row.split(",")[-1] for row in rows}) > 1
                origin.with_suffix(".framemd5").write_text(hashes)
                return hashlib.sha256(response.content).hexdigest()

            plain = complete(body, "text-to-video")
            report["job"] = plain["job"]["data"]
            report["source_sha256"] = save_video(plain, "generated-shot.webm")
            report["checks"].append(
                "actual text-to-video graph, deduplicated task and eight decoded, non-identical frames"
            )

            ref_request = {
                **body,
                "idempotency_key": uuid4().hex,
                "profile_id": "animatediff-reference",
                "reference_asset_id": still["asset"]["id"],
                "reference_asset_version": still["asset"]["version"],
            }
            conditioned = complete(ref_request, "image-to-video")
            data = conditioned["job"]["data"]
            assert data["reference_asset_ref"]["id"] == still["asset"]["id"]
            assert data["reference_asset_ref"]["version"] == still["asset"]["version"]
            report["conditioned_job"] = data
            report["conditioned_sha256"] = save_video(conditioned, "reference-motion.webm")

            def pixels(path, sec):
                return subprocess.check_output(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        str(sec),
                        "-i",
                        str(path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=64:64",
                        "-pix_fmt",
                        "rgb24",
                        "-f",
                        "rawvideo",
                        "-",
                    ]
                )

            original = pixels(folder / "generated-still.png", 0)
            assert len(original) == 64 * 64 * 3
            report["reference_frame_comparison"] = {}
            for name in ("generated-shot.webm", "reference-motion.webm"):
                samples = {}
                for sec in (0, 0.5, 0.875):
                    frame = pixels(folder / name, sec)
                    assert len(frame) == len(original)
                    samples[str(sec)] = round(
                        sum(abs(a - b) for a, b in zip(original, frame)) / len(frame), 3
                    )
                report["reference_frame_comparison"][name] = samples
            report["reference_frame_measurement"] = (
                "RGB24 average absolute difference at 64x64; color similarity alone cannot certify object identity"
            )
            asset = conditioned["asset"]
            report["checks"].append(
                "actual reference image, pinned project revision, uploaded before inference, eight distinct-time decoded video frames, source retained"
            )

            edits = "/api/v1/production/auto-edits"
            plan = post(
                edits,
                {
                    "project_id": fixture.pid,
                    "asset_ids": [asset["id"]],
                    "goal": "使用生成的杯子镜头制作一秒实物候选",
                    "mode": "local_vision",
                    "target_seconds": 1,
                    "width": 384,
                    "height": 384,
                    "fps": 24,
                    "idempotency_key": uuid4().hex,
                },
            )
            sid = plan["session"]["id"]
            assert plan["plan"], plan["session"]["data"].get("issue")
            cut = post(
                edits + "/" + sid + "/approve", {"expected_revision": plan["session"]["version"]}
            )
            done = post(
                edits + "/" + sid + "/render",
                {
                    "expected_revision": cut["session"]["version"],
                    "timeline_revision": cut["timeline"]["version"],
                },
            )
            artifact = done["artifact"]
            assert artifact
            resp = api.get("/api/v1/media/artifacts/" + artifact["id"] + "/file")
            resp.raise_for_status()
            output = folder / "generated-cut.mp4"
            output.write_bytes(resp.content)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-map",
                    "0:v",
                    "-map",
                    "0:a",
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
            )
            report["checks"].append(
                "generated source → versioned rough-cut → approval → full audio/video decode"
            )
            report["render_sha256"] = hashlib.sha256(resp.content).hexdigest()
            report["passed"] = True
        finally:
            if hasattr(fixture, "temp"):
                fixture.tearDown()
            worker.terminate()
            try:
                worker.wait(8)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
            (folder / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--main",
        type=Path,
        default=os.getenv("AI_SHIP_COMFY_MAIN"),
        required=not os.getenv("AI_SHIP_COMFY_MAIN"),
    )
    p.add_argument(
        "--python",
        type=Path,
        default=os.getenv("AI_SHIP_COMFY_PYTHON"),
        required=not os.getenv("AI_SHIP_COMFY_PYTHON"),
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=48171)
    run(p.parse_args())
