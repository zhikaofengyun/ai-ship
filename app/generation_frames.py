"""Select a displayed video frame by presentation time for a pinned generation reference."""

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile

from .errors import DomainError
from .media import SAFE_FORMATS, persist_blob, resolve_media, run_process
from .production import digest, ref, save
from .store import get_store


def checked_time(probe, at):
    if (
        not probe.get("has_video")
        or probe.get("is_still")
        or at is None
        or not 0 <= at < probe.get("duration", 0)
    ):
        raise DomainError(
            "REFERENCE_FRAME_RANGE", "请选择有动态画面的项目视频，时码须在素材的半开时段内。", 422
        )


def num(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def decode(path, at):
    probe = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        SAFE_FORMATS,
        "-select_streams",
        "V:0",
    ]
    report, _ = run_process(
        [
            *probe,
            "-show_entries",
            "format=start_time:stream=start_time,duration",
            "-of",
            "json",
            str(path),
        ],
        30,
    )
    metadata = json.loads(report)
    stream = next(iter(metadata.get("streams", [])), {})
    start = num(stream.get("start_time"))
    origin = num(metadata.get("format", {}).get("start_time"))
    if origin is None:
        origin = start
    if origin is None:
        raise DomainError("REFERENCE_FRAME_PTS", "视频缺少可核对的时码原点。", 422)
    end = num(stream.get("duration"))
    requested = Decimal(str(at)) + origin
    if (start is not None and requested < start) or (
        start is not None and end is not None and requested >= start + end
    ):
        raise DomainError("REFERENCE_FRAME_RANGE", "所选时刻超出实际画面轨。", 422)
    # Start at the preceding seek point, decode presentation timestamps and use the image
    # displayed at t. Frame count / nominal FPS is unreliable for VFR camera originals.
    interval = f"{max(origin,requested-Decimal('0.5')):.9f}%{requested+Decimal('1'):.9f}"
    report, _ = run_process(
        [
            *probe,
            "-read_intervals",
            interval,
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,duration_time,pkt_duration_time",
            "-of",
            "json",
            str(path),
        ],
        30,
    )
    times = {}
    for frame in json.loads(report).get("frames", []):
        pts = num(frame.get("best_effort_timestamp_time"))
        if pts is not None:
            times[pts] = num(frame.get("duration_time", frame.get("pkt_duration_time")))
    selected = max((v for v in times if v <= requested), default=None)
    if selected is None:
        raise DomainError("REFERENCE_FRAME_PTS", "无法确定该时刻显示的源帧。", 422)
    upper = min((v for v in times if v > selected), default=None)
    if upper is None and times[selected] is not None:
        upper = selected + times[selected]
    if upper is None or requested >= upper:
        raise DomainError("REFERENCE_FRAME_PTS", "所选时刻没有可核对的显示帧。", 422)
    relative = selected - origin
    png, log = run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-xerror",
            "-copyts",
            "-start_at_zero",
            "-ss",
            f"{max(Decimal(0),relative-Decimal('0.000002')):.9f}",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            SAFE_FORMATS,
            "-i",
            str(path),
            "-map",
            "0:V:0",
            "-map_metadata",
            "-1",
            "-frames:v",
            "1",
            "-filter_threads",
            "1",
            "-vf",
            "showinfo,scale=w='min(4096,iw)':h='min(4096,ih)':force_original_aspect_ratio=decrease",
            "-pix_fmt",
            "rgba",
            "-threads",
            "1",
            "-c:v",
            "png",
            "-f",
            "image2pipe",
            "pipe:1",
        ],
        30,
        True,
    )
    # showinfo prints the PTS in time-base ticks with no rounding.
    m = re.search(r"config in time_base:\s*(\d+)/(\d+)", log)
    pts = re.search(r"\bn:\s*0\s+pts:\s*(-?\d+)\s+pts_time:", log)
    actual = (Decimal(pts[1]) * Decimal(m[1]) / Decimal(m[2])) if m and pts and int(m[2]) else None
    if (
        actual is None
        or abs(relative - actual) > Decimal("0.000002")
        or not png.startswith(b"\x89PNG\r\n\x1a\n")
        or len(png) > 50 * 1024 * 1024
    ):
        raise DomainError("REFERENCE_FRAME_DECODE", "输出 PNG 与所选源帧的显示时间无法对应。", 422)
    return png, {
        "source_time_seconds": at,
        "frame_pts_seconds": float(actual),
        "frame_end_seconds": float(upper - origin),
        "frame_pts_ticks": int(pts[1]),
        "time_base": m[1] + "/" + m[2],
        "source_start_seconds": float(origin),
        "kind": "video_frame",
    }


def pixels(actor, project, item, blob, path, at, *, persist):
    store = get_store()
    rid = (
        "assetversion_"
        + digest(
            [
                "generation-frame-v1",
                actor.tenant_id,
                project,
                ref(item),
                blob["data"]["checksum"],
                at,
            ]
        )[:40]
    )

    def cached():
        try:
            r = store.get(rid, actor.tenant_id)
        except DomainError as e:
            if e.code == "NOT_FOUND":
                return None
            raise
        frame, storage, p = resolve_media(store, actor, r["id"], project_id=project)
        evidence = frame["data"]["generation_reference_frame"]
        if (
            evidence["asset_ref"] != ref(item)
            or evidence["original_sha256"] != blob["data"]["checksum"]
            or storage["data"]["checksum"] != evidence["png_sha256"]
        ):
            raise DomainError("REFERENCE_FRAME_CHANGED", "参考帧来源或字节已变化。", 409)
        if p.stat().st_size > 50 * 1024 * 1024:
            raise DomainError("REFERENCE_IMAGE_LIMIT", "已存参考帧超过容量。", 422)
        png = p.read_bytes()
        if sha256(png).hexdigest() != evidence["png_sha256"]:
            raise DomainError("REFERENCE_FRAME_CHANGED", "参考帧内容指纹无法复核。", 409)
        return {**evidence, "frame_ref": ref(frame)}, png

    found = cached()
    if found:
        return found
    png, timing = decode(path, at)
    evidence = {
        **timing,
        "asset_ref": ref(item),
        "original_sha256": blob["data"]["checksum"],
        "png_sha256": sha256(png).hexdigest(),
        "width": int.from_bytes(png[16:20], "big"),
        "height": int.from_bytes(png[20:24], "big"),
        "bytes": len(png),
    }
    if not persist:
        return evidence, png
    with tempfile.TemporaryDirectory() as tmp:
        private = Path(tmp) / "frame.png"
        private.write_bytes(png)
        data = persist_blob(private, actor.tenant_id)
    with store.transaction():
        found = cached()
        if found:
            return found
        current, _, _ = resolve_media(store, actor, item["id"], project_id=project)
        if current["version"] != item["version"]:
            raise DomainError("REFERENCE_REVISION_CHANGED", "参考素材已变化。", 409)
        storage = save(
            actor,
            "BlobLocation",
            project,
            {**data, "purpose": "generation_reference_frame", "mime_type": "image/png"},
            [ref(item)],
        )
        frame = store.create(
            "AssetVersion",
            {
                "asset_id": item["id"],
                "variant": "generation_reference_frame",
                "filename": "reference-frame.png",
                "blob_location_id": storage["id"],
                "content_hash": evidence["png_sha256"],
                "media_type": "image/png",
                "status": "available",
                "probe": {
                    "is_still": True,
                    "has_video": True,
                    "has_audio": False,
                    "video_codec": "png",
                    "duration": 0,
                    "width": evidence["width"],
                    "height": evidence["height"],
                },
                "generation_reference_frame": evidence,
            },
            tenant_id=actor.tenant_id,
            project_id=project,
            actor_id=actor.actor_id,
            record_id=rid,
            refs=[ref(item), ref(storage)],
            reason="从固定素材实际解码指定时码的画面",
        )
        return {**evidence, "frame_ref": ref(frame)}, png
