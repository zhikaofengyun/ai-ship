"""Derive a bounded, lossless, fixed-rate video from a pinned source presentation interval."""

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from . import generation_frames
from .errors import DomainError
from .media import SAFE_FORMATS, persist_blob, probe_media, resolve_media, run_process
from .production import digest, ref, save
from .store import get_store


def bounds(probe, start, end, cfg, path=None):
    fps = cfg["reference_fps"]
    if start is None or end is None or not probe.get("has_video") or probe.get("is_still"):
        raise DomainError("REFERENCE_SEGMENT_RANGE", "连续参考需要项目视频及起止时码。", 422)
    a, b = Decimal(str(start)), Decimal(str(end))
    count = (b - a) * fps
    frames = int(count.to_integral_value())
    if not (
        0 <= a < b <= Decimal(str(probe.get("duration", 0)))
        and abs(count - frames) <= Decimal("0.000001")
        and 2 <= frames <= cfg["max_reference_frames"]
    ):
        raise DomainError(
            "REFERENCE_SEGMENT_RANGE",
            f"区间须在原片内，并对应 {fps} fps 的 2–{cfg['max_reference_frames']} 个完整帧。",
            422,
        )
    if path is not None:
        # A container may include a longer audio track or start at a nonzero timestamp.
        meta, _ = run_process(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                SAFE_FORMATS,
                "-select_streams",
                "V:0",
                "-show_entries",
                "format=start_time:stream=start_time,duration",
                "-of",
                "json",
                str(path),
            ],
            30,
        )
        meta = json.loads(meta)
        stream = next(iter(meta.get("streams", [])), {})
        first = generation_frames.num(stream.get("start_time"))
        origin = generation_frames.num(meta.get("format", {}).get("start_time"))
        if origin is None:
            origin = first
        length = generation_frames.num(stream.get("duration"))
        if origin is not None and first is not None and length is not None:
            if a + origin < first or b + origin > first + length:
                raise DomainError(
                    "REFERENCE_SEGMENT_RANGE", "连续参考区间须完整落在画面轨内。", 422
                )
        else:
            # Some VFR/WebM streams omit stream duration. Verify the display interval at the last microsecond.
            generation_frames.decode(path, float(b - Decimal("0.000001")))
    return [float(a + Decimal(i) / fps) for i in range(frames)]


def prepare(actor, project, item, blob, path, start, end, cfg):
    fps = cfg["reference_fps"]
    times = bounds(item["data"].get("probe", {}), start, end, cfg)
    store = get_store()
    rid = (
        "assetversion_"
        + digest(
            [
                "generation-segment-v1",
                actor.tenant_id,
                project,
                ref(item),
                blob["data"]["checksum"],
                start,
                end,
                fps,
            ]
        )[:40]
    )

    def cached():
        try:
            record, location, existing = resolve_media(store, actor, rid, project_id=project)
        except DomainError as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise
        proof = record["data"]["generation_reference_segment"]
        if existing.stat().st_size > 50 * 1024 * 1024:
            raise DomainError("REFERENCE_SEGMENT_LIMIT", "参考区间超出上传容量。", 422)
        data = existing.read_bytes()
        if (
            proof["asset_ref"] != ref(item)
            or proof["original_sha256"] != blob["data"]["checksum"]
            or proof["segment_sha256"] != location["data"]["checksum"]
            or sha256(data).hexdigest() != proof["segment_sha256"]
        ):
            raise DomainError("REFERENCE_SEGMENT_CHANGED", "参考区间内容指纹无法复核。", 409)
        return {**proof, "segment_ref": ref(record)}, data

    found = cached()
    if found:
        return found
    bounds(item["data"].get("probe", {}), start, end, cfg, path)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pts = []
        for i, at in enumerate(times):
            png, timing = generation_frames.decode(path, at, display_aspect=True)
            (work / f"{i:03}.png").write_bytes(png)
            pts.append(timing["frame_pts_seconds"])
        outfile = work / "segment.mp4"
        run_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-framerate",
                str(fps),
                "-i",
                str(work / "%03d.png"),
                "-vf",
                "scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease,setsar=1",
                "-filter_threads",
                "1",
                "-an",
                "-c:v",
                "libx264rgb",
                "-pix_fmt",
                "rgb24",
                "-crf",
                "0",
                "-preset",
                "ultrafast",
                "-threads",
                "1",
                "-movflags",
                "+faststart",
                str(outfile),
            ],
            60,
        )
        if outfile.stat().st_size > 50 * 1024 * 1024:
            raise DomainError("REFERENCE_SEGMENT_LIMIT", "参考区间超出上传容量。", 422)
        data = outfile.read_bytes()
        probe = probe_media(outfile)
        stored = persist_blob(outfile, actor.tenant_id)
    proof = {
        "kind": "video_segment",
        "asset_ref": ref(item),
        "original_sha256": blob["data"]["checksum"],
        "source_start_seconds": start,
        "source_end_seconds": end,
        "source_frame_pts_seconds": pts,
        "reference_fps": fps,
        "frames": len(times),
        "duration": len(times) / fps,
        "segment_sha256": sha256(data).hexdigest(),
        "width": probe["width"],
        "height": probe["height"],
        "bytes": len(data),
    }
    with store.transaction():
        found = cached()
        if found:
            return found
        current, _, _ = resolve_media(store, actor, item["id"], project_id=project)
        if ref(current) != ref(item):
            raise DomainError("REFERENCE_REVISION_CHANGED", "原视频修订已变化。", 409)
        location = save(
            actor,
            "BlobLocation",
            project,
            {**stored, "purpose": "generation_reference_segment", "mime_type": "video/mp4"},
            [ref(item)],
        )
        record = store.create(
            "AssetVersion",
            {
                "asset_id": item["id"],
                "variant": "generation_reference_segment",
                "filename": "reference-segment.mp4",
                "blob_location_id": location["id"],
                "content_hash": proof["segment_sha256"],
                "media_type": "video/mp4",
                "status": "available",
                "probe": probe,
                "generation_reference_segment": proof,
            },
            tenant_id=actor.tenant_id,
            project_id=project,
            actor_id=actor.actor_id,
            record_id=rid,
            refs=[ref(item), ref(location)],
            reason="按原视频显示时码冻结连续参考区间",
        )
        return {**proof, "segment_ref": ref(record)}, data
