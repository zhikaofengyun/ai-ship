"""Scoped cooldown, bounded exploration, and recoverable local media tiers (U04–U06)."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import floor
import json
import os
import shutil
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write, require_approval
from .errors import DomainError
from .media import (
    blob_path,
    check_current_media_rights,
    hash_file,
    persist_blob,
    probe_media,
    resolve_media,
    root_dir,
    tenant_key,
)
from .production import Body, record, ref, revise, save, timestamp
from .selection import ChosenVersion, Label, Note, on_request
from .store import get_store

router = APIRouter(prefix="/production/assets", tags=["asset-lifecycle"])


class AssetCommand(Body):
    project_id: Label
    request_id: Label


def records(actor, pid, kind):
    items = get_store().list(actor.tenant_id, project_id=pid, kind=kind, limit=2000)
    if len(items) == 2000:
        raise DomainError(
            "ASSET_SCAN_LIMIT", "本机统计已到单项目扫描上限，请缩小分区；未使用截断的样本。", 409
        )
    return items


def rid(value):
    return value.get("id") if isinstance(value, dict) else value


def current_pin(actor, pid, pin, kind="AssetVersion"):
    item = record(actor, pin.id, kind, project=pid)
    if item["version"] != pin.version:
        raise DomainError("STALE_ASSET_INPUT", "素材或策略版本已变化，请重新核对。", 409)
    return item


class CooldownRule(Body):
    mode: Literal["use_count", "post_views", "exclude", "retain"] = "use_count"
    window_days: int = Field(default=7, ge=1, le=365)
    threshold: int = Field(default=3, ge=1, le=100000000)
    account_id: Label | None = None
    audience: Label | None = None
    expires_at: str | None = None
    reason: Note
    hard_limit: bool = False


class Cooldown(AssetCommand):
    asset: ChosenVersion
    rule: CooldownRule


def origin_links(actor, pid):
    """Use the immutable creation revisions when evaluating original source families."""
    with get_store().connection() as c:
        rows = c.execute(
            """SELECT r.id,v.data_json,v.refs_json FROM records r JOIN revisions v
                ON r.id=v.record_id AND r.tenant_id=v.tenant_id AND v.version=1
                WHERE r.tenant_id=? AND r.project_id=? AND r.kind='AssetVersion'""",
            (actor.tenant_id, pid),
        )
        result = {}
        for row in rows:
            data = json.loads(row["data_json"])
            parents = {
                r["id"] for r in json.loads(row["refs_json"]) if r.get("kind") == "AssetVersion"
            }
            if data.get("asset_id"):
                parents.add(data["asset_id"])
            result[row["id"]] = parents
        return result


class Ledger:
    """Resolve same-origin variants and typed families, keeping observation units distinct."""

    def __init__(self, actor, pid):
        self.now = datetime.now(timezone.utc)
        self.origins = origin_links(actor, pid)
        self.shots = {s["id"]: s for s in records(actor, pid, "Shot")}
        self.groups = []
        for group in records(actor, pid, "Cluster"):
            data = group["data"]
            if (
                data.get("status") in {"retired", "revoked", "rejected", "proposed"}
                or data.get("comparison_scope") == "segments"
            ):
                continue
            if data.get("cluster_level") not in {"exact", "near", "provenance"}:
                continue
            members = set()
            for member in data.get("members") or []:
                if not isinstance(member, dict):
                    continue
                mid = member.get("id")
                if member.get("kind") == "Shot" and mid in self.shots:
                    mid = self.shots[mid]["data"].get("asset_version_id") or self.shots[mid][
                        "data"
                    ].get("asset_id")
                if isinstance(mid, str):
                    members.add(mid)
            if members:
                self.groups.append((group, members))
        self.usage = records(actor, pid, "Usage")
        self.posts = records(actor, pid, "Post")
        self.metrics = records(actor, pid, "Metric")
        self.accounts = {a["id"]: a for a in records(actor, pid, "Account")}

    def family(self, version):
        ids = {version["id"]}
        evidence = {}
        while True:
            before = len(ids)
            for version_id, parents in self.origins.items():
                links = {version_id, *parents}
                if links & ids:
                    ids.update(links)
            for group, members in self.groups:
                if members & ids:
                    ids.update(members)
                    evidence[group["id"]] = group
            if len(ids) == before:
                return ids, list(evidence.values())

    def observations(self, version, rule):
        ids, evidence = self.family(version)
        start = self.now - timedelta(days=rule.window_days)

        def recent(value):
            if not value:
                return False
            stamp = timestamp(value)
            return start <= stamp <= self.now

        rendered = set()
        reservations = set()
        artifacts = set()
        all_uses = set()
        for usage in self.usage:
            d = usage["data"]
            shot = self.shots.get(d.get("shot_id"), {}).get("data", {})
            sources = (
                d.get("asset_id"),
                rid(d.get("asset_version_ref")),
                d.get("asset_version_id"),
                shot.get("asset_id"),
                shot.get("asset_version_id"),
            )
            if not any(s in ids for s in sources):
                continue
            if d.get("reservation_status") == "reserved":
                expiry = d.get("lease_expires_at") or d.get("expires_at")
                if expiry and timestamp(expiry) > self.now:
                    reservations.add(usage["id"])
                    evidence.append(usage)
            if d.get("status") != "rendered" and not d.get("actual_use_committed_at"):
                continue
            unit = (d.get("artifact_id"), d.get("clip_use_id") or usage["id"])
            all_uses.add(unit)
            if d.get("artifact_id"):
                artifacts.add(d["artifact_id"])
            if recent(d.get("actual_use_committed_at") or usage["created_at"]):
                rendered.add(unit)
            evidence.append(usage)

        scoped = bool(rule.account_id or rule.audience)
        confirmed, unresolved = set(), set()
        for post in self.posts:
            d = post["data"]
            if (d.get("artifact_id") or d.get("submitted_artifact_id")) not in artifacts:
                continue
            account = self.accounts.get(d.get("account_id"), {})
            audience = d.get("audience_scope") or account.get("data", {}).get("audience_scope")
            if scoped and not (
                (rule.account_id and d.get("account_id") == rule.account_id)
                or (rule.audience and audience == rule.audience)
            ):
                continue
            if (
                d.get("final_media_status") != "verified_same"
                or not d.get("media_mapping_evidence_refs")
                or not d.get("actual_published_at")
            ):
                unresolved.add(post["id"])
                continue
            if recent(d["actual_published_at"]):
                confirmed.add(post["id"])
                evidence.append(post)
                if account:
                    evidence.append(account)
        # Only comparable cumulative post views are eligible. A family may occur
        # multiple times in a post; one post contributes its latest snapshot once.
        latest = {}
        for metric in self.metrics:
            d = metric["data"]
            post_id = d.get("post_record_id")
            if (
                post_id not in confirmed
                or d.get("metric_name") != "views"
                or d.get("unit") not in {"views", "count"}
                or d.get("counting_mode") != "cumulative"
                or d.get("paid_or_organic", "mixed") not in {"mixed", "unknown"}
                or not recent(d.get("observed_at"))
            ):
                continue
            value = d.get("value_or_null")
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or value < 0
                or int(value) != value
            ):
                continue
            order = (timestamp(d["observed_at"]), metric["created_at"], metric["id"])
            if post_id not in latest or latest[post_id][0] < order:
                latest[post_id] = (order, int(value), metric)
        evidence.extend(item[2] for item in latest.values())
        return {
            "family_refs": sorted(ids),
            "lifetime_rendered": len(all_uses),
            "window_rendered": len(rendered),
            "active_reservations": len(reservations),
            "confirmed_posts": len(confirmed),
            "unmapped_posts": len(unresolved),
            "published_count_bounds": [len(confirmed), len(confirmed) + len(unresolved)],
            "post_views_estimate": sum(x[1] for x in latest.values()) if latest else None,
            "view_coverage": [len(latest), len(confirmed)],
            "audience_overlap_unknown": True,
        }, list({e["id"]: e for e in evidence}.values())


def pressure(stats, rule):
    if rule.mode == "post_views":
        if (
            stats["post_views_estimate"] is None
            or stats["view_coverage"][0] < stats["view_coverage"][1]
        ):
            raise DomainError(
                "EXPOSURE_UNAVAILABLE",
                "缺少经核实的成片映射或完整可比的累计帖子播放快照，无法使用曝光估计。",
                409,
            )
        return stats["post_views_estimate"], "cumulative_post_views_proxy"
    if rule.mode in {"exclude", "retain"}:
        return None, "manual_scoped_exception"
    if rule.account_id or rule.audience:
        low, high = stats["published_count_bounds"]
        if rule.hard_limit and low < rule.threshold <= high:
            raise DomainError(
                "PUBLICATION_MAPPING_UNCERTAIN", "帖子映射缺失，无法判定是否达到硬配额。", 409
            )
        return low, "distinct_verified_posts"
    return stats["window_rendered"], "local_rendered_clip_uses"


@router.post("/cooldown")
def cooldown(body: Cooldown, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        item = current_pin(actor, body.project_id, body.asset)
        rule = body.rule
        if rule.account_id:
            record(actor, rule.account_id, "Account", project=body.project_id)
        if rule.mode in {"exclude", "retain"} and not rule.expires_at:
            raise DomainError("EXPIRY_REQUIRED", "人工例外必须设置未来到期时间。")
        ledger = Ledger(actor, body.project_id)
        expires = (
            timestamp(rule.expires_at)
            if rule.expires_at
            else ledger.now + timedelta(days=rule.window_days)
        )
        if expires <= ledger.now:
            raise DomainError("POLICY_EXPIRED", "冷却规则已到期。")
        stats, evidence = ledger.observations(item, rule)
        count, method = pressure(stats, rule)
        route = {
            "post_views": "U04-R1",
            "use_count": "U04-R2",
            "exclude": "U04-R3",
            "retain": "U04-R3",
        }[rule.mode]
        policy = save(
            actor,
            "Policy",
            body.project_id,
            {
                "name": rule.reason[:180],
                "feature_id": "U04",
                "route_id": route,
                "status": "selectable",
                "typed_config": {
                    **rule.model_dump(),
                    "expires_at": expires.isoformat(),
                    "asset_version_id": item["id"],
                },
                "evaluation": {"method": method, "as_of": ledger.now.isoformat(), **stats},
            },
            [ref(item), *map(ref, evidence)],
        )
        score = save(
            actor,
            "AssetScore",
            body.project_id,
            {
                "asset_version_ref": ref(item),
                "policy_version": ref(policy),
                "feature_id": "U04",
                "route_id": route,
                "fatigue": min(1, count / rule.threshold) if count is not None else None,
                "pressure": count,
                "tier": (
                    "cooldown"
                    if rule.mode == "exclude" or (count is not None and count >= rule.threshold)
                    else "eligible"
                ),
                "uncertainty": "次数和帖子播放是复用压力代理；重合受众及镜头实际观看人数未知。",
                "evaluated_at": ledger.now.isoformat(),
                "expires_at": expires.isoformat(),
                "tier_reason": rule.reason,
            },
            [ref(policy), ref(item)],
        )
        return {"policy": policy, "score": score}

    return ok(on_request("asset-cooldown", body, actor, apply), request)


class VerifyPost(AssetCommand):
    post: ChosenVersion
    observed_file: ChosenVersion
    published_at: str
    evidence: Note
    audience: Label | None = None


@router.post("/post-mapping")
def verify_post(body: VerifyPost, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        pid = body.project_id
        post = current_pin(actor, pid, body.post, "Post")
        pub = record(actor, post["data"].get("publication_id") or "", "Publication", project=pid)
        subject = record(actor, pub["data"]["artifact_id"], "Artifact", project=pid)
        observed = current_pin(actor, pid, body.observed_file)
        if post["data"].get("locked") or post["data"].get("final_media_status") in {
            "verified_same",
            "verified_transformed",
        }:
            raise DomainError("POST_MAPPING_EXISTS", "请先复核既有最终媒体映射。", 409)
        at = timestamp(body.published_at)
        if at > datetime.now(timezone.utc):
            raise DomainError("FUTURE_OBSERVATION", "发布时间不能在未来。")
        _, _, actual = resolve_media(
            get_store(), actor, observed["id"], project_id=pid, purpose="inspect"
        )
        _, _, submitted = resolve_media(
            get_store(), actor, subject["id"], project_id=pid, purpose="inspect"
        )
        if subject["version"] != pub["data"].get("artifact_version"):
            raise DomainError(
                "ARTIFACT_VERSION_CHANGED", "交接成片版本已变化，须核对原提交版本。", 409
            )
        if hash_file(actual) != hash_file(submitted):
            raise DomainError(
                "FINAL_MEDIA_DIFFERS", "帖子下载件与提交成片的字节不同，需要另行核实时间映射。", 409
            )
        audit = save(
            actor,
            "Audit",
            pid,
            {
                "name": "核对帖子成片",
                "method": "human_identified_download_and_exact_hash",
                "post_ref": ref(post),
                "observed_media": ref(observed),
                "submitted_artifact": ref(subject),
                "evidence": body.evidence,
                "actor": actor.actor_id,
                "published_at": at.isoformat(),
                "content_hash": hash_file(actual),
            },
            [ref(post), ref(observed), ref(subject)],
        )
        updated = revise(
            actor,
            post,
            {
                "final_media_status": "verified_same",
                "artifact_id": subject["id"],
                "actual_published_at": at.isoformat(),
                "media_mapping_evidence_refs": [ref(audit)],
                "audience_scope": body.audience,
            },
            body.evidence,
            [*post["refs"], ref(audit), ref(subject)],
        )
        return {"post": updated, "audit": audit}

    return ok(on_request("post-media-mapping", body, actor, apply), request)


class ProcessingEvidence(AssetCommand):
    source: ChosenVersion
    evidence: Note


@router.post("/processing-evidence")
def processing_evidence(
    body: ProcessingEvidence, request: Request, actor: Actor = Depends(current_actor)
):
    require_approval(actor)

    def apply():
        source = current_pin(actor, body.project_id, body.source, "Source")
        check_current_media_rights(get_store(), actor, source, "process")
        scope = source["data"].get("processing_scope")
        if source["data"].get("locked") or (scope and not isinstance(scope, dict)):
            raise DomainError("SCOPE_REVIEW_REQUIRED", "已有用途限制或人工锁定，请先复核。", 409)
        updated = revise(
            actor,
            source,
            {
                "processing_scope": {
                    **(scope or {}),
                    "allowed": True,
                    "purpose": "local_private_edit",
                    "evidence": body.evidence,
                    "reviewed_by": actor.actor_id,
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            "审核本地制作用途；不授权公开发布",
        )
        return {"source": updated}

    return ok(on_request("asset-processing-evidence", body, actor, apply), request)


class Exploration(AssetCommand):
    assets: list[ChosenVersion] = Field(min_length=1, max_length=100)
    policies: list[ChosenVersion] = Field(default_factory=list, max_length=100)
    slots: int = Field(ge=1, le=16)
    exploration_share: float = Field(ge=0, le=1)
    quality_floor: float = Field(ge=0, le=1)
    account_id: Label | None = None
    audience: Label | None = None
    name: Label


@router.post("/exploration")
def exploration(body: Exploration, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pid = body.project_id
        record(actor, pid, "Project")
        if len({a.id for a in body.assets}) != len(body.assets) or len(
            {p.id for p in body.policies}
        ) != len(body.policies):
            raise DomainError("DUPLICATE_CANDIDATE", "候选素材和冷却规则各自不能重复。")
        if body.account_id:
            record(actor, body.account_id, "Account", project=pid)
        ledger = Ledger(actor, pid)
        quality = {}
        for score in records(actor, pid, "AssetScore"):
            d = score["data"]
            key = rid(d.get("asset_version_ref"))
            val = (d.get("dimension_scores") or {}).get("quality", d.get("quality"))
            if (
                val is not None
                and isinstance(val, (int, float))
                and not isinstance(val, bool)
                and 0 <= val <= 1
            ):
                # Do not carry a score over to a different version of the footage.
                pin = next((r for r in score["refs"] if r["id"] == key), None)
                if pin and (key, pin["version"]) not in quality:
                    quality[(key, pin["version"])] = (val, score)
        policies = []
        for pin in body.policies:
            policy = current_pin(actor, pid, pin, "Policy")
            d = policy["data"]
            if d.get("feature_id") != "U04" or d.get("status") != "selectable":
                raise DomainError("WRONG_COOLDOWN_POLICY", "请选择已计算的冷却规则。")
            cfg = d["typed_config"]
            rule = CooldownRule.model_validate(
                {k: cfg[k] for k in CooldownRule.model_fields if k in cfg}
            )
            if timestamp(rule.expires_at) <= ledger.now:
                raise DomainError("POLICY_EXPIRED", "冷却规则已到期，请重新计算。", 409)
            if (rule.account_id or rule.audience) and not (
                (rule.account_id and rule.account_id == body.account_id)
                or (rule.audience and rule.audience == body.audience)
            ):
                raise DomainError("POLICY_SCOPE_MISMATCH", "冷却规则不适用于当前账号或受众。", 409)
            policies.append((rule, cfg["asset_version_id"], policy))
        default_rule = CooldownRule(reason="本地选材")
        eligible, omitted, evidence = [], [], {}
        for pin in body.assets:
            item = current_pin(actor, pid, pin)
            evidence[item["id"]] = item
            try:
                _, _, path = resolve_media(get_store(), actor, item["id"], project_id=pid)
                check_current_media_rights(
                    get_store(), actor, item, require_processing_evidence=True
                )
                probe = probe_media(path)
                if not probe["has_video"] or probe.get("is_still"):
                    raise DomainError("NOT_TIMED_VIDEO", "素材不含定时画面。")
                q, basis = quality.get((item["id"], item["version"]), (None, None))
                if q is None or q < body.quality_floor:
                    raise DomainError("QUALITY_FLOOR", "缺少当前素材版本的合格质量评分。")
                stats, observations = ledger.observations(item, default_rule)
                evidence.update({r["id"]: r for r in [basis, *observations]})
                penalty = 0.0
                retained = False
                for rule, target, policy in policies:
                    if target not in stats["family_refs"]:
                        continue
                    current, refs = ledger.observations(item, rule)
                    evidence.update({r["id"]: r for r in refs})
                    count, _ = pressure(current, rule)
                    if rule.mode == "exclude" or (
                        rule.hard_limit and count is not None and count >= rule.threshold
                    ):
                        raise DomainError("ASSET_COOLDOWN", rule.reason)
                    if rule.mode == "retain":
                        retained = True
                    if count is not None:
                        penalty = max(penalty, min(1, count / rule.threshold))
                eligible.append(
                    {
                        "asset": ref(item),
                        "asset_id": item["data"]["asset_id"],
                        "family_refs": stats["family_refs"],
                        "quality": q,
                        "priority": q * (1 if retained else (1 - penalty)),
                        "uses": stats["lifetime_rendered"],
                        "tier": item["data"].get("storage_tier", "online"),
                    }
                )
            except DomainError as error:
                omitted.append({"asset": ref(item), "code": error.code, "reason": error.message})
        eligible.sort(key=lambda a: (a["tier"] == "low_priority", -a["priority"], a["asset"]["id"]))
        fresh = [a for a in eligible if a["uses"] == 0]
        proven = [a for a in eligible if a["uses"] > 0]
        quota = floor(body.slots * body.exploration_share + 0.5)
        selected = fresh[:quota] + proven[: body.slots - len(fresh[:quota])]
        # The exploration allocation is an upper bound; scarcity is visible.
        for a in selected:
            a["pool"] = "exploration" if a["uses"] == 0 else "retained"
        plan = save(
            actor,
            "Plan",
            pid,
            {
                "name": body.name,
                "feature_id": "U05",
                "route_id": "U05-R2",
                "status": "draft",
                "candidates": selected,
                "omissions": omitted,
                "unfilled": body.slots - len(selected),
                "constraints": {
                    "requested_slots": body.slots,
                    "exploration_share": body.exploration_share,
                    "exploration_quota": quota,
                    "quality_floor": body.quality_floor,
                    "account_id": body.account_id,
                    "audience": body.audience,
                    "purpose": "local_private_edit",
                    "evaluated_at": ledger.now.isoformat(),
                },
                "cooldown_policies": [ref(p) for _, _, p in policies],
            },
            [*map(ref, evidence.values()), *[ref(p) for _, _, p in policies]],
        )
        return {"plan": plan}

    return ok(on_request("asset-exploration", body, actor, apply), request)


def verify_exploration_selection(actor, pid, chosen, media_ids):
    """A saved shortlist is not a perpetual permission; recheck it when used."""
    if chosen is None:
        return None
    plan = current_pin(actor, pid, ChosenVersion.model_validate(chosen), "Plan")
    data = plan["data"]
    candidates = data.get("candidates") or []
    if data.get("feature_id") != "U05" or data.get("status") != "draft" or not candidates:
        raise DomainError("INVALID_EXPLORATION_PLAN", "请选择有效且非空的探索方案。", 409)
    ledger = Ledger(actor, pid)
    policies = []
    for entry in data.get("cooldown_policies", []):
        policy = current_pin(
            actor, pid, ChosenVersion(id=entry["id"], version=entry["version"]), "Policy"
        )
        cfg = policy["data"]["typed_config"]
        rule = CooldownRule.model_validate(
            {k: cfg[k] for k in CooldownRule.model_fields if k in cfg}
        )
        if timestamp(rule.expires_at) <= ledger.now:
            raise DomainError("POLICY_EXPIRED", "探索方案引用的冷却规则已到期，请重新选材。", 409)
        policies.append((cfg["asset_version_id"], rule))
    remaining = set(media_ids)
    for c in candidates:
        pin = c["asset"]
        item = current_pin(actor, pid, ChosenVersion(id=pin["id"], version=pin["version"]))
        available = {item["id"]}
        if item["data"].get("variant") == "original":
            available.add(item["data"]["asset_id"])
        if not (available & remaining):
            raise DomainError("SELECTION_CHANGED", "制作素材与探索方案不同，请重新核对。", 409)
        remaining -= available
        check_current_media_rights(get_store(), actor, item, require_processing_evidence=True)
        if item["data"].get("storage_tier") in {"local_cold", "quarantine"}:
            raise DomainError("ASSET_OFFLINE", "候选素材已离线。", 409)
        for target, rule in policies:
            family, _ = ledger.family(item)
            if target not in family:
                continue
            stats, _ = ledger.observations(item, rule)
            count, _ = pressure(stats, rule)
            if rule.mode == "exclude" or (
                rule.hard_limit and count is not None and count >= rule.threshold
            ):
                raise DomainError("ASSET_COOLDOWN", "候选素材已达到当前复用限制，请重新选材。", 409)
    if remaining or len(media_ids) != len(candidates):
        raise DomainError("SELECTION_CHANGED", "制作素材与探索方案不同，请重新核对。", 409)
    return plan


class TierChange(AssetCommand):
    asset: ChosenVersion
    tier: Literal["online", "low_priority", "local_cold", "quarantine"]
    reason: Note


def cold_copy(path, actor, checksum):
    key = f"objects/{tenant_key(actor.tenant_id)}/cold/{checksum[:2]}/{checksum}"
    target = root_dir() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_name(uuid.uuid4().hex + ".tmp")
        try:
            with path.open("rb") as src, tmp.open("xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            if hash_file(tmp) != checksum:
                raise DomainError("OBJECT_CORRUPTION", "冷藏副本校验失败，原存储仍有效。", 409)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    elif hash_file(target) != checksum:
        raise DomainError("OBJECT_CORRUPTION", "既有冷藏副本校验失败。", 409)
    return key


@router.post("/tier")
def change_tier(body: TierChange, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pid = body.project_id
        item = current_pin(actor, pid, body.asset)
        d = item["data"]
        old = d.get("storage_tier", "online")
        if body.tier == old:
            raise DomainError("TIER_UNCHANGED", "素材已处于该层级。", 409)
        if d.get("locked") or d.get("status") in {"frozen", "locked"}:
            raise DomainError("ASSET_LOCKED", "请先核对并解除人工锁定。", 409)
        if old in {"local_cold", "quarantine"} and body.tier != "online":
            raise DomainError("RESTORE_REQUIRED", "请先核权恢复，再重新分层。", 409)
        store = get_store()
        blob = record(actor, d.get("blob_location_id") or "", "BlobLocation", project=pid)
        checksum = d.get("content_hash") or blob["data"].get("checksum")
        if not checksum:
            raise DomainError("OBJECT_UNVERIFIED", "素材缺少可核实的内容摘要。", 409)
        origin = (
            record(actor, d["asset_id"], "Asset", project=pid)
            if d.get("variant") == "original"
            else None
        )
        if origin and origin["data"].get("locked"):
            raise DomainError("ASSET_LOCKED", "原素材已被锁定。", 409)
        tag_base = (
            d.get("tag_base_revision", item["version"])
            if d.get("tier_revision") == item["version"]
            else item["version"]
        )
        changes = {
            "storage_tier": body.tier,
            "tier_reason": body.reason,
            "tag_base_revision": tag_base,
            "tier_revision": item["version"] + 1,
        }
        refs = [ref(item), ref(blob)]
        if body.tier == "local_cold":
            path = blob_path(blob, actor.tenant_id)
            if hash_file(path) != checksum:
                raise DomainError("OBJECT_CORRUPTION", "归档前校验失败。", 409)
            location = save(
                actor,
                "BlobLocation",
                pid,
                {
                    "object_key": cold_copy(path, actor, checksum),
                    "storage_tier": "local_cold",
                    "checksum": checksum,
                    "size_bytes": path.stat().st_size,
                    "availability": "restore_required",
                    "storage_backend": "local_verified_copy",
                },
                [ref(blob)],
            )
            changes["cold_location_id"] = location["id"]
            refs.append(ref(location))
        if body.tier == "online":
            check_current_media_rights(get_store(), actor, item, require_processing_evidence=True)
            location = (
                record(actor, d["cold_location_id"], "BlobLocation", project=pid)
                if old == "local_cold"
                else blob
            )
            path = blob_path(location, actor.tenant_id)
            if hash_file(path) != checksum:
                raise DomainError("OBJECT_CORRUPTION", "恢复时媒体字节与冻结摘要不一致。", 409)
            probe = probe_media(path)
            if old == "local_cold":
                restored = persist_blob(path, actor.tenant_id)
                if restored["object_key"] != blob["data"]["object_key"]:
                    raise DomainError(
                        "OBJECT_LOCATION_MISMATCH", "在线对象位置与原始登记不符。", 409
                    )
            changes["recovery_probe"] = probe
            refs.append(ref(location))
        route = (
            "U06-R1"
            if "local_cold" in (old, body.tier)
            else ("U06-R2" if "low_priority" in (old, body.tier) else "U06-R3")
        )
        result = revise(actor, item, changes, body.reason)
        if origin:
            revise(actor, origin, {"storage_tier": body.tier}, body.reason)
        event = save(
            actor,
            "Event",
            pid,
            {
                "feature_id": "U06",
                "route_id": route,
                "name": "素材分层变更",
                "aggregate_version": result["version"],
                "from_tier": old,
                "to_tier": body.tier,
                "reason": body.reason,
                "verified_checksum": checksum if body.tier in {"online", "local_cold"} else None,
                "retains_source_bytes": True,
                "restore_bytes": (
                    blob["data"].get("size_bytes") if body.tier == "local_cold" else None
                ),
                "storage_backend": "local",
                "bytes_deleted": 0,
            },
            [ref(result), *refs],
        )
        return {"asset": result, "event": event}

    return ok(on_request("asset-tier", body, actor, apply), request)
