"""Project-local colour/composition clustering from decoded frames. No semantic inference."""

import csv
import hashlib
from functools import lru_cache
import io
import json
import re
import shutil
import tempfile
from pathlib import Path
from fractions import Fraction
from math import floor
from statistics import fmean
from .auth import actor_still_authorized
from .errors import DomainError
from .media import resolve_media, probe_media, run_process
from .production import ref
from .store import canonical, get_store


def sample(path, seconds):
    pixels, log = run_process(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "info",
            "-copyts",
            "-start_at_zero",
            "-threads",
            "1",
            "-filter_threads",
            "1",
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
        "rgb_sha256": hashlib.sha256(pixels).hexdigest(),
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
                    "-threads",
                    "1",
                    "-filter_threads",
                    "1",
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
                    return {"status": "unavailable", "at": None, "words": [], "retryable": True}
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
    except DomainError as exc:
        return {
            "status": "unavailable",
            "at": at,
            "words": [],
            "code": exc.code,
            "retryable": exc.code in {"MEDIA_BUSY", "MEDIA_TIMEOUT", "MEDIA_EXECUTION_FAILED"},
        }
    except (ValueError, KeyError):
        return {"status": "unavailable", "at": at, "words": []}


OBSERVATION_METHOD = "rgb-quadrants-3/tesseract-lstm-v1"


def observation_tags(samples, ocr):
    """Measured frame properties and OCR readings are evidence, never product assertions."""
    tags = []
    for frame in samples:
        rgb = [fmean(frame["descriptor"][c::3]) for c in range(3)]
        dominant = max(range(3), key=rgb.__getitem__)
        if max(rgb) - min(rgb) > 0.18:
            tag = ("红色调", "绿色调", "蓝色调")[dominant]
        else:
            tag = "低饱和色调"
        tags.append(
            {
                "category": "color",
                "label": tag,
                "source_seconds": frame["at"],
                "method": "sampled_rgb",
                "sample_rgb_sha256": frame["rgb_sha256"],
            }
        )
    for word in ocr.get("words", []):
        if word["confidence"] >= 0.5:
            tags.append(
                {
                    "category": "visible_text",
                    "label": word["text"],
                    "method": "tesseract_lstm",
                    "source_seconds": ocr["at"],
                    "ocr_confidence": word["confidence"],
                    "sample_sha256": ocr["frame_sha256"],
                    "box_px": word["box_px"],
                }
            )
    return tags


def measure_source(path):
    probe = probe_media(path)
    if not probe.get("has_video"):
        raise DomainError("NO_VISUAL_STREAM", "此素材没有画面。")
    still = probe["is_still"]
    duration = floor(float(probe["duration"]) * 1000000) / 1000000
    if still:
        instants = [0]
    else:
        if duration < 0.04:
            raise DomainError("SOURCE_TOO_SHORT", "当前渲染器需要至少 0.04 秒的片段。")
        try:
            rate = Fraction(probe["fps"])
        except (ZeroDivisionError, ValueError, TypeError):
            rate = 0
        usable = max(0, duration - (float(1 / rate) if rate > 0 else 0.04))
        instants = sorted({round(usable * f, 6) for f in (0.12, 0.50, 0.85)})
    samples = [sample(path, t) for t in instants]
    return {
        "is_still": still,
        "duration": duration,
        "samples": samples,
        "display_width": probe.get("display_width", probe["width"]),
        "display_height": probe.get("display_height", probe["height"]),
    }


def observe_source(store, actor, project, rid, *, refresh=False):
    source, blob, path = resolve_media(store, actor, rid, project_id=project)
    scope = (actor.tenant_id, project, blob["id"])
    analysis = None
    if not refresh:
        with store.connection() as db:
            row = db.execute(
                "SELECT analysis_json FROM media_observations WHERE tenant=? AND project=? "
                "AND blob_id=? AND blob_pin=? AND method=?",
                (*scope, blob["hash"], OBSERVATION_METHOD),
            ).fetchone()
        if row:
            analysis = json.loads(row[0])
            if not analysis["ocr"].get("retryable"):
                return source, analysis, True
    if analysis is None:
        analysis = measure_source(path)
    samples = analysis["samples"]
    middle = samples[len(samples) // 2]
    ocr = recognize_text(path, middle["at"], middle["seek_requested"], frame_receipt=True)
    ocr["words"] = [word for word in ocr["words"] if word["confidence"] >= 0.5]
    analysis["ocr"] = ocr
    tags = observation_tags(samples, ocr)
    w, h = analysis["display_width"], analysis["display_height"]
    tags.append(
        {
            "label": "竖屏" if h > w else ("横屏" if w > h else "方形"),
            "category": "orientation",
            "method": "ffprobe_display_matrix",
        }
    )
    with store.transaction(), store.connection() as db:
        if not actor_still_authorized(actor):
            raise DomainError("IDENTITY_REVOKED", "读取期间工作区权限已变化。", 403)
        current, live_blob, _ = resolve_media(store, actor, rid, project_id=project)
        if current["hash"] != source["hash"] or live_blob["hash"] != blob["hash"]:
            raise DomainError("SOURCE_CHANGED", "分析期间素材已变化，请重试。", 409)
        if ocr["status"] != "read":
            # A slower failed reader must not replace a successful concurrent
            # reading of the same bytes and analysis method.
            found = db.execute(
                "SELECT analysis_json FROM media_observations WHERE tenant=? AND project=? "
                "AND blob_id=? AND blob_pin=? AND method=? "
                "AND json_extract(analysis_json,'$.ocr.status')='read'",
                (*scope, blob["hash"], OBSERVATION_METHOD),
            ).fetchone()
            if found:
                return source, json.loads(found[0]), True
        db.execute(
            "INSERT INTO media_observations VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant,project,blob_id) DO UPDATE SET blob_pin=excluded.blob_pin,"
            "method=excluded.method,analysis_json=excluded.analysis_json,tags_json=excluded.tags_json",
            (*scope, blob["hash"], OBSERVATION_METHOD, canonical(analysis), canonical(tags)),
        )
    return source, analysis, False


def effective_suggestions(suggestions, assertions):
    def overrides(assertion, candidate):
        interval = assertion.get("interval")
        return (
            assertion.get("reviewer_lock") is True
            and assertion.get("entity") == candidate["category"]
            and isinstance(interval, list)
            and len(interval) == 2
            and all(type(x) in (float, int) for x in interval)
            and interval[0] <= candidate["source_seconds"] < interval[1]
        )

    return [c for c in suggestions if not any(overrides(a, c) for a in assertions)]


def editor_tags(store, actor, project, source):
    with store.read_snapshot() as db:
        if source["kind"] == "Asset":
            original = db.execute(
                "SELECT r.id FROM revisions v JOIN records r ON r.tenant_id=v.tenant_id AND r.id=v.record_id AND r.latest_version=v.version "
                "WHERE v.tenant_id=? AND r.project_id=? AND r.kind='AssetVersion' AND "
                "json_extract(v.data_json,'$.asset_id')=? AND json_extract(v.data_json,'$.variant')='original' "
                "AND json_extract(v.data_json,'$.blob_location_id')=? ORDER BY r.id LIMIT 1",
                (actor.tenant_id, project, source["id"], source["data"].get("blob_location_id")),
            ).fetchone()
            if not original:
                return []
            source = store.get(original["id"], actor.tenant_id)
        data = source["data"]
        first = (
            data.get("tag_base_revision", source["version"])
            if data.get("tier_revision") == source["version"]
            else source["version"]
        )
        assertions = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT v.data_json FROM reference_index x JOIN records r ON r.tenant_id=x.tenant_id AND r.id=x.source_id "
                "AND r.latest_version=x.source_version JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id "
                "AND v.version=x.source_version WHERE x.tenant_id=? AND x.target_id=? AND x.target_version BETWEEN ? AND ? "
                "AND r.project_id=? AND r.kind='Tag' AND coalesce(json_extract(v.data_json,'$.status'),'') "
                "NOT IN ('revoked','rejected','archived','invalidated') ORDER BY v.created_at DESC,r.id LIMIT 32",
                (actor.tenant_id, source["id"], first, source["version"], project),
            )
        ]
        machine = db.execute(
            "SELECT labels_json FROM media_clip_candidates WHERE tenant=? AND project=? AND version_id=? AND pin=?",
            (actor.tenant_id, project, source["id"], source["hash"]),
        ).fetchone()
    labels = [
        {
            "label": str(a["label"])[:300],
            "category": a.get("entity"),
            "interval": a.get("interval"),
            "method": a.get("method"),
        }
        for a in assertions
        if a.get("label")
    ]
    for tag in effective_suggestions(json.loads(machine[0]) if machine else [], assertions):
        labels.append(
            {
                k: tag[k]
                for k in ("category", "label", "source_seconds", "method", "review_state", "cosine")
            }
        )
    return labels


def inspect(actor, project_id, asset_ids):
    sources = []
    store = get_store()
    for rid in asset_ids:
        asset, analysis, _ = observe_source(store, actor, project_id, rid)
        if asset["kind"] not in {"Asset", "AssetVersion"} or analysis["is_still"]:
            raise DomainError("NOT_TIMED_VIDEO", "画面候选需要项目中的定时视频素材。")
        sources.append(
            {
                "asset_id": asset["id"],
                "asset_version": asset["version"],
                "name": str(asset["data"].get("filename") or asset["data"].get("name") or "原片")[
                    :240
                ],
                "duration": analysis["duration"],
                "samples": analysis["samples"],
                "ocr": analysis["ocr"],
                "labels": editor_tags(store, actor, project_id, asset),
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

    def matched_tags(source):
        return [
            t
            for t in source.get("labels", [])
            if t.get("label") and t["label"].casefold() in goal.casefold()
        ]

    def relevance(item):
        tags = matched_tags(item[1])
        return (-sum(t["method"] == "manual" for t in tags), -len(matches(item[1])), -len(tags))

    ordered = sorted(enumerate(sources), key=relevance)
    for i, source in ordered:

        def priority(frame):
            quality = frame["contrast"] + frame["edge"] - abs(frame["luminance"] - 0.5) * 0.5
            variety = min(1, distance(frame["descriptor"], previous)) if previous else 0.0
            return quality + 0.3 * variety - 0.25 * groups.get(frame["cluster"], 0)

        chosen = max(source["samples"], key=priority)
        length = durations[i]
        if length < 0.04:
            continue
        center = chosen["at"]
        tags = matched_tags(source)
        for tag in tags:
            interval = tag.get("interval")
            if (
                isinstance(interval, list)
                and len(interval) == 2
                and all(type(x) in (int, float) for x in interval)
                and 0 <= interval[0] < interval[1] <= source["duration"]
            ):
                center = (interval[0] + interval[1]) / 2
                break
            if isinstance(tag.get("source_seconds"), (int, float)):
                center = tag["source_seconds"]
                break
        start = round(max(0, min(source["duration"] - length, center - length / 2)), 6)
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
        "approach": "优先复用与目标匹配的人工标注区间，再参考实帧 OCR、视觉候选、对比度和构图聚类选段。",
        "shots": shots,
        "missing": missing,
    }
