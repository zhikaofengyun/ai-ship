"""Versioned project search projections. Source records and live permissions stay authoritative."""

from collections import defaultdict
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from . import frame_vectors, text_vectors, visual_index, visual_candidates
from .api import Body, ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .media import check_current_media_rights, require_project
from .production import media_duration
from .store import digest, get_store, new_id, now

router = APIRouter(prefix="/production/asset-index", tags=["素材检索"])
SPACE = "atlas-6/literal-trigram-v1"


class Build(Body):
    project_id: str
    mode: Literal["shadow", "incremental"] = "shadow"
    request_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class Switch(Body):
    project_id: str
    snapshot: str
    expected_revision: int = Field(ge=0)
    action: Literal["activate", "rollback"] = "activate"


class Query(Body):
    project_id: str
    text: str = Field(default="", max_length=300)
    channel: Literal["indexed", "source", "semantic", "visual"] = "indexed"
    semantic_threshold: float = Field(default=0.30, ge=-1, le=1)
    visual_threshold: float = Field(default=0.20, ge=-1, le=1)
    media_type: Literal["any", "video", "audio", "image"] = "any"
    min_seconds: float = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    expected_view: str | None = Field(default=None, min_length=64, max_length=64)


def head(conn, actor, project):
    r = conn.execute(
        "SELECT generation,revision FROM media_search_heads WHERE tenant=? AND project=?",
        (actor.tenant_id, project),
    ).fetchone()
    return dict(r) if r else {"generation": None, "revision": 0}


def require_revision(current, expected):
    if current["revision"] != expected:
        raise DomainError("INDEX_CONFLICT", "素材索引已更新，请刷新后再操作。", 409)


def snapshots(conn, actor, project):
    rows = conn.execute(
        """SELECT s.id,s.stamp,s.mode,s.space,s.watermark,s.checked,s.reused,s.based_on,
        EXISTS(SELECT 1 FROM media_search_switches h WHERE h.tenant=s.tenant AND h.project=s.project AND h.generation=s.id) AS previously_active
        FROM media_search_snapshots s WHERE s.tenant=? AND s.project=? ORDER BY s.rowid DESC""",
        (actor.tenant_id, project),
    )
    return [dict(r) for r in rows]


def source_rows(store, conn, actor, project):
    # One read snapshot across the versions, parent metadata, tags and rights.
    records = conn.execute(
        """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.* FROM records r JOIN revisions v
        ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
        WHERE r.tenant_id=? AND r.project_id=? AND r.kind IN ('AssetVersion','Tag','Asset')
        ORDER BY r.kind,r.id""",
        (actor.tenant_id, project),
    )
    assets, versions, tags = {}, [], defaultdict(list)
    for row in records:
        item = store._row_record(row)
        if item["kind"] == "Asset":
            assets[item["id"]] = item
        elif item["kind"] == "AssetVersion":
            versions.append(item)
        elif item["status"] not in {"revoked", "rejected", "archived", "invalidated"}:
            for link in item["refs"]:
                if link["kind"] == "AssetVersion":
                    tags[link["id"]].append((link["version"], item))
    observations = dict(
        conn.execute(
            "SELECT o.blob_id,o.tags_json FROM media_observations o JOIN records b "
            "ON b.tenant_id=o.tenant AND b.id=o.blob_id AND b.project_id=o.project JOIN revisions v "
            "ON v.tenant_id=b.tenant_id AND v.record_id=b.id AND v.version=b.latest_version "
            "WHERE o.tenant=? AND o.project=? AND o.blob_pin=v.content_hash AND o.method=?",
            (actor.tenant_id, project, visual_candidates.OBSERVATION_METHOD),
        )
    )
    suggestions = {
        (vid, pin): labels
        for vid, pin, labels in conn.execute(
            "SELECT version_id,pin,labels_json FROM media_clip_candidates WHERE tenant=? AND project=?",
            (actor.tenant_id, project),
        )
    }
    rows = {}
    for v in versions:
        data = v["data"]
        asset = assets.get(data.get("asset_id"))
        if (
            not asset
            or data.get("storage_tier") in {"quarantine", "local_cold"}
            or (
                asset["data"].get("storage_tier") in {"quarantine", "local_cold"}
                and (
                    data.get("variant") == "original"
                    or data.get("blob_location_id") == asset["data"].get("blob_location_id")
                )
            )
        ):
            continue
        label = data.get("filename") or asset["data"].get("name") or "素材"
        tag_base = (
            data.get("tag_base_revision", v["version"])
            if data.get("tier_revision") == v["version"]
            else v["version"]
        )
        valid = [t for rev, t in tags[v["id"]] if tag_base <= rev <= v["version"]]
        machine = json.loads(observations.get(data.get("blob_location_id"), "[]"))
        machine.extend(
            visual_candidates.effective_suggestions(
                json.loads(suggestions.get((v["id"], v["hash"]), "[]")), [t["data"] for t in valid]
            )
        )
        terms = [label, *(tag["label"] for tag in machine)] + [
            str(t["data"].get("label", "")) for t in valid
        ]
        payload = {
            "asset_id": asset["id"],
            "version_id": v["id"],
            "revision": v["version"],
            "storage_tier": data.get("storage_tier", "online"),
            "name": label,
            "media_type": str(asset["data"].get("media_type", "")).partition("/")[0],
            "seconds": media_duration(data),
            "terms": " ".join(terms).casefold(),
            "tags": [
                {
                    "label": t["data"].get("label", ""),
                    "category": t["data"].get("entity"),
                    "interval": t["data"].get("interval"),
                    "method": t["data"].get("method"),
                }
                for t in valid
            ]
            + machine,
        }
        inputs = [v["hash"], asset["hash"], [t["hash"] for t in valid]]
        pin = digest([*inputs, machine] if machine else inputs)
        rows[v["id"]] = (pin, payload, v)
    return rows


def signature(rows):
    return digest([(k, v[0]) for k, v in sorted(rows.items())])


@router.get("/history")
def history(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    store = get_store()
    require_project(store, actor, project_id)
    with store.read_snapshot() as c:
        return ok(
            {
                "head": head(c, actor, project_id),
                "snapshots": snapshots(c, actor, project_id),
                "analysis_queue": dict(
                    c.execute(
                        "SELECT state,count(*) FROM material_analysis_jobs WHERE tenant=? AND project=? GROUP BY state",
                        (actor.tenant_id, project_id),
                    )
                ),
            },
            request,
        )


@router.post("/build")
def build(body: Build, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store, project = get_store(), body.project_id
    require_project(store, actor, project)
    with store.transaction(), store.connection() as c:
        current = head(c, actor, project)
        previous = c.execute(
            "SELECT * FROM media_search_snapshots WHERE tenant=? AND project=? AND request_key=?",
            (actor.tenant_id, project, body.request_key),
        ).fetchone()
        if previous:
            if previous["mode"] != body.mode:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一构建请求已用于其他路线。", 409)
            return ok({"snapshot": previous["id"], "head": current, "replayed": True}, request)
        require_revision(current, body.expected_revision)
        if body.mode == "incremental" and not current["generation"]:
            raise DomainError("INDEX_BASE_REQUIRED", "先建立并启用第一份索引。", 409)
        docs = source_rows(store, c, actor, project)
        watermark = c.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events WHERE tenant_id=? AND project_id=?",
            (actor.tenant_id, project),
        ).fetchone()[0]
        prior = {}
        if body.mode == "incremental":
            prior = {
                row["version_id"]: row
                for row in c.execute(
                    "SELECT version_id,pin,payload FROM media_search_rows WHERE generation=?",
                    (current["generation"],),
                )
            }
        reused = sum(key in prior and prior[key]["pin"] == d[0] for key, d in docs.items())
        sid = new_id("index")
        c.execute(
            "INSERT INTO media_search_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                actor.tenant_id,
                project,
                now(),
                body.mode,
                SPACE,
                watermark,
                signature(docs),
                len(docs),
                reused,
                current["generation"],
                body.request_key,
            ),
        )
        for key, (pin, payload, _) in docs.items():
            raw = (
                prior[key]["payload"]
                if key in prior and prior[key]["pin"] == pin
                else json.dumps(payload, ensure_ascii=False)
            )
            row_id = c.execute(
                "INSERT INTO media_search_rows(generation,version_id,pin,payload) VALUES(?,?,?,?)",
                (sid, key, pin, raw),
            ).lastrowid
            c.execute(
                "INSERT INTO media_search_terms(rowid,terms) VALUES(?,?)",
                (row_id, payload["terms"]),
            )
        return ok(
            {
                "snapshot": sid,
                "head": current,
                "checked": len(docs),
                "reused": reused,
                "replayed": False,
            },
            request,
        )


@router.post("/switch")
def switch(body: Switch, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store, project = get_store(), body.project_id
    require_project(store, actor, project)
    with store.transaction(), store.connection() as c:
        current = head(c, actor, project)
        row = c.execute(
            "SELECT * FROM media_search_snapshots WHERE id=? AND tenant=? AND project=?",
            (body.snapshot, actor.tenant_id, project),
        ).fetchone()
        if not row:
            raise DomainError("INDEX_NOT_FOUND", "找不到当前项目的索引。", 404)
        if current["generation"] == row["id"] and current["revision"] in (
            body.expected_revision,
            body.expected_revision + 1,
        ):
            return ok(current, request)
        require_revision(current, body.expected_revision)
        if body.action == "rollback":
            formerly_used = c.execute(
                "SELECT 1 FROM media_search_switches WHERE tenant=? AND project=? AND generation=?",
                (actor.tenant_id, project, row["id"]),
            ).fetchone()
            if not formerly_used:
                raise DomainError("INDEX_NOT_PREVIOUS", "只能回退到本项目已启用过的索引。", 409)
        elif signature(source_rows(store, c, actor, project)) != row["source_digest"]:
            raise DomainError(
                "INDEX_SOURCE_CHANGED", "构建后素材或标注已经变化，请重建再启用。", 409
            )
        count = c.execute(
            "SELECT COUNT(*) FROM media_search_rows r JOIN media_search_terms t ON t.rowid=r.row_id WHERE generation=?",
            (row["id"],),
        ).fetchone()[0]
        if count != row["checked"] or row["space"] != SPACE:
            raise DomainError("INDEX_INCOMPLETE", "索引覆盖数量或文本空间不符。", 409)
        c.execute(
            "INSERT INTO media_search_heads VALUES(?,?,?,?) ON CONFLICT(tenant,project) DO UPDATE SET generation=excluded.generation,revision=excluded.revision",
            (actor.tenant_id, project, row["id"], current["revision"] + 1),
        )
        c.execute(
            "INSERT INTO media_search_switches VALUES(?,?,?,?,?,?)",
            (actor.tenant_id, project, current["revision"] + 1, row["id"], actor.actor_id, now()),
        )
        return ok(head(c, actor, project), request)


class Embed(Body):
    project_id: str
    limit: int = Field(default=128, ge=1, le=256)


@router.post("/embed")
def embed(body: Embed, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store = get_store()
    require_project(store, actor, body.project_id)
    model = text_vectors.local_model()
    if not model:
        raise DomainError("TEXT_MODEL_UNAVAILABLE", "尚未配置本地 ONNX 语义模型。", 503)
    space = model[0]
    with store.read_snapshot() as db:
        rows = source_rows(store, db, actor, body.project_id)
        existing = dict(
            db.execute(
                "SELECT version_id,pin FROM media_text_vectors WHERE tenant=? AND project=? AND space=?",
                (actor.tenant_id, body.project_id, space),
            )
        )
        pending = []
        for vid, (pin, payload, version) in rows.items():
            if existing.get(vid) == pin:
                continue
            try:
                check_current_media_rights(store, actor, version, purpose="process")
            except DomainError:
                continue
            pending.append((vid, pin, payload["terms"]))
    selected = pending[: body.limit]
    encoded = []
    for i in range(0, len(selected), 16):
        chunk = selected[i : i + 16]
        vectors = text_vectors.encode(model, [v[2] for v in chunk])
        encoded.extend(
            (v[0], v[1], vector.tobytes()) for v, vector in zip(chunk, vectors, strict=True)
        )
    saved = 0
    with store.transaction(), store.connection() as db:
        current = source_rows(store, db, actor, body.project_id)
        for vid, pin, vector in encoded:
            entry = current.get(vid)
            if not entry or entry[0] != pin:
                continue
            try:
                check_current_media_rights(store, actor, entry[2], purpose="process")
            except DomainError:
                continue
            db.execute(
                "INSERT INTO media_text_vectors VALUES(?,?,?,?,?,?) ON CONFLICT(tenant,project,space,version_id) DO UPDATE SET pin=excluded.pin,embedding=excluded.embedding",
                (actor.tenant_id, body.project_id, space, vid, pin, vector),
            )
            saved += 1
    return ok({"processed": saved, "remaining": len(pending) - saved, "space": space}, request)


class FrameBuild(Body):
    project_id: str
    limit: int = Field(default=4, ge=1, le=12)
    after: str = Field(default="", max_length=256)


class Analyze(FrameBuild):
    refresh: bool = False


@router.post("/analyze")
def analyze(body: Analyze, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store, project = get_store(), body.project_id
    require_project(store, actor, project)
    with store.connection() as db:
        page = db.execute(
            "SELECT r.id FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND "
            "v.record_id=r.id AND v.version=r.latest_version WHERE r.tenant_id=? AND r.project_id=? "
            "AND r.kind='AssetVersion' AND r.id>? AND json_extract(v.data_json,'$.blob_location_id') "
            "IS NOT NULL ORDER BY r.id LIMIT ?",
            (actor.tenant_id, project, body.after, body.limit + 1),
        ).fetchall()
    model = None
    model_status = "unconfigured"
    try:
        model = frame_vectors.local_model()
        if model:
            model_status = "available"
    except DomainError as exc:
        model_status = exc.code
    result = {"processed": 0, "reused": 0, "clip_frames": 0, "errors": [], "ocr_unavailable": 0}
    for row in page[: body.limit]:
        try:
            source, observation, reused = visual_candidates.observe_source(
                store, actor, project, row["id"], refresh=body.refresh
            )
            result["reused" if reused else "processed"] += 1
            result["ocr_unavailable"] += observation["ocr"]["status"] != "read"
            if model:
                result["clip_frames"] += visual_index.index_source(
                    store,
                    actor,
                    project,
                    model,
                    row["id"],
                    source["hash"],
                    observation["duration"],
                    observation["is_still"],
                )
            if observation["ocr"].get("retryable"):
                # Leave the worker's bounded retry pending. Measured tags are
                # already usable by search and a later scan can recover the OCR.
                continue
            with store.connection() as db:
                db.execute(
                    "UPDATE material_analysis_jobs SET state=?,code=NULL WHERE tenant=? AND project=? "
                    "AND version_id=? AND state IN ('failed','queued','measured')",
                    (
                        (
                            "completed"
                            if model_status in {"available", "unconfigured"}
                            else "measured"
                        ),
                        actor.tenant_id,
                        project,
                        row["id"],
                    ),
                )
        except DomainError as exc:
            if exc.code == "IDENTITY_REVOKED":
                raise
            if exc.status not in (403, 404) and exc.code not in {
                "NO_VISUAL_STREAM",
                "ASSET_OFFLINE",
            }:
                result["errors"].append(
                    {"version_id": row["id"], "code": exc.code, "message": exc.message}
                )
    return ok(
        {
            **result,
            "clip_status": model_status,
            "next_after": page[body.limit - 1]["id"] if len(page) > body.limit else None,
        },
        request,
    )


@router.post("/frames/build")
def build_frames(body: FrameBuild, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store = get_store()
    require_project(store, actor, body.project_id)
    return ok(
        visual_index.build(store, actor, body.project_id, source_rows, body.limit, body.after),
        request,
    )


@router.post("/query")
def query(body: Query, request: Request, actor: Actor = Depends(current_actor)):
    store, project = get_store(), body.project_id
    require_project(store, actor, project)
    terms = tuple(dict.fromkeys(re.findall(r"[^\W_]+", body.text.casefold(), re.UNICODE)))
    model = None
    query_vector = None
    semantic_error = None
    visual = body.channel == "visual"
    threshold = body.visual_threshold if visual else body.semantic_threshold
    if visual and terms:
        try:
            model = frame_vectors.local_model()
            if model:
                query_vector = frame_vectors.encode_query(model, body.text)
        except DomainError as exc:
            semantic_error = exc.message
    elif body.channel == "semantic" and terms:
        try:
            model = text_vectors.local_model()
            if model:
                query_vector = text_vectors.encode(model, [body.text])[0]
        except DomainError as exc:
            semantic_error = exc.message
    with store.read_snapshot() as c:
        current = head(c, actor, project)
        rows = source_rows(store, c, actor, project)
        active = current["generation"] if body.channel != "source" else None
        idx = {}
        ranked = {}
        if active:
            idx = dict(
                c.execute(
                    "SELECT version_id,pin FROM media_search_rows WHERE generation=?", (active,)
                )
            )
            phrases = [t for t in terms if len(t) >= 3]
            if phrases:
                expression = " OR ".join('"' + p + '"' for p in phrases)
                ranked = dict(
                    c.execute(
                        """SELECT r.version_id,bm25(media_search_terms)
                    FROM media_search_terms JOIN media_search_rows r ON r.row_id=media_search_terms.rowid
                    WHERE media_search_terms MATCH ? AND r.generation=?""",
                        (expression, active),
                    )
                )
        scores = {}
        frame_hits = {}
        if visual and query_vector is not None:
            frame_hits = visual_index.matches(
                store, c, actor, project, rows, model[0], query_vector
            )
            scores = {vid: hit["score"] for vid, hit in frame_hits.items()}
        elif query_vector is not None:
            import numpy as np

            for vid, pin, blob in c.execute(
                "SELECT version_id,pin,embedding FROM media_text_vectors WHERE tenant=? AND project=? AND space=?",
                (actor.tenant_id, project, model[0]),
            ):
                if vid in rows and rows[vid][0] == pin and len(blob) == query_vector.nbytes:
                    scores[vid] = float(
                        np.clip(np.dot(query_vector, np.frombuffer(blob, dtype="<f4")), -1, 1)
                    )
        hits = []
        for key, (pin, payload, version) in rows.items():
            if body.media_type != "any" and body.media_type != payload["media_type"]:
                continue
            if (payload["seconds"] or 0) < body.min_seconds:
                continue
            found = [t for t in terms if t in payload["terms"]]
            if terms and not found and (key not in scores or scores[key] < threshold):
                continue
            try:
                check_current_media_rights(store, actor, version, purpose="process")
            except DomainError:
                continue
            tag_match = None
            for tag in payload["tags"] if found else []:
                if not any(t in str(tag.get("label", "")).casefold() for t in found):
                    continue
                interval = tag.get("interval")
                at = (
                    interval[0]
                    if isinstance(interval, list) and interval
                    else tag.get("source_seconds")
                )
                if type(at) in (int, float) and at >= 0:
                    tag_match = {
                        "source_seconds": at,
                        "label": tag["label"],
                        "method": tag.get("method"),
                    }
                    break
            is_current = key in idx and idx[key] == pin
            hits.append(
                {
                    **{k: v for k, v in payload.items() if k != "terms"},
                    "matched": found,
                    **({"tag_match": tag_match} if tag_match else {}),
                    **({"semantic_score": round(scores[key], 6)} if key in scores else {}),
                    **(
                        {
                            "frame_match": {
                                "source_seconds": frame_hits[key]["source_seconds"],
                                "sample_rgb_sha256": frame_hits[key]["sample_rgb_sha256"],
                                "space": model[0],
                            }
                        }
                        if key in frame_hits and scores[key] >= threshold
                        else {}
                    ),
                    "source": "index" if is_current else "source",
                    "coverage": len(found) / len(terms) if terms else 1,
                    "rank": round(float(ranked.get(key, 0)), 9) if is_current else 0,
                }
            )
        hits.sort(
            key=lambda r: (
                r["storage_tier"] == "low_priority",
                -r["coverage"],
                r["rank"],
                r["name"].casefold(),
                r["version_id"],
            )
        )
        if visual and scores:
            # Pixels and query text share one CLIP space. Retain literal-only
            # matches after time-coded hits, including names below threshold.
            hits.sort(
                key=lambda r: (
                    r["storage_tier"] == "low_priority",
                    "frame_match" not in r,
                    -r["semantic_score"] if "frame_match" in r else 0,
                    -r["coverage"],
                    r["rank"],
                    r["version_id"],
                )
            )
        elif scores:
            literal = {
                r["version_id"]: i for i, r in enumerate((r for r in hits if r["matched"]), 1)
            }
            dense = {
                r["version_id"]: i
                for i, r in enumerate(
                    sorted(
                        (
                            r
                            for r in hits
                            if "semantic_score" in r and r["semantic_score"] >= threshold
                        ),
                        key=lambda r: (-r["semantic_score"], r["version_id"]),
                    ),
                    1,
                )
            }
            for r in hits:
                k = r["version_id"]
                r["fusion_score"] = sum(
                    1 / (60 + rank[k]) for rank in (literal, dense) if k in rank
                )
            hits.sort(
                key=lambda r: (
                    r["storage_tier"] == "low_priority",
                    -r["fusion_score"],
                    r["version_id"],
                )
            )
        result_pins = [(r["version_id"], rows[r["version_id"]][0], r["source"]) for r in hits]
        if visual:
            result_pins = [
                (pin, r.get("frame_match"), r.get("semantic_score"))
                for pin, r in zip(result_pins, hits, strict=True)
            ]
        view = digest(
            [
                current,
                body.text,
                body.media_type,
                body.channel,
                body.min_seconds,
                [model[0], threshold] if scores else None,
                result_pins,
            ]
        )
        if body.expected_view and body.expected_view != view:
            raise DomainError("RESULT_SET_CHANGED", "素材、权限或索引已变化，请重新检索。", 409)
        end = body.offset + body.limit
        fallback = None
        if visual and not terms:
            fallback = "输入画面描述可按实际采样帧查找。"
        elif visual and not scores:
            fallback = (
                semantic_error + " " if semantic_error else ""
            ) + "尚无可用的当前画面匹配，已按素材名和有效标注检索。"
        elif body.channel == "semantic" and not scores:
            fallback = semantic_error or (
                "本项目没有与当前模型匹配的向量，已按素材名和有效标注检索。"
                if model
                else "未登记兼容的向量模型，已按素材名和有效人工标注检索。"
            )
        elif body.channel == "indexed" and not active:
            fallback = "尚未启用项目索引，已直接检索素材。"
        return ok(
            {
                "head": current,
                "space": SPACE,
                "count": len(hits),
                "items": hits[body.offset : end],
                "view": view,
                "offset": body.offset,
                "next_offset": end if end < len(hits) else None,
                "vector_available": bool(scores),
                "vector_space": model[0] if scores else None,
                "requested_channel": body.channel,
                "fallback": fallback,
            },
            request,
        )
