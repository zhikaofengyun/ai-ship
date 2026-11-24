"""Versioned human decisions for creative review (T06) and storyboard selection (N02)."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from .api import ok
from .auth import Actor, current_actor, require_approval, require_write
from .errors import DomainError
from .media import probe_media, resolve_media
from .production import current_dependency_problem, record, ref, revise, save, timestamp
from .store import get_store

router = APIRouter(prefix="/production/selection", tags=["selection"])

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]


class SelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ChosenVersion(SelectionBody):
    id: Label
    version: StrictInt = Field(ge=1)


class ConsideredBrief(ChosenVersion):
    reason: Note


class ReviewBrief(SelectionBody):
    project_id: Label
    request_id: Label
    chosen: ChosenVersion
    alternatives: list[ConsideredBrief] = Field(default_factory=list, max_length=24)
    products: list[ChosenVersion] = Field(default_factory=list, max_length=50)
    reason: Note
    purpose: Label
    expires_at: str = Field(min_length=1, max_length=80)


def live(actor, chosen, kind, project_id):
    item = record(actor, chosen.id, kind, project=project_id)
    if item["version"] != chosen.version:
        raise DomainError("STALE_SELECTION", "内容已更新，须重新查看后确认。", 409)
    if problem := current_dependency_problem(item):
        raise DomainError(problem["code"], problem["message"], 409)
    return item


def on_request(operation, body, actor, execute):
    store = get_store()
    key = operation + ":" + body.request_id
    return store.idempotent(
        actor.tenant_id,
        key,
        key,
        {"principal": actor.actor_id, **body.model_dump(mode="json")},
        execute,
    )


@router.post("/brief")
def approve_brief(body: ReviewBrief, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        project = record(actor, body.project_id, "Project")
        selected = live(actor, body.chosen, "Brief", body.project_id)
        if not (selected["data"].get("intent") or selected["data"].get("description")):
            raise DomainError("BRIEF_EMPTY", "请先填写候选创意的具体内容。")
        if selected["data"].get("status") == "approved":
            raise DomainError("BRIEF_ALREADY_APPROVED", "此简报版本已经批准。", 409)
        options = [selected["id"], *[entry.id for entry in body.alternatives]]
        if len(options) != len(set(options)):
            raise DomainError("DUPLICATE_OPTION", "创意候选不能重复。")
        others = [live(actor, v, "Brief", body.project_id) for v in body.alternatives]
        products = [live(actor, v, "Product", body.project_id) for v in body.products]
        known_products = {p["id"]: p["version"] for p in products}
        if len(products) != len(known_products):
            raise DomainError("DUPLICATE_PRODUCT", "商品事实不能重复。")
        if selected["data"].get("claims") and not products:
            raise DomainError("PRODUCT_EVIDENCE_REQUIRED", "含商品主张的简报须绑定商品证据。")
        if any(
            not p["data"].get("evidence") and not p["data"].get("evidence_source_id")
            for p in products
        ):
            raise DomainError("PRODUCT_EVIDENCE_REQUIRED", "商品事实缺少可核对的来源。")
        for dependency in selected["refs"]:
            item = record(actor, dependency["id"], project=body.project_id)
            if item["version"] != dependency["version"] or current_dependency_problem(item):
                raise DomainError(
                    "BRIEF_DEPENDENCY_CHANGED", "候选引用的事实已变化或失效，请先修订简报。", 409
                )
            if item["kind"] == "Product" and known_products.get(item["id"]) != item["version"]:
                raise DomainError("PRODUCT_EVIDENCE_REQUIRED", "请纳入简报已经引用的商品版本。")
        expiry = timestamp(body.expires_at)
        if expiry <= datetime.now(timezone.utc):
            raise DomainError("APPROVAL_EXPIRED", "请选择未来的有效期。")
        resulting_refs = list(
            {
                (r["id"], r["version"]): r
                for r in [*selected["refs"], ref(project), *[ref(p) for p in products]]
            }.values()
        )
        approved = revise(
            actor,
            selected,
            {"status": "approved", "approval_purpose": body.purpose},
            "人工选定执行简报",
            resulting_refs,
        )
        approval = save(
            actor,
            "Approval",
            body.project_id,
            {
                "name": selected["data"].get("name") or "创意简报批准",
                "kind": "content_check",
                "subject_kind": "Brief",
                "subject_id": approved["id"],
                "subject_version": approved["version"],
                "subject_hash": approved["hash"],
                "decision": "approved",
                "status": "approved",
                "operation": "use_creative_brief",
                "scope": {"purpose": body.purpose},
                "principal": actor.actor_id,
                "actor_role": actor.role,
                "expires_at": expiry.isoformat(),
                "evidence": body.reason,
                "review_evidence_refs": [ref(p) for p in products],
            },
            [ref(approved), *[ref(p) for p in products]],
        )
        decision = save(
            actor,
            "DecisionRecord",
            body.project_id,
            {
                "name": "创意候选选择",
                "decision_kind": "selection",
                "selection_reason": body.reason,
                "decider_principal": actor.actor_id,
                "selected_option": ref(approved),
                "options": [
                    {"candidate": ref(selected), "outcome": "selected", "reason": body.reason},
                    *[
                        {"candidate": ref(item), "outcome": "not_selected", "reason": option.reason}
                        for item, option in zip(others, body.alternatives)
                    ],
                ],
                "approval_refs": [ref(approval)],
            },
            [ref(selected), ref(approved), ref(approval), *[ref(item) for item in others]],
        )
        return {"brief": approved, "approval": approval, "decision": decision}

    return ok(on_request("review-brief", body, actor, apply), request)


class SelectShot(SelectionBody):
    project_id: Label
    request_id: Label
    plan: ChosenVersion | None = None
    plan_name: Label
    slot: Label
    constraints: Note
    decision: Literal["select", "gap"]
    asset: ChosenVersion | None = None
    source_in: float | None = Field(default=None, ge=0)
    source_out: float | None = Field(default=None, gt=0)
    evidence: Note
    lock: bool = False
    unlock_previous: bool = False


@router.post("/shot")
def select_shot(body: SelectShot, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        project = record(actor, body.project_id, "Project")
        plan = live(actor, body.plan, "Plan", body.project_id) if body.plan else None
        if plan and (
            plan["data"].get("locked")
            or plan["data"].get("status") in {"approved", "frozen", "locked"}
        ):
            raise DomainError("PLAN_LOCKED", "分镜方案已冻结，请另存为草稿。", 409)
        slots = list((plan or {}).get("data", {}).get("slots") or [])
        if any(not isinstance(slot, dict) for slot in slots):
            raise DomainError("SLOT_FORMAT", "分镜包含其他格式的槽位，请另存方案后选片。", 409)
        matches = [
            i
            for i, s in enumerate(slots)
            if s.get("name") == body.slot or s.get("slot_id") == body.slot
        ]
        if len(matches) > 1:
            raise DomainError("AMBIGUOUS_SLOT", "有重名的分镜位置，请先区分命名。", 409)
        if matches and slots[matches[0]].get("locked") and not body.unlock_previous:
            raise DomainError("SLOT_LOCKED", "这个分镜已人工锁定，须明确解锁后替换。", 409)
        slot = {
            **(slots[matches[0]] if matches else {}),
            "name": body.slot,
            "constraints": body.constraints,
            "status": "selected" if body.decision == "select" else "gap",
            "evidence": body.evidence,
            "locked": body.lock,
            "evidence_kind": "human_observation",
        }
        media = None
        if body.decision == "select":
            if not body.asset or body.source_in is None or body.source_out is None:
                raise DomainError("SHOT_SOURCE_REQUIRED", "请选择素材并填写原片起止时间。")
            media, _, path = resolve_media(
                get_store(), actor, body.asset.id, project_id=body.project_id
            )
            if (
                media["kind"] not in {"Asset", "AssetVersion"}
                or media["version"] != body.asset.version
            ):
                raise DomainError("STALE_SHOT", "素材版本已变化，请重新选片。", 409)
            # Use the verified bytes, not an editable ledger field, to bound a source range.
            probe = probe_media(path)
            if not probe.get("has_video") or probe.get("is_still"):
                raise DomainError("NOT_TIMED_VIDEO", "此位置需要含可播放画面的定时视频。")
            if not body.source_in < body.source_out <= probe["duration"] + 0.000001:
                raise DomainError("SHOT_OUT_OF_RANGE", "时间区间超出原片时长。")
            if media["kind"] != "AssetVersion":
                raise DomainError("SHOT_VERSION_REQUIRED", "请选择具体的素材版本。")
            shot = save(
                actor,
                "Shot",
                body.project_id,
                {
                    "name": body.slot,
                    "asset_version_id": media["id"],
                    "source_in": body.source_in,
                    "source_out": body.source_out,
                    "frame_rate": probe.get("fps"),
                    "evidence": body.evidence,
                },
                [ref(media)],
            )
            slot["shot_ref"] = ref(shot)
            slot["selected_source"] = {
                "asset": ref(media),
                "source_in": body.source_in,
                "source_out": body.source_out,
            }
        elif body.asset or body.source_in is not None or body.source_out is not None or body.lock:
            raise DomainError("INVALID_GAP", "缺口不能同时提交素材、时间或锁定。")
        else:
            slot.pop("selected_source", None)
            slot.pop("shot_ref", None)
        if matches:
            slots[matches[0]] = slot
        else:
            if len(slots) >= 500:
                raise DomainError("SLOT_LIMIT", "单个方案最多容纳 500 个分镜位置。")
            slots.append(slot)
        # Prior versions keep the old source. Unrelated slots retain their references.
        data = (plan or {}).get("data", {})
        manual_refs = [
            s["selected_source"]["asset"]
            for s in slots
            if isinstance(s.get("selected_source"), dict)
            and isinstance(s["selected_source"].get("asset"), dict)
        ]
        old_manual = {
            (s["selected_source"]["asset"]["id"], s["selected_source"]["asset"]["version"])
            for s in data.get("slots", [])
            if isinstance(s.get("selected_source"), dict)
            and isinstance(s["selected_source"].get("asset"), dict)
        }
        unrelated = [
            r for r in (plan or {}).get("refs", []) if (r["id"], r["version"]) not in old_manual
        ]
        refs = list(
            {(r["id"], r["version"]): r for r in [ref(project), *unrelated, *manual_refs]}.values()
        )
        old_shots = {
            s["shot_ref"]["id"]
            for s in data.get("slots", [])
            if isinstance(s.get("shot_ref"), dict) and "id" in s["shot_ref"]
        }
        kept_shots = [
            r
            for r in data.get("slot_shot_refs", []) or []
            if (r.get("id") if isinstance(r, dict) else r) not in old_shots
        ]
        refs = [r for r in refs if r["id"] not in old_shots]
        active_shots = [s["shot_ref"] for s in slots if isinstance(s.get("shot_ref"), dict)]
        refs.extend(active_shots)
        changes = {
            "name": body.plan_name,
            "slots": slots,
            "slot_shot_refs": kept_shots + active_shots,
        }
        saved = (
            revise(actor, plan, changes, "人工选片或登记镜头缺口", refs)
            if plan
            else save(actor, "Plan", body.project_id, changes, refs)
        )
        return {"plan": saved, "slot": slot}

    return ok(on_request("select-shot", body, actor, apply), request)
