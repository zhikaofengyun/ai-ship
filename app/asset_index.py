"""Versioned project search projections. Source records and live permissions stay authoritative."""

from collections import defaultdict
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

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
    channel: Literal["indexed", "source", "semantic"] = "indexed"
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
    rows = {}
    for v in versions:
        data = v["data"]
        asset = assets.get(data.get("asset_id"))
        if not asset or data.get("storage_tier") in {"quarantine", "local_cold"}:
            continue
        label = data.get("filename") or asset["data"].get("name") or "素材"
        tag_base = (
            data.get("tag_base_revision", v["version"])
            if data.get("tier_revision") == v["version"]
            else v["version"]
        )
        valid = [t for rev, t in tags[v["id"]] if tag_base <= rev <= v["version"]]
        terms = [label] + [str(t["data"].get("label", "")) for t in valid]
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
            ],
        }
        pin = digest([v["hash"], asset["hash"], [t["hash"] for t in valid]])
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
            {"head": head(c, actor, project_id), "snapshots": snapshots(c, actor, project_id)},
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


@router.post("/query")
def query(body: Query, request: Request, actor: Actor = Depends(current_actor)):
    store, project = get_store(), body.project_id
    require_project(store, actor, project)
    terms = tuple(dict.fromkeys(re.findall(r"[^\W_]+", body.text.casefold(), re.UNICODE)))
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
        hits = []
        for key, (pin, payload, version) in rows.items():
            if body.media_type != "any" and body.media_type != payload["media_type"]:
                continue
            if (payload["seconds"] or 0) < body.min_seconds:
                continue
            found = [t for t in terms if t in payload["terms"]]
            if terms and not found:
                continue
            try:
                check_current_media_rights(store, actor, version, purpose="process")
            except DomainError:
                continue
            is_current = key in idx and idx[key] == pin
            hits.append(
                {
                    **{k: v for k, v in payload.items() if k != "terms"},
                    "matched": found,
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
        view = digest(
            [
                current,
                body.text,
                body.media_type,
                body.channel,
                body.min_seconds,
                [(h["version_id"], rows[h["version_id"]][0], h["source"]) for h in hits],
            ]
        )
        if body.expected_view and body.expected_view != view:
            raise DomainError("RESULT_SET_CHANGED", "素材、权限或索引已变化，请重新检索。", 409)
        end = body.offset + body.limit
        fallback = None
        if body.channel == "semantic":
            fallback = "未登记兼容的向量模型，已按素材名和有效人工标注检索。"
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
                "vector_available": False,
                "requested_channel": body.channel,
                "fallback": fallback,
            },
            request,
        )
