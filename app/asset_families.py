"""Pairwise, evidence-backed duplicate checks and reviewable families for S03 and S04."""

from fractions import Fraction
from statistics import fmean
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write, actor_still_authorized
from .asset_lifecycle import AssetCommand, current_pin
from .errors import DomainError
from .media import hash_file, probe_media, resolve_media
from .production import now, record, ref, revise, save
from .selection import ChosenVersion, Note, on_request
from .store import get_store
from .variant_fingerprints import picture_difference, scene, sound, sound_difference
from .visual_candidates import sample

router = APIRouter(prefix="/production/asset-families", tags=["asset-families"])


class SampleRange(ChosenVersion):
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)


class Compare(AssetCommand):
    left: SampleRange
    right: SampleRange
    visual_threshold: float = Field(default=0.90, ge=0.5, le=1)
    audio_threshold: float = Field(default=0.85, ge=0.5, le=1)


def inspect_range(actor, pid, pin):
    item = current_pin(actor, pid, pin)
    _, _, path = resolve_media(get_store(), actor, item["id"], project_id=pid)
    probe = probe_media(path)
    if not probe["has_video"] or probe.get("is_still"):
        raise DomainError("NOT_TIMED_VIDEO", "区间对照需要可解码的定时视频。")
    end = pin.end if pin.end is not None else probe["duration"]
    if end <= pin.start or end > probe["duration"] + 1e-6:
        raise DomainError("SOURCE_RANGE", "待对照的区间超出视频时长。")
    # Use decoded source clocks and one frame of tail margin.
    try:
        rate = float(Fraction(probe.get("fps") or "0"))
    except (ValueError, ZeroDivisionError):
        rate = 0
    if rate <= 0:
        raise DomainError("FPS_UNAVAILABLE", "无法核实帧率及采样边界。", 409)
    last = max(pin.start, end - 1 / rate)
    times = sorted({round(pin.start + (last - pin.start) * f, 6) for f in (0, 0.5, 1)})
    frames = [{"shape": scene(path, t), "color": sample(path, t)["descriptor"]} for t in times]
    interval = min(end - pin.start, 0.75)
    audio_at = pin.start + (end - pin.start - interval) / 2
    return item, {
        "asset": ref(item),
        "range": [pin.start, end],
        "whole_file": pin.start == 0 and abs(end - probe["duration"]) < 1e-6,
        "file_sha256": hash_file(path),
        "frames": frames,
        "audio": sound(path, audio_at, interval, probe["has_audio"]),
        "audio_range": [audio_at, audio_at + interval],
    }


def measured_pair(a, b):
    diffs = []
    for fraction in (0, 0.5, 1):
        first = a["frames"][round(fraction * (len(a["frames"]) - 1))]
        second = b["frames"][round(fraction * (len(b["frames"]) - 1))]
        shape = picture_difference(first["shape"], second["shape"])
        color = fmean(abs(x - y) for x, y in zip(first["color"], second["color"]))
        diffs.append(max(shape, color))
    acoustic = sound_difference(a["audio"], b["audio"])
    return {
        "visual": round(1 - fmean(diffs), 4),
        "frame_similarities": [round(1 - v, 4) for v in diffs],
        "sound": round(1 - acoustic, 4) if acoustic is not None else None,
    }


def visible_evidence(d):
    return {
        "asset": d["asset"],
        "range": d["range"],
        "file_sha256": d["file_sha256"],
        "whole_file": d["whole_file"],
        "sampled_frames": [
            {"at": f["shape"]["at"], "pixel_digest": f["shape"]["sha256"], "color": f["color"]}
            for f in d["frames"]
        ],
        "audio_status": d["audio"]["status"],
        "audio_range": d["audio_range"],
    }


@router.post("/compare")
def compare(body: Compare, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    if body.left.id == body.right.id:
        raise DomainError("DISTINCT_VERSIONS_REQUIRED", "请选择两个不同的素材版本。")

    # Avoid holding SQLite's writer lock during FFmpeg reads. A replay still goes
    # through on_request, which checks the complete request and actor fingerprint.
    store = get_store()
    with store.connection() as conn:
        cached = conn.execute(
            "SELECT 1 FROM command_results WHERE tenant_id=? AND idempotency_key=?",
            (actor.tenant_id, "asset-compare:" + body.request_id),
        ).fetchone()
    measured = (
        None
        if cached
        else (
            inspect_range(actor, body.project_id, body.left),
            inspect_range(actor, body.project_id, body.right),
        )
    )

    def apply():
        if measured is None:
            raise DomainError("COMPARISON_RETRY", "读取已保存的请求时状态变化，请重试。", 409)
        (v1, a), (v2, b) = measured
        if not actor_still_authorized(actor):
            raise DomainError("IDENTITY_REVOKED", "核验期间工作区权限已变化。", 403)
        for pin in (body.left, body.right):
            current_pin(actor, body.project_id, pin)
            resolve_media(store, actor, pin.id, project_id=body.project_id)
        metrics = measured_pair(a, b)
        exact = a["file_sha256"] == b["file_sha256"]
        near = min(metrics["frame_similarities"]) >= body.visual_threshold and (
            metrics["sound"] is None or metrics["sound"] >= body.audio_threshold
        )
        label = "exact" if exact else ("near_candidate" if near else "distinct_samples")
        assessment = save(
            actor,
            "Audit",
            body.project_id,
            {
                "feature_id": "S03",
                "route_id": "S03-R1",
                "name": "素材区间对照",
                "classification": label,
                "method": "sha256_rgb_quadrants_normalized_luma_audio_bands_v1",
                "left": visible_evidence(a),
                "right": visible_evidence(b),
                "measurements": metrics,
                "thresholds": {"picture": body.visual_threshold, "sound": body.audio_threshold},
                "uncertainty": "取样区间的相似度不能说明权利与来源相同，缺少音轨时只比较画面。",
            },
            [ref(v1), ref(v2)],
        )
        cluster = None
        if exact:
            cluster = save(
                actor,
                "Cluster",
                body.project_id,
                {
                    "name": "相同字节",
                    "feature_id": "S03",
                    "route_id": "S03-R1",
                    "cluster_level": "exact",
                    "algorithm": "full_sha256",
                    "members": [ref(v1), ref(v2)],
                    "similarity": 1,
                    "member_kind": "AssetVersion",
                    "status": "active",
                    "evidence_id": assessment["id"],
                },
                [ref(assessment), ref(v1), ref(v2)],
            )
        return {"audit": assessment, "cluster": cluster}

    return ok(on_request("asset-compare", body, actor, apply), request)


class Group(AssetCommand):
    mode: Literal["review_near", "provenance", "semantic", "retire"]
    members: list[ChosenVersion] = Field(default_factory=list, max_length=32)
    audit: ChosenVersion | None = None
    group: ChosenVersion | None = None
    name: Note
    reason: Note


def immutable_roots(actor, pid, item):
    todo = [item]
    visited, roots = set(), set()
    while todo:
        current = todo.pop()
        v = record(actor, current["id"], "AssetVersion", version=1, project=pid)
        if v["id"] in visited:
            continue
        visited.add(v["id"])
        if len(visited) > 128:
            raise DomainError("FAMILY_DEPTH_LIMIT", "来源链超过本机归组上限。", 409)
        asset_id = v["data"].get("asset_id")
        if asset_id:
            asset = record(actor, asset_id, "Asset", project=pid)
            roots.add(asset["id"])
        for r in v["refs"]:
            if r["kind"] == "AssetVersion":
                todo.append(
                    record(actor, r["id"], "AssetVersion", version=r["version"], project=pid)
                )
    return roots


@router.post("/group")
def group(body: Group, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pid = body.project_id
        if body.mode == "retire":
            if not body.group or body.audit or body.members:
                raise DomainError("GROUP_REQUIRED", "撤销只需要当前素材族版本及理由。")
            prev = current_pin(actor, pid, body.group, "Cluster")
            if (
                prev["data"].get("cluster_level") not in {"near", "semantic"}
                or prev["data"].get("status") == "retired"
            ):
                raise DomainError(
                    "IMMUTABLE_FAMILY", "字节相同或可证同源的事实无法通过人工拆组覆盖。", 409
                )
            return {
                "cluster": revise(
                    actor,
                    prev,
                    {"status": "retired", "review_reason": body.reason, "reviewed_at": now()},
                    body.reason,
                )
            }
        if body.group:
            raise DomainError("INVALID_GROUP", "新建组不能指定被替换的组。")
        refs = []
        if body.mode == "review_near":
            if not body.audit or body.members:
                raise DomainError("AUDIT_REQUIRED", "请选取一份有疑似重复结论的区间对照。")
            audit = current_pin(actor, pid, body.audit, "Audit")
            d = audit["data"]
            if d.get("feature_id") != "S03" or d.get("classification") != "near_candidate":
                raise DomainError("NOT_NEAR_CANDIDATE", "这份记录未显示待复核的近似区间。")
            entries = [d["left"], d["right"]]
            items = [
                current_pin(
                    actor, pid, ChosenVersion(id=e["asset"]["id"], version=e["asset"]["version"])
                )
                for e in entries
            ]
            members = [{**ref(v), "range": entry["range"]} for v, entry in zip(items, entries)]
            level, route = "near", "S03-R3"
            refs.append(ref(audit))
            extra = {
                "comparison_scope": (
                    "whole_file" if all(e["whole_file"] for e in entries) else "segments"
                ),
                "similarity": d["measurements"]["visual"],
            }
        else:
            if (
                body.audit
                or len(body.members) < 2
                or len({m.id for m in body.members}) != len(body.members)
            ):
                raise DomainError("FAMILY_MEMBERS", "请选择至少两个不同的项目素材版本。")
            items = [current_pin(actor, pid, pin) for pin in body.members]
            members = list(map(ref, items))
            level, route = (
                ("provenance", "S04-R2") if body.mode == "provenance" else ("semantic", "S04-R3")
            )
            extra = {}
            if level == "provenance":
                roots = set.intersection(*(immutable_roots(actor, pid, v) for v in items))
                if not roots:
                    raise DomainError(
                        "NO_SHARED_ORIGIN", "缺少可证的共同源素材，不能登记为同源族。", 409
                    )
                extra["source_asset_ids"] = sorted(roots)
        info = {
            "name": body.name[:200],
            "feature_id": route[:3],
            "route_id": route,
            "cluster_level": level,
            "member_kind": "AssetVersion",
            "status": "active",
            "algorithm": body.mode,
            "members": members,
            "reviewed_at": now(),
            "review_reason": body.reason,
            "reviewer": actor.actor_id,
            **extra,
        }
        return {
            "cluster": save(actor, "Cluster", pid, info, [*refs, *map(ref, items)], body.reason)
        }

    return ok(on_request("asset-family-group", body, actor, apply), request)
