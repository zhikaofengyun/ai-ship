"""Render and fully verify 100 portrait cuts of the repo's attributed CC BY-SA excerpt.

From the repository root: python evidence/batch-production/real-footage/batch/verify_coffee_batch.py --repo . --output /tmp/ai-ship-coffee-result.json
Requires the repository's requirements-dev.txt, NumPy and the FFmpeg tools on Linux.
"""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

FPS, WIDTH, HEIGHT = 25, 432, 768
CUT_FRAMES, SAMPLE_HZ, COUNT = 38, 16000, 100
STARTS = list(range(0, 200, 20))


def run_cmd(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, timeout=60, **kwargs)


def api(r):
    if r.is_error:
        raise RuntimeError(
            f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:700]}"
        )
    return r.json()["data"]


def probe(path):
    return json.loads(
        run_cmd(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ).stdout
    )


def repository_observation(repo):
    def git(*args):
        return run_cmd("git", "-C", str(repo), *args, text=True).stdout.strip()

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "app_tree": git("rev-parse", "HEAD:app"),
        "app_tree_clean": not git("status", "--porcelain", "--", "app"),
        "observed_utc": datetime.now(timezone.utc).isoformat(),
    }


def bounded_tools(temp):
    available = sorted(os.sched_getaffinity(0))
    used = available[-2:]
    os.sched_setaffinity(0, used)
    bin_dir = temp / "bin"
    bin_dir.mkdir()
    versions = {}
    for tool in ("ffmpeg", "ffprobe"):
        real = shutil.which(tool)
        versions[tool] = run_cmd(real, "-version").stdout.decode().splitlines()[0]
        target = bin_dir / tool
        # The application normally requests two FFmpeg threads for one render. For this
        # shared-host run, give each of the two worker encoders/decoders one codec thread.
        target.write_text(
            "#!"
            + sys.executable
            + '\nimport os,sys\nargs=sys.argv[1:]\nfor i,v in enumerate(args[:-1]):\n if v in ("-threads","-threads:v","-threads:a","-filter_threads","-filter_complex_threads"):args[i+1]="1"\nos.execv('
            + repr(real)
            + ",["
            + repr(real)
            + ',"-cpucount","1",*args])\n'
        )
        target.chmod(0o755)
    os.environ["PATH"] = str(bin_dir) + ":" + os.environ["PATH"]
    os.environ.update(
        OPENBLAS_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        NUMEXPR_NUM_THREADS="1",
    )
    return {
        "host_logical_cpus": os.cpu_count(),
        "initial_affinity_count": len(available),
        "allowed_cpu_ids": used,
        "workers": 2,
        "per_ffmpeg_cpu_count": 1,
        "per_worker_video_codec_threads": 1,
        **versions,
        "platform": platform.platform(),
        "python": platform.python_version(),
    }


def decode(client, item, project, asset, source_pcm, folder):
    import numpy as np

    row = api(client.get("records/" + item["artifact_id"]))
    timeline = api(client.get("records/" + item["timeline_id"]))
    assert row["project_id"] == project and timeline["project_id"] == project
    clips = timeline["data"]["clips"]
    assert len(clips) == 2
    starts = []
    for c in clips:
        start = round(c["source_in"] * FPS)
        end = round(c["source_out"] * FPS)
        assert start in STARTS and end - start == CUT_FRAMES and c["asset_id"] == asset
        starts.append(start)
    media = client.get(
        "media/artifacts/" + item["artifact_id"] + "/file", params={"download": "true"}
    )
    media.raise_for_status()
    body = media.content
    digest = hashlib.sha256(body).hexdigest()
    assert digest == row["data"]["content_hash"]
    path = folder / "latest.mp4"
    pcm_path = folder / "latest.pcm"
    path.write_bytes(body)
    metadata = probe(path)
    streams = metadata["streams"]
    (video,) = [s for s in streams if s["codec_type"] == "video"]
    (audio,) = [s for s in streams if s["codec_type"] == "audio"]
    assert (
        video["width"],
        video["height"],
        video["codec_name"],
        video["avg_frame_rate"],
        audio["codec_name"],
    ) == (WIDTH, HEIGHT, "h264", "25/1", "aac")
    framehash = (
        run_cmd(
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "1",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-c:v",
            "rawvideo",
            "-threads:v",
            "1",
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "pipe:1",
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_HZ),
            "-f",
            "s16le",
            "-y",
            str(pcm_path),
        )
        .stdout.decode()
        .splitlines()
    )
    frames = [l.split(",")[-1].strip() for l in framehash if l and not l.startswith("#")]
    pcm = pcm_path.read_bytes()
    path.unlink()
    pcm_path.unlink()
    decoded = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    assert (
        len(frames) == CUT_FRAMES * 2
        and abs(len(decoded) / SAMPLE_HZ - CUT_FRAMES * 2 / FPS) < 0.06
    )
    rms_db = 20 * np.log10(max(float(np.sqrt(np.mean(decoded**2))) / 32768, 1e-12))
    scores = []
    # Compare against the corresponding source segment, allowing ±32 ms of filter/AAC delay.
    # Exclude trim boundaries, downsample both traces identically, and normalize each pair.
    for index, start in enumerate(starts):
        offset = start * SAMPLE_HZ // FPS
        out = index * CUT_FRAMES * SAMPLE_HZ // FPS
        margin = SAMPLE_HZ // 5
        n = SAMPLE_HZ
        lag = 512
        ref = source_pcm[offset + margin : offset + margin + n : 4].copy()
        ref -= ref.mean()
        win = decoded[out + margin - lag : out + margin + n + lag : 4]
        score = []
        for shift in range(2 * lag // 4 + 1):
            part = win[shift : shift + len(ref)]
            centered = part - part.mean()
            norm = float(np.linalg.norm(ref) * np.linalg.norm(centered))
            score.append(float(np.dot(ref, centered) / norm) if norm > 0 else 0)
        scores.append(max(score))
    return {
        "ordinal": item["ordinal"],
        "timeline_id": timeline["id"],
        "artifact_id": row["id"],
        "bytes": len(body),
        "sha": digest,
        "video_sha": hashlib.sha256("\n".join(frames).encode()).hexdigest(),
        "pcm_sha": hashlib.sha256(pcm).hexdigest(),
        "frames": len(frames),
        "samples": len(decoded),
        "duration": float(metadata["format"]["duration"]),
        "stream_sample_rate": int(audio["sample_rate"]),
        "channels": audio["channels"],
        "source_frame_starts": starts,
        "rms_dbfs": float(rms_db),
        "correlations": scores,
    }


def stats(values):
    return {
        "min": round(min(values), 5),
        "median": round(statistics.median(values), 5),
        "max": round(max(values), 5),
    }


def main(repo, output):
    start_at = datetime.now(timezone.utc).isoformat()
    source_dir = repo / "evidence/batch-production/real-footage"
    source = source_dir / "coffee-excerpt.mp4"
    original = json.loads((source_dir / "coffee-evidence.json").read_text())
    input_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert input_sha == original["attributed_download_sha256"]
    lic = original["source"]
    assert lic["license"] == "CC BY-SA 4.0" and lic["author"] == "Keria T"
    sys.path.insert(0, str(repo / "scripts"))
    from verify_portrait_batch import server

    with tempfile.TemporaryDirectory(prefix="coffee-100-") as temp:
        folder = Path(temp)
        env = bounded_tools(folder)
        env["repository"] = repository_observation(repo)
        import numpy as np

        sp = probe(source)
        (vt,) = [s for s in sp["streams"] if s["codec_type"] == "video"]
        (at,) = [s for s in sp["streams"] if s["codec_type"] == "audio"]
        assert (vt["width"], vt["height"], round(float(vt["duration"]) * FPS)) == (
            WIDTH,
            HEIGHT,
            225,
        )
        assert "Keria T" in sp["format"]["tags"]["artist"]
        reference = np.frombuffer(
            run_cmd(
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-threads",
                "1",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_HZ),
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "pipe:1",
            ).stdout,
            dtype="<i2",
        ).astype(np.float32)
        with server(folder) as client:
            t = time.monotonic()
            project = api(
                client.post(
                    "projects",
                    json={
                        "name": "咖啡烘焙摄制素材百条剪辑核对",
                        "width": WIDTH,
                        "height": HEIGHT,
                        "duration": CUT_FRAMES * 2 / FPS,
                    },
                )
            )
            pid = project["id"]
            imported = api(
                client.post(
                    "media/import",
                    params={"project_id": pid, "filename": source.name},
                    headers={"content-type": "video/mp4"},
                    content=source.read_bytes(),
                )
            )
            asset = imported["asset"]
            sid = asset["data"]["source_id"]
            source_record = api(client.get("records/" + sid))
            rights = {
                "title": lic["title"],
                "author": lic["author"],
                "license": lic["license"],
                "license_url": lic["license_url"],
                "source_page": lic["description_url"],
                "source_original_sha256": original["source_sha256"],
                "excerpt_sha256": input_sha,
                "changes": "Excerpt of first 9 s of original; attribution retained in input. This benchmark trims and reorders it; no derived movies distributed.",
            }
            source_record = api(
                client.patch(
                    "records/" + sid,
                    json={
                        "expected_revision": source_record["version"],
                        "data": {
                            **source_record["data"],
                            "attribution_evidence": rights,
                        },
                        "reason": "登记本次本地导入节选的原作、作者、CC BY-SA 4.0、改动与双重摘要。",
                    },
                )
            )
            assert source_record["data"]["attribution_evidence"] == rights
            info = api(
                client.get(
                    "production/assets/admission",
                    params={"project_id": pid, "source_id": sid},
                )
            )
            admission = api(
                client.post(
                    "production/assets/admission",
                    json={
                        "project_id": pid,
                        "request_id": "coffee-ccby-sa-reviewed",
                        "source": {k: info["source"][k] for k in ("id", "version")},
                        "expected_revision": info["revision"],
                        "action": "approve",
                        "allowed_purposes": ["inspect", "process", "export"],
                        "reason": f"Verified local attributed excerpt {input_sha}; {lic['author']}, licensed CC BY-SA 4.0, {lic['description_url']}. Local inspection, editing and export; no external distribution.",
                    },
                )
            )
            assert admission["scope_status"] == "approve"
            asset = api(client.get("records/" + asset["id"]))
            prep = time.monotonic() - t
            choices = [
                {
                    "asset_id": asset["id"],
                    "version": asset["version"],
                    "source_in": i / FPS,
                    "source_out": (i + CUT_FRAMES) / FPS,
                }
                for i in STARTS
            ]
            spec = {
                "project_id": pid,
                "name": "同一咖啡实拍原声 · 100组双镜头",
                "idempotency_key": "coffee-100-v1",
                "count": COUNT,
                "width": WIDTH,
                "height": HEIGHT,
                "fps": FPS,
                "seed": 20261125,
                "slots": [{"choices": choices}, {"choices": choices}],
                "output": "render",
            }
            t = time.monotonic()
            b = api(client.post("production/batches", json=spec))
            enqueue = time.monotonic() - t
            bid = b["id"]
            peak = 0
            last_print = -1
            while True:
                b = api(client.get("production/batches/" + bid, params={"limit": 1}))
                peak = max(peak, b["counts"].get("running", 0))
                done = b["counts"].get("succeeded", 0)
                if done // 10 != last_print:
                    print(
                        "rendered",
                        done,
                        "elapsed",
                        round(time.monotonic() - t, 1),
                        flush=True,
                    )
                    last_print = done // 10
                if b["status"] in ("completed", "completed_with_errors", "cancelled"):
                    break
                if time.monotonic() - t > 1800:
                    raise TimeoutError(b)
                time.sleep(0.3)
            complete = time.monotonic() - t
            assert b["counts"]["succeeded"] == COUNT, b
            stamp = time.monotonic()
            after = 0
            rows = []
            while True:
                page = api(
                    client.get(
                        "production/batches/" + bid,
                        params={"limit": 25, "after": after},
                    )
                )
                rows.extend(
                    decode(client, item, pid, asset["id"], reference, folder)
                    for item in page["items"]
                )
                print("fully decoded", len(rows), flush=True)
                if page["next_after"] is None:
                    break
                after = page["next_after"]
            verification = time.monotonic() - stamp
            assert len(rows) == COUNT
            pairs = [tuple(r["source_frame_starts"]) for r in rows]
            scores = [s for r in rows for s in r["correlations"]]
    result = {
        "started_at": start_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source": rights,
        "source_streams": {
            "seconds": float(vt["duration"]),
            "fps": vt["avg_frame_rate"],
            "video": vt["codec_name"],
            "width": vt["width"],
            "height": vt["height"],
            "audio": at["codec_name"],
            "channels": at["channels"],
            "sample_rate": int(at["sample_rate"]),
        },
        "environment": env,
        "timing_seconds": {
            "import_attribution_and_approval": round(prep, 3),
            "enqueue_response": round(enqueue, 3),
            "enqueue_through_terminal_status": round(complete, 3),
            "terminal_status_to_final_full_verification": round(verification, 3),
            "render_then_verify_wall": round(complete + verification, 3),
        },
        "batch": {
            "id": bid,
            "output": "render",
            "workers_configured": 2,
            "peak_concurrently_running_items_observed": peak,
            "requested": COUNT,
            "counts": b["counts"],
            "seed": 20261125,
            "fps": FPS,
            "dimensions": [WIDTH, HEIGHT],
            "frames_per_slot": CUT_FRAMES,
            "slots": 2,
            "candidate_start_frames_per_slot": STARTS,
            "recipe_distinct_pairs": len(set(pairs)),
            "forward_pairs": sum(a < b for a, b in pairs),
            "reverse_pairs": sum(a > b for a, b in pairs),
            "same_trim_pairs": sum(a == b for a, b in pairs),
        },
        "rights": {
            "source_id": sid,
            "source_version": source_record["version"],
            "admission_revision": admission["revision"],
            "decision_id": admission["decision"]["id"],
            "action": admission["scope_status"],
            "purposes": admission["decision"]["data"]["allowed_purposes"],
            "source_archive_rechecked_by_sha256": True,
            "original_rights_evidence": "Versioned input provenance in repo, input MP4 attribution and source snapshot; no new remote verification in this run.",
        },
        "verification": {
            "downloads": len(rows),
            "matched_stored_artifact_sha256": len(rows),
            "distinct_timelines": len({r["timeline_id"] for r in rows}),
            "distinct_artifact_records": len({r["artifact_id"] for r in rows}),
            "unique_file_sha256": len({r["sha"] for r in rows}),
            "unique_decoded_frame_sequence_sha256": len({r["video_sha"] for r in rows}),
            "unique_decoded_mono_pcm_sha256": len({r["pcm_sha"] for r in rows}),
            "video_stream": "H.264, 432x768 25 fps",
            "audio_stream": "AAC",
            "audio_stream_hz": sorted({r["stream_sample_rate"] for r in rows}),
            "audio_stream_channels": sorted({r["channels"] for r in rows}),
            "duration_seconds": stats([r["duration"] for r in rows]),
            "video_frames_each": sorted({r["frames"] for r in rows}),
            "video_frames_total": sum(r["frames"] for r in rows),
            "decoded_mono_16khz_samples_each": stats([r["samples"] for r in rows]),
            "download_bytes_total": sum(r["bytes"] for r in rows),
            "mono_rms_dbfs": stats([r["rms_dbfs"] for r in rows]),
            "source_audio_clip_correlations": stats(scores),
            "source_audio_clip_count": len(scores),
            "source_audio_clip_correlation_at_least_0_75": sum(s >= 0.75 for s in scores),
            "ordered_hash_manifest_sha256": hashlib.sha256(
                json.dumps(
                    [
                        (r["ordinal"], r["sha"], r["video_sha"], r["pcm_sha"])
                        for r in sorted(rows, key=lambda r: r["ordinal"])
                    ],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        },
        "limitations": [
            "One attributed 9 s source, deterministic two-slot recuts, 3.04 s duration. No model generation, public upload, subjective quality or audience-performance evaluation.",
            "Single shared host, local SQLite and object storage, 2 workers with 2-CPU total affinity. Times reflect this run and co-scheduled processes.",
            "Audio comparison uses decoded mono PCM downsampled to 4 kHz over a 1 s interior window per slot and optimizes normalized correlation over ±32 ms. Full frame/audio streams are decoded for hashes.",
            "Output files existed only in the temporary application store. Download verification reused one temporary filename; no derived movies are retained or distributed.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["verification"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.repo.resolve(), args.output.resolve())
