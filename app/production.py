"""Evidence-based production operations from the Atlas. No fabricated external facts.

These handlers operate on immutable, tenant-scoped records. A technical media
pass, human attestation, provider receipt and public observation remain distinct.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import csv
import hashlib
import io
import json
import math
import statistics
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import Actor, current_actor, require_write, require_approval
from .errors import DomainError
from .store import get_store
from .api import ok

router = APIRouter(prefix="/production", tags=["production"])


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: str, field: str = "time") -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        raise DomainError("INVALID_TIME", f"{field} 必须是包含时区的 ISO 8601 时间。")


def normalized_window(value: dict) -> dict:
    return {key: timestamp(value[key], key).isoformat() for key in ("start", "end")}


def current_dependency_problem(item: dict) -> dict | None:
    """Current rights and fact validity cannot be frozen into an old grant."""
    data = item["data"]
    if data.get("revoked_at") or data.get("status") in {
        "revoked",
        "deleted",
        "deleting",
        "restricted",
        "expired",
    }:
        return {
            "code": "DEPENDENCY_NOT_CURRENT",
            "message": f"{item['kind']} 已撤回或不可继续使用。",
            "record_id": item["id"],
        }
    validity = data.get("validity")
    if validity and not isinstance(validity, dict):
        return {
            "code": "DEPENDENCY_VALIDITY_UNVERIFIABLE",
            "message": "有效期仍是说明文字，须核实为结构化时间。",
            "record_id": item["id"],
        }
    expiry = (
        data.get("expires_at") or data.get("valid_until") or (validity or {}).get("valid_until")
    )
    if expiry:
        try:
            expired = timestamp(expiry) <= datetime.now(timezone.utc)
        except DomainError:
            return {
                "code": "DEPENDENCY_VALIDITY_UNVERIFIABLE",
                "message": "有效期无法核实，不能按有效处理。",
                "record_id": item["id"],
            }
        if expired:
            return {
                "code": "DEPENDENCY_NOT_CURRENT",
                "message": f"{item['kind']} 已过期。",
                "record_id": item["id"],
            }
    valid_from = data.get("valid_from") or (validity or {}).get("valid_from")
    if valid_from:
        try:
            active = timestamp(valid_from) <= datetime.now(timezone.utc)
        except DomainError:
            return {
                "code": "DEPENDENCY_VALIDITY_UNVERIFIABLE",
                "message": "生效时间无法核实。",
                "record_id": item["id"],
            }
        if not active:
            return {
                "code": "DEPENDENCY_NOT_ACTIVE",
                "message": f"{item['kind']} 尚未生效。",
                "record_id": item["id"],
            }
    return None


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def ref(record: dict, **extra) -> dict:
    return {"id": record["id"], "version": record["version"], "kind": record["kind"], **extra}


def record(
    actor: Actor,
    rid: str,
    kind: str | None = None,
    version: int | None = None,
    project: str | None = None,
) -> dict:
    item = get_store().get(rid, actor.tenant_id, version=version)
    if kind and item["kind"] != kind:
        raise DomainError("WRONG_KIND", f"需要 {kind} 类型的记录。")
    if (
        project
        and item.get("project_id") != project
        and not (item["kind"] == "Project" and item["id"] == project)
    ):
        raise DomainError("PROJECT_MISMATCH", "记录不属于当前项目。", 409)
    return item


def save(
    actor: Actor, kind: str, project: str, data: dict, refs: list | None = None, reason: str = ""
) -> dict:
    record(actor, project, "Project")
    return get_store().create(
        kind,
        data,
        tenant_id=actor.tenant_id,
        project_id=project,
        actor_id=actor.actor_id,
        refs=refs or [],
        reason=reason or "用户提交可追溯的领域事实",
    )


def revise(actor: Actor, item: dict, changes: dict, reason: str, refs: list | None = None) -> dict:
    return get_store().update(
        item["id"],
        {**item["data"], **changes},
        tenant_id=actor.tenant_id,
        expected_revision=item["version"],
        actor_id=actor.actor_id,
        refs=refs if refs is not None else item.get("refs", []),
        reason=reason,
    )


def number(
    value: Any, name: str, minimum: float | None = None, maximum: float | None = None
) -> float:
    if isinstance(value, bool):
        raise DomainError("INVALID_NUMBER", f"{name} 必须是有限数字。")
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise DomainError("INVALID_NUMBER", f"{name} 必须是有限数字。")
    if (
        not math.isfinite(n)
        or (minimum is not None and n < minimum)
        or (maximum is not None and n > maximum)
    ):
        raise DomainError("INVALID_NUMBER", f"{name} 超出允许范围。")
    return n


def media_duration(data: dict) -> float | None:
    """Prefer the actual FFprobe projection; accept explicitly older contracts."""
    probes = []
    for key in ("probe", "media_probe"):
        probe = data.get(key)
        if probe is not None and not isinstance(probe, dict):
            raise DomainError("INVALID_MEDIA_PROBE", "媒体探测结果必须是结构化对象。")
        probes.append((probe or {}).get("duration"))
    for value in (*probes, data.get("duration")):
        if value is not None:
            return number(value, "media duration", 0)
    return None


class TrendInput(Body):
    project_id: str
    title: str = Field(min_length=1, max_length=300)
    canonical_url: str
    observed_at: str
    expires_at: str
    evidence: str = Field(min_length=1, max_length=30000)
    platform: str = Field(min_length=1, max_length=100)
    region: str = "unspecified"
    observations: dict = Field(default_factory=dict)


@router.post("/trends")
def import_trend(body: TrendInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    if urlparse(body.canonical_url).scheme not in {"http", "https"}:
        raise DomainError("INVALID_SOURCE", "热点证据需要可追溯的 http/https 来源链接。")
    seen, expiry = timestamp(body.observed_at), timestamp(body.expires_at)
    if expiry <= seen or seen > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise DomainError("INVALID_WINDOW", "观测时间不能在未来，有效期必须晚于观测时间。")
    with get_store().transaction():
        source = save(
            actor,
            "Source",
            body.project_id,
            {
                "canonical_url": body.canonical_url,
                "origin_kind": "manual_observation",
                "provenance": body.evidence,
                "grant_scope": [],
                "expires_at": body.expires_at,
                "license_snapshot": {"status": "unverified"},
            },
        )
        trend = save(
            actor,
            "Trend",
            body.project_id,
            {
                "title": body.title,
                "platform": body.platform,
                "region": body.region,
                "observed_at": body.observed_at,
                "expires_at": body.expires_at,
                "raw_ref": source["id"],
                "observations": body.observations,
                "status": (
                    "expired" if expiry <= datetime.now(timezone.utc) else "evidence_recorded"
                ),
                "verification": "manual_observation_not_independent_confirmation",
            },
            [ref(source)],
        )
    return ok(trend, request)


class ProductImport(Body):
    project_id: str
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    source_id: str


@router.post("/products/import")
def import_products(body: ProductImport, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    source = record(actor, body.source_id, "Source", project=body.project_id)
    reader = csv.DictReader(io.StringIO(body.csv_text.lstrip("\ufeff")))
    if not reader.fieldnames or not {"SKU", "price", "currency", "valid_until"}.issubset(
        reader.fieldnames
    ):
        raise DomainError(
            "CSV_COLUMNS",
            "CSV 需要 SKU, price, currency, valid_until 列；可选 quantity, name, evidence。",
        )
    rows, errors, seen = [], [], set()
    for line, row in enumerate(reader, 2):
        if line > 1002:
            raise DomainError("IMPORT_LIMIT", "单批最多 1000 行；请拆分导入。")
        try:
            sku = (row.get("SKU") or "").strip()
            if not sku or sku in seen:
                raise ValueError("SKU 为空或在批内重复")
            price = Decimal(row.get("price", ""))
            if not price.is_finite() or price < 0:
                raise ValueError("价格需为非负有限数")
            currency = (row.get("currency") or "").upper().strip()
            if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
                raise ValueError("币种需为三个英文字母的明确代码")
            expiry = timestamp(row.get("valid_until", ""), "valid_until")
            quantity = int(row["quantity"]) if row.get("quantity") else None
            if quantity is not None and quantity < 0:
                raise ValueError("数量不能为负")
            evidence = (row.get("evidence") or "").strip()
            if not evidence and not source["data"].get("provenance"):
                raise ValueError("商品事实缺少证据内容")
            seen.add(sku)
            rows.append(
                {
                    "SKU": sku,
                    "price": str(price),
                    "currency": currency,
                    "quantity": quantity,
                    "name": row.get("name") or sku,
                    "validity": {"valid_until": expiry.isoformat()},
                    "evidence": evidence or source["data"].get("provenance"),
                    "evidence_source_id": source["id"],
                    "status": "expired" if expiry <= datetime.now(timezone.utc) else "recorded",
                }
            )
        except (ValueError, InvalidOperation, DomainError) as exc:
            errors.append({"line": line, "reason": str(exc)})
    created = []
    with get_store().transaction():
        for row in rows:
            created.append(
                save(actor, "Product", body.project_id, row, [ref(source)], "导入有来源的商品事实")
            )
    return ok(
        {"records": created, "errors": errors, "imported": len(created), "rejected": len(errors)},
        request,
    )


class TagInput(Body):
    project_id: str
    asset_version_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = Field(min_length=1, max_length=300)
    category: Literal[
        "subject", "SKU", "quantity", "action", "scene", "quality", "language", "editorial_role"
    ]
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=4000)
    locked: bool = True


@router.post("/tags")
def add_tag(body: TagInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    asset_version = record(actor, body.asset_version_id, "AssetVersion", project=body.project_id)
    if body.end <= body.start:
        raise DomainError("INVALID_RANGE", "镜头终点必须晚于起点。")
    duration = media_duration(asset_version["data"])
    if duration is not None and body.end > float(duration) + 0.05:
        raise DomainError("OUTSIDE_MEDIA", "标签区间超出真实素材时长。")
    with get_store().transaction():
        shot = save(
            actor,
            "Shot",
            body.project_id,
            {
                "asset_version_id": asset_version["id"],
                "source_in": body.start,
                "source_out": body.end,
                "source_timebase_num": 1,
                "source_timebase_den": 1000000,
                "source_pts_start": round(body.start * 1000000),
                "source_pts_end": round(body.end * 1000000),
            },
            [ref(asset_version)],
        )
        tag = save(
            actor,
            "Tag",
            body.project_id,
            {
                "shot_id": shot["id"],
                "subject_kind": "shot",
                "subject_ref": shot["id"],
                "interval": [body.start, body.end],
                "entity": body.category,
                body.category: body.label,
                "label": body.label,
                "confidence": body.confidence,
                "method": "manual",
                "schema_version": "atlas-6.0",
                "reviewer_lock": body.locked,
                "evidence_frame_refs": [],
                "evidence": body.evidence,
                "author": actor.actor_id,
                "status": "human_assertion",
            },
            [ref(shot), ref(asset_version, range=[body.start, body.end])],
        )
    return ok({"shot": shot, "tag": tag}, request)


@router.get("/search")
def search_assets(
    request: Request,
    project_id: str,
    q: str = "",
    min_duration: float = 0,
    max_duration: float = 86400,
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    if min_duration < 0 or max_duration < min_duration or not math.isfinite(max_duration):
        raise DomainError("INVALID_RANGE", "检索时长范围无效。")
    store = get_store()
    versions = store.list(actor.tenant_id, kind="AssetVersion", project_id=project_id, limit=500)
    tags = store.list(actor.tenant_id, kind="Tag", project_id=project_id, limit=500)
    by_version = defaultdict(list)
    for tag in tags:
        for link in tag.get("refs", []):
            if link.get("kind") == "AssetVersion":
                by_version[link["id"]].append(tag)
    hits = []
    for item in versions:
        d = item["data"]
        if d.get("storage_tier") in {"local_cold", "quarantine"}:
            continue
        if d.get("status") in {"revoked", "archived", "rejected", "unavailable"}:
            continue
        asset_id = d.get("asset_id")
        asset = record(actor, asset_id, "Asset") if asset_id else None
        if asset and asset["data"].get("status") in {"revoked", "archived"}:
            continue
        if asset and asset["data"].get("source_id"):
            source = record(actor, asset["data"]["source_id"], "Source")
            sd = source["data"]
            if (
                sd.get("status") == "revoked"
                or sd.get("revoked_at")
                or (
                    sd.get("expires_at")
                    and timestamp(sd["expires_at"]) <= datetime.now(timezone.utc)
                )
            ):
                continue
        from .media import check_current_media_rights

        try:
            check_current_media_rights(store, actor, item)
        except DomainError:
            continue
        duration = media_duration(d)
        if duration is None and asset:
            duration = media_duration(asset["data"])
        if duration is None and (min_duration > 0 or max_duration < 86400):
            continue
        if duration is not None and not min_duration <= float(duration) <= max_duration:
            continue
        text = json.dumps(
            [
                d,
                (asset or {}).get("data", {}).get("filename"),
                [t["data"] for t in by_version[item["id"]]],
            ],
            ensure_ascii=False,
        ).lower()
        if q.strip() and not all(token in text for token in q.lower().split()):
            continue
        hits.append(
            {
                "version": item,
                "asset": asset,
                "tags": by_version[item["id"]],
                "method": "structured_and_text",
                "reason": "关键词与真实标签/元数据匹配" if q else "符合结构化筛选",
            }
        )
    hits.sort(key=lambda h: h["version"]["data"].get("storage_tier") == "low_priority")
    return ok(
        {
            "items": hits,
            "count": len(hits),
            "method": "structured_and_text",
            "semantic_search": "not_connected",
            "limit": 500,
            "may_be_truncated": len(versions) == 500 or len(tags) == 500,
        },
        request,
    )


class ReservationInput(Body):
    project_id: str
    shot_id: str
    owner_ref: str
    ttl_seconds: int = Field(default=900, ge=10, le=86400)
    max_concurrent: int = Field(default=1, ge=1, le=100)


@router.post("/usage/reserve")
def reserve_usage(body: ReservationInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    shot = record(actor, body.shot_id, "Shot", project=body.project_id)
    owner = record(actor, body.owner_ref, project=body.project_id)
    with get_store().transaction():
        for key, kind in (("asset_version_id", "AssetVersion"), ("asset_id", "Asset")):
            if shot["data"].get(key):
                source = record(actor, shot["data"][key], kind, project=body.project_id)
                if source["data"].get("storage_tier") in {"local_cold", "quarantine"} and (
                    kind == "AssetVersion" or not shot["data"].get("asset_version_id")
                ):
                    raise DomainError("ASSET_OFFLINE", "不可预留已冷藏或隔离的素材。", 409)
        existing = get_store().list(
            actor.tenant_id, kind="Usage", project_id=body.project_id, limit=500
        )
        active = [
            u
            for u in existing
            if u["data"].get("shot_id") == shot["id"]
            and u["data"].get("reservation_status") == "reserved"
            and timestamp(u["data"].get("expires_at", "")) > datetime.now(timezone.utc)
        ]
        same = next((u for u in active if u["data"].get("owner_ref") == owner["id"]), None)
        if same:
            return ok(same, request)
        if len(existing) == 500:
            raise DomainError(
                "RESERVATION_SCAN_LIMIT", "预留账本超过本机检查界限，需要分区后重试。", 409
            )
        if len(active) >= body.max_concurrent:
            raise DomainError(
                "USAGE_RESERVED",
                "镜头正在被其他任务预留。",
                409,
                {"active": [u["id"] for u in active]},
            )
        item = save(
            actor,
            "Usage",
            body.project_id,
            {
                "shot_id": shot["id"],
                "owner_ref": owner["id"],
                "reservation_status": "reserved",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds)
                ).isoformat(),
                "exposure": None,
                "missing_reason": "尚未发布；预留不是实际曝光",
            },
            [ref(shot), ref(owner)],
        )
    return ok(item, request)


class ReleaseInput(Body):
    expected_revision: int = Field(ge=1)


@router.post("/usage/{usage_id}/release")
def release_usage(
    usage_id: str, body: ReleaseInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    item = record(actor, usage_id, "Usage")
    if item["version"] != body.expected_revision:
        raise DomainError("REVISION_CONFLICT", "预留版本已变化。", 409)
    if item["data"].get("reservation_status") == "committed":
        raise DomainError("ALREADY_COMMITTED", "已发生的使用不能通过释放预留抹除。", 409)
    return ok(
        revise(
            actor,
            item,
            {"reservation_status": "released", "released_at": now()},
            "释放未提交的素材预留",
        ),
        request,
    )


class ScoreInput(Body):
    project_id: str
    asset_version_id: str
    dimensions: dict[str, float | None]
    weights: dict[str, float] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


@router.post("/assets/score")
def score_asset(body: ScoreInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    from .score_policies import checked_weights, production_weights, weighted_score

    item = record(actor, body.asset_version_id, "AssetVersion", project=body.project_id)
    if body.weights is None:
        weights, policy_refs = production_weights(actor, body.project_id)
    else:
        weights, policy_refs = checked_weights(body.weights), []
    result, coverage = weighted_score(body.dimensions, weights)
    refs = [ref(item), *policy_refs] + [
        ref(record(actor, rid, project=body.project_id)) for rid in body.evidence_refs
    ]
    score = save(
        actor,
        "AssetScore",
        body.project_id,
        {
            "asset_version_ref": item["id"],
            "dimension_scores": body.dimensions,
            "observed_score": result,
            "coverage": coverage,
            "score_policy": policy_refs[0] if policy_refs else None,
            "weight_version": digest(weights),
            "weights": weights,
            "context_dimensions": body.context,
            "sample_n": len(body.evidence_refs),
            "input_snapshot_refs": body.evidence_refs,
            "uncertainty": "缺失维度未填零；分数为已观测维度加权均值，不是因果归因。",
            "evaluated_at": now(),
            "eligibility": "scored" if result is not None else "insufficient_evidence",
        },
        refs,
    )
    return ok(score, request)


class AlignmentInput(Body):
    project_id: str
    script_id: str
    audio_id: str
    anchors: list[dict] = Field(min_length=1, max_length=10000)


@router.post("/voice/align")
def align_voice(body: AlignmentInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    script = record(actor, body.script_id, "Script", project=body.project_id)
    audio = record(actor, body.audio_id, "Audio", project=body.project_id)
    text = script["data"].get("text", "")
    duration = media_duration(audio["data"])
    cleaned, previous = [], 0.0
    for i, anchor in enumerate(body.anchors):
        token = str(anchor.get("token", "")).strip()
        if not token or token not in text:
            raise DomainError("SCRIPT_MISMATCH", f"锚点 {i + 1} 的词句未出现在冻结脚本中。")
        status = anchor.get("timing_status", "estimated")
        if status not in {"untimed", "estimated", "aligned", "verified"}:
            raise DomainError("INVALID_STATUS", "口播锚点状态无效。")
        start = end = None
        if status != "untimed":
            if duration is None:
                raise DomainError(
                    "AUDIO_DURATION_UNVERIFIED",
                    "有时码锚点必须先获得真实音轨时长；可以保留无时码草稿。",
                )
            start = number(anchor.get("start"), "start", 0)
            end = number(anchor.get("end"), "end", 0)
            if (
                end <= start
                or start < previous
                or (duration is not None and end > float(duration) + 0.05)
            ):
                raise DomainError("ALIGNMENT_RANGE", "锚点需顺序排列，不可重叠或超出真实音轨。")
            previous = end
        if status == "verified" and not anchor.get("evidence"):
            raise DomainError("EVIDENCE_REQUIRED", "核验过的锚点必须附人工核验依据。")
        cleaned.append(
            {
                "token": token,
                "time_range": [start, end] if start is not None else None,
                "timing_status": status,
                "align_method": "manual",
                "confidence": anchor.get("confidence"),
                "evidence": anchor.get("evidence"),
                "semantic": anchor.get("semantic", {}),
                "anchor_id": f"anchor-{i + 1}",
            }
        )
    result = save(
        actor,
        "VoiceMap",
        body.project_id,
        {
            "audio_version": ref(audio),
            "script_version": ref(script),
            "anchors": cleaned,
            "audio_hash": audio["data"].get("content_hash") or audio["data"].get("bytes_hash"),
            "audio_hash_algorithm": "sha256",
            "timebase_num": 1,
            "timebase_den": 1000000,
            "validation_status": "manual_timing_recorded",
            "semantic_validation": "requires_visual_evidence",
        },
        [ref(script), ref(audio)],
    )
    return ok(result, request)


class TraceInput(Body):
    time_map_id: str
    output_seconds: float = Field(ge=0)


@router.post("/time-map/trace")
def trace_time(body: TraceInput, request: Request, actor: Actor = Depends(current_actor)):
    mapping = record(actor, body.time_map_id, "TimeMap")
    matches, omissions = [], []

    def timebase(value):
        if not isinstance(value, dict) or not value.get("num") or not value.get("den"):
            raise DomainError("TIMEBASE_MISSING", "时码映射缺少明确的有理数时基。")
        try:
            result = Fraction(str(value["num"])) / Fraction(str(value["den"]))
        except (ValueError, ZeroDivisionError):
            raise DomainError("TIMEBASE_INVALID", "时码映射的有理数时基无效。")
        if result <= 0:
            raise DomainError("TIMEBASE_INVALID", "时基必须大于零。")
        return result

    for segment in mapping["data"].get("segments", []):
        if "output_interval" in segment:
            unit = timebase(mapping["data"].get("target_timebase"))
            output_tick = Fraction(str(body.output_seconds)) / unit
            lo, hi = (Fraction(str(t)) for t in segment["output_interval"])
            if not lo <= output_tick < hi or hi <= lo:
                continue
            for contribution in segment.get("contributions", []):
                curve = contribution.get("mapping_curve", {})
                if curve.get("kind", "linear") not in {"linear", "constant", "hold"}:
                    omissions.append(
                        {
                            "segment_id": segment.get("segment_id"),
                            "source_ref": contribution.get("source_ref"),
                            "reason": "unsupported_mapping_curve",
                        }
                    )
                    continue
                source_ref = contribution.get("source_ref")
                source_id = source_ref.get("id") if isinstance(source_ref, dict) else source_ref
                source_timebases = mapping["data"].get("source_timebase", {})
                source_base = (
                    contribution.get("source_timebase")
                    or source_timebases.get(source_id)
                    or source_timebases
                )
                source_unit = timebase(source_base)
                source_interval = contribution.get("source_interval")
                if not isinstance(source_interval, list) or len(source_interval) != 2:
                    omissions.append(
                        {
                            "segment_id": segment.get("segment_id"),
                            "source_ref": source_ref,
                            "reason": "source_interval_missing",
                        }
                    )
                    continue
                start, end = (Fraction(str(t)) for t in source_interval)
                source_tick = (
                    start
                    if curve.get("kind") in {"constant", "hold"}
                    else start + (output_tick - lo) * (end - start) / (hi - lo)
                )
                matches.append(
                    {
                        "source_ref": source_ref,
                        "source_seconds": float(source_tick * source_unit),
                        "source_tick": float(source_tick),
                        "source_timebase": source_base,
                        "source_interval": source_interval,
                        "output_interval": segment["output_interval"],
                        "target_timebase": mapping["data"]["target_timebase"],
                        "contribution": contribution.get("role", "unknown"),
                        "weight_curve": contribution.get("weight_curve"),
                        "occlusion_evaluated": contribution.get("occlusion_evaluated", False),
                        "mapping_quality": contribution.get(
                            "precision",
                            segment.get(
                                "mapping_quality", mapping["data"].get("mapping_quality", "unknown")
                            ),
                        ),
                    }
                )
            continue
        output = segment.get("output_range") or segment.get("target_range")
        source = segment.get("source_range")
        if not output or not source or len(output) != 2 or len(source) != 2:
            continue
        lo, hi = map(float, output)
        if lo <= body.output_seconds < hi and hi > lo:
            t = (body.output_seconds - lo) / (hi - lo)
            matches.append(
                {
                    "source_ref": segment.get("source_ref")
                    or segment.get("input_ref")
                    or segment.get("asset_version_id"),
                    "source_seconds": float(source[0]) + t * (float(source[1]) - float(source[0])),
                    "output_range": output,
                    "source_range": source,
                    "contribution": segment.get("contribution", "video"),
                    "mapping_quality": segment.get(
                        "mapping_quality", mapping["data"].get("mapping_quality", "unknown")
                    ),
                }
            )
    return ok(
        {
            "mapping": ref(mapping),
            "output_seconds": body.output_seconds,
            "contributors": matches,
            "status": "partial" if matches and omissions else "mapped" if matches else "unmapped",
            "omissions": omissions,
            "interval_convention": "half-open [start,end)",
            "boundary": "按实际记录的时基与支持的曲线反查；层权重不等于最终逐像素可见贡献。",
        },
        request,
    )


class ApprovalInput(Body):
    subject_id: str
    subject_version: int = Field(ge=1)
    kind: Literal["content_check", "rights_check", "review_finding", "execution_authorization"]
    decision: Literal["approved", "rejected"]
    evidence: str = Field(min_length=1, max_length=20000)
    expires_at: str
    operation: str = "review"
    account_id: str | None = None
    metadata_hash: str | None = None


@router.post("/approvals")
def approve(body: ApprovalInput, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    subject = record(actor, body.subject_id, version=body.subject_version)
    expiry = timestamp(body.expires_at)
    if expiry <= datetime.now(timezone.utc):
        raise DomainError("EXPIRED_APPROVAL", "确认单到期时间必须在未来。")
    refs = [ref(subject)]
    if body.account_id:
        refs.append(ref(record(actor, body.account_id, "Account", project=subject["project_id"])))
    result = save(
        actor,
        "Approval",
        subject["project_id"],
        {
            "principal": actor.actor_id,
            "actor_role": actor.role,
            "subject_kind": subject["kind"],
            "subject_id": subject["id"],
            "subject_version": subject["version"],
            "subject_hash": subject["hash"],
            "artifact_hash": subject["data"].get("checksum"),
            "kind": body.kind,
            "status": body.decision,
            "decision": body.decision,
            "scope": {"operation": body.operation, "account_id": body.account_id},
            "operation": body.operation,
            "account_id": body.account_id,
            "metadata_hash": body.metadata_hash,
            "expires_at": body.expires_at,
            "review_evidence_refs": [],
            "evidence": body.evidence,
            "attestation_kind": "human_review",
            "reviewed_at": now(),
        },
        refs,
        "对冻结版本作明确人工确认",
    )
    return ok(result, request)


def resolve_schedule(local: str, zone: str, fold: int | None) -> dict:
    try:
        tz = ZoneInfo(zone)
        naive = datetime.fromisoformat(local)
        if naive.tzinfo is not None:
            raise ValueError("local time must be naive")
    except (ValueError, ZoneInfoNotFoundError):
        raise DomainError("INVALID_SCHEDULE", "请使用不带偏移的本地时间和有效 IANA 时区。")
    candidates = []
    for f in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=f)
        back = aware.astimezone(timezone.utc).astimezone(tz)
        if back.replace(tzinfo=None) == naive:
            candidates.append((f, aware))
    if not candidates:
        raise DomainError("NONEXISTENT_LOCAL_TIME", "夏令时切换导致此本地时间不存在。")
    ambiguous = len({x[1].utcoffset() for x in candidates}) > 1
    if ambiguous and fold not in (0, 1):
        raise DomainError(
            "AMBIGUOUS_LOCAL_TIME", "此时间出现两次；请选择第一次 fold=0 或第二次 fold=1。"
        )
    chosen = next((x[1] for x in candidates if x[0] == (fold or 0)), candidates[0][1])
    return {
        "requested_local_datetime": local,
        "timezone": zone,
        "fold": fold or 0,
        "scheduled_at": chosen.astimezone(timezone.utc).isoformat(),
        "utc_offset": str(chosen.utcoffset()),
    }


class PublicationInput(Body):
    project_id: str
    artifact_id: str
    account_id: str
    title: str = Field(min_length=1, max_length=2200)
    channel: Literal["manual", "tiktok_direct", "tiktok_inbox"] = "manual"
    privacy: str | None = None
    local_datetime: str | None = None
    timezone: str = "Asia/Shanghai"
    fold: int | None = Field(default=None, ge=0, le=1)
    disclosures: dict = Field(default_factory=dict)


@router.post("/publications")
def create_publication(
    body: PublicationInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    artifact = record(actor, body.artifact_id, "Artifact", project=body.project_id)
    account = record(actor, body.account_id, "Account", project=body.project_id)
    metadata = {"title": body.title, "privacy": body.privacy, "disclosures": body.disclosures}
    schedule = (
        resolve_schedule(body.local_datetime, body.timezone, body.fold)
        if body.local_datetime
        else {}
    )
    publication = save(
        actor,
        "Publication",
        body.project_id,
        {
            "account_id": account["id"],
            "artifact_id": artifact["id"],
            "artifact_version": artifact["version"],
            "artifact_hash": artifact["hash"],
            "metadata": metadata,
            "metadata_hash": digest(metadata),
            "channel": body.channel,
            "status": "draft",
            "outcome": "not_sent",
            "expected_post_count": None,
            "post_refs": [],
            "title": body.title,
            **schedule,
        },
        [ref(artifact), ref(account)],
    )
    return ok(publication, request)


def publication_preflight(actor: Actor, publication: dict) -> dict:
    data, problems, checks = publication["data"], [], []
    artifact = record(actor, data["artifact_id"], "Artifact", version=data.get("artifact_version"))
    account = record(actor, data["account_id"], "Account")
    from .media import check_current_media_rights

    try:
        check_current_media_rights(get_store(), actor, artifact, purpose="export")
        checks.append({"check": "current_media_rights", "passed": True})
    except DomainError as error:
        checks.append({"check": "current_media_rights", "passed": False})
        problems.append({"code": error.code, "message": error.message, "details": error.details})
    if artifact["hash"] != data.get("artifact_hash"):
        problems.append({"code": "ARTIFACT_CHANGED", "message": "发布意图的冻结产物不一致。"})
    ad = artifact["data"]
    quality = ad.get("quality_report", {})
    technical = (
        quality.get("technical_passed") is True
        or quality.get("status") == "technical_passed"
        or ad.get("status") in {"technical_passed", "qc_passed"}
    )
    if not technical:
        problems.append({"code": "MEDIA_QC_REQUIRED", "message": "缺少真实媒体技术质检通过记录。"})
    approvals = get_store().list(
        actor.tenant_id, kind="Approval", project_id=publication["project_id"], limit=500
    )
    valid = [
        a
        for a in approvals
        if a["data"].get("subject_id") == artifact["id"]
        and a["data"].get("subject_version") == artifact["version"]
        and a["data"].get("subject_hash") == artifact["hash"]
        and a["data"].get("status") == "approved"
        and a["data"].get("expires_at")
        and timestamp(a["data"]["expires_at"]) > datetime.now(timezone.utc)
    ]
    for kind in ("content_check", "rights_check"):
        found = [a for a in valid if a["data"].get("kind") == kind]
        checks.append(
            {"check": kind, "passed": bool(found), "evidence_refs": [a["id"] for a in found]}
        )
        if not found:
            problems.append(
                {
                    "code": kind.upper() + "_REQUIRED",
                    "message": "当前产物需要未过期的"
                    + ("内容" if kind == "content_check" else "素材与音乐使用权")
                    + "确认。",
                }
            )
    # Traverse the actual frozen version graph; source revocation is current, not frozen.
    seen, queue = set(), [artifact]
    while queue and len(seen) < 1000:
        item = queue.pop()
        key = (item["id"], item["version"])
        if key in seen:
            continue
        seen.add(key)
        if item["kind"] in {"Source", "Grant", "Product"}:
            current = record(actor, item["id"])
            problem = current_dependency_problem(current)
            if problem:
                problems.append(problem)
            revision = current["data"].get("content_change_version", 0)
            if item["kind"] == "Source" and (
                type(revision) is not int or revision > item["version"]
            ):
                problems.append(
                    {
                        "code": "SOURCE_CONTENT_CHANGED",
                        "message": "产物引用了修订前的来源，需复核并绑定新版本。",
                        "record_id": item["id"],
                    }
                )
            if (
                item["kind"] == "Source"
                and current["data"].get("origin_availability") == "removed_at_origin"
            ):
                checks.append(
                    {
                        "check": "origin_removed",
                        "record_id": item["id"],
                        "action": "review_archived_source",
                        "origin_change_alone_revokes_rights": False,
                    }
                )
        owner = record(actor, item["id"]) if item["kind"] in {"Source", "Grant"} else item
        for link in owner.get("refs", []):
            queue.append(record(actor, link["id"], version=link.get("version")))
    if queue:
        problems.append(
            {"code": "DEPENDENCY_SCAN_LIMIT", "message": "依赖超过本机核查上限，不能跳过。"}
        )
    if data.get("outcome") in {
        "outcome_unknown",
        "submitted",
        "accepted",
        "manual_reported",
        "published",
    }:
        problems.append(
            {
                "code": "RECONCILE_FIRST",
                "message": "已发出或结果未知，必须核对原发布意图，禁止重复发送。",
            }
        )
    if data.get("channel") != "manual":
        capability = account["data"].get("api_connection_status")
        if capability != "verified":
            problems.append(
                {
                    "code": "PROVIDER_NOT_VERIFIED",
                    "message": "账号 API 能力未实测，当前可以使用人工交接路线。",
                }
            )
        problems.append(
            {
                "code": "LIVE_PUBLISH_NOT_ENABLED",
                "message": "本版本未开放自动外发；保留平台适配合同及人工交接/回执链路。",
            }
        )
    return {
        "publication_id": publication["id"],
        "version": publication["version"],
        "can_handoff": not problems,
        "can_send": False,
        "problems": problems,
        "checks": checks,
        "checked_at": now(),
        "source": "frozen_artifact_current_rights_and_capabilities",
        "scanned_versions": len(seen),
    }


@router.get("/publications/{publication_id}/preflight")
def preflight(publication_id: str, request: Request, actor: Actor = Depends(current_actor)):
    return ok(publication_preflight(actor, record(actor, publication_id, "Publication")), request)


class ReceiptInput(Body):
    expected_revision: int = Field(ge=1)
    platform_post_id: str = Field(min_length=1, max_length=300)
    post_url: str
    observed_at: str
    evidence: str = Field(min_length=1, max_length=20000)
    visibility: Literal["unknown", "private", "public", "removed"] = "unknown"


@router.post("/publications/{publication_id}/receipt")
def manual_receipt(
    publication_id: str, body: ReceiptInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    if urlparse(body.post_url).scheme not in {"https", "http"}:
        raise DomainError("INVALID_RECEIPT_URL", "回执链接必须是 http/https URL。")
    if timestamp(body.observed_at) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise DomainError("FUTURE_OBSERVATION", "不能填入尚未发生的回执观测时间。")
    with get_store().transaction():
        pub = record(actor, publication_id, "Publication")
        if pub["version"] != body.expected_revision:
            raise DomainError("REVISION_CONFLICT", "发布意图已改变，请刷新。", 409)
        account = record(actor, pub["data"]["account_id"], "Account")
        with get_store().connection() as connection:
            duplicate = connection.execute(
                """SELECT r.id FROM records r JOIN revisions v
                ON r.tenant_id=v.tenant_id AND r.id=v.record_id AND r.latest_version=v.version
                WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Post'
                AND json_extract(v.data_json,'$.account_id')=? AND json_extract(v.data_json,'$.platform_post_id')=? LIMIT 1""",
                (actor.tenant_id, pub["project_id"], account["id"], body.platform_post_id),
            ).fetchone()
        repeated = record(actor, duplicate[0], "Post") if duplicate else None
        if repeated:
            if repeated["data"].get("publication_id") != pub["id"]:
                raise DomainError(
                    "POST_ALREADY_ASSOCIATED", "该帖子已关联另一个发布意图，需要人工核对关联。", 409
                )
            return ok({"post": repeated, "publication": pub, "duplicate": True}, request)
        event = save(
            actor,
            "PlatformEvent",
            pub["project_id"],
            {
                "provider": account["data"].get("provider", "manual"),
                "publication_id": pub["id"],
                "event_type": "manual_receipt",
                "event_time": body.observed_at,
                "received_at": now(),
                "source_kind": "manual",
                "signature_result": "not_applicable",
                "evidence": body.evidence,
                "dedupe_key": digest([account["id"], body.platform_post_id, "manual_receipt"]),
                "post_url": body.post_url,
            },
            [ref(pub), ref(account)],
        )
        post = save(
            actor,
            "Post",
            pub["project_id"],
            {
                "provider": account["data"].get("provider", "manual"),
                "account_id": account["id"],
                "platform_post_id": body.platform_post_id,
                "publication_id": pub["id"],
                "submitted_artifact_id": pub["data"]["artifact_id"],
                "final_media_status": "unknown",
                "post_url": body.post_url,
                "last_observed_at": body.observed_at,
                "visibility": body.visibility,
                "association_status": "manual_reported",
                "correlation_evidence": event["id"],
                "observation_permission": "manual_attestation",
                "independently_verified": False,
            },
            [ref(pub), ref(account), ref(event)],
        )
        updated = revise(
            actor,
            pub,
            {
                "post_refs": [*pub["data"].get("post_refs", []), post["id"]],
                "outcome": "manual_reported",
                "status": "receipt_recorded",
            },
            "记录人工回执；不伪称平台独立确认",
            [*pub.get("refs", []), ref(post)],
        )
    return ok({"post": post, "publication": updated, "event": event, "duplicate": False}, request)


class MetricRow(Body):
    account_id: str
    post_record_id: str | None = None
    metric_name: str = Field(min_length=1, max_length=100)
    value: float | None = None
    missing_reason: str | None = None
    unit: str = Field(min_length=1, max_length=100)
    window_start: str
    window_end: str
    observed_at: str
    source: str = Field(min_length=1, max_length=2000)
    currency: str | None = None
    paid_or_organic: Literal["paid", "organic", "mixed", "unknown"] = "unknown"
    denominator: float | None = Field(default=None, ge=0)
    data_mature_after: str | None = None
    counting_mode: Literal["cumulative", "interval"] | None = None


class MetricImport(Body):
    project_id: str
    rows: list[MetricRow] = Field(min_length=1, max_length=1000)


@router.post("/metrics/import")
def import_metrics(body: MetricImport, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    return ok(store_metric_rows(body, actor), request)


def store_metric_rows(body, actor, archive=None):
    records, omitted = [], []
    with get_store().transaction():
        fingerprints = set()
        for index, row in enumerate(body.rows):
            account = record(actor, row.account_id, "Account", project=body.project_id)
            refs = [ref(account), *([ref(archive)] if archive else [])]
            if row.post_record_id:
                post = record(actor, row.post_record_id, "Post", project=body.project_id)
                if post["data"].get("account_id") != account["id"]:
                    raise DomainError("METRIC_ACCOUNT_MISMATCH", "指标帖子与账号不一致。")
                refs.append(ref(post))
            start, end, seen = (
                timestamp(row.window_start),
                timestamp(row.window_end),
                timestamp(row.observed_at),
            )
            if (
                end <= start
                or seen < end
                or seen > datetime.now(timezone.utc) + timedelta(minutes=5)
            ):
                raise DomainError("METRIC_WINDOW", "指标窗口需有序且不能晚于观测时间。")
            if row.value is None and not row.missing_reason:
                raise DomainError(
                    "MISSING_REASON_REQUIRED", "空指标必须说明未取到的原因；不能补零。"
                )
            if row.value is not None and row.missing_reason:
                raise DomainError("CONTRADICTORY_METRIC", "有观测值时不应同时标记缺失。")
            if row.data_mature_after:
                timestamp(row.data_mature_after)
            snapshot = {
                **row.model_dump(exclude={"counting_mode"} if row.counting_mode is None else set()),
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "observed_at": seen.isoformat(),
                "data_mature_after": (
                    timestamp(row.data_mature_after).isoformat() if row.data_mature_after else None
                ),
            }
            fingerprint = digest(snapshot)
            with get_store().connection() as connection:
                duplicate = connection.execute(
                    """SELECT 1 FROM records r JOIN revisions v
                    ON r.tenant_id=v.tenant_id AND r.id=v.record_id AND r.latest_version=v.version
                    WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Metric'
                    AND json_extract(v.data_json,'$.intake_fingerprint')=? LIMIT 1""",
                    (actor.tenant_id, body.project_id, fingerprint),
                ).fetchone()
            if fingerprint in fingerprints or duplicate:
                omitted.append({"row": index, "reason": "same_snapshot_already_recorded"})
                continue
            fingerprints.add(fingerprint)
            data = {
                **snapshot,
                "value_or_null": row.value,
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "scope_kind": "post" if row.post_record_id else "account",
                "scope_subject_ref": row.post_record_id or row.account_id,
                "intake_fingerprint": fingerprint,
                "attribution_rule": "source_reported_not_causal",
                "timezone": "UTC",
            }
            if archive:
                data["archive_row"] = index + 1
                data["archive_ref"] = ref(archive)
            records.append(
                save(
                    actor, "Metric", body.project_id, data, refs, "导入保留来源和口径的真实指标快照"
                )
            )
    return {"records": records, "duplicates": omitted, "count": len(records)}


@router.get("/analytics")
def analytics(request: Request, project_id: str, actor: Actor = Depends(current_actor)):
    record(actor, project_id, "Project")
    items = get_store().list(actor.tenant_id, kind="Metric", project_id=project_id, limit=500)
    latest = {}
    for item in items:
        d = item["data"]
        key = (
            d.get("scope_kind"),
            d.get("scope_subject_ref") or d.get("post_record_id") or d.get("account_id"),
            d.get("metric_name"),
            d.get("unit"),
            d.get("currency"),
            json.dumps(normalized_window(d["window"]), sort_keys=True),
            d.get("paid_or_organic"),
            d.get("source"),
        )
        if key not in latest or timestamp(d["observed_at"]) > timestamp(
            latest[key]["data"]["observed_at"]
        ):
            latest[key] = item
    grouped = defaultdict(list)
    for key, item in latest.items():
        grouped[(key[0], *key[2:])].append(item)
    groups = []
    for key, rows in grouped.items():
        values = [
            r["data"].get("value_or_null")
            for r in rows
            if r["data"].get("value_or_null") is not None
        ]
        mature = all(
            not r["data"].get("data_mature_after")
            or timestamp(r["data"]["data_mature_after"]) <= datetime.now(timezone.utc)
            for r in rows
        )
        groups.append(
            {
                "scope_kind": key[0],
                "metric_name": key[1],
                "unit": key[2],
                "currency": key[3],
                "window": json.loads(key[4]),
                "paid_or_organic": key[5],
                "source": key[6],
                "observations": len(values),
                "missing": len(rows) - len(values),
                "mean": statistics.mean(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "mature": mature,
                "metric_refs": [r["id"] for r in rows],
            }
        )
    return ok(
        {
            "groups": groups,
            "snapshot_count": len(items),
            "deduplicated_observations": len(latest),
            "causal_claim": False,
            "cross_scope_totals": "not_computed_to_avoid_double_counting",
            "may_be_truncated": len(items) == 500,
        },
        request,
    )


class ExperimentInput(Body):
    project_id: str
    name: str = Field(min_length=1, max_length=300)
    hypothesis: str = Field(min_length=1, max_length=10000)
    unit_ids: list[str] = Field(min_length=4, max_length=1000)
    seed: str = Field(min_length=1, max_length=200)
    metric_name: str
    min_units_per_arm: int = Field(default=2, ge=2, le=500)
    stop_rule: str = Field(min_length=1, max_length=3000)
    window_start: str
    window_end: str


@router.post("/experiments")
def create_experiment(
    body: ExperimentInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    if len(set(body.unit_ids)) != len(body.unit_ids):
        raise DomainError("DUPLICATE_UNIT", "实验单位不能重复。")
    if timestamp(body.window_end) <= timestamp(body.window_start):
        raise DomainError("INVALID_WINDOW", "实验观察窗口无效。")
    units = [record(actor, rid, project=body.project_id) for rid in body.unit_ids]
    ranked = sorted(body.unit_ids, key=lambda rid: digest([body.seed, rid]))
    assignments = {rid: ("baseline" if i % 2 == 0 else "candidate") for i, rid in enumerate(ranked)}
    exp = save(
        actor,
        "Experiment",
        body.project_id,
        {
            "name": body.name,
            "hypothesis": body.hypothesis,
            "unit": "declared_record",
            "assignment": assignments,
            "assignment_method": "seeded_hash_balanced",
            "seed_hash": digest(body.seed),
            "metric_name": body.metric_name,
            "min_units_per_arm": body.min_units_per_arm,
            "stop_rule": body.stop_rule,
            "windows": normalized_window({"start": body.window_start, "end": body.window_end}),
            "guardrails": [],
            "status": "planned",
            "result": None,
        },
        [ref(unit) for unit in units],
        "冻结实验分配；未伪造观测或因果结论",
    )
    return ok(exp, request)


class EvaluationInput(Body):
    expected_revision: int = Field(ge=1)
    metric_ids: list[str] = Field(min_length=1, max_length=1000)


@router.post("/experiments/{experiment_id}/evaluate")
def evaluate_experiment(
    experiment_id: str,
    body: EvaluationInput,
    request: Request,
    actor: Actor = Depends(current_actor),
):
    require_write(actor)
    from .score_policies import PREFIX as SCORE_RELEASE_PREFIX

    if experiment_id.startswith(SCORE_RELEASE_PREFIX):
        raise DomainError("FROZEN_EXPERIMENT", "评分策略回归使用固定的逐例结果。", 409)
    exp = record(actor, experiment_id, "Experiment")
    if exp["version"] != body.expected_revision:
        raise DomainError("REVISION_CONFLICT", "实验定义已更新。", 409)
    assignments = exp["data"]["assignment"]
    metrics = [record(actor, rid, "Metric", project=exp["project_id"]) for rid in body.metric_ids]
    by_unit, exclusions, signatures = {}, [], set()
    for metric in metrics:
        d = metric["data"]
        unit = d.get("scope_subject_ref") or d.get("post_record_id") or d.get("account_id")
        if unit not in assignments or d.get("metric_name") != exp["data"]["metric_name"]:
            raise DomainError("EXPERIMENT_SCOPE", "指标不属于冻结实验单位或目标指标。")
        if normalized_window(d["window"]) != normalized_window(exp["data"]["windows"]):
            raise DomainError("EXPERIMENT_WINDOW", "指标窗口必须与冻结实验窗口精确相同。")
        if unit not in by_unit or timestamp(d["observed_at"]) > timestamp(
            by_unit[unit]["data"]["observed_at"]
        ):
            by_unit[unit] = metric
    eligible = {}
    for unit, metric in by_unit.items():
        d = metric["data"]
        if d.get("value_or_null") is None or (
            d.get("data_mature_after")
            and timestamp(d["data_mature_after"]) > datetime.now(timezone.utc)
        ):
            exclusions.append({"id": metric["id"], "reason": "missing_or_immature"})
            continue
        signatures.add(
            (d.get("unit"), d.get("currency"), d.get("paid_or_organic"), d.get("source"))
        )
        eligible[unit] = metric
    if len(signatures) > 1:
        raise DomainError(
            "INCOMPARABLE_METRICS", "币种、单位、来源与付费/自然口径不一致，禁止强行比较。"
        )
    arms = {
        name: [
            m["data"]["value_or_null"] for uid, m in eligible.items() if assignments[uid] == name
        ]
        for name in ("baseline", "candidate")
    }
    minimum = exp["data"].get("min_units_per_arm", 2)
    sufficient = all(len(v) >= minimum for v in arms.values())
    result = {
        "status": "descriptive_comparison" if sufficient else "insufficient_evidence",
        "n": {k: len(v) for k, v in arms.items()},
        "means": {k: statistics.mean(v) if v else None for k, v in arms.items()},
        "exclusions": exclusions,
        "difference": None,
        "standard_error": None,
        "causal_claim": False,
        "limitations": [
            "仅报告已观测单位的描述性差异",
            "分配执行、干扰、选择偏差与停止规则还需审查",
            "缺失和未成熟指标不补零",
        ],
    }
    if sufficient:
        result["difference"] = result["means"]["candidate"] - result["means"]["baseline"]
        result["standard_error"] = math.sqrt(
            sum(statistics.variance(v) / len(v) for v in arms.values())
        )
    updated = revise(
        actor,
        exp,
        {"result": result, "status": "evaluated", "evaluated_at": now()},
        "按冻结实验与真实指标形成有边界的结果",
        [*exp.get("refs", []), *[ref(m) for m in metrics]],
    )
    return ok(updated, request)


@router.get("/trace/{record_id}")
def lineage(
    record_id: str,
    request: Request,
    direction: Literal["upstream", "downstream", "both"] = "upstream",
    actor: Actor = Depends(current_actor),
):
    root = record(actor, record_id)
    nodes, edges, seen, edge_keys, queue = [], [], set(), set(), deque([root])
    enqueued = {(root["id"], root["version"])}
    scan_limited = False

    def enqueue(item: dict):
        nonlocal scan_limited
        identity = (item["id"], item["version"])
        if identity not in enqueued:
            if len(enqueued) >= 1000:
                scan_limited = True
                return
            enqueued.add(identity)
            queue.append(item)

    def add_edge(parent: dict, child: dict, link: dict):
        source, target = f"{parent['id']}@{parent['version']}", f"{child['id']}@{child['version']}"
        edge_key = (source, target, link.get("field"), json.dumps(link.get("range")))
        if edge_key not in edge_keys and len(edges) < 5000:
            edge_keys.add(edge_key)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "field": link.get("field"),
                    "range": link.get("range"),
                    "consumed_fields": link.get("fields", []),
                }
            )

    while queue and len(nodes) < 1000:
        item = queue.popleft()
        key = f"{item['id']}@{item['version']}"
        if key in seen:
            continue
        seen.add(key)
        nodes.append(
            {
                "key": key,
                "id": item["id"],
                "version": item["version"],
                "kind": item["kind"],
                "hash": item["hash"],
                "created_at": item["created_at"],
                "name": item["data"].get("name") or item["data"].get("title"),
            }
        )
        if direction in {"upstream", "both"}:
            for r in item.get("refs", []):
                parent = record(actor, r["id"], version=r.get("version"))
                add_edge(parent, item, r)
                enqueue(parent)
        if direction in {"downstream", "both"}:
            with get_store().connection() as connection:
                rows = connection.execute(
                    "SELECT * FROM reference_index WHERE tenant_id=? AND target_id=? AND target_version=? ORDER BY source_id,source_version LIMIT 1001",
                    (actor.tenant_id, item["id"], item["version"]),
                ).fetchall()
            for row in rows:
                child = record(actor, row["source_id"], version=row["source_version"])
                add_edge(
                    item,
                    child,
                    {
                        "field": row["field_name"],
                        "range": json.loads(row["range_json"]) if row["range_json"] else None,
                        "fields": json.loads(row["consumed_fields_json"]),
                    },
                )
                enqueue(child)
            if len(rows) > 1000:
                scan_limited = True
    return ok(
        {
            "root": ref(root),
            "nodes": nodes,
            "edges": edges,
            "direction": direction,
            "truncated": bool(queue) or scan_limited or len(edges) >= 5000,
            "boundary": "仅使用已提交的真实版本引用；功能导航关系不作为生产谱系。",
        },
        request,
    )


class ReplayInput(Body):
    project_id: str
    root_ids: list[str] = Field(min_length=1, max_length=100)
    mode: Literal["inspect", "recorded_responses", "deterministic_media"] = "inspect"


@router.post("/replay/prepare")
def prepare_replay(body: ReplayInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    roots = [record(actor, rid, project=body.project_id) for rid in body.root_ids]
    if any(r["kind"] not in {"TransformRun", "RenderManifest"} for r in roots):
        raise DomainError(
            "REPLAY_ROOT_KIND",
            "重放须从真实 TransformRun 或 RenderManifest 开始，不能把任意记录当作已执行结果。",
        )
    from .media import blob_path, hash_file, check_current_media_rights

    seen, dependencies, missing, byte_checks, queue = set(), [], [], [], roots[:]
    for root in roots:
        try:
            check_current_media_rights(get_store(), actor, root, purpose="process")
        except DomainError as error:
            missing.append({"code": error.code, "record_id": root["id"], "details": error.details})
        required = (
            ("recipe_id", "source_ref", "output_ref", "tool_versions", "finished_at")
            if root["kind"] == "TransformRun"
            else ("artifact_id", "timeline_ref", "output_hash", "tool_versions", "finished_at")
        )
        absent = [field for field in required if not root["data"].get(field)]
        if absent:
            missing.append(
                {"code": "EXECUTION_EVIDENCE_MISSING", "record_id": root["id"], "fields": absent}
            )
    while queue and len(dependencies) < 1000:
        item = queue.pop()
        key = (item["id"], item["version"])
        if key in seen:
            continue
        seen.add(key)
        dependencies.append({**ref(item), "hash": item["hash"]})
        if item["kind"] in {"Source", "Grant", "Product", "Asset", "AssetVersion", "Audio"}:
            current = record(actor, item["id"])
            problem = current_dependency_problem(current)
            if problem:
                missing.append(problem)
            if item["kind"] in {"Source", "Grant"} and current["version"] != item["version"]:
                queue.append(current)
        if item["kind"] == "BlobLocation":
            try:
                path = blob_path(item, actor.tenant_id)
                expected = item["data"].get("checksum") or item["data"].get("content_hash")
                actual = hash_file(path)
                valid = (
                    bool(expected)
                    and actual == expected
                    and path.stat().st_size == item["data"].get("size_bytes")
                )
                byte_checks.append(
                    {
                        **ref(item),
                        "expected_sha256": expected,
                        "actual_sha256": actual,
                        "size_bytes": path.stat().st_size,
                        "passed": valid,
                    }
                )
                if not valid:
                    missing.append({"code": "OBJECT_CORRUPTION", "record_id": item["id"]})
            except DomainError as exc:
                missing.append({"code": exc.code, "record_id": item["id"]})
            except OSError:
                missing.append({"code": "OBJECT_UNREADABLE", "record_id": item["id"]})
        for link in item.get("refs", []):
            try:
                queue.append(record(actor, link["id"], version=link.get("version")))
            except DomainError as exc:
                missing.append(
                    {"code": exc.code, "record_id": link["id"], "version": link.get("version")}
                )
    if queue:
        raise DomainError("REPLAY_GRAPH_LIMIT", "重放依赖超过本机检查上限。", 409)
    manifest = save(
        actor,
        "ReplayManifest",
        body.project_id,
        {
            "root_refs": [ref(r) for r in roots],
            "dependency_refs": dependencies,
            "mode": body.mode,
            "side_effect_policy": "deny_external_writes",
            "missing_dependencies": missing,
            "byte_checks": byte_checks,
            "manifest_hash": digest({"dependencies": dependencies, "byte_checks": byte_checks}),
            "status": "blocked" if missing else "prepared_not_executed",
            "execution_enabled": False,
            "replay_level": "verified_dependency_snapshot",
            "tolerances": {},
            "created_at": now(),
        },
        [ref(r) for r in roots],
    )
    return ok(manifest, request)
