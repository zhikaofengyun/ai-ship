"""Render two reproducible camera moves from the same imported photo and human timelines."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import batches
from tests.test_production import ProductionBase


def gray_frame(path, frame):
    return subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{frame})",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ]
    )


def displacement(path, axis):
    # Find the integer translation minimizing mean absolute luma difference over a
    # fixed central region. The same pixels are scored for each candidate shift.
    first, last = gray_frame(path, 0), gray_frame(path, 47)
    assert len(first) == len(last) == 432 * 768
    sample = [(x, y, first[y * 432 + x]) for y in range(208, 560, 13) for x in range(116, 316, 11)]

    def error(dx, dy):
        return sum(abs(v - last[(y + dy) * 432 + x + dx]) for x, y, v in sample) / len(sample)

    along = range(-110, 111) if axis == "x" else range(-198, 199)
    candidates = [
        (delta, cross) if axis == "x" else (cross, delta)
        for delta in along
        for cross in range(-2, 3)
    ]
    best, dx, dy = min((error(dx, dy), dx, dy) for dx, dy in candidates)
    return {
        "dx": dx,
        "dy": dy,
        "aligned_mean_luma_error": round(best, 4),
        "unshifted_mean_luma_error": round(error(0, 0), 4),
        "compared_pixels": len(sample),
    }


def contact_sheet(output):
    select = r"select='eq(n,0)+eq(n,24)+eq(n,47)',scale=162:288,tile=3x1"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output / "horizontal.mp4"),
            "-i",
            str(output / "vertical.mp4"),
            "-filter_complex_threads",
            "1",
            "-filter_complex",
            f"[0:v]{select}[h];[1:v]{select}[v];[h][v]vstack",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-q:v",
            "2",
            str(output / "frames.jpg"),
        ],
        check=True,
    )


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    footage = ROOT / "evidence/batch-production/real-footage/coffee-excerpt.mp4"
    credit = json.loads(footage.with_name("coffee-evidence.json").read_text())
    assert hashlib.sha256(footage.read_bytes()).hexdigest() == credit["attributed_download_sha256"]
    photo = subprocess.check_output(
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
            "-threads",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "pipe:1",
        ]
    )
    evidence = {
        "photo_sha256": hashlib.sha256(photo).hexdigest(),
        "source": credit["source"],
        "items": [],
    }
    fixture = ProductionBase()
    fixture.setUp()
    try:
        client = fixture.client

        def post(endpoint, body):
            response = client.post("/api/v1" + endpoint, json=body)
            response.raise_for_status()
            return response.json()["data"]

        result = client.post(
            "/api/v1/media/import",
            params={"project_id": fixture.pid, "filename": "coffee-table.png"},
            content=photo,
        )
        result.raise_for_status()
        asset = result.json()["data"]["asset"]
        assert asset["data"]["probe"]["is_still"]
        output_format = {"width": 432, "height": 768, "fps": 24}
        duration = 2
        for label, axis, start, end in [("horizontal", "x", -4, -104), ("vertical", "y", -4, -188)]:
            clip = {
                "id": "photo",
                "name": "咖啡与桌布",
                "asset_id": asset["id"],
                "source_in": 0,
                "source_out": duration,
                "timeline_start": 0,
                "track": 0,
                "fit": "cover",
                "width": 540,
                "height": 960,
                "x": -54,
                "y": -96,
                "keyframes": {
                    axis: [{"time": 0, "value": start}, {"time": (47 / 24), "value": end}]
                },
            }
            template = post(
                "/records",
                {
                    "kind": "Timeline",
                    "project_id": fixture.pid,
                    "data": {
                        "name": label,
                        "clips": [clip],
                        "format": output_format,
                        "audio_tracks": [],
                        "captions": [],
                        "locks": [{"path": "clips.0.keyframes", "owner": "editor"}],
                    },
                    "refs": [{"id": asset["id"], "version": asset["version"]}],
                },
            )
            recipe = {
                "name": "原照运镜 " + label,
                "project_id": fixture.pid,
                "idempotency_key": "photo-move-" + label,
                "count": 1,
                "output": "render",
                "template": {"id": template["id"], "version": template["version"]},
                "slots": [
                    {
                        "choices": [
                            {
                                "asset_id": asset["id"],
                                "version": asset["version"],
                                "source_out": duration,
                            }
                        ]
                    }
                ],
                "max_generation_calls": 0,
                **output_format,
            }
            estimate = post("/production/batches/estimate", recipe)
            assert estimate["generation_calls"] == 0
            batch = post("/production/batches", recipe)
            assert batches.work_once(fixture.store)
            page = client.get("/api/v1/production/batches/" + batch["id"]).json()["data"]
            assert page["counts"]["succeeded"] == 1, page
            row = page["items"][0]
            resp = client.get("/api/v1/media/artifacts/" + row["artifact_id"] + "/file")
            resp.raise_for_status()
            path = output / (label + ".mp4")
            path.write_bytes(resp.content)
            artifact = fixture.store.get(row["artifact_id"], "local")
            mapping = next(
                r
                for r in fixture.store.list("local", kind="TimeMap", project_id=fixture.pid)
                if r["data"]["output_ref"]["id"] == artifact["id"]
            )
            layer = mapping["data"]["segments"][0]["contributions"][0]
            assert (
                layer["source_ref"]["id"] == asset["id"]
                and layer["mapping_curve"]["kind"] == "hold"
            )
            assert layer["source_interval"] == [0, 0]
            hashes = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-f",
                    "framemd5",
                    "-",
                ],
                text=True,
            )
            frames = [
                line.split(",")[-1].strip()
                for line in hashes.splitlines()
                if not line.startswith("#")
            ]
            assert len(frames) == 48 and len(set(frames)) >= 45
            assert artifact["data"]["qc"]["checks"]["full_decode"]
            measured = displacement(path, axis)
            assert (
                abs(measured["d" + axis] - (end - start)) <= 2
                and measured["aligned_mean_luma_error"]
                < measured["unshifted_mean_luma_error"] * 0.2
            ), measured
            evidence["items"].append(
                {
                    "motion": label,
                    "canvas": output_format,
                    "template": template["data"],
                    "template_pin": recipe["template"],
                    "batch_id": batch["id"],
                    "artifact_id": artifact["id"],
                    "generation_calls": estimate["generation_calls"],
                    "requested_axis_displacement_px": end - start,
                    "measured_translation": measured,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "decoded_frames": len(frames),
                    "distinct_decoded_frames": len(set(frames)),
                    "mapping": layer,
                }
            )
    finally:
        fixture.tearDown()
    contact_sheet(output)
    (output / "report.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output)
