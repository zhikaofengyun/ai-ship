"""Project-local colour/composition clustering from decoded frames. No semantic inference."""

import csv
import hashlib
from functools import lru_cache
import io
import re
import shutil
import tempfile
from pathlib import Path
from fractions import Fraction
from math import floor
from statistics import fmean
from .errors import DomainError
from .media import resolve_media, probe_media, run_process
from .production import ref
from .store import get_store


def sample(path, seconds):
    pixels, log = run_process(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "info",
            "-copyts",
            "-start_at_zero",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            str(seconds),
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            "showinfo,scale=16:12:flags=area",
            "-fps_mode",
            "passthrough",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        timeout=15,
        binary_output=True,
    )
    pts = re.search(r"\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:([\d.eE+-]+)", log)
    if len(pixels) != 16 * 12 * 3 or not pts:
        raise DomainError("FRAME_UNAVAILABLE", "候选片段没有可解码的视频帧。", 409)
    rgb = list(zip(pixels[::3], pixels[1::3], pixels[2::3]))
    luma = [(r * 0.2126 + g * 0.7152 + b * 0.0722) / 255 for r, g, b in rgb]
    # Spatial means distinguish compositions with the same global average colour.
    vector = []
    for row in range(2):
        for col in range(2):
            cells = [
                rgb[y * 16 + x]
                for y in range(6 * row, 6 * row + 6)
                for x in range(8 * col, 8 * col + 8)
            ]
            vector.extend(round(fmean(channel) / 255, 4) for channel in zip(*cells))
    edge = fmean(
        abs(luma[y * 16 + x] - luma[y * 16 + x - 1]) for y in range(12) for x in range(1, 16)
    )
    return {
        "at": float(pts[1]),
        "seek_requested": seconds,
        "descriptor": vector,
        "luminance": round(fmean(luma), 4),
        "contrast": round(max(luma) - min(luma), 4),
        "edge": round(edge, 4),
    }


def distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def clusters(samples):
    """Lloyd iterations with deterministic farthest-point initialisation, at most four groups."""
    features = [s["descriptor"] for s in samples]
    centers = [features[0]]
    for _ in range(min(4, len(samples)) - 1):
        candidate = max(features, key=lambda x: min(distance(x, c) for c in centers))
        if min(distance(candidate, c) for c in centers) < 0.025:
            break
        centers.append(candidate)
    for _ in range(16):
        labels = [min(range(len(centers)), key=lambda i: distance(f, centers[i])) for f in features]
        next_centers = []
        for i, c in enumerate(centers):
            members = [f for f, label in zip(features, labels) if i == label]
            next_centers.append(list(map(fmean, zip(*members))) if members else c)
        if max(distance(a, b) for a, b in zip(centers, next_centers)) < 0.000001:
            break
        centers = next_centers
    for sample, f in zip(samples, features):
        sample["cluster"] = min(range(len(centers)), key=lambda i: distance(f, centers[i]))
    return len(centers)


@lru_cache(maxsize=24)
def model_hash(path, size, mtime):
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def recognize_text(path, at, seek, *, frame_receipt=False):
    """Optional LSTM OCR on one bounded source frame; a reading is never a verified product claim."""
    if not shutil.which("tesseract"):
        return {"status": "unavailable", "at": at, "words": []}
    try:
        with tempfile.TemporaryDirectory(prefix="ai-ship-frame-") as d:
            image = Path(d) / "sample.png"
            _, log = run_process(
                [
                    "ffmpeg",
                    "-v",
                    "info" if frame_receipt else "error",
                    *(["-copyts", "-start_at_zero"] if frame_receipt else []),
                    "-protocol_whitelist",
                    "file,pipe",
                    "-ss",
                    str(seek),
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-vf",
                    ("showinfo," if frame_receipt else "") + "scale=w='min(1280,iw)':h=-1",
                    "-threads",
                    "1",
                    str(image),
                ],
                timeout=15,
            )
            if frame_receipt:
                pts = re.search(r"\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:([\d.eE+-]+)", log)
                if not pts or not image.is_file():
                    return {"status": "unavailable", "at": None, "words": []}
                at = float(pts[1])
            langs, _ = run_process(["tesseract", "--list-langs"], timeout=5)
            languages = [
                lang for lang in ("eng", "chi_sim", "chi_tra") if lang in langs.splitlines()
            ]
            if not languages:
                return {"status": "language_unavailable", "at": at, "words": []}
            language_hashes = {}
            directory = re.search(r'^List of available languages in "([^"]+)"', langs)
            if directory:
                for lang in languages:
                    weights = Path(directory[1]) / (lang + ".traineddata")
                    if weights.is_file():
                        stat = weights.stat()
                        language_hashes[lang] = model_hash(
                            str(weights), stat.st_size, stat.st_mtime_ns
                        )
            version, _ = run_process(["tesseract", "--version"], timeout=5)
            with image.open("rb") as stream:
                header = stream.read(24)
            result, _ = run_process(
                [
                    "tesseract",
                    str(image),
                    "stdout",
                    "--oem",
                    "1",
                    "--psm",
                    "11",
                    "-l",
                    "+".join(languages),
                    "tsv",
                ],
                timeout=15,
            )
            words = []
            for row in csv.DictReader(io.StringIO(result), delimiter="\t"):
                if (
                    row["level"] == "5"
                    and row["text"].strip()
                    and float(row["conf"]) >= (0 if frame_receipt else 50)
                ):
                    words.append(
                        {
                            "text": row["text"][:120],
                            "confidence": round(float(row["conf"]) / 100, 3),
                            **(
                                {
                                    "line": [
                                        int(row[k]) for k in ("block_num", "par_num", "line_num")
                                    ]
                                }
                                if frame_receipt
                                else {}
                            ),
                            "box_px": [int(row[k]) for k in ("left", "top", "width", "height")],
                        }
                    )
            return {
                "status": "read",
                **(
                    {"frame_sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
                    if frame_receipt
                    else {}
                ),
                "engine": "tesseract_lstm",
                "engine_version": version.splitlines()[0] if version else None,
                "model_sha256": language_hashes,
                "analysis_size": [
                    int.from_bytes(header[16:20], "big"),
                    int.from_bytes(header[20:24], "big"),
                ],
                "languages": languages,
                "at": at,
                "words": words[:60],
                "coordinate_space": "analysis_frame_pixels",
            }
    except (DomainError, ValueError, KeyError):
        return {"status": "unavailable", "at": at, "words": []}


def inspect(actor, project_id, asset_ids):
    sources = []
    for rid in asset_ids:
        asset, _, path = resolve_media(get_store(), actor, rid, project_id=project_id)
        if asset["kind"] not in {"Asset", "AssetVersion"}:
            raise DomainError("NOT_TIMED_VIDEO", "画面候选需要项目中的视频素材。")
        probe = probe_media(path)
        if not probe.get("has_video") or probe.get("is_still") or not probe.get("duration"):
            raise DomainError("NOT_TIMED_VIDEO", "候选需包含真实、可解码的定时画面。")
        duration = floor(float(probe["duration"]) * 1000000) / 1000000
        rate = Fraction(probe.get("fps") or "25")
        frame_seconds = float(1 / rate) if rate > 0 else 0.04
        if duration < 0.04:
            raise DomainError("SOURCE_TOO_SHORT", "当前渲染器需要至少 0.04 秒的片段。")
        usable = max(0, duration - frame_seconds)
        instants = sorted({round(usable * f, 6) for f in (0.12, 0.50, 0.85)})
        samples = [sample(path, t) for t in instants]
        sources.append(
            {
                "asset_id": asset["id"],
                "asset_version": asset["version"],
                "name": str(asset["data"].get("filename") or asset["data"].get("name") or "原片")[
                    :240
                ],
                "duration": duration,
                "samples": samples,
                "ocr": recognize_text(
                    path, samples[len(samples) // 2]["at"], instants[len(instants) // 2]
                ),
                "record_ref": ref(asset),
            }
        )
    clusters([s for source in sources for s in source["samples"]])
    return sources


def local_proposal(goal, sources, target_seconds):
    # Water-fill durations, so short sources don't waste the remaining allocation.
    durations = [0.0] * len(sources)
    active = set(range(len(sources)))
    remaining = min(target_seconds, sum(s["duration"] for s in sources))
    while active:
        share = remaining / len(active)
        capped = [i for i in active if sources[i]["duration"] <= share]
        if not capped:
            for i in active:
                durations[i] = share
            break
        for i in capped:
            durations[i] = sources[i]["duration"]
            remaining -= durations[i]
            active.remove(i)
    shots, missing = [], []
    groups = {}
    previous = None
    terms = set(re.findall(r"[^\W_]+", goal.casefold()))

    def matches(source):
        return [
            w["text"]
            for w in source.get("ocr", {}).get("words", [])
            if w["text"].casefold() in terms
        ]

    ordered = sorted(enumerate(sources), key=lambda item: -len(matches(item[1])))
    for i, source in ordered:

        def priority(frame):
            quality = frame["contrast"] + frame["edge"] - abs(frame["luminance"] - 0.5) * 0.5
            variety = min(1, distance(frame["descriptor"], previous)) if previous else 0.0
            return quality + 0.3 * variety - 0.25 * groups.get(frame["cluster"], 0)

        chosen = max(source["samples"], key=priority)
        length = durations[i]
        if length < 0.04:
            continue
        start = round(max(0, min(source["duration"] - length, chosen["at"] - length / 2)), 6)
        end = min(source["duration"], round(start + length, 6))
        groups[chosen["cluster"]] = groups.get(chosen["cluster"], 0) + 1
        previous = chosen["descriptor"]
        shots.append(
            {
                "asset_id": source["asset_id"],
                "source_in": start,
                "source_out": end,
                "reason": f"实测 {chosen['at']:.2f} 秒画面，亮度 {chosen['luminance']:.2f}，对比度 {chosen['contrast']:.2f}，构图聚类 {chosen['cluster']+1}。",
            }
        )
    return {
        "title": goal[:100],
        "approach": "按目标与实帧 OCR 的词命中排序，同分保留素材顺序，按实测对比度和构图聚类选段。",
        "shots": shots,
        "missing": missing,
    }
