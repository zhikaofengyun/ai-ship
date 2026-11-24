"""One actual director invocation, grounded opening verification, explicit recovery and decoded export."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.local_director import local_director
from tests.test_auto_edits import AutoEditTests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--server",
        default=os.getenv("AI_SHIP_LLAMA_SERVER"),
        required=not os.getenv("AI_SHIP_LLAMA_SERVER"),
        type=Path,
    )
    p.add_argument(
        "--model",
        default=os.getenv("AI_SHIP_TEST_GGUF"),
        required=not os.getenv("AI_SHIP_TEST_GGUF"),
        type=Path,
    )
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": []}
    try:
        verify(args, out, report)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def verify(args, out, report):
    fixture = AutoEditTests()
    with ExitStack() as stack:
        fixture.setUp()
        stack.callback(fixture.tearDown)
        endpoint, alias = stack.enter_context(local_director(args.server, args.model))
        model = fixture.create(
            "ModelProfile", {"provider": endpoint, "model_id": alias, "structured_output": True}
        )
        body = {
            **fixture.body,
            "goal": "Make a short FRESH LIME product cut. Use only measured footage, with no added claims.",
            "opening_text": "FRESH LIME",
            "mode": "model",
            "target_seconds": 3.2,
            "model_profile_id": model["id"],
        }
        d = fixture.post("", body)
        base = d["session"]
        plan = d["plan"]["data"]
        c = plan["opening_check"]
        with args.model.open("rb") as f:
            weights = hashlib.file_digest(f, "sha256").hexdigest()
        report.update(
            {
                "model": args.model.name,
                "weights_sha256": weights,
                "sampling_seed": 42,
                "brief": body["goal"],
                "required_opening": body["opening_text"],
                "proposal": {
                    "id": d["plan"]["id"],
                    "stage": base["data"]["stage"],
                    "issue": base["data"]["issue"],
                    "selection": plan["model_selection"],
                    "shots": plan["shots"],
                    "opening_check": c,
                },
                "director_call_receipts": [
                    {
                        k: r["data"].get(k)
                        for k in (
                            "outcome",
                            "actual_provider_model",
                            "provider_request_id",
                            "usage",
                            "response",
                        )
                    }
                    for r in fixture.store.list("local", kind="ToolCall")
                ],
                "preference_retries": 0,
            }
        )
        assert len(report["director_call_receipts"]) == 1
        assert report["director_call_receipts"][0]["outcome"] == "succeeded"
        report["checks"].append(
            "One authenticated local GGUF director completed an actual model call; original order and rationale preserved."
        )
        shot = plan["shots"][0]
        original = fixture.client.get("/api/v1/media/" + shot["asset_id"] + "/file")
        original.raise_for_status()
        src = Path(fixture.temp.name) / "selected-source-for-proof.mp4"
        src.write_bytes(original.content)
        selected = out / "model-selected-first-frame.png"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(shot["source_in"]),
                "-i",
                str(src),
                "-frames:v",
                "1",
                str(selected),
            ],
            check=True,
        )
        selected_text = (
            subprocess.check_output(
                ["tesseract", str(selected), "stdout", "-l", "eng", "--psm", "7"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        assert selected_text.upper() in {"SAMPLE", "FRESH LIME"}
        assert (selected_text.upper() == "FRESH LIME") == (c["status"] == "matched")
        report["independent_selected_source"] = {
            "asset_id": shot["asset_id"],
            "source_in": shot["source_in"],
            "source_bytes_sha256": hashlib.sha256(original.content).hexdigest(),
            "frame_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
            "ocr": selected_text,
        }
        report["checks"].append(
            "Compared the selected interval's opening frame to a separate decode and OCR of downloaded original bytes."
        )
        if c["status"] == "matched":
            assert base["data"]["stage"] == "review"
            report["human_replacement"] = False
        else:
            assert c["status"] == "conflict" and base["data"]["stage"] == "blocked", c
            raw = fixture.client.post(
                fixture.base + "/" + base["id"] + "/approve",
                json={"expected_revision": base["version"]},
            )
            assert raw.status_code == 409
            candidate = c["alternatives"][0]
            d = fixture.post(
                "/" + base["id"] + "/replace-shot",
                {
                    "expected_revision": base["version"],
                    "shot_index": 0,
                    **{k: candidate[k] for k in ("asset_id", "source_in", "source_out")},
                    "expected_asset_version": candidate["asset_version"],
                    "reason": "验收者明确采用实帧读到 FRESH LIME 的首镜候选。",
                },
            )
            assert d["plan"]["data"]["opening_check"]["status"] == "matched"
            assert (
                fixture.client.get(fixture.base + "/" + base["id"]).json()["data"]["session"]
                == base
            )
            report["human_replacement"] = {
                "child_plan_id": d["plan"]["id"],
                "parent_approve_http": raw.status_code,
                "asset_version": candidate["asset_version"],
                "parent_unchanged": True,
            }
        rid = d["session"]["id"]
        edit = fixture.post("/" + rid + "/approve", {"expected_revision": d["session"]["version"]})
        movie = fixture.post(
            "/" + rid + "/render",
            {
                "expected_revision": edit["session"]["version"],
                "timeline_revision": edit["timeline"]["version"],
            },
        )
        assert movie["session"]["data"]["stage"] == "rendered", movie["session"]["data"]["issue"]
        result = fixture.client.get(
            "/api/v1/media/artifacts/" + movie["artifact"]["id"] + "/file?download=true"
        )
        result.raise_for_status()
        mp4 = out / "opening-export.mp4"
        mp4.write_bytes(result.content)
        frame = out / "encoded-opening.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-frames:v", "1", str(frame)],
            check=True,
        )
        # The source is 640:240 in a black-letterboxed 320:180 output.
        # Independently read the actual first frame's source-picture rectangle.
        roi = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(frame),
                "-vf",
                "crop=320:120:0:30",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ]
        )
        observed = (
            subprocess.check_output(
                ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "7"],
                input=roi,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        assert observed.upper() == "FRESH LIME", observed
        report["download"] = {
            "sha256": hashlib.sha256(result.content).hexdigest(),
            "bytes": len(result.content),
            "encoded_frame_sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
            "frame_index": 0,
            "ocr_roi_xywh": [0, 30, 320, 120],
            "independent_tesseract": observed,
            "qc": movie["artifact"]["data"]["qc"]["opening_check"],
            "ffprobe": json.loads(
                subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=codec_name,width,height,r_frame_rate:format=duration",
                        "-of",
                        "json",
                        str(mp4),
                    ]
                )
            ),
        }
        report["checks"].append(
            "Approved only an evidence-matched proposal; an independently decoded first frame of the actual downloaded H.264 export reads FRESH LIME."
        )
        report["passed"] = True
        print(
            json.dumps(
                {
                    "passed": True,
                    "original": c["status"],
                    "model_choice": plan["model_selection"]["choice"],
                    "ocr": observed,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
