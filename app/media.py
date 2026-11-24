"""Real, bounded local media execution; no simulated provider success.

Every stored byte is addressed by its SHA-256 below a tenant-specific root.
FFmpeg commands are argument arrays, with no user-controlled filter expressions.
This adapter proves technical output; factual/content/rights QA remains explicit.
"""

from __future__ import annotations

import array

try:
    import fcntl
except ImportError:  # The supported production host is Linux; Windows users run the container.
    fcntl = None
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from fractions import Fraction
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app import transitions
from app.auth import current_actor, require_write
from app.store import DomainError, get_store

router = APIRouter(prefix="/media", tags=["media"])
MAX_BYTES = 256 * 1024 * 1024
MAX_DURATION = 3600
MAX_CLIPS = 64
MAX_PIXELS = 4096 * 2160
MAX_RENDER_SECONDS = 300
MAX_LINEAGE_RECORDS = 1000
MAX_LINEAGE_DEPTH = 64
MAX_LINEAGE_LINKS = 10000
PROCESS_SLOTS = threading.BoundedSemaphore(2)
SAFE_FORMATS = "mov,matroska,avi,mp3,wav,flac,ogg,aac,image2,png_pipe,jpeg_pipe,webp_pipe,bmp_pipe"
UNSUPPORTED = {
    "watermark_removal",
    "remove_watermark",
    "inpaint",
    "ai_inpaint",
    "frame_interpolation",
    "masks",
    "keyframes",
    "transitions",
    "effects",
    "speed_ramp",
    "reverse",
}
RECIPE_KEYS = {
    "trim_start",
    "trim_end",
    "scale",
    "width",
    "height",
    "brightness",
    "contrast",
    "saturation",
    "gamma",
    "sharpness",
    "rotation",
    "flip_horizontal",
    "flip_vertical",
    "speed",
    "volume",
    "loudness_lufs",
    "true_peak_db",
    "origin",
    "name",
    "template",
    "skip_reasons",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(data: Any, request: Request | None = None) -> dict:
    return {
        "data": data,
        "trace_id": getattr(getattr(request, "state", None), "trace_id", None) or str(uuid.uuid4()),
    }


def root_dir() -> Path:
    path = Path(os.environ.get("AI_SHIP_DATA_DIR", "var")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenant_key(tenant_id: str) -> str:
    return hashlib.sha256(tenant_id.encode()).hexdigest()[:24]


def safe_filename(value: str) -> str:
    value = value.replace("\\", "/")
    p = PurePosixPath(value)
    if (
        not value
        or not p.parts
        or len(value) > 600
        or p.is_absolute()
        or any(part in ("", ".", "..") for part in p.parts)
        or ":" in value
        or "\x00" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise DomainError("INVALID_FILENAME", "文件名必须是无上级跳转的相对路径", 422)
    return str(p)


def number(
    value: Any, name: str, minimum: float, maximum: float, default: float | None = None
) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise DomainError("INVALID_PARAMETER", f"{name} 必须是数值", 422)
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise DomainError("INVALID_PARAMETER", f"{name} 必须是数值", 422)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise DomainError("INVALID_PARAMETER", f"{name} 必须在 {minimum} 至 {maximum} 之间", 422)
    return result


def ticks(value: float) -> int:
    return round(Fraction(str(value)) * 1_000_000)


def ref(record: dict, field: str | None = None) -> dict:
    out = {"id": record["id"], "kind": record["kind"], "version": record["version"]}
    if field:
        out["field"] = field
    return out


def actor_kwargs(actor: Any, project_id: str) -> dict:
    return {"tenant_id": actor.tenant_id, "project_id": project_id, "actor_id": actor.actor_id}


def require_project(store: Any, actor: Any, project_id: str) -> dict:
    project = store.get(project_id, actor.tenant_id)
    if project["kind"] != "Project":
        raise DomainError("INVALID_PROJECT", "project_id 必须引用当前租户的项目", 422)
    return project


def check_revision(record: dict, expected_revision: Any) -> None:
    if expected_revision != record["version"]:
        raise DomainError(
            "REVISION_CONFLICT",
            "对象已变化，请刷新后基于当前版本重新操作",
            409,
            {"expected_revision": expected_revision, "current_revision": record["version"]},
        )


def _run_process_unlimited(
    arguments: list[str], timeout: int = 60, binary_output: bool = False
) -> tuple[Any, str]:
    """Bounded process, no shell; all spawned descendants stop on timeout."""
    executable = shutil.which(arguments[0])
    if executable is None:
        raise DomainError("MEDIA_TOOL_UNAVAILABLE", f"未安装 {arguments[0]}", 503)
    try:
        process = subprocess.Popen(
            [executable, *arguments[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            out, err = process.communicate(timeout=min(max(1, timeout), MAX_RENDER_SECONDS))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise DomainError("MEDIA_TIMEOUT", "媒体执行超过时限，进程已停止；原件未改动", 422)
    except OSError as exc:
        raise DomainError(
            "MEDIA_EXECUTION_FAILED", "无法启动媒体执行器", 503, {"type": type(exc).__name__}
        )
    stderr = err.decode("utf-8", "replace")[-14000:]
    if process.returncode:
        # Only safe excerpts; internal object paths are not needed by clients.
        safe_error = re.sub(re.escape(str(root_dir())), "[media-store]", stderr[-4000:])
        raise DomainError(
            "MEDIA_EXECUTION_FAILED",
            "媒体解析或执行失败，未生成合格产物",
            422,
            {"exit_code": process.returncode, "stderr": safe_error},
        )
    return (out if binary_output else out.decode("utf-8", "replace")), stderr


def run_process(
    arguments: list[str], timeout: int = 60, binary_output: bool = False
) -> tuple[Any, str]:
    if not PROCESS_SLOTS.acquire(timeout=10):
        raise DomainError("MEDIA_BUSY", "本地媒体执行资源已占用，请稍后重试", 429)
    try:
        return _run_process_unlimited(arguments, timeout, binary_output)
    finally:
        PROCESS_SLOTS.release()


def media_mime(probe: dict) -> str:
    if probe.get("is_still"):
        return {
            "png": "image/png",
            "mjpeg": "image/jpeg",
            "webp": "image/webp",
            "bmp": "image/bmp",
        }.get(probe.get("video_codec"), "application/octet-stream")
    fmt = probe.get("format_name") or ""
    if probe.get("has_video"):
        if "matroska" in fmt:
            return (
                "video/webm"
                if probe.get("video_codec") in {"vp8", "vp9", "av1"}
                else "video/x-matroska"
            )
        if "avi" in fmt:
            return "video/x-msvideo"
        return "video/mp4"
    if "mp3" in fmt:
        return "audio/mpeg"
    if "ogg" in fmt:
        return "audio/ogg"
    if "flac" in fmt:
        return "audio/flac"
    if "aac" in fmt:
        return "audio/aac"
    return "audio/wav"


@lru_cache(maxsize=1)
def tool_versions() -> dict:
    versions = {}
    for executable in ("ffmpeg", "ffprobe"):
        try:
            out, _ = run_process([executable, "-version"], 10)
            versions[executable] = out.splitlines()[0]
        except DomainError:
            versions[executable] = None
    return versions


def probe_media(path: Path) -> dict:
    output, _ = run_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            SAFE_FORMATS,
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        30,
    )
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        raise DomainError("INVALID_MEDIA", "无法读取媒体结构", 422)
    streams = raw.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video and not audio:
        raise DomainError("INVALID_MEDIA", "文件没有可解码的视频或音频流", 422)
    candidates = [raw.get("format", {}).get("duration"), *(s.get("duration") for s in streams)]
    duration = max(
        (
            float(x)
            for x in candidates
            if x is not None and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(x))
        ),
        default=0,
    )
    width, height = int((video or {}).get("width", 0)), int((video or {}).get("height", 0))
    if duration > MAX_DURATION or width > 8192 or height > 8192 or width * height > 8192 * 4320:
        raise DomainError("MEDIA_RESOURCE_LIMIT", "媒体时长或尺寸超过当前本地执行器边界", 422)
    is_still = bool(
        video
        and duration == 0
        and (video or {}).get("codec_name") in {"png", "mjpeg", "webp", "bmp"}
    )
    if duration <= 0 and not is_still:
        raise DomainError(
            "UNKNOWN_MEDIA_DURATION", "无法确认完整媒体时长，拒绝把它伪装为静态图片", 422
        )
    # Keep bounded technical metadata, not arbitrarily large/secret embedded tags.
    clean_streams = [
        {
            k: s[k]
            for k in (
                "index",
                "codec_name",
                "codec_type",
                "width",
                "height",
                "pix_fmt",
                "sample_rate",
                "channels",
                "channel_layout",
                "time_base",
                "start_time",
                "duration",
                "r_frame_rate",
                "avg_frame_rate",
                "nb_frames",
            )
            if k in s
        }
        for s in streams[:32]
    ]
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_video": bool(video),
        "has_audio": bool(audio),
        "format_name": raw.get("format", {}).get("format_name"),
        "streams": clean_streams,
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "fps": (video or {}).get("avg_frame_rate", "0/0"),
        "source_time_base": (video or audio or {}).get("time_base"),
        "is_still": is_still,
        "probe_version": tool_versions().get("ffprobe"),
    }


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=256)
def verified_object_hash(path: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    return hash_file(Path(path))


def persist_blob(path: Path, tenant_id: str) -> dict:
    checksum = hash_file(path)
    key = f"objects/{tenant_key(tenant_id)}/{checksum[:2]}/{checksum}"
    destination = root_dir() / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(path, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    elif hash_file(destination) != checksum:
        raise DomainError("OBJECT_CORRUPTION", "对象存储的既有字节校验失败", 409)
    return {
        "object_key": key,
        "checksum": checksum,
        "content_hash": checksum,
        "size_bytes": destination.stat().st_size,
        "storage_backend": "local_content_addressed",
        "availability": "available",
        "verified_at": now(),
    }


def blob_path(blob: dict, tenant_id: str) -> Path:
    key = blob["data"].get("object_key", "")
    prefix = f"objects/{tenant_key(tenant_id)}/"
    if not isinstance(key, str) or not key.startswith(prefix) or ".." in PurePosixPath(key).parts:
        raise DomainError("INVALID_OBJECT_LOCATION", "对象位置与当前租户不匹配", 403)
    path = (root_dir() / key).resolve()
    base = (root_dir() / "objects" / tenant_key(tenant_id)).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise DomainError("OBJECT_UNAVAILABLE", "对象字节不可用，不能假装缓存命中", 404)
    return path


def check_current_media_rights(
    store: Any, actor: Any, record: dict, purpose: str = "process"
) -> None:
    """Check current restrictions along *actual pinned* inputs, without granting a license.

    Private technical processing of an unverified upload is still an unapproved
    draft. An absent expiry is unknown, not expired or authorized. Inspection of
    expired originals is permitted for audit; revocation forbids byte access.
    Old manifests do not grandfather a newly expired or revoked source/grant.
    """
    if purpose not in {"inspect", "process", "export"}:
        raise DomainError("INVALID_MEDIA_PURPOSE", "无效的媒体使用目的", 422)
    pending = deque([(record, 0)])
    queued = {(record["id"], record["version"])}
    visited: set[tuple[str, int]] = set()
    current_by_id: dict[str, dict] = {}
    traversed_links = 0
    blocked = {"revoked", "deleted", "deleting", "restricted", "denied"}
    explicit_links = {
        "source_id": {"Source"},
        "asset_id": {"Asset"},
        "asset_version_id": {"AssetVersion"},
        "parent_asset_version_ids": {"AssetVersion"},
        "parent_audio_version_id": {"Audio"},
        "parent_artifact_id": {"Artifact"},
        "grant_id": {"Grant"},
        "grant_ids": {"Grant"},
        "rights_grant_ref": {"Grant"},
        "rights_grant_ids": {"Grant"},
        "provenance_sources": {"Source"},
    }
    checked_at = datetime.now(timezone.utc)

    def boundary(value: Any, field: str, owner: dict) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            if not isinstance(value, str):
                raise ValueError("not an ISO datetime")
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("timezone required")
            return stamp.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            raise DomainError(
                "MEDIA_RIGHTS_INVALID",
                "授权有效期必须是带时区的 ISO 时间，无法确认时不继续制作",
                409,
                {"record_id": owner["id"], "field": field},
            )

    while pending:
        pinned, depth = pending.popleft()
        identity = (pinned["id"], pinned["version"])
        queued.discard(identity)
        if identity in visited:
            continue
        if depth > MAX_LINEAGE_DEPTH or len(visited) >= MAX_LINEAGE_RECORDS:
            raise DomainError(
                "MEDIA_RIGHTS_GRAPH_LIMIT",
                "媒体来源图超过核验上限，未将未检查的来源当作可用",
                409,
                {"max_records": MAX_LINEAGE_RECORDS, "max_depth": MAX_LINEAGE_DEPTH},
            )
        visited.add(identity)
        current = current_by_id.get(pinned["id"])
        if current is None:
            current = store.get(pinned["id"], actor.tenant_id)
            current_by_id[pinned["id"]] = current
        data = current["data"]
        statuses = {str(data.get("status", "")).lower(), str(data.get("rights_status", "")).lower()}
        if statuses & blocked or data.get("revoked_at") or data.get("deleted_at"):
            raise DomainError(
                "MEDIA_REVOKED",
                "该媒体或其实际来源链已经撤权、删除或受限",
                403,
                {"record_id": current["id"], "kind": current["kind"]},
            )
        if purpose != "inspect":
            if "expired" in statuses:
                raise DomainError(
                    "MEDIA_RIGHTS_EXPIRED",
                    "该媒体来源或授权已到期，不能继续制作或导出",
                    403,
                    {"record_id": current["id"]},
                )
            if current["kind"] in {"Source", "Grant"}:
                validity = data.get("validity")
                validity = validity if isinstance(validity, dict) else {}
                expiry_values = {
                    "expires_at": data.get("expires_at"),
                    "valid_until": data.get("valid_until"),
                    "validity.valid_until": validity.get("valid_until"),
                }
                for field, value in expiry_values.items():
                    expires = boundary(value, field, current)
                    if expires is not None and expires <= checked_at:
                        raise DomainError(
                            "MEDIA_RIGHTS_EXPIRED",
                            "该媒体实际来源的当前授权已经到期，不能继续制作或导出",
                            403,
                            {
                                "record_id": current["id"],
                                "field": field,
                                "expires_at": expires.isoformat(),
                            },
                        )
                for field, value in {
                    "valid_from": data.get("valid_from"),
                    "validity.valid_from": validity.get("valid_from"),
                }.items():
                    starts = boundary(value, field, current)
                    if starts is not None and starts > checked_at:
                        raise DomainError(
                            "MEDIA_RIGHTS_NOT_ACTIVE",
                            "来源授权尚未生效，不能开始制作",
                            403,
                            {"record_id": current["id"], "field": field},
                        )
                scope = data.get("processing_scope")
                if scope is False or (isinstance(scope, dict) and scope.get("allowed") is False):
                    raise DomainError(
                        "MEDIA_PROCESSING_DENIED",
                        "该来源明确禁止本地制作处理",
                        403,
                        {"record_id": current["id"]},
                    )

        # Frozen creative dependencies stay frozen. Rights objects use their
        # current references so that a later attached grant cannot be bypassed.
        owner = current if current["kind"] in {"Source", "Grant"} else pinned
        links = [dict(r) for r in owner.get("refs", [])]
        linked_ids = {r["id"] for r in links}
        for field, kinds in explicit_links.items():
            raw = owner["data"].get(field)
            for value in raw if isinstance(raw, list) else [raw]:
                link = value if isinstance(value, dict) else {"id": value}
                if not link.get("id") or link["id"] in linked_ids:
                    continue
                links.append({**link, "expected_kinds": kinds})
                linked_ids.add(link["id"])
        for link in links:
            traversed_links += 1
            if traversed_links > MAX_LINEAGE_LINKS:
                raise DomainError("MEDIA_RIGHTS_GRAPH_LIMIT", "媒体来源图的引用边超过核验上限", 409)
            target = store.get(link["id"], actor.tenant_id, version=link.get("version"))
            if link.get("expected_kinds") and target["kind"] not in link["expected_kinds"]:
                raise DomainError("INVALID_RIGHTS_REFERENCE", "媒体授权链中的引用类型不正确", 409)
            # A project is a scope, not a dependency on every later project edit.
            target_identity = (target["id"], target["version"])
            if (
                target["kind"] != "Project"
                and target_identity not in visited
                and target_identity not in queued
            ):
                if len(visited) + len(queued) >= MAX_LINEAGE_RECORDS:
                    raise DomainError(
                        "MEDIA_RIGHTS_GRAPH_LIMIT",
                        "媒体来源图超过核验上限，未检查的来源不能进入制作",
                        409,
                    )
                pending.append((target, depth + 1))
                queued.add(target_identity)


def resolve_media(
    store: Any,
    actor: Any,
    media_id: str,
    version_id: str | None = None,
    project_id: str | None = None,
    *,
    purpose: str = "process",
) -> tuple[dict, dict, Path]:
    record = store.get(version_id or media_id, actor.tenant_id)
    if record["kind"] not in {"Asset", "AssetVersion", "Audio", "Artifact"}:
        raise DomainError("INVALID_MEDIA_REFERENCE", "引用必须是素材、素材版本、音频或成片", 422)
    if project_id and record.get("project_id") != project_id:
        raise DomainError("PROJECT_SCOPE_MISMATCH", "媒体属于不同项目，请显式复制或授权后使用", 403)
    if version_id and record["data"].get("asset_id") != media_id:
        raise DomainError("INVALID_MEDIA_REFERENCE", "素材版本不属于选定素材", 422)
    check_current_media_rights(store, actor, record, purpose)
    blob_id = record["data"].get("blob_location_id")
    if not blob_id:
        raise DomainError("OBJECT_UNAVAILABLE", "媒体没有真实对象位置", 409)
    blob = store.get(blob_id, actor.tenant_id)
    if blob["kind"] != "BlobLocation":
        raise DomainError("INVALID_OBJECT_LOCATION", "媒体落点不是 BlobLocation", 409)
    path = blob_path(blob, actor.tenant_id)
    expected_hash = record["data"].get("content_hash") or blob["data"].get("checksum")
    stat = path.stat()
    if (
        expected_hash
        and verified_object_hash(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        != expected_hash
    ):
        raise DomainError("OBJECT_CORRUPTION", "对象内容已变化，校验失败", 409)
    return record, blob, path


def import_saved_file(
    store: Any,
    actor: Any,
    project_id: str,
    filename: str,
    path: Path,
    source_note: str = "",
    *,
    import_entry: dict | None = None,
    import_batch: dict | None = None,
    generated_by: dict | None = None,
    held_from: dict | None = None,
) -> dict:
    require_project(store, actor, project_id)
    filename = safe_filename(filename)
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise DomainError("UPLOAD_SIZE_LIMIT", "文件为空或超过 256 MiB 单文件限制", 413)
    checksum = hash_file(path)
    kwargs = actor_kwargs(actor, project_id)
    try:
        probe = probe_media(path)
    except DomainError as error:
        with store.transaction():
            batch = import_batch or store.create(
                "ImportBatch",
                {
                    "name": Path(filename).name,
                    "intake_mode": (
                        "still_hold"
                        if held_from
                        else ("generated_candidate" if generated_by else "browser_file_selection")
                    ),
                    "status": "failed",
                    "file_count": 1,
                },
                **kwargs,
            )
            failed_data = {
                "import_batch_id": batch["id"],
                "relative_path": filename,
                "size_bytes": size,
                "content_hash": checksum,
                "transferred_bytes": size,
                "status": "failed",
                "error": {
                    "code": error.code if hasattr(error, "code") else "INVALID_MEDIA",
                    "message": "媒体探测失败",
                },
                "read_complete_at": now(),
            }
            if import_entry:
                current_entry = store.get(import_entry["id"], actor.tenant_id)
                entry = store.update(
                    import_entry["id"],
                    {**current_entry["data"], **failed_data},
                    tenant_id=actor.tenant_id,
                    expected_revision=current_entry["version"],
                    actor_id=actor.actor_id,
                    refs=[ref(batch)],
                )
                current_batch = store.get(batch["id"], actor.tenant_id)
                store.update(
                    batch["id"],
                    {**current_batch["data"], "status": "failed"},
                    tenant_id=actor.tenant_id,
                    expected_revision=current_batch["version"],
                    actor_id=actor.actor_id,
                )
            else:
                entry = store.create("ImportEntry", failed_data, refs=[ref(batch)], **kwargs)
        raise DomainError(
            "INVALID_MEDIA",
            "文件完整接收但媒体探测失败；失败记录已保留",
            422,
            {"import_entry_id": entry["id"]},
        )
    prior = next(
        (
            r
            for r in store.list(
                actor.tenant_id, kind="Asset", project_id=project_id, q=checksum, limit=2000
            )
            if r["data"].get("content_hash") == checksum
        ),
        None,
    )
    blob_data = persist_blob(path, actor.tenant_id)
    mimetype = media_mime(probe)
    with store.transaction():
        prior = next(
            (
                r
                for r in store.list(
                    actor.tenant_id, kind="Asset", project_id=project_id, q=checksum, limit=2000
                )
                if r["data"].get("content_hash") == checksum
            ),
            None,
        )
        batch = import_batch or store.create(
            "ImportBatch",
            {
                "name": Path(filename).name,
                "intake_mode": (
                    "still_hold"
                    if held_from
                    else ("generated_candidate" if generated_by else "browser_file_selection")
                ),
                "status": "completed",
                "file_count": 1,
                "completed_count": 1,
            },
            **kwargs,
        )
        source = store.create(
            "Source",
            {
                "kind": (
                    "timed_still_derivative"
                    if held_from
                    else ("generated_candidate" if generated_by else "local_upload")
                ),
                "relative_path": filename,
                "original_url": None,
                "rights_status": "unverified",
                "rights_note": source_note[:2000],
                "content_hash": checksum,
                "source_note_is_evidence": False,
                **({"generation_call_ref": ref(generated_by)} if generated_by else {}),
                **({"still_source_ref": ref(held_from)} if held_from else {}),
            },
            refs=[ref(r) for r in (generated_by, held_from) if r],
            **kwargs,
        )
        if prior:
            asset = prior
            version = next(
                (
                    r
                    for r in store.list(
                        actor.tenant_id,
                        kind="AssetVersion",
                        project_id=project_id,
                        q=prior["id"],
                        limit=2000,
                    )
                    if r["data"].get("asset_id") == prior["id"]
                    and r["data"].get("variant") == "original"
                ),
                None,
            )
            if not version:
                raise DomainError(
                    "INCOMPLETE_ASSET", "既有素材缺少原件版本，需要修复后才能复用", 409
                )
            blob_id = asset["data"]["blob_location_id"]
        else:
            blob = store.create(
                "BlobLocation",
                {**blob_data, "mime_type": mimetype, "purpose": "original"},
                refs=[ref(source)],
                **kwargs,
            )
            blob_id = blob["id"]
            asset = store.create(
                "Asset",
                {
                    "name": Path(filename).name,
                    "filename": filename,
                    "content_hash": checksum,
                    "size_bytes": size,
                    "media_type": mimetype,
                    "probe": probe,
                    "blob_location_id": blob_id,
                    "source_id": source["id"],
                    "status": "available",
                    "rights_status": source["data"]["rights_status"],
                },
                refs=[ref(source), ref(blob)],
                **kwargs,
            )
            version = store.create(
                "AssetVersion",
                {
                    "asset_id": asset["id"],
                    "variant": "original",
                    "filename": filename,
                    "content_hash": checksum,
                    "blob_location_id": blob_id,
                    "probe": probe,
                    "status": "available",
                    "parameter_origins": {},
                    "original_immutable": True,
                },
                refs=[ref(asset), ref(blob), ref(source)],
                **kwargs,
            )
        entry_data = {
            "import_batch_id": batch["id"],
            "relative_path": filename,
            "size_bytes": size,
            "content_hash": checksum,
            "transferred_bytes": size,
            "chunk_manifest": (
                import_entry["data"].get("chunk_manifest", [])
                if import_entry
                else [{"offset": 0, "size": size, "checksum": checksum, "status": "verified"}]
            ),
            "status": "deduplicated" if prior else "completed",
            "asset_id": asset["id"],
            "source_id": source["id"],
            "blob_location_id": blob_id,
            "read_complete_at": now(),
        }
        if import_entry:
            current_entry = store.get(import_entry["id"], actor.tenant_id)
            entry = store.update(
                import_entry["id"],
                {**current_entry["data"], **entry_data},
                tenant_id=actor.tenant_id,
                expected_revision=current_entry["version"],
                actor_id=actor.actor_id,
                refs=[ref(batch), ref(source), ref(asset), ref(version)],
            )
            current_batch = store.get(batch["id"], actor.tenant_id)
            store.update(
                batch["id"],
                {**current_batch["data"], "status": "completed", "completed_count": 1},
                tenant_id=actor.tenant_id,
                expected_revision=current_batch["version"],
                actor_id=actor.actor_id,
            )
        else:
            entry = store.create(
                "ImportEntry",
                entry_data,
                refs=[ref(batch), ref(source), ref(asset), ref(version)],
                **kwargs,
            )
    return {
        "asset": asset,
        "version": version,
        "probe": probe,
        "duplicate": bool(prior),
        "import_entry": entry,
        "preview_url": f"/api/v1/media/{asset['id']}/file",
        "capability_notes": [
            "完整文件校验；raw import 中断需重传，可用 /media/uploads 分块续传。后台文件监视未启用。"
        ],
    }


def audio_tempo(speed: float) -> str:
    parts = []
    while speed > 2:
        parts.append("atempo=2")
        speed /= 2
    while speed < 0.5:
        parts.append("atempo=0.5")
        speed /= 0.5
    parts.append(f"atempo={speed:.8f}")
    return ",".join(parts)


def even_dimension(value: Any, name: str, default: int) -> int:
    numeric = number(value, name, 16, 4096, default)
    if numeric != int(numeric):
        raise DomainError("INVALID_DIMENSIONS", f"{name} 必须是整数像素尺寸", 422)
    result = int(numeric)
    if result % 2:
        raise DomainError("INVALID_DIMENSIONS", f"{name} 必须是偶数，以便输出 yuv420p", 422)
    return result


def assert_supported(data: dict, allowed: set | None = None) -> None:
    unsupported = [
        key
        for key in UNSUPPORTED
        if key not in (allowed or set()) and data.get(key) not in (None, False, [], {})
    ]
    if unsupported:
        raise DomainError(
            "MEDIA_CAPABILITY_UNAVAILABLE",
            "当前本地渲染器不支持这些效果，设计已保留但不会静默忽略",
            422,
            {"unsupported": unsupported},
        )


def validate_recipe(recipe: Any, probe: dict) -> dict:
    if not isinstance(recipe, dict):
        raise DomainError("INVALID_RECIPE", "recipe 必须是对象", 422)
    assert_supported(recipe)
    extra = set(recipe) - RECIPE_KEYS
    if extra:
        raise DomainError(
            "UNKNOWN_RECIPE_FIELDS", "配方含当前执行器未定义的参数", 422, {"fields": sorted(extra)}
        )
    result = dict(recipe)
    ranges = {
        "scale": (1, 4, 1),
        "brightness": (-1, 1, 0),
        "contrast": (0, 3, 1),
        "saturation": (0, 3, 1),
        "gamma": (0.1, 10, 1),
        "sharpness": (0, 2, 0),
        "speed": (0.25, 4, 1),
        "volume": (0, 4, 1),
    }
    for name, (minimum, maximum, default) in ranges.items():
        result[name] = number(recipe.get(name), name, minimum, maximum, default)
    if recipe.get("template") == "atlas_preprocess" and not 1.011 <= result["scale"] <= 1.022:
        raise DomainError(
            "ATLAS_TEMPLATE_SCALE",
            "原文预处理模板缩放必须在 1.011–1.022 之间；自由变换需另选模板",
            422,
        )
    duration = probe["duration"]
    if duration <= 0:
        raise DomainError(
            "STILL_TRANSFORM_UNAVAILABLE",
            "静态图片请放入时间线并明确展示时长；素材配方仅处理有时长的媒体",
            422,
        )
    result["trim_start"] = number(recipe.get("trim_start"), "trim_start", 0, duration, 0)
    result["trim_end"] = number(recipe.get("trim_end"), "trim_end", 0, duration, duration)
    if result["trim_end"] - result["trim_start"] < 0.04:
        raise DomainError("INVALID_TRIM", "保留区间必须为正且至少 0.04 秒", 422)
    result["rotation"] = number(recipe.get("rotation"), "rotation", 0, 270, 0)
    if result["rotation"] not in (0, 90, 180, 270):
        raise DomainError("INVALID_ROTATION", "当前支持 0/90/180/270 度旋转", 422)
    for key in ("flip_horizontal", "flip_vertical"):
        if key in recipe and not isinstance(recipe[key], bool):
            raise DomainError("INVALID_PARAMETER", f"{key} 必须是布尔值", 422)
    if "loudness_lufs" in recipe:
        if not probe["has_audio"]:
            raise DomainError("NO_AUDIO", "无音轨素材不能做响度归一", 422)
        result["loudness_lufs"] = number(recipe["loudness_lufs"], "loudness_lufs", -70, -5)
        result["true_peak_db"] = number(recipe.get("true_peak_db"), "true_peak_db", -9, 0, -1)
    if probe["has_video"]:
        w, h = probe["width"], probe["height"]
        if result["rotation"] in (90, 270):
            w, h = h, w
        result["width"] = even_dimension(recipe.get("width"), "width", w - w % 2)
        result["height"] = even_dimension(recipe.get("height"), "height", h - h % 2)
        if result["width"] * result["height"] > MAX_PIXELS:
            raise DomainError("MEDIA_RESOURCE_LIMIT", "输出总像素超过本地执行器边界", 422)
    return result


def transform_filters(recipe: dict, probe: dict) -> tuple[str | None, str | None]:
    start, end, speed = recipe["trim_start"], recipe["trim_end"], recipe["speed"]
    video = [f"trim=start={start:.9f}:end={end:.9f}", f"setpts=(PTS-STARTPTS)/{speed:.9f}"]
    rotation = recipe["rotation"]
    if rotation == 90:
        video.append("transpose=1")
    elif rotation == 270:
        video.append("transpose=2")
    elif rotation == 180:
        video.extend(["hflip", "vflip"])
    if recipe.get("flip_horizontal"):
        video.append("hflip")
    if recipe.get("flip_vertical"):
        video.append("vflip")
    if probe["has_video"]:
        w, h = recipe["width"], recipe["height"]
        video.append(
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        if recipe["scale"] != 1:
            scale = recipe["scale"]
            video.append(
                f"scale=trunc(iw*{scale:.9f}/2)*2:trunc(ih*{scale:.9f}/2)*2,crop={w}:{h}:(iw-ow)/2:(ih-oh)/2"
            )
        video.append(
            f"eq=brightness={recipe['brightness']}:contrast={recipe['contrast']}:saturation={recipe['saturation']}:gamma={recipe['gamma']}"
        )
        if recipe["sharpness"]:
            video.append(f"unsharp=5:5:{recipe['sharpness']}:5:5:0")
        video.append("format=yuv420p")
    audio = [
        f"atrim=start={start:.9f}:end={end:.9f}",
        "asetpts=PTS-STARTPTS",
        audio_tempo(speed),
        f"volume={recipe['volume']:.9f}",
    ]
    if "loudness_lufs" in recipe:
        audio.append(
            f"loudnorm=I={recipe['loudness_lufs']}:TP={recipe['true_peak_db']}:LRA=11:print_format=json"
        )
    else:
        # Explicit ceiling preserves safety when gain is raised; not a claimed loudness target.
        audio.append("alimiter=limit=0.98:level=false")
    audio.append("aresample=48000")
    return (
        ",".join(video) if probe["has_video"] else None,
        ",".join(audio) if probe["has_audio"] else None,
    )


def technical_qc(path: Path, expected: dict, duration: float) -> dict:
    probe = probe_media(path)
    checks = {
        "bytes_present": path.stat().st_size > 0,
        "duration_positive": probe["duration"] > 0,
        "duration_within_tolerance": abs(probe["duration"] - duration)
        <= max(0.25, 2 / float(expected.get("fps", 25))),
    }
    if expected.get("has_video", True):
        checks["dimensions"] = (
            probe["width"] == expected["width"] and probe["height"] == expected["height"]
        )
    if expected.get("has_audio") is not None:
        checks["audio_presence"] = probe["has_audio"] == expected["has_audio"]
    # Decode full output to detect corrupted packets; a successful mux is insufficient.
    run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ],
        min(MAX_RENDER_SECONDS, max(30, int(duration * 2))),
    )
    checks["full_decode"] = True
    passed = all(checks.values())
    if not passed:
        raise DomainError(
            "MEDIA_QC_FAILED", "实际输出未通过完整技术校验", 422, {"checks": checks, "probe": probe}
        )
    return {
        "status": "technical_passed",
        "checks": checks,
        "probe": probe,
        "content_review_required": True,
        "publish_ready": False,
        "not_checked": [
            "商品事实与数量",
            "来源和使用许可",
            "字幕语义和可读性",
            "审美与场景连续性",
            "人工指定音画语义锚点",
        ],
    }


def transform_asset(store: Any, actor: Any, asset_id: str, body: dict) -> dict:
    project_id = body.get("project_id")
    require_project(store, actor, project_id)
    source, source_blob, path = resolve_media(
        store, actor, asset_id, body.get("asset_version_id"), project_id
    )
    if source["kind"] not in {"Asset", "AssetVersion", "Audio"}:
        raise DomainError(
            "INVALID_TRANSFORM_SOURCE", "成片需先导入素材库；当前配方只处理素材版本和音频", 422
        )
    original_asset_id = (
        source["id"] if source["kind"] == "Asset" else source["data"].get("asset_id")
    )
    derivative_kind = "Audio" if source["kind"] == "Audio" else "AssetVersion"
    check_revision(store.get(asset_id, actor.tenant_id), body.get("expected_revision"))
    probe = probe_media(path)
    recipe = validate_recipe(body.get("recipe", {}), probe)
    duration = (recipe["trim_end"] - recipe["trim_start"]) / recipe["speed"]
    kwargs = actor_kwargs(actor, project_id)
    recipe_record = store.create(
        "TransformRecipe",
        {
            "name": recipe.get("name", "逐素材处理配方"),
            "ordered_steps": [
                "trim",
                "retime",
                "rotate",
                "resize",
                "crop_scale",
                "color",
                "sharpen",
                "audio_gain",
                "loudness_or_peak_limit",
            ],
            "effective_parameters": recipe,
            "parameter_origins": {
                k: "user" if k in body.get("recipe", {}) else "adapter_identity_default"
                for k in recipe
            },
            "recipe_hash": hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest(),
            "intent_mode": "source_order_preserving",
        },
        refs=[ref(source)],
        **kwargs,
    )
    transform_run = store.create(
        "TransformRun",
        {
            "status": "running",
            "recipe_id": recipe_record["id"],
            "source_ref": ref(source),
            "started_at": now(),
            "tool_versions": tool_versions(),
        },
        refs=[ref(recipe_record), ref(source)],
        **kwargs,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="ai-ship-transform-") as temp:
            output = Path(temp) / ("output.mp4" if probe["has_video"] else "output.wav")
            vf, af = transform_filters(recipe, probe)
            args = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "info",
                "-threads",
                "2",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
            ]
            if vf:
                args += [
                    "-map",
                    "0:v:0",
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-threads",
                    "2",
                ]
            if af:
                args += [
                    "-map",
                    "0:a:0",
                    "-af",
                    af,
                    "-c:a",
                    "aac" if probe["has_video"] else "pcm_s16le",
                    "-ar",
                    "48000",
                ]
            else:
                args += ["-an"]
            if probe["has_video"]:
                args += ["-movflags", "+faststart", "-pix_fmt", "yuv420p"]
            args += ["-t", f"{duration:.9f}", "-fs", str(MAX_BYTES), str(output)]
            _, stderr = run_process(args, min(MAX_RENDER_SECONDS, max(60, int(duration * 4))))
            qc = technical_qc(
                output,
                {
                    "has_video": probe["has_video"],
                    "has_audio": probe["has_audio"],
                    "width": recipe.get("width"),
                    "height": recipe.get("height"),
                },
                duration,
            )
            blob_data = persist_blob(output, actor.tenant_id)
        # Recheck CAS after expensive work; an intervening revocation/lock change is not overwritten.
        latest_source = store.get(asset_id, actor.tenant_id)
        check_revision(latest_source, body.get("expected_revision"))
        if latest_source["data"].get("status") in {"revoked", "deleted", "restricted"}:
            raise DomainError("MEDIA_REVOKED", "执行期间素材使用权变化，产物不能提交", 403)
        loudness_match = re.findall(r'\{\s*"input_i".*?\}', stderr, re.S)
        loudness = json.loads(loudness_match[-1]) if loudness_match else None
        with store.transaction():
            check_revision(store.get(asset_id, actor.tenant_id), body.get("expected_revision"))
            resolve_media(store, actor, asset_id, body.get("asset_version_id"), project_id)
            blob = store.create(
                "BlobLocation",
                {
                    **blob_data,
                    "purpose": "derivative",
                    "mime_type": "video/mp4" if probe["has_video"] else "audio/wav",
                },
                refs=[ref(source)],
                **kwargs,
            )
            version = store.create(
                derivative_kind,
                {
                    "asset_id": original_asset_id,
                    "variant": "derivative",
                    "filename": "derived.mp4" if probe["has_video"] else "derived.wav",
                    "blob_location_id": blob["id"],
                    "content_hash": blob_data["checksum"],
                    "probe": qc["probe"],
                    "parent_asset_version_ids": (
                        [source["id"]] if source["kind"] == "AssetVersion" else []
                    ),
                    "parent_audio_version_id": source["id"] if source["kind"] == "Audio" else None,
                    "input_refs": [ref(source)],
                    "effective_parameters": recipe,
                    "parameter_origins": recipe_record["data"]["parameter_origins"],
                    "tool_versions": tool_versions(),
                    "qc": qc,
                    "status": "available",
                },
                refs=[ref(source), ref(blob), ref(recipe_record)],
                **kwargs,
            )
            time_map = store.create(
                "TimeMap",
                {
                    "input_refs": [ref(source)],
                    "output_ref": ref(version),
                    "source_timebase": {source["id"]: {"num": 1, "den": 1000000}},
                    "target_timebase": {"num": 1, "den": 1000000},
                    "mapping_stage": "realized",
                    "mapping_quality": "approximate",
                    "tolerance_seconds": None,
                    "timing_boundary": "Normalized microsecond edit intervals; full source VFR PTS correspondence is not verified",
                    "intent_mode": "source_order_preserving",
                    "segments": [
                        {
                            "segment_id": "segment-1",
                            "output_interval": [0, ticks(duration)],
                            "map_kind": "forward_linear" if recipe["speed"] != 1 else "cut",
                            "mapping_quality": "approximate",
                            "contributions": [
                                {
                                    "source_ref": ref(source),
                                    "source_interval": [
                                        ticks(recipe["trim_start"]),
                                        ticks(recipe["trim_end"]),
                                    ],
                                    "mapping_curve": {
                                        "kind": "linear",
                                        "source_start_tick": ticks(recipe["trim_start"]),
                                        "speed": recipe["speed"],
                                    },
                                    "role": "visual" if probe["has_video"] else "audio",
                                    "precision": "approximate",
                                }
                            ],
                        }
                    ],
                },
                refs=[ref(source), ref(version)],
                **kwargs,
            )
            transform_run = store.update(
                transform_run["id"],
                {
                    **transform_run["data"],
                    "status": "succeeded",
                    "output_ref": ref(version),
                    "time_map_id": time_map["id"],
                    "qc": qc,
                    "loudness_measurement": loudness,
                    "finished_at": now(),
                },
                tenant_id=actor.tenant_id,
                expected_revision=transform_run["version"],
                actor_id=actor.actor_id,
                refs=[ref(source), ref(version), ref(time_map), ref(recipe_record)],
                reason="实际字节与完整技术校验通过",
            )
        return {
            "version": version,
            "recipe": recipe_record,
            "run": transform_run,
            "time_map": time_map,
            "qc": qc,
            "preview_url": f"/api/v1/media/{version['id']}/file",
        }
    except Exception as exc:
        current_run = store.get(transform_run["id"], actor.tenant_id)
        if current_run["data"].get("status") != "succeeded":
            store.update(
                current_run["id"],
                {
                    **current_run["data"],
                    "status": "failed",
                    "error_code": getattr(exc, "code", "MEDIA_EXECUTION_FAILED"),
                    "finished_at": now(),
                },
                tenant_id=actor.tenant_id,
                expected_revision=current_run["version"],
                actor_id=actor.actor_id,
                reason="真实媒体执行失败，原件保留",
            )
        raise


def normalize_keyframes(raw: Any, duration: float) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict) or set(raw) - {"opacity", "x", "y"}:
        raise DomainError(
            "KEYFRAME_CAPABILITY_UNAVAILABLE", "当前实际渲染只支持 opacity/x/y 线性关键帧", 422
        )
    result = {}
    for prop, points in raw.items():
        if not isinstance(points, list) or not 1 <= len(points) <= 128:
            raise DomainError("INVALID_KEYFRAME", "每条关键帧曲线需要 1–128 个点", 422)
        normalized = []
        for point in points:
            if not isinstance(point, dict) or set(point) - {"time", "value", "easing"}:
                raise DomainError("INVALID_KEYFRAME", "关键帧只接受 time/value/easing", 422)
            if point.get("easing", "linear") != "linear":
                raise DomainError(
                    "KEYFRAME_EASING_UNAVAILABLE", "当前渲染仅支持 linear；其他曲线保留草稿", 422
                )
            normalized.append(
                {
                    "time": number(point.get("time"), "keyframe.time", 0, duration),
                    "value": number(
                        point.get("value"),
                        f"keyframe.{prop}",
                        0 if prop == "opacity" else -4096,
                        1 if prop == "opacity" else 4096,
                    ),
                    "easing": "linear",
                }
            )
        normalized.sort(key=lambda point: point["time"])
        if len({p["time"] for p in normalized}) != len(normalized):
            raise DomainError("KEYFRAME_CONFLICT", "同一曲线同一时刻不可重复写入", 422)
        result[prop] = normalized
    return result


def keyframe_expression(points: list[dict], variable: str) -> str:
    # Values are validated finite numbers. The filter expression is generated,
    # never accepted as arbitrary text from the caller.
    expression = f"{points[-1]['value']:.9f}"
    for left, right in reversed(list(zip(points, points[1:]))):
        interpolation = f"({left['value']:.9f}+({right['value'] - left['value']:.9f})*(({variable})-{left['time']:.9f})/{right['time'] - left['time']:.9f})"
        expression = f"if(lt(({variable}),{right['time']:.9f}),{interpolation},{expression})"
    return f"if(lt(({variable}),{points[0]['time']:.9f}),{points[0]['value']:.9f},{expression})"


def normalize_clip(
    store: Any, actor: Any, project_id: str, clip: dict, ordinal: int, audio_only: bool = False
) -> dict:
    if not isinstance(clip, dict):
        raise DomainError("INVALID_CLIP", "片段必须是对象", 422)
    assert_supported(clip, allowed={"keyframes"})
    unsupported_visual = [
        key
        for key in (
            "rotation",
            "brightness",
            "contrast",
            "saturation",
            "gamma",
            "sharpness",
            "scale",
            "mask",
        )
        if key in clip
    ]
    if unsupported_visual:
        raise DomainError(
            "CLIP_EFFECT_UNAVAILABLE",
            "请先把这些画面处理保存为实际衍生素材，再引用到时间线",
            422,
            {"fields": unsupported_visual},
        )
    transition = transitions.transition_spec(clip.get("transition_in"))
    if audio_only and transition:
        raise DomainError("INVALID_TRANSITION", "画面转场不能挂在独立声音轨上")
    media_id = clip.get("asset_id") or clip.get("source_id")
    if not media_id:
        source_ref = clip.get("source_ref", {})
        media_id = source_ref.get("id") if isinstance(source_ref, dict) else source_ref
    if not media_id:
        raise DomainError("MISSING_MEDIA", "片段缺少真实素材引用", 422, {"clip": ordinal})
    record, blob, path = resolve_media(
        store, actor, media_id, clip.get("asset_version_id"), project_id
    )
    probe = probe_media(path)
    if audio_only and not probe["has_audio"]:
        raise DomainError("NO_AUDIO", "音频轨引用的素材没有音轨", 422, {"clip": ordinal})
    if not audio_only and not probe["has_video"]:
        raise DomainError("NO_VIDEO", "画面轨引用的素材没有视频流", 422, {"clip": ordinal})
    source_range = clip.get("source_range", [0, probe["duration"]])
    if isinstance(source_range, dict):
        source_range = [source_range.get("start", 0), source_range.get("end", probe["duration"])]
    if not isinstance(source_range, list) or len(source_range) != 2:
        raise DomainError("INVALID_CLIP", "source_range 必须是 [start,end]", 422)
    available_duration = probe["duration"] or MAX_DURATION
    source_in = number(clip.get("source_in", source_range[0]), "source_in", 0, available_duration)
    source_out = number(
        clip.get("source_out", source_range[1] or clip.get("duration", 0)),
        "source_out",
        0,
        available_duration,
    )
    if source_out <= source_in:
        raise DomainError("INVALID_CLIP", "源片段必须有正时长", 422)
    speed = number(clip.get("speed"), "speed", 0.25, 4, 1)
    start = number(
        clip.get("timeline_start", clip.get("start")), "timeline_start", 0, MAX_DURATION, 0
    )
    duration = (source_out - source_in) / speed
    if start + duration > MAX_DURATION:
        raise DomainError("MEDIA_RESOURCE_LIMIT", "时间线超过本地执行器时长上限", 422)
    return {
        "id": str(clip.get("id", clip.get("clip_use_id", f"clip-{ordinal}"))),
        "record": record,
        "blob": blob,
        "path": path,
        "probe": probe,
        "source_in": source_in,
        "source_out": source_out,
        "timeline_start": start,
        "duration": duration,
        "speed": speed,
        "volume": number(clip.get("volume"), "volume", 0, 4, 1),
        "opacity": number(clip.get("opacity"), "opacity", 0, 1, 1),
        "track": int(number(clip.get("track"), "track", 0, 100, ordinal)),
        "x": int(number(clip.get("x"), "x", -4096, 4096, 0)),
        "y": int(number(clip.get("y"), "y", -4096, 4096, 0)),
        "width": clip.get("width"),
        "height": clip.get("height"),
        "fit": clip.get("fit", "contain"),
        "audio_only": audio_only,
        "transition_in": transition,
        "keyframes": normalize_keyframes(clip.get("keyframes"), duration),
    }


def render_time_segments(clips: list[dict], duration: float) -> list[dict]:
    boundaries = sorted(
        {
            0,
            ticks(duration),
            *(ticks(c["timeline_start"]) for c in clips),
            *(ticks(c["timeline_start"] + c["duration"]) for c in clips),
        }
    )
    segments = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        contributions = []
        for clip in clips:
            begin, finish = ticks(clip["timeline_start"]), ticks(
                clip["timeline_start"] + clip["duration"]
            )
            if begin <= start and finish >= end:
                source_start = ticks(clip["source_in"]) + round((start - begin) * clip["speed"])
                source_end = ticks(clip["source_in"]) + round((end - begin) * clip["speed"])
                roles = (
                    ["audio"]
                    if clip["audio_only"]
                    else (["visual", "audio"] if clip["probe"]["has_audio"] else ["visual"])
                )
                for role in roles:
                    contributions.append(
                        {
                            "source_ref": ref(clip["record"]),
                            "clip_use_id": clip["id"],
                            "source_interval": [source_start, source_end],
                            "mapping_curve": {
                                "kind": "linear",
                                "source_start_tick": source_start,
                                "speed": clip["speed"],
                            },
                            "weight_curve": (
                                {
                                    "kind": "piecewise_linear",
                                    "points": clip["keyframes"]["opacity"],
                                    "clock": "clip_local_output_seconds",
                                    "time_origin_tick": begin,
                                }
                                if role == "visual" and clip["keyframes"].get("opacity")
                                else {
                                    "kind": "constant",
                                    "value": clip["volume"] if role == "audio" else clip["opacity"],
                                }
                            ),
                            "contribution_semantics": (
                                "layer_weight_before_compositing"
                                if role == "visual"
                                else "gain_before_master_limiter"
                            ),
                            "transitions": [
                                {**fx, "side": side}
                                for side, fx in (
                                    ("incoming", clip.get("incoming_fx")),
                                    ("outgoing", clip.get("outgoing_fx")),
                                )
                                if fx and ticks(fx["start"]) < end and ticks(fx["end"]) > start
                            ],
                            "occlusion_evaluated": False,
                            "role": role,
                            "precision": "approximate",
                        }
                    )
        segments.append(
            {
                "segment_id": f"segment-{index + 1}",
                "output_interval": [start, end],
                "map_kind": (
                    "mix" if len(contributions) > 1 else "cut" if contributions else "insert"
                ),
                "mapping_quality": "approximate",
                "contributions": contributions,
                "background": "declared_black" if not contributions else None,
            }
        )
    return segments


def render_timeline(store: Any, actor: Any, body: dict) -> dict:
    project_id = body.get("project_id")
    require_project(store, actor, project_id)
    timeline = store.get(body.get("timeline_id"), actor.tenant_id)
    if timeline["kind"] != "Timeline" or timeline.get("project_id") != project_id:
        raise DomainError("INVALID_TIMELINE", "必须选择当前项目的时间线", 422)
    check_revision(timeline, body.get("expected_revision"))
    data = timeline["data"]
    if data.get("locked") is not True and data.get("status") not in {
        "locked",
        "approved",
        "已提交",
    }:
        raise DomainError("TIMELINE_NOT_LOCKED", "渲染前请冻结当前时间线版本", 409)
    # Opening literals bind the original accepted timeline and survive later editor revisions.
    first_revision = store.get(timeline["id"], actor.tenant_id, version=1)
    opening_text = first_revision["data"].get("opening_text")
    if opening_text is not None and (
        not isinstance(opening_text, str)
        or not opening_text.strip()
        or len(opening_text) > 120
        or not opening_text.isprintable()
    ):
        raise DomainError("INVALID_OPENING_TEXT", "时间线初版的首镜原片文字条件无效。", 422)
    assert_supported(data)
    if data.get("keyframe_curves"):
        raise DomainError(
            "MEDIA_CAPABILITY_UNAVAILABLE",
            "关键帧草稿已保存；当前 FFmpeg adapter 尚未编译关键帧曲线",
            422,
            {"unsupported": ["keyframe_curves"]},
        )
    clip_data = data.get("clips", data.get("clip_uses", []))
    audio_data = data.get("audio_tracks", [])
    if "clips" in data and "clip_uses" in data:
        raise DomainError("AMBIGUOUS_TIMELINE", "clips 与 clip_uses 不可同时存在", 422)
    if (
        not isinstance(clip_data, list)
        or not 1 <= len(clip_data) <= MAX_CLIPS
        or not isinstance(audio_data, list)
        or len(audio_data) > 16
    ):
        raise DomainError("INVALID_TIMELINE", "时间线需要 1–64 个画面片段，最多 16 个额外音轨", 422)
    clips = [normalize_clip(store, actor, project_id, c, i) for i, c in enumerate(clip_data)]
    audio_clips = [
        normalize_clip(store, actor, project_id, c, i + len(clips), True)
        for i, c in enumerate(audio_data)
    ]
    clips.sort(key=lambda c: (c["track"], c["timeline_start"]))
    all_clips = clips + audio_clips
    if len({c["id"] for c in all_clips}) != len(all_clips):
        raise DomainError("DUPLICATE_CLIP", "每个实际片段使用必须具有唯一 ID", 422)
    duration = max(c["timeline_start"] + c["duration"] for c in all_clips)
    if data.get("duration") is not None:
        requested_duration = number(data["duration"], "duration", 0.04, MAX_DURATION)
        if abs(requested_duration - duration) > 0.05:
            raise DomainError(
                "TIMELINE_DURATION_MISMATCH",
                "声明时长与实际片段覆盖不一致，请显式裁切或调整片段",
                422,
                {"declared": requested_duration, "actual": duration},
            )
    profile = {**data.get("format", {}), **body.get("profile", {})}
    width = even_dimension(profile.get("width"), "width", 1280)
    height = even_dimension(profile.get("height"), "height", 720)
    fps_value = profile.get("fps", 25)
    if isinstance(fps_value, dict):
        numerator = fps_value.get("num")
        denominator = fps_value.get("den")
        if any(
            isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 100000
            for v in (numerator, denominator)
        ):
            raise DomainError("INVALID_FPS", "有理数帧率需要正整数 num/den", 422)
        fps_fraction = Fraction(numerator, denominator)
        fps = number(float(fps_fraction), "fps", 1, 60)
    else:
        fps = number(fps_value, "fps", 1, 60)
        fps_fraction = Fraction(str(fps)).limit_denominator(1001)
    if width * height > MAX_PIXELS:
        raise DomainError("MEDIA_RESOURCE_LIMIT", "输出总像素超过当前执行器边界", 422)
    joins = transitions.resolve_transitions(clips, fps)
    fps_expr = f"{fps_fraction.numerator}/{fps_fraction.denominator}"
    captions = data.get("captions", [])
    if not isinstance(captions, list) or len(captions) > 400:
        raise DomainError("INVALID_CAPTIONS", "字幕必须是数组且不超过 400 条", 422)
    normalized_captions = []
    for caption in captions:
        if (
            not isinstance(caption, dict)
            or not isinstance(caption.get("text"), str)
            or len(caption["text"]) > 2000
        ):
            raise DomainError("INVALID_CAPTIONS", "每条字幕必须包含不超过 2000 字符的文本", 422)
        start = number(caption.get("start"), "caption.start", 0, duration)
        end = number(caption.get("end"), "caption.end", 0, duration)
        if end <= start:
            raise DomainError("INVALID_CAPTIONS", "字幕结束时间必须大于开始时间", 422)
        color = caption.get("color", "#ffffff")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise DomainError("INVALID_CAPTIONS", "字幕颜色必须使用 #RRGGBB", 422)
        normalized_captions.append(
            {
                **caption,
                "start": start,
                "end": end,
                "color": color,
                "font_size": number(
                    caption.get("font_size"), "font_size", 8, 160, min(48, max(16, height / 18))
                ),
            }
        )
    kwargs = actor_kwargs(actor, project_id)
    refs = [
        ref(timeline),
        *{
            (c["record"]["id"], c["record"]["version"]): ref(c["record"]) for c in all_clips
        }.values(),
    ]
    job = store.create(
        "RenderJob",
        {
            "status": "running",
            "timeline_id": timeline["id"],
            "timeline_version": timeline["version"],
            "profile": {"width": width, "height": height, "fps": fps},
            "started_at": now(),
            "expected_duration": duration,
            "renderer": "ffmpeg_local",
            "resource_limits": {
                "threads": 2,
                "timeout_seconds": MAX_RENDER_SECONDS,
                "max_bytes": MAX_BYTES,
            },
        },
        refs=refs,
        **kwargs,
    )
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="ai-ship-render-") as temp:
            tempdir = Path(temp)
            output = tempdir / "artifact.mp4"
            args = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-threads",
                "2",
                "-filter_complex_threads",
                "1",
            ]
            for clip in all_clips:
                if clip["probe"]["is_still"]:
                    args += ["-loop", "1"]
                args += ["-protocol_whitelist", "file,pipe", "-i", str(clip["path"])]
            filters = [
                f"color=c=black:s={width}x{height}:r={fps_expr}:d={duration:.9f}[base]",
                f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.9f}[silence]",
            ]
            visual_label = "base"
            audio_labels = ["silence"]
            for index, clip in enumerate(all_clips):
                start, end, position = clip["source_in"], clip["source_out"], clip["timeline_start"]
                if not clip["audio_only"]:
                    w = even_dimension(clip["width"], "clip.width", width)
                    h = even_dimension(clip["height"], "clip.height", height)
                    if clip["fit"] not in {"contain", "cover"}:
                        raise DomainError("INVALID_CLIP_FIT", "fit 只支持 contain 或 cover", 422)
                    fit_filter = (
                        f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0"
                        if clip["fit"] == "contain"
                        else f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
                    )
                    opacity_filter = f"colorchannelmixer=aa={clip['opacity']:.8f}"
                    if clip["keyframes"].get("opacity"):
                        alpha_expr = keyframe_expression(clip["keyframes"]["opacity"], "T")
                        opacity_filter = (
                            f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({alpha_expr})'"
                        )
                    filters.append(
                        f"[{index}:v:0]trim=start={start:.9f}:end={end:.9f},setpts=(PTS-STARTPTS)/{clip['speed']:.9f},{fit_filter},setsar=1,fps={fps_expr},format=rgba,{opacity_filter}{transitions.video_filters(clip)},setpts=PTS+{position:.9f}/TB[v{index}]"
                    )
                    next_label = f"stack{index}"
                    x_expr = (
                        keyframe_expression(clip["keyframes"]["x"], f"t-{position:.9f}")
                        if clip["keyframes"].get("x")
                        else str(clip["x"])
                    )
                    y_expr = (
                        keyframe_expression(clip["keyframes"]["y"], f"t-{position:.9f}")
                        if clip["keyframes"].get("y")
                        else str(clip["y"])
                    )
                    filters.append(
                        f"[{visual_label}][v{index}]overlay=x='{x_expr}':y='{y_expr}':eof_action=pass:shortest=0:enable='gte(t,{position:.9f})*lt(t,{position + clip['duration']:.9f})'[{next_label}]"
                    )
                    visual_label = next_label
                if clip["probe"]["has_audio"] and clip["volume"] > 0:
                    label = f"audio{index}"
                    delay = int(round(position * 1000))
                    filters.append(
                        f"[{index}:a:0]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,{audio_tempo(clip['speed'])},volume={clip['volume']:.9f},aresample=48000,aformat=channel_layouts=stereo{transitions.audio_filters(clip)},adelay={delay}|{delay}[{label}]"
                    )
                    audio_labels.append(label)
            font = next(
                (
                    path
                    for path in [
                        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    ]
                    if path.is_file()
                ),
                None,
            )
            if captions and font is None:
                raise DomainError("FONT_UNAVAILABLE", "缺少字幕字体，不能输出不完整成片", 422)
            for index, caption in enumerate(normalized_captions):
                textfile = tempdir / f"caption-{index}.txt"
                textfile.write_text(caption["text"].replace("\x00", ""), encoding="utf-8")
                label = f"caption{index}"
                filters.append(
                    f"[{visual_label}]drawtext=fontfile={font}:textfile={textfile}:expansion=none:fontsize={caption['font_size']:.2f}:fontcolor={caption['color'][1:]}:borderw=2:bordercolor=black@0.7:x=(w-text_w)/2:y=h-text_h-{height * .08:.2f}:enable='gte(t,{caption['start']:.9f})*lt(t,{caption['end']:.9f})'[{label}]"
                )
                visual_label = label
            filters.append(f"[{visual_label}]format=yuv420p[outv]")
            filters.append(
                "".join(f"[{label}]" for label in audio_labels)
                + f"amix=inputs={len(audio_labels)}:normalize=0:duration=longest,alimiter=limit=0.98:level=false,atrim=duration={duration:.9f}[outa]"
            )
            args += [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-fs",
                str(MAX_BYTES),
            ]
            if opening_text:
                from .opening_check import check_output, check_visible_origin

                origin = check_visible_origin(opening_text, clips, normalized_captions)
                preview = tempdir / "opening-preview.mp4"
                # Use the current revision's complete composition and encoder settings.
                run_process([*args, "-frames:v", "1", "-t", f"{1/fps:.9f}", str(preview)], 40)
                preflight = check_output(preview, opening_text, 1 / fps)
            run_process(
                [*args, "-t", f"{duration:.9f}", str(output)],
                min(MAX_RENDER_SECONDS, max(60, int(duration * 6))),
            )
            qc = technical_qc(
                output, {"width": width, "height": height, "fps": fps, "has_audio": True}, duration
            )
            if opening_text:
                qc["opening_check"] = {
                    "expected_text": opening_text,
                    "status": "matched",
                    "timeline_id": timeline["id"],
                    "timeline_version": timeline["version"],
                    "origin": origin,
                    "preflight": preflight,
                    "encoded_first_frame": check_output(output, opening_text, duration),
                }
            blob_data = persist_blob(output, actor.tenant_id)
        latest_timeline = store.get(timeline["id"], actor.tenant_id)
        check_revision(latest_timeline, timeline["version"])
        for clip in all_clips:
            current = store.get(clip["record"]["id"], actor.tenant_id)
            if current["data"].get("status") in {"revoked", "deleted", "restricted"}:
                raise DomainError("MEDIA_REVOKED", "渲染期间来源撤权，停止提交当前产物", 403)
        segments = render_time_segments(all_clips, duration)
        with store.transaction():
            check_revision(store.get(timeline["id"], actor.tenant_id), timeline["version"])
            for clip in all_clips:
                resolve_media(store, actor, clip["record"]["id"], project_id=project_id)
            blob = store.create(
                "BlobLocation",
                {**blob_data, "purpose": "artifact", "mime_type": "video/mp4"},
                refs=refs,
                **kwargs,
            )
            artifact = store.create(
                "Artifact",
                {
                    "name": f"{data.get('name', '作品')} · {width}×{height}",
                    "filename": "AI-ship-video.mp4",
                    "blob_location_id": blob["id"],
                    "content_hash": blob_data["checksum"],
                    "size_bytes": blob_data["size_bytes"],
                    "timeline_id": timeline["id"],
                    "timeline_version": timeline["version"],
                    "render_job_id": job["id"],
                    "probe": qc["probe"],
                    "qc": qc,
                    "status": "technical_passed",
                    "publish_ready": False,
                },
                refs=[*refs, ref(job), ref(blob)],
                **kwargs,
            )
            time_map = store.create(
                "TimeMap",
                {
                    "input_refs": [ref(c["record"]) for c in all_clips],
                    "output_ref": ref(artifact),
                    "source_timebase": {
                        c["record"]["id"]: {"num": 1, "den": 1000000} for c in all_clips
                    },
                    "target_timebase": {"num": 1, "den": 1000000},
                    "segments": segments,
                    "mapping_stage": "realized",
                    "mapping_quality": "approximate",
                    "tolerance_seconds": None,
                    "output_frame_interval_seconds": 1 / fps,
                    "timing_boundary": "Source VFR PTS correspondence and pixel occlusion are not fully audited",
                    "intent_mode": "creative_edit",
                },
                refs=[*refs, ref(artifact)],
                **kwargs,
            )
            manifest = store.create(
                "RenderManifest",
                {
                    "artifact_id": artifact["id"],
                    "timeline_ref": ref(timeline),
                    "render_job_id": job["id"],
                    "output_hash": blob_data["checksum"],
                    "blob_location_id": blob["id"],
                    "time_map_id": time_map["id"],
                    "actual_profile": {
                        "width": width,
                        "height": height,
                        "fps": fps,
                        "fps_rational": {
                            "num": fps_fraction.numerator,
                            "den": fps_fraction.denominator,
                        },
                    },
                    "tool_versions": tool_versions(),
                    "actual_input_refs": [ref(c["record"]) for c in all_clips],
                    "transitions": joins,
                    "clip_uses": [
                        {
                            k: c[k]
                            for k in (
                                "id",
                                "source_in",
                                "source_out",
                                "timeline_start",
                                "duration",
                                "speed",
                                "volume",
                                "opacity",
                                "track",
                                "x",
                                "y",
                                "fit",
                                "keyframes",
                            )
                        }
                        | {"source_ref": ref(c["record"])}
                        for c in all_clips
                    ],
                    "captions": normalized_captions,
                    "qc": qc,
                    "determinism": "tolerance_comparable",
                    "byte_identical_replay_guaranteed": False,
                    "finished_at": now(),
                },
                refs=[*refs, ref(artifact), ref(time_map), ref(job)],
                **kwargs,
            )
            for clip in all_clips:
                store.create(
                    "Usage",
                    {
                        "clip_use_id": clip["id"],
                        "asset_id": (
                            clip["record"]["id"]
                            if clip["record"]["kind"] == "Asset"
                            else clip["record"]["data"].get("asset_id")
                        ),
                        "asset_version_ref": ref(clip["record"]),
                        "timeline_id": timeline["id"],
                        "artifact_id": artifact["id"],
                        "source_range": [clip["source_in"], clip["source_out"]],
                        "output_range": [
                            clip["timeline_start"],
                            clip["timeline_start"] + clip["duration"],
                        ],
                        "status": "rendered",
                        "exposure_status": "not_published",
                        "contribution_kind": "audio" if clip["audio_only"] else "visual",
                    },
                    refs=[ref(clip["record"]), ref(timeline), ref(artifact), ref(manifest)],
                    **kwargs,
                )
            job = store.update(
                job["id"],
                {
                    **job["data"],
                    "status": "succeeded",
                    "artifact_id": artifact["id"],
                    "render_manifest_id": manifest["id"],
                    "qc": qc,
                    "finished_at": now(),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
                tenant_id=actor.tenant_id,
                expected_revision=job["version"],
                actor_id=actor.actor_id,
                refs=[*refs, ref(artifact), ref(manifest)],
                reason="真实渲染与全片技术解码校验通过；内容审阅另行确认",
            )
            store.create(
                "Cost",
                {
                    "category": "local_render",
                    "render_job_id": job["id"],
                    "actual": None,
                    "currency": "USD",
                    "unit": "wall_seconds",
                    "quantity": round(time.monotonic() - started, 3),
                    "pricing_status": "unconfigured",
                    "missing_reason": "没有配置设备和人工单价，不伪造费用",
                },
                refs=[ref(job), ref(artifact)],
                **kwargs,
            )
        return {
            "job": job,
            "artifact": artifact,
            "manifest": manifest,
            "time_map": time_map,
            "qc": qc,
            "preview_url": f"/api/v1/media/artifacts/{artifact['id']}/file",
            "download_url": f"/api/v1/media/artifacts/{artifact['id']}/file?download=true",
            "package_url": f"/api/v1/media/artifacts/{artifact['id']}/package",
        }
    except Exception as exc:
        current_job = store.get(job["id"], actor.tenant_id)
        if current_job["data"].get("status") != "succeeded":
            store.update(
                current_job["id"],
                {
                    **current_job["data"],
                    "status": "failed",
                    "error_code": getattr(exc, "code", "MEDIA_EXECUTION_FAILED"),
                    "finished_at": now(),
                },
                tenant_id=actor.tenant_id,
                expected_revision=current_job["version"],
                actor_id=actor.actor_id,
                reason="渲染失败；不创建可交付假状态",
            )
        raise


def waveform_data(store: Any, actor: Any, asset_id: str, buckets: int = 180) -> dict:
    record, blob, path = resolve_media(store, actor, asset_id, purpose="inspect")
    probe = probe_media(path)
    if not probe["has_audio"]:
        return {
            "available": False,
            "reason": "no_audio_stream",
            "peaks": [],
            "duration": probe["duration"],
        }
    if not 16 <= buckets <= 1000:
        raise DomainError("WAVEFORM_BUCKETS", "波形桶数必须在 16–1000 之间", 422)
    output, _ = run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "2000",
            "-t",
            str(MAX_DURATION),
            "-f",
            "s16le",
            "pipe:1",
        ],
        max(30, int(probe["duration"] / 2)),
        True,
    )
    samples = array.array("h")
    samples.frombytes(output[: len(output) - len(output) % 2])
    if os.sys.byteorder != "little":
        samples.byteswap()
    step = max(1, math.ceil(len(samples) / buckets))
    peaks = [
        round(max((abs(x) for x in samples[start : start + step]), default=0) / 32768, 5)
        for start in range(0, len(samples), step)
    ]
    return {
        "available": True,
        "source_ref": ref(record),
        "duration": probe["duration"],
        "sample_rate": 2000,
        "bucket_count": len(peaks),
        "peaks": peaks,
        "algorithm": "mono_downsampled_absolute_peak",
        "purpose": "visual_navigation_not_loudness_measurement",
    }


def extract_audio(store: Any, actor: Any, asset_id: str, body: dict) -> dict:
    source, _, path = resolve_media(store, actor, asset_id)
    project_id = body.get("project_id") or source["project_id"]
    require_project(store, actor, project_id)
    if project_id != source["project_id"]:
        raise DomainError("PROJECT_SCOPE_MISMATCH", "音轨提取项目与素材项目不一致", 403)
    if body.get("expected_revision") is not None:
        check_revision(source, body["expected_revision"])
    probe = probe_media(path)
    if not probe["has_audio"]:
        raise DomainError("NO_AUDIO", "素材没有可提取的音频流", 422)
    kwargs = actor_kwargs(actor, project_id)
    with tempfile.TemporaryDirectory(prefix="ai-ship-audio-") as temp:
        output = Path(temp) / "audio.wav"
        run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-threads",
                "2",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-vn",
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-fs",
                str(MAX_BYTES),
                str(output),
            ],
            max(30, int(probe["duration"])),
        )
        qc = technical_qc(output, {"has_video": False, "has_audio": True}, probe["duration"])
        blob_data = persist_blob(output, actor.tenant_id)
    with store.transaction():
        resolve_media(store, actor, asset_id, project_id=project_id)
        blob = store.create(
            "BlobLocation",
            {**blob_data, "purpose": "audio", "mime_type": "audio/wav"},
            refs=[ref(source)],
            **kwargs,
        )
        audio = store.create(
            "Audio",
            {
                "name": "原声音轨",
                "filename": "extracted-audio.wav",
                "parent_asset_ref": ref(source),
                "asset_id": (
                    source["id"] if source["kind"] == "Asset" else source["data"].get("asset_id")
                ),
                "asset_version_id": source["id"] if source["kind"] == "AssetVersion" else None,
                "parent_audio_version_id": source["id"] if source["kind"] == "Audio" else None,
                "blob_location_id": blob["id"],
                "content_hash": blob_data["checksum"],
                "probe": qc["probe"],
                "sample_rate": 48000,
                "status": "available",
                "transcription_status": "not_run",
                "word_timings": None,
            },
            refs=[ref(source), ref(blob)],
            **kwargs,
        )
    return {"audio": audio, "qc": qc, "preview_url": f"/api/v1/media/{audio['id']}/file"}


def frame_png(store: Any, actor: Any, asset_id: str, at: float = 0, width: int = 640) -> bytes:
    record, _, path = resolve_media(store, actor, asset_id, purpose="inspect")
    probe = probe_media(path)
    if not probe["has_video"]:
        raise DomainError("NO_VIDEO", "素材没有画面流", 422)
    at = number(at, "time", 0, max(0, probe["duration"] - 0.001), 0)
    width = even_dimension(width, "preview.width", 640)
    if width > 1920:
        raise DomainError("MEDIA_RESOURCE_LIMIT", "帧预览最多 1920 像素宽", 422)
    args = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "2",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        f"{at:.9f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "png",
        "-f",
        "image2pipe",
        "pipe:1",
    ]
    result, _ = run_process(args, 30, True)
    if not result.startswith(b"\x89PNG\r\n\x1a\n"):
        raise DomainError("FRAME_UNAVAILABLE", "没有得到指定时间的真实帧", 422)
    return result


def detect_shots(store: Any, actor: Any, asset_id: str, threshold: float = 0.3) -> dict:
    source, _, path = resolve_media(store, actor, asset_id)
    probe = probe_media(path)
    if not probe["has_video"] or probe["duration"] <= 0:
        raise DomainError("NO_VIDEO_DURATION", "镜头检测需要有时长的视频流", 422)
    threshold = number(threshold, "threshold", 0.01, 1)
    with tempfile.TemporaryDirectory(prefix="ai-ship-scenes-") as temp:
        metadata_file = Path(temp) / "scenes.txt"
        run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "2",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-vf",
                f"select='gt(scene,{threshold:.6f})',metadata=print:key=lavfi.scene_score:file={metadata_file}",
                "-an",
                "-f",
                "null",
                "-",
            ],
            min(MAX_RENDER_SECONDS, max(45, int(probe["duration"] * 2))),
        )
        if metadata_file.stat().st_size > 64 * 1024 * 1024:
            raise DomainError("SCENE_RESULT_LIMIT", "镜头候选过多，请提高阈值或缩短分析区间", 422)
        metadata = metadata_file.read_text()
    # Metadata contains actual decoded PTS; no truncated stderr or synthetic frame-index times.
    cuts = sorted(
        set(float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", metadata))
    )
    cuts = [value for value in cuts if 0.04 < value < probe["duration"] - 0.04]
    if len(cuts) > 1999:
        raise DomainError("SCENE_RESULT_LIMIT", "镜头候选超过 2000 段，请提高阈值或分段分析", 422)
    boundaries = [0, *cuts, probe["duration"]]
    shots = []
    with store.transaction():
        resolve_media(store, actor, asset_id)
        for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            shots.append(
                store.create(
                    "Shot",
                    {
                        "name": f"镜头候选 {i + 1}",
                        "asset_id": source["data"].get("asset_id", source["id"]),
                        "source_ref": ref(source),
                        "time_range": [start, end],
                        "start": start,
                        "end": end,
                        "keyframe_time": round((start + end) / 2, 6),
                        "detection_method": "ffmpeg_scene_difference",
                        "threshold": threshold,
                        "review_status": "unreviewed",
                        "semantic_labels": [],
                        "tool_versions": tool_versions(),
                    },
                    refs=[ref(source)],
                    **actor_kwargs(actor, source["project_id"]),
                )
            )
    return {
        "shots": shots,
        "actual_cut_pts_seconds": cuts,
        "source_ref": ref(source),
        "threshold": threshold,
        "boundary": "仅检测画面差异；不代表镜头语义、商品数量或连续性已验证。",
    }


def analyze_audio_onsets(store: Any, actor: Any, asset_id: str) -> dict:
    source, _, path = resolve_media(store, actor, asset_id)
    probe = probe_media(path)
    if not probe["has_audio"]:
        raise DomainError("NO_AUDIO", "素材没有可分析的音轨", 422)
    pcm, _ = run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "2000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        min(MAX_RENDER_SECONDS, max(30, int(probe["duration"]))),
        True,
    )
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if os.sys.byteorder != "little":
        samples.byteswap()
    window = 40  # 20 ms at 2 kHz, explicitly a coarse navigation analysis.
    energies = [
        math.sqrt(
            sum((x / 32768) ** 2 for x in samples[i : i + window])
            / max(1, len(samples[i : i + window]))
        )
        for i in range(0, len(samples), window)
    ]
    flux = (
        [0, *(max(0, energies[i] - energies[i - 1]) for i in range(1, len(energies)))]
        if energies
        else []
    )
    mean = sum(flux) / max(1, len(flux))
    variance = sum((x - mean) ** 2 for x in flux) / max(1, len(flux))
    threshold = mean + 1.5 * math.sqrt(variance)
    candidates = []
    last_time = -1.0
    for i in range(1, max(1, len(flux) - 1)):
        if flux[i] > max(threshold, 0.001) and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
            moment = i * 0.02
            if moment - last_time < 0.08:
                continue
            candidates.append(
                {
                    "time": round(moment, 4),
                    "energy_flux": round(flux[i], 6),
                    "confidence": "heuristic_unvalidated",
                }
            )
            last_time = moment
    if len(candidates) > 20000:
        raise DomainError("AUDIO_RESULT_LIMIT", "起音候选超过 20000，需分段分析", 422)
    with store.transaction():
        resolve_media(store, actor, asset_id)
        analysis = store.create(
            "Music",
            {
                "name": "声音起音候选",
                "source_ref": ref(source),
                "duration": probe["duration"],
                "onset_candidates": candidates,
                "analysis_method": "positive_short_time_rms_flux",
                "analysis_sample_rate": 2000,
                "window_ms": 20,
                "threshold": threshold,
                "beat_grid": None,
                "bpm": None,
                "downbeats": None,
                "semantic_emotion": None,
                "review_status": "unreviewed",
                "not_inferred": ["BPM", "强拍", "乐段", "音乐情绪", "音乐权利"],
                "tool_versions": tool_versions(),
            },
            refs=[ref(source)],
            **actor_kwargs(actor, source["project_id"]),
        )
    return {"analysis": analysis, "boundary": "短时能量变化候选，不等同于节拍、BPM或语义卡点。"}


@router.get("/{asset_id}/frame")
def frame_endpoint(asset_id: str, time: float = 0, width: int = 640, actor=Depends(current_actor)):
    return Response(
        frame_png(get_store(), actor, asset_id, time, width),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=0", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{asset_id}/detect-shots")
def detect_shots_endpoint(
    asset_id: str, body: dict, request: Request, actor=Depends(current_actor)
):
    require_write(actor)
    return envelope(detect_shots(get_store(), actor, asset_id, body.get("threshold", 0.3)), request)


@router.post("/{asset_id}/analyze-audio")
def analyze_audio_endpoint(asset_id: str, request: Request, actor=Depends(current_actor)):
    require_write(actor)
    return envelope(analyze_audio_onsets(get_store(), actor, asset_id), request)


def create_package(store: Any, actor: Any, artifact_id: str) -> tuple[dict, Path]:
    artifact, _, path = resolve_media(store, actor, artifact_id, purpose="export")
    if artifact["kind"] != "Artifact":
        raise DomainError("INVALID_ARTIFACT", "交付包需要真实成片", 422)
    project_id = artifact["project_id"]
    manifests = [
        r
        for r in store.list(
            actor.tenant_id, kind="RenderManifest", project_id=project_id, q=artifact_id, limit=2000
        )
        if r["data"].get("artifact_id") == artifact_id
    ]
    if not manifests:
        raise DomainError("MISSING_MANIFEST", "成片缺少实际渲染清单", 409)
    manifest = manifests[0]
    timeline_ref = manifest["data"]["timeline_ref"]
    timeline = store.get(timeline_ref["id"], actor.tenant_id, version=timeline_ref["version"])
    time_map = store.get(manifest["data"]["time_map_id"], actor.tenant_id)
    with tempfile.TemporaryDirectory(prefix="ai-ship-package-") as temp:
        destination = Path(temp) / "delivery.zip"
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.write(path, "video.mp4")
            for name, record in (
                ("timeline.json", timeline),
                ("render-manifest.json", manifest),
                ("time-map.json", time_map),
            ):
                archive.writestr(name, json.dumps(record, ensure_ascii=False, indent=2))
            archive.writestr(
                "README.txt",
                "AI-ship 真实媒体交付包\n包含实际成片及冻结时间线、渲染清单、来源区间映射。\n原件未嵌入此包；回导需同租户可读取原素材引用。\n状态：技术校验通过，商品事实、许可、语义和审美仍需内容审阅。\n映射精度：容差内可比；不保证逐位一致重渲染。\n外部编辑软件专有工程格式尚未适配。\n",
            )
        blob_data = persist_blob(destination, actor.tenant_id)
    kwargs = actor_kwargs(actor, project_id)
    with store.transaction():
        resolve_media(store, actor, artifact_id, purpose="export")
        blob = store.create(
            "BlobLocation",
            {**blob_data, "purpose": "delivery_package", "mime_type": "application/zip"},
            refs=[ref(artifact), ref(manifest), ref(timeline)],
            **kwargs,
        )
        package = store.create(
            "DeliveryPackage",
            {
                "artifact_id": artifact_id,
                "render_manifest_id": manifest["id"],
                "timeline_ref": ref(timeline),
                "blob_location_id": blob["id"],
                "checksum": blob_data["checksum"],
                "status": "available",
                "included": [
                    "video.mp4",
                    "timeline.json",
                    "render-manifest.json",
                    "time-map.json",
                    "README.txt",
                ],
                "omissions": ["原始素材字节", "外部编辑器专有工程格式", "内容审阅凭证"],
                "roundtrip_mode": "ai_ship_timeline_json",
            },
            refs=[ref(artifact), ref(manifest), ref(timeline), ref(blob)],
            **kwargs,
        )
    return package, blob_path(blob, actor.tenant_id)


def capabilities() -> dict:
    return {
        "adapter": "ffmpeg_local",
        "tools": tool_versions(),
        "implemented": [
            "content_hash_import",
            "resumable_chunk_upload",
            "immutable_originals",
            "technical_probe",
            "range_file_streaming",
            "waveform",
            "audio_extract",
            "frame_preview",
            "scene_difference_shot_candidates",
            "energy_onset_candidates",
            "ordered_trim",
            "constant_speed",
            "rotation",
            "flip",
            "crop_scale",
            "color_eq",
            "sharpen",
            "audio_gain",
            "loudness_normalization",
            "multi_track_overlay",
            "two_source_transitions_with_audio_crossfade",
            "linear_opacity_xy_keyframes",
            "audio_mix",
            "captions",
            "output_profiles",
            "full_decode_qc",
            "render_manifest",
            "time_map",
            "delivery_package",
        ],
        "transitions": list(transitions.KINDS),
        "unavailable": [
            "ai_watermark_removal",
            "semantic_voice_alignment",
            "frame_interpolation",
            "nonlinear_or_other_property_keyframes",
            "masks",
            "automatic_fact_verification",
            "automatic_license_verification",
            "complex_transitions",
            "native_editor_roundtrip",
            "distributed_media_worker",
        ],
        "limits": {
            "max_upload_bytes": MAX_BYTES,
            "max_duration_seconds": MAX_DURATION,
            "max_video_clips": MAX_CLIPS,
            "max_audio_tracks": 16,
            "max_output_pixels": MAX_PIXELS,
            "max_process_seconds": MAX_RENDER_SECONDS,
        },
        "source_docs": [
            "https://ffmpeg.org/ffprobe.html",
            "https://ffmpeg.org/ffmpeg-filters.html",
        ],
    }


MAX_CHUNK_BYTES = 4 * 1024 * 1024


def upload_path(entry: dict, actor: Any) -> Path:
    key = entry["data"].get("upload_key", "")
    base = (root_dir() / "tmp" / "uploads" / tenant_key(actor.tenant_id)).resolve()
    path = (root_dir() / key).resolve()
    if not path.is_relative_to(base) or not path.name.endswith(".part"):
        raise DomainError("INVALID_UPLOAD_LOCATION", "续传位置不属于当前工作区", 403)
    return path


def start_upload(store: Any, actor: Any, body: dict) -> dict:
    project_id = body.get("project_id")
    require_project(store, actor, project_id)
    filename = safe_filename(body.get("filename", ""))
    total = body.get("size_bytes")
    if isinstance(total, bool) or not isinstance(total, int) or not 0 < total <= MAX_BYTES:
        raise DomainError(
            "UPLOAD_SIZE_LIMIT", "size_bytes 必须是 1–256 MiB 范围内的整数字节数", 422
        )
    expected_hash = body.get("content_hash")
    if expected_hash is not None and (
        not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        raise DomainError("INVALID_HASH", "content_hash 必须是小写 SHA-256", 422)
    key = f"tmp/uploads/{tenant_key(actor.tenant_id)}/{uuid.uuid4().hex}.part"
    path = root_dir() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=False)
    kwargs = actor_kwargs(actor, project_id)
    try:
        with store.transaction():
            batch = store.create(
                "ImportBatch",
                {
                    "name": Path(filename).name,
                    "intake_mode": "browser_resumable_upload",
                    "status": "receiving",
                    "file_count": 1,
                },
                **kwargs,
            )
            entry = store.create(
                "ImportEntry",
                {
                    "import_batch_id": batch["id"],
                    "relative_path": filename,
                    "size_bytes": total,
                    "expected_hash": expected_hash,
                    "transferred_bytes": 0,
                    "chunk_manifest": [],
                    "status": "receiving",
                    "upload_key": key,
                    "source_note": str(body.get("source_note", ""))[:2000],
                },
                refs=[ref(batch)],
                **kwargs,
            )
        return {"entry": entry, "chunk_size": MAX_CHUNK_BYTES, "offset": 0}
    except Exception:
        path.unlink(missing_ok=True)
        raise


def append_upload_chunk(
    store: Any, actor: Any, entry_id: str, offset: int, data: bytes, checksum: str | None = None
) -> dict:
    if not data or len(data) > MAX_CHUNK_BYTES:
        raise DomainError("UPLOAD_CHUNK_SIZE", "每个分块必须为 1 byte 至 4 MiB", 413)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise DomainError("UPLOAD_OFFSET", "分块偏移必须是非负整数", 422)
    actual_hash = hashlib.sha256(data).hexdigest()
    if checksum is not None and checksum != actual_hash:
        raise DomainError("CHUNK_HASH_MISMATCH", "分块 SHA-256 校验失败，未写入", 422)
    with store.transaction():
        entry = store.get(entry_id, actor.tenant_id)
        if entry["kind"] != "ImportEntry" or entry["data"].get("status") != "receiving":
            raise DomainError("UPLOAD_NOT_RECEIVING", "当前导入条目不接受分块", 409)
        payload = entry["data"]
        path = upload_path(entry, actor)
        committed = payload["transferred_bytes"]
        if not path.is_file() or path.stat().st_size < committed:
            raise DomainError("UPLOAD_BYTES_MISSING", "已确认的分块字节丢失，不能伪造续传进度", 409)
        if offset < committed:
            matching = next(
                (
                    c
                    for c in payload["chunk_manifest"]
                    if c["offset"] == offset
                    and c["size"] == len(data)
                    and c["checksum"] == actual_hash
                ),
                None,
            )
            if matching:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    if hashlib.sha256(handle.read(len(data))).hexdigest() == actual_hash:
                        return {"entry": entry, "offset": committed, "duplicate_chunk": True}
            raise DomainError(
                "UPLOAD_OFFSET_CONFLICT",
                "该偏移已提交不同内容",
                409,
                {"expected_offset": committed},
            )
        if offset != committed or offset + len(data) > payload["size_bytes"]:
            raise DomainError(
                "UPLOAD_OFFSET_CONFLICT",
                "分块必须从已确认位置继续，且不能超过声明大小",
                409,
                {"expected_offset": committed},
            )
        with path.open("r+b") as handle:
            handle.truncate(
                committed
            )  # Crash after append before DB commit: uncommitted tail is discarded.
            handle.seek(committed)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        updated = store.update(
            entry_id,
            {
                **payload,
                "transferred_bytes": offset + len(data),
                "chunk_manifest": [
                    *payload["chunk_manifest"],
                    {
                        "offset": offset,
                        "size": len(data),
                        "checksum": actual_hash,
                        "status": "verified",
                    },
                ],
            },
            tenant_id=actor.tenant_id,
            expected_revision=entry["version"],
            actor_id=actor.actor_id,
            reason="分块真实字节与校验值已持久化",
        )
        return {"entry": updated, "offset": offset + len(data), "duplicate_chunk": False}


def _finish_upload(store: Any, actor: Any, entry_id: str) -> dict:
    entry = store.get(entry_id, actor.tenant_id)
    if entry["kind"] != "ImportEntry" or not entry["data"].get("upload_key"):
        raise DomainError("INVALID_UPLOAD", "不是可续传导入条目", 422)
    if entry["data"].get("status") in {"completed", "deduplicated"}:
        asset = store.get(entry["data"]["asset_id"], actor.tenant_id)
        versions = store.list(
            actor.tenant_id,
            kind="AssetVersion",
            project_id=entry["project_id"],
            q=asset["id"],
            limit=2000,
        )
        version = next(
            v
            for v in versions
            if v["data"].get("asset_id") == asset["id"] and v["data"].get("variant") == "original"
        )
        return {
            "asset": asset,
            "version": version,
            "probe": asset["data"]["probe"],
            "duplicate": True,
            "import_entry": entry,
            "preview_url": f"/api/v1/media/{asset['id']}/file",
        }
    if entry["data"].get("status") != "receiving":
        raise DomainError("UPLOAD_NOT_RECEIVING", "该导入已关闭或失败", 409)
    payload = entry["data"]
    if payload["transferred_bytes"] != payload["size_bytes"]:
        raise DomainError(
            "UPLOAD_INCOMPLETE",
            "尚未接收所有分块，不能完成入库",
            409,
            {"offset": payload["transferred_bytes"], "size": payload["size_bytes"]},
        )
    path = upload_path(entry, actor)
    if not path.is_file() or path.stat().st_size != payload["size_bytes"]:
        raise DomainError("UPLOAD_BYTES_MISSING", "分块实际字节数不匹配", 409)
    checksum = hash_file(path)
    if payload.get("expected_hash") and checksum != payload["expected_hash"]:
        raise DomainError("UPLOAD_HASH_MISMATCH", "完整文件校验失败，尚未入库", 422)
    batch = store.get(payload["import_batch_id"], actor.tenant_id)
    result = import_saved_file(
        store,
        actor,
        entry["project_id"],
        payload["relative_path"],
        path,
        payload.get("source_note", ""),
        import_entry=entry,
        import_batch=batch,
    )
    path.unlink(missing_ok=True)
    return result


def finish_upload(store: Any, actor: Any, entry_id: str) -> dict:
    entry = store.get(entry_id, actor.tenant_id)
    if entry["data"].get("status") in {"completed", "deduplicated"}:
        return _finish_upload(store, actor, entry_id)
    if fcntl is None:
        raise DomainError(
            "UPLOAD_LOCK_UNAVAILABLE",
            "原子续传完成需要 Linux 文件锁；Windows 请在 Docker/WSL 中运行服务",
            503,
        )
    path = upload_path(entry, actor)
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        current = store.get(entry_id, actor.tenant_id)
        if current["data"].get("status") in {"completed", "deduplicated"}:
            return _finish_upload(store, actor, entry_id)
        raise DomainError("UPLOAD_BYTES_MISSING", "续传字节不可用", 409)
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DomainError(
                "UPLOAD_BUSY", "该文件正在核验入库，请查询原条目，不要新建重复上传", 409
            )
        return _finish_upload(store, actor, entry_id)


@router.delete("/uploads/{entry_id}")
def cancel_upload_endpoint(entry_id: str, request: Request, actor=Depends(current_actor)):
    require_write(actor)
    store = get_store()
    with store.transaction():
        entry = store.get(entry_id, actor.tenant_id)
        if entry["kind"] != "ImportEntry" or not entry["data"].get("upload_key"):
            raise DomainError("INVALID_UPLOAD", "不是可续传上传", 422)
        if entry["data"]["status"] in {"completed", "deduplicated"}:
            raise DomainError(
                "UPLOAD_ALREADY_COMMITTED", "素材已入库；撤销上传不能删除已发生的素材事实", 409
            )
        path = upload_path(entry, actor)
        if fcntl is None:
            raise DomainError(
                "UPLOAD_LOCK_UNAVAILABLE", "上传核销需要 Linux 文件锁；请使用 Docker/WSL", 503
            )
        if path.exists():
            with path.open("rb") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DomainError("UPLOAD_BUSY", "文件正在完成入库，请稍后核对", 409)
                path.unlink(missing_ok=True)
        updated = store.update(
            entry_id,
            {**entry["data"], "status": "cancelled", "cancelled_at": now()},
            tenant_id=actor.tenant_id,
            expected_revision=entry["version"],
            actor_id=actor.actor_id,
            reason="取消未完成上传，保留已确认分块记录",
        )
    return envelope({"entry": updated}, request)


@router.post("/uploads")
def start_upload_endpoint(body: dict, request: Request, actor=Depends(current_actor)):
    require_write(actor)
    return envelope(start_upload(get_store(), actor, body), request)


@router.get("/uploads/{entry_id}")
def upload_status_endpoint(entry_id: str, actor=Depends(current_actor)):
    entry = get_store().get(entry_id, actor.tenant_id)
    if entry["kind"] != "ImportEntry" or not entry["data"].get("upload_key"):
        raise DomainError("INVALID_UPLOAD", "不是可续传导入条目", 422)
    return envelope({"entry": entry, "offset": entry["data"]["transferred_bytes"]})


@router.put("/uploads/{entry_id}")
async def upload_chunk_endpoint(
    entry_id: str, offset: int, request: Request, actor=Depends(current_actor)
):
    require_write(actor)
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > MAX_CHUNK_BYTES:
            raise DomainError("UPLOAD_CHUNK_SIZE", "分块超过 4 MiB", 413)
    result = await run_in_threadpool(
        append_upload_chunk,
        get_store(),
        actor,
        entry_id,
        offset,
        bytes(buffer),
        request.headers.get("x-chunk-sha256"),
    )
    return envelope(result, request)


@router.post("/uploads/{entry_id}/complete")
def finish_upload_endpoint(entry_id: str, request: Request, actor=Depends(current_actor)):
    require_write(actor)
    return envelope(finish_upload(get_store(), actor, entry_id), request)


@router.get("/capabilities")
def media_capabilities(actor=Depends(current_actor)):
    return envelope(capabilities())


@router.post("/import")
async def import_endpoint(
    request: Request, project_id: str, filename: str, actor=Depends(current_actor)
):
    require_write(actor)
    store = get_store()
    require_project(store, actor, project_id)
    filename = safe_filename(filename)
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_BYTES:
                raise DomainError("UPLOAD_SIZE_LIMIT", "超过 256 MiB 单文件上传限制", 413)
        except ValueError:
            raise DomainError("INVALID_CONTENT_LENGTH", "Content-Length 无效", 422)
    temp_dir = root_dir() / "tmp"
    temp_dir.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="import-", dir=temp_dir)
    path = Path(tmp)
    try:
        total = 0
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise DomainError("UPLOAD_SIZE_LIMIT", "超过 256 MiB 单文件上传限制", 413)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        result = await run_in_threadpool(
            import_saved_file,
            store,
            actor,
            project_id,
            filename,
            path,
            request.headers.get("x-source-note", ""),
        )
        return envelope(result, request)
    finally:
        path.unlink(missing_ok=True)


@router.post("/render")
def render_endpoint(body: dict, request: Request, actor=Depends(current_actor)):
    require_write(actor)
    return envelope(render_timeline(get_store(), actor, body), request)


@router.get("/artifacts/{artifact_id}/file")
def artifact_file_endpoint(artifact_id: str, download: bool = False, actor=Depends(current_actor)):
    record, blob, path = resolve_media(
        get_store(), actor, artifact_id, purpose="export" if download else "inspect"
    )
    if record["kind"] != "Artifact":
        raise DomainError("INVALID_ARTIFACT", "需要真实成片引用", 422)
    return FileResponse(
        path,
        media_type=blob["data"].get("mime_type", "video/mp4"),
        filename="AI-ship-video.mp4" if download else None,
        headers={
            "ETag": '"' + record["data"]["content_hash"] + '"',
            "Cache-Control": "private, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/artifacts/{artifact_id}/package")
def package_endpoint(artifact_id: str, actor=Depends(current_actor)):
    require_write(actor)
    package, path = create_package(get_store(), actor, artifact_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename="AI-ship-delivery.zip",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{asset_id}/file")
def media_file_endpoint(asset_id: str, download: bool = False, actor=Depends(current_actor)):
    record, blob, path = resolve_media(
        get_store(), actor, asset_id, purpose="export" if download else "inspect"
    )
    return FileResponse(
        path,
        media_type=blob["data"].get("mime_type", "application/octet-stream"),
        filename=Path(record["data"].get("filename", "media")).name if download else None,
        headers={
            "ETag": '"'
            + record["data"].get("content_hash", blob["data"].get("checksum", ""))
            + '"',
            "Cache-Control": "private, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{asset_id}/transform")
def transform_endpoint(asset_id: str, body: dict, request: Request, actor=Depends(current_actor)):
    require_write(actor)
    return envelope(transform_asset(get_store(), actor, asset_id, body), request)


@router.get("/{asset_id}/waveform")
def waveform_endpoint(asset_id: str, buckets: int = 180, actor=Depends(current_actor)):
    return envelope(waveform_data(get_store(), actor, asset_id, buckets))


@router.post("/{asset_id}/extract-audio")
def extract_audio_endpoint(
    asset_id: str, body: dict, request: Request, actor=Depends(current_actor)
):
    require_write(actor)
    return envelope(extract_audio(get_store(), actor, asset_id, body), request)
