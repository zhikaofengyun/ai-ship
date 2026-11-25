"""Time-coded CLIP index. Source rows and access decisions remain authoritative."""

import threading

from . import frame_vectors
from .errors import DomainError
from .media import check_current_media_rights, resolve_media

_BUILDS = threading.BoundedSemaphore(1)


def _readable(store, actor, record):
    try:
        check_current_media_rights(store, actor, record, purpose="process")
        return True
    except DomainError:
        return False


def build(store, actor, project, rows_for, limit, after):
    if not _BUILDS.acquire(blocking=False):
        raise DomainError("CLIP_BUILD_BUSY", "本机正在建立视频画面索引，请在本批结束后重试。", 429)
    try:
        return _build(store, actor, project, rows_for, limit, after)
    finally:
        _BUILDS.release()


def _build(store, actor, project, rows_for, limit, after):
    model = frame_vectors.local_model()
    if not model:
        raise DomainError("CLIP_UNAVAILABLE", "请先在服务器配置本地 CLIP 视觉与文本模型。", 503)
    scope = (actor.tenant_id, project, model[0])
    pending = []
    with store.read_snapshot() as db:
        rows = rows_for(store, db, actor, project)
        existing = dict(
            db.execute(
                "SELECT DISTINCT version_id,pin FROM media_frame_vectors WHERE tenant=? AND project=? AND space=?",
                scope,
            )
        )
        for vid in sorted(rows):
            _, data, record = rows[vid]
            pin = record["hash"]
            if (
                vid <= after
                or data["media_type"] != "video"
                or not record["data"].get("blob_location_id")
                or existing.get(vid) == pin
            ):
                continue
            if _readable(store, actor, record):
                pending.append((vid, pin, data["seconds"]))
    encoded = []
    errors = []
    for vid, pin, seconds in pending[:limit]:
        try:
            _, _, path = resolve_media(store, actor, vid, project_id=project, purpose="process")
            encoded.append((vid, pin, frame_vectors.video_frames(model, path, seconds)))
        except DomainError as exc:
            if exc.status in (429, 503):
                raise
            # Do not return identifying details for sources that became inaccessible.
            if exc.status not in (403, 404):
                errors.append({"version_id": vid, "code": exc.code, "message": exc.message})
    count = samples = 0
    if encoded:
        with store.transaction(), store.connection() as db:
            current = rows_for(store, db, actor, project)
            for vid, pin, frames in encoded:
                row = current.get(vid)
                if not row or row[2]["hash"] != pin or not _readable(store, actor, row[2]):
                    continue
                db.execute(
                    "DELETE FROM media_frame_vectors WHERE tenant=? AND project=? AND space=? AND version_id=?",
                    (*scope, vid),
                )
                db.executemany(
                    "INSERT INTO media_frame_vectors(tenant,project,space,version_id,pin,pts_us,rgb_sha256,embedding) VALUES(?,?,?,?,?,?,?,?)",
                    [(*scope, vid, pin, pts, h, v) for pts, (h, v) in frames.items()],
                )
                count += 1
                samples += len(frames)
    return {
        "space": model[0],
        "processed": count,
        "frames": samples,
        "errors": errors,
        "remaining": max(0, len(pending) - limit),
        "next_after": pending[limit - 1][0] if len(pending) > limit else None,
    }


def matches(store, db, actor, project, rows, space, query_vector):
    import numpy as np

    best = {}
    allowed = {}
    for vid, pin, pts, h, blob in db.execute(
        "SELECT version_id,pin,pts_us,rgb_sha256,embedding FROM media_frame_vectors WHERE tenant=? AND project=? AND space=? ORDER BY version_id,pts_us",
        (actor.tenant_id, project, space),
    ):
        if vid not in rows or rows[vid][2]["hash"] != pin or len(blob) != query_vector.nbytes:
            continue
        if vid not in allowed:
            allowed[vid] = _readable(store, actor, rows[vid][2])
        if not allowed[vid]:
            continue
        score = float(np.dot(query_vector, np.frombuffer(blob, dtype="<f4")))
        if not np.isfinite(score):
            continue
        score = max(-1.0, min(1.0, score))
        if vid not in best or score > best[vid]["score"]:
            best[vid] = {"score": score, "source_seconds": pts / 1_000_000, "sample_rgb_sha256": h}
    return best


def confirm_frame(store, actor, project, record, frame, start, end):
    resolve_media(store, actor, record["id"], project_id=project, purpose="process")
    pts = round(frame.source_seconds * 1_000_000)
    if frame.version != record["version"] or not start <= pts / 1_000_000 < end:
        raise DomainError("FRAME_EVIDENCE_MISMATCH", "标注区间须包含所选素材版本的采样帧。", 409)
    with store.connection() as db:
        row = db.execute(
            "SELECT 1 FROM media_frame_vectors WHERE tenant=? AND project=? AND space=? AND version_id=? AND pin=? AND pts_us=? AND rgb_sha256=?",
            (
                actor.tenant_id,
                project,
                frame.space,
                record["id"],
                record["hash"],
                pts,
                frame.sample_rgb_sha256,
            ),
        ).fetchone()
    if not row:
        raise DomainError(
            "FRAME_EVIDENCE_MISMATCH", "所选帧与已保存的模型、素材版本或画面指纹不符。", 409
        )
    return {
        "asset_version_id": record["id"],
        "source_pts_us": pts,
        "source_rgb_sha256": frame.sample_rgb_sha256,
        "space": frame.space,
        "revision": record["version"],
    }
