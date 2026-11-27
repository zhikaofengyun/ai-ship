"""Time-coded CLIP index. Source rows and access decisions remain authoritative."""

from functools import lru_cache
import json
import threading

from .auth import actor_still_authorized

from . import frame_vectors
from .errors import DomainError
from .media import check_current_media_rights, resolve_media
from .store import digest

_BUILDS = threading.BoundedSemaphore(1)


# Limited, explicitly versioned zero-shot vocabulary. Scores are cosines, not probabilities.
VOCABULARY = {
    "scene": (
        ("室内", "an indoor room"),
        ("城市街道", "an urban street surrounded by buildings"),
        ("户外自然", "a natural outdoor landscape"),
        ("摄影棚背景", "an isolated object photographed against a plain studio background"),
        ("花园", "a garden full of flowering plants"),
        ("厨房或咖啡馆桌面", "a tabletop in a kitchen or cafe"),
        ("图文画面", "a digital illustration with printed words"),
    ),
    "subject": (
        ("人物", "a person"),
        ("面孔", "a human face"),
        ("服饰展示", "a person wearing clothing"),
        ("手持商品", "hands holding a product"),
        ("商品包装", "a product in its retail packaging"),
        ("食物", "a dish of food"),
        ("杯装饮品", "coffee, tea or a beverage in a cup"),
        ("电子设备", "an electronic device"),
        ("美妆护肤", "a cosmetic or skincare product"),
        ("交通工具", "a vehicle"),
        ("动物", "an animal"),
        ("昆虫", "insects on flowers"),
        ("花卉植物", "a flower or plant"),
        ("珠宝首饰", "a piece of jewelry"),
        ("印刷文字", "letters printed on a plain background"),
    ),
}


MIN_COSINE = 0.23
MIN_MARGIN = 0.02
LABEL_REVISION = digest(["clip_candidate_v1", VOCABULARY, MIN_COSINE, MIN_MARGIN])


@lru_cache(maxsize=1)
def label_vectors(model):
    return {
        axis: [
            (name, frame_vectors.encode_query(model, "a photo of " + prompt))
            for name, prompt in choices
        ]
        for axis, choices in VOCABULARY.items()
    }


def candidate_labels(model, frames):
    import numpy as np

    labels = []
    for pts, (sha, raw) in sorted(frames.items()):
        vector = np.frombuffer(raw, dtype="<f4")
        for axis, options in label_vectors(model).items():
            scores = sorted(((float(vector @ v), n) for n, v in options), reverse=True)
            best, name = scores[0]
            margin = best - scores[1][0]
            if not np.isfinite(best) or best < MIN_COSINE or margin < MIN_MARGIN:
                continue
            labels.append(
                {
                    "label": name,
                    "category": axis,
                    "method": "clip_candidate_v1",
                    "vocabulary": LABEL_REVISION,
                    "source_seconds": pts / 1_000_000,
                    "sample_rgb_sha256": sha,
                    "cosine": round(best, 5),
                    "margin": round(margin, 5),
                    "space": model[0],
                    "review_state": "suggested",
                }
            )
    return labels


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
        classified = dict(
            db.execute(
                "SELECT version_id,pin FROM media_clip_candidates WHERE tenant=? AND project=? AND space=?",
                (actor.tenant_id, project, model[0] + "/" + LABEL_REVISION),
            )
        )
        for vid in sorted(rows):
            _, data, record = rows[vid]
            pin = record["hash"]
            if (
                vid <= after
                or data["media_type"] not in {"video", "image"}
                or not record["data"].get("blob_location_id")
                or (existing.get(vid) == pin and classified.get(vid) == pin)
            ):
                continue
            if _readable(store, actor, record):
                pending.append((vid, pin, data["seconds"], data["media_type"] == "image"))
    errors = []
    count = samples = 0
    for vid, pin, seconds, still in pending[:limit]:
        try:
            added = index_source(store, actor, project, model, vid, pin, seconds, still)
            count += bool(added)
            samples += added
        except DomainError as exc:
            if exc.status in (429, 503):
                raise
            if exc.status not in (403, 404):
                errors.append({"version_id": vid, "code": exc.code, "message": exc.message})
    return {
        "space": model[0],
        "processed": count,
        "frames": samples,
        "errors": errors,
        "remaining": max(0, len(pending) - limit),
        "next_after": pending[limit - 1][0] if len(pending) > limit else None,
    }


def index_source(store, actor, project, model, vid, pin, seconds, still):
    source, _, path = resolve_media(store, actor, vid, project_id=project, purpose="process")
    if source["hash"] != pin:
        raise DomainError("SOURCE_CHANGED", "素材已变化，请重试。", 409)
    scope = (actor.tenant_id, project, model[0], vid)
    label_space = model[0] + "/" + LABEL_REVISION
    with store.connection() as db:
        cached = db.execute(
            "SELECT 1 FROM media_clip_candidates WHERE tenant=? AND project=? AND space=? AND version_id=? AND pin=?",
            (actor.tenant_id, project, label_space, vid, pin),
        ).fetchone()
        frames = {
            pts: (sha, embedding)
            for pts, sha, embedding in db.execute(
                "SELECT pts_us,rgb_sha256,embedding FROM media_frame_vectors "
                "WHERE tenant=? AND project=? AND space=? AND version_id=? AND pin=?",
                (*scope, pin),
            )
        }
    if frames and cached:
        return 0
    if not frames:
        frames = frame_vectors.video_frames(model, path, seconds, still=still)
    labels = candidate_labels(model, frames)
    with store.transaction(), store.connection() as db:
        if not actor_still_authorized(actor):
            raise DomainError("IDENTITY_REVOKED", "读取期间工作区权限已变化。", 403)
        current, _, _ = resolve_media(store, actor, vid, project_id=project, purpose="process")
        if current["hash"] != pin:
            raise DomainError("SOURCE_CHANGED", "分析期间素材已变化，请重试。", 409)
        db.execute(
            "DELETE FROM media_frame_vectors WHERE tenant=? AND project=? AND space=? AND version_id=?",
            scope,
        )
        db.executemany(
            "INSERT INTO media_frame_vectors(tenant,project,space,version_id,pin,pts_us,rgb_sha256,embedding) VALUES(?,?,?,?,?,?,?,?)",
            [(*scope, pin, pts, h, v) for pts, (h, v) in frames.items()],
        )
        db.execute(
            "INSERT INTO media_clip_candidates VALUES(?,?,?,?,?,?) ON CONFLICT(tenant,project,version_id) "
            "DO UPDATE SET pin=excluded.pin,space=excluded.space,labels_json=excluded.labels_json",
            (
                actor.tenant_id,
                project,
                vid,
                pin,
                label_space,
                json.dumps(labels, ensure_ascii=False),
            ),
        )
    return len(frames)


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
