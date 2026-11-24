"""Local manual-publication quotas. Count, concurrency, money and remote effects stay independent."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt

from .api import ok
from .auth import Actor, current_actor, require_approval, require_write
from .errors import DomainError
from .production import (
    Body,
    current_dependency_problem,
    digest,
    publication_preflight,
    record,
    ref,
    revise,
    timestamp,
)
from .selection import ChosenVersion, Label, Note, on_request
from .store import get_store

router = APIRouter(prefix="/production/account-quota", tags=["manual publication quota"])
DAY = timedelta(days=1)
CATEGORY = "manual_publication_slots"


def clock():
    return datetime.now(timezone.utc)


def owned(actor, project, identity, prefix):
    try:
        return record(actor, prefix + digest([project, identity]), "Cost", project=project)
    except DomainError as error:
        if error.code != "NOT_FOUND":
            raise
        return None


def policy_for(actor, account):
    return owned(actor, account["project_id"], account["id"], "account_quota_")


def ticket_for(actor, pub):
    return owned(actor, pub["project_id"], pub["id"], "publication_slot_")


def write(actor, old, project, identity, prefix, data, refs, reason):
    if old:
        return get_store().update(
            old["id"],
            data,
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            expected_revision=old["version"],
            reason=reason,
            refs=refs,
        )
    return get_store().create(
        "Cost",
        data,
        tenant_id=actor.tenant_id,
        project_id=project,
        actor_id=actor.actor_id,
        record_id=prefix + digest([project, identity]),
        refs=refs,
        reason=reason,
    )


class Settings(Body):
    project_id: Label
    account_id: Label
    request_id: Label
    expected_revision: StrictInt = Field(ge=0)
    daily_slots: StrictInt = Field(ge=1, le=100000)
    concurrent_slots: StrictInt = Field(ge=1, le=1000)
    hold_seconds: StrictInt = Field(ge=10, le=86400)
    reason: Note


def check_revision(item, version):
    if (item["version"] if item else 0) != version:
        raise DomainError("REVISION_CONFLICT", "记录已更新，请读取最新状态。", 409)


@router.post("/settings")
def configure(body: Settings, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        account = record(actor, body.account_id, "Account", project=body.project_id)
        policy = policy_for(actor, account)
        check_revision(policy, body.expected_revision)
        if body.concurrent_slots > body.daily_slots:
            raise DomainError("QUOTA_CONFIGURATION", "并发名额不能超过每日名额。")
        data = {
            "name": "人工交付 · 本地账号配额",
            "reservation_kind": "manual_quota_settings",
            "account_id": account["id"],
            "daily_slots": body.daily_slots,
            "concurrent_slots": body.concurrent_slots,
            "hold_seconds": body.hold_seconds,
            "scope": "local_account_record",
            "unit": "manual_handoff",
            "platform_capacity": "unknown",
        }
        # Reconfiguration preserves any outstanding provider embargo and capability snapshot.
        return write(
            actor,
            policy,
            body.project_id,
            account["id"],
            "account_quota_",
            {**(policy["data"] if policy else {}), **data},
            policy["refs"] if policy else [ref(account)],
            body.reason,
        )

    return ok(on_request("quota-settings", body, actor, apply), request)


def state_of(ticket, instant, pub_revision):
    if not ticket:
        return "none"
    d = ticket["data"]
    state = d["status"]
    if state in ("held", "queued"):
        if d["publication_version"] != pub_revision:
            return "stale"
        if timestamp(d["valid_until"]) <= instant:
            return "expired"
    return state


def ensure_intent_editable(actor, publication, requested_account):
    """Call in the writer's transaction. Receipts and observations are separate facts."""
    ticket = ticket_for(actor, publication)
    if not ticket:
        return
    phase = state_of(ticket, clock(), publication["version"])
    if phase in {"held", "in_handoff"}:
        raise DomainError(
            "PUBLICATION_QUOTA_PINNED",
            "该发布版本已预留或已交给执行人，先释放未用席位或核对交接结果。",
            409,
        )
    if requested_account != ticket["data"]["account_id"]:
        raise DomainError("PUBLICATION_QUOTA_PINNED", "已排队或预留的意图不能改绑账号。", 409)


def account_usage(actor, account, instant):
    with get_store().connection() as db:
        rows = db.execute(
            """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.*,p.latest_version AS pub_revision
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            JOIN records p ON p.tenant_id=r.tenant_id AND p.id=json_extract(v.data_json,'$.publication_id')
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Cost'
              AND json_extract(v.data_json,'$.reservation_kind')=? AND json_extract(v.data_json,'$.account_id')=?
              AND json_extract(v.data_json,'$.status') IN ('held','queued','in_handoff')
            ORDER BY json_extract(v.data_json,'$.enqueued_at'),r.id""",
            (actor.tenant_id, account["project_id"], CATEGORY, account["id"]),
        )
        live = []
        queue = []
        for raw in rows:
            item = get_store()._row_record(raw)
            d = item["data"]
            state = state_of(item, instant, raw["pub_revision"])
            if state in ("held", "in_handoff"):
                live.append(item)
            elif state == "queued" and timestamp(d["not_before"]) <= instant:
                queue.append(item["id"])
        # Count each observed post once. Out-of-band and delayed receipts also use the local window.
        used = db.execute(
            """SELECT COUNT(*),min(r.created_at) FROM records r JOIN revisions v
            ON r.tenant_id=v.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Post' AND julianday(r.created_at)>julianday(?)
              AND json_extract(v.data_json,'$.account_id')=?""",
            (actor.tenant_id, account["project_id"], (instant - DAY).isoformat(), account["id"]),
        ).fetchone()
    return live, queue, used[0]


def blocked_reason(policy, instant):
    d = policy["data"]
    if not d.get("refresh_required"):
        return None
    return (
        "RATE_LIMIT_BACKOFF"
        if instant < timestamp(d["retry_after"])
        else "CAPABILITY_REFRESH_REQUIRED"
    )


def view(actor, account, pub=None):
    instant = clock()
    policy = policy_for(actor, account)
    ticket = ticket_for(actor, pub) if pub else None
    status = state_of(ticket, instant, pub["version"]) if pub else None
    live, queue, used = account_usage(actor, account, instant)
    head = queue[0] if queue else None
    gate = blocked_reason(policy, instant) if policy else None
    if not gate and current_dependency_problem(account):
        gate = "ACCOUNT_UNAVAILABLE"
    if not gate and pub:
        if ticket and ticket["data"]["account_id"] != account["id"]:
            gate = "ACCOUNT_CHANGED"
        elif status in ("in_handoff", "settled"):
            gate = "RECONCILE_FIRST"
        elif policy and status == "held":
            outstanding = sum(t["data"]["status"] == "in_handoff" for t in live)
            held_ids = [t["id"] for t in live if t["data"]["status"] == "held"]
            position = outstanding + held_ids.index(ticket["id"]) + 1
            if (
                position > policy["data"]["concurrent_slots"]
                or used + position > policy["data"]["daily_slots"]
            ):
                gate = "QUOTA_CAPACITY_CHANGED"
        elif policy and status != "held":
            gate = "QUOTA_RESERVATION_REQUIRED"
    return {
        "policy": policy,
        "ticket": ticket,
        "effective_state": status,
        "held_or_unknown": len(live),
        "unresolved_handoffs": sum(t["data"]["status"] == "in_handoff" for t in live),
        "receipt_count_24h": used,
        "ready_queue": len(queue),
        "queue_position": (
            queue.index(ticket["id"]) + 1 if ticket and ticket["id"] in queue else None
        ),
        "first_ticket": head,
        "block": gate,
        "platform_capacity": "unknown",
    }


@router.get("/{project_id}/{account_id}")
def status(
    project_id: str,
    account_id: str,
    request: Request,
    publication_id: str | None = None,
    actor: Actor = Depends(current_actor),
):
    with get_store().read_snapshot():
        account = record(actor, account_id, "Account", project=project_id)
        pub = (
            record(actor, publication_id, "Publication", project=project_id)
            if publication_id
            else None
        )
        if pub and pub["data"].get("account_id") != account_id:
            raise DomainError("ACCOUNT_MISMATCH", "发布意图不属于此账号。", 409)
        return ok(view(actor, account, pub), request)


class Action(Body):
    project_id: Label
    request_id: Label
    publication: ChosenVersion
    expected_ticket_version: StrictInt = Field(ge=0)
    action: Literal["reserve", "handoff", "release", "defer"]
    reason: Note
    not_before: str | None = None


def save_ticket(actor, pub, account, old, data, extra=()):
    return write(
        actor,
        old,
        pub["project_id"],
        pub["id"],
        "publication_slot_",
        {
            "name": "人工交付 · 席位账本",
            "reservation_kind": CATEGORY,
            "account_id": account["id"],
            "publication_id": pub["id"],
            "publication_version": pub["version"],
            "unit": "manual_handoff",
            **data,
        },
        [ref(pub), ref(account), *extra],
        data.get("reason") or "核对交接席位",
    )


@router.post("/act")
def action(body: Action, request: Request, actor: Actor = Depends(current_actor)):
    if body.action == "handoff" or (body.action == "release" and actor.role == "reviewer"):
        require_approval(actor)
    else:
        require_write(actor)

    def apply():
        pub = record(actor, body.publication.id, "Publication", project=body.project_id)
        check_revision(pub, body.publication.version)
        account = record(actor, pub["data"]["account_id"], "Account", project=body.project_id)
        info = view(actor, account, pub)
        ticket, policy = info["ticket"], info["policy"]
        check_revision(ticket, body.expected_ticket_version)
        if not policy:
            raise DomainError("QUOTA_SETTINGS_REQUIRED", "先为这个账号设置本地配额。", 409)
        state = info["effective_state"]
        instant = clock()
        if ticket and ticket["data"]["account_id"] != account["id"]:
            raise DomainError("ACCOUNT_CHANGED", "已预留的意图不能跨账号转移。", 409)
        if state == "settled":
            raise DomainError("RECONCILE_FIRST", "已回填回执，不能重复交付。", 409)
        if body.action == "release":
            if not ticket:
                raise DomainError("NO_RESERVATION", "没有可释放的席位。", 409)
            if state == "released":
                return ticket
            if state == "in_handoff":
                require_approval(actor)
            return save_ticket(
                actor,
                pub,
                account,
                ticket,
                {
                    "status": "released",
                    "reason": body.reason,
                    "closed_at": instant.isoformat(),
                    "resolution": (
                        "operator_confirmed_not_submitted"
                        if state == "in_handoff"
                        else "unused_released"
                    ),
                },
                [ref(policy)],
            )
        if state == "in_handoff":
            raise DomainError(
                "RECONCILE_FIRST", "交接已开始且外部结果未核对，不能再次预留或延期。", 409
            )
        if current_dependency_problem(account):
            raise DomainError("ACCOUNT_UNAVAILABLE", "账号已失效。", 409)
        if (
            pub["data"].get("outcome") not in (None, "not_sent")
            or pub["data"].get("channel") != "manual"
        ):
            raise DomainError("HANDOFF_UNAVAILABLE", "仅支持尚未发出的人工发布意图。", 409)
        schedule = pub["data"].get("scheduled_at")
        earliest = max(instant, timestamp(schedule)) if schedule else instant
        if body.action == "defer":
            if not body.not_before or timestamp(body.not_before) <= instant:
                raise DomainError("DEFER_TIME", "延期时间须在未来。")
            earliest = max(earliest, timestamp(body.not_before))
            return save_ticket(
                actor,
                pub,
                account,
                ticket,
                {
                    "status": "queued",
                    "reason": body.reason,
                    "enqueued_at": instant.isoformat(),
                    "not_before": earliest.isoformat(),
                    "valid_until": (earliest + DAY).isoformat(),
                },
                [ref(policy)],
            )
        if body.not_before:
            raise DomainError("DEFER_TIME", "延期时间仅用于延期操作。")
        check = publication_preflight(actor, pub, check_quota=False)
        if not check["can_handoff"]:
            raise DomainError(
                "PREFLIGHT_REQUIRED", "人工交接的内容或权利条件未通过。", 409, check["problems"]
            )
        # Content checks can outlive a short hold. Resolve liveness again at the commit boundary.
        info = view(actor, account, pub)
        state = info["effective_state"]
        instant = clock()
        if state == "held" and not info["block"]:
            if body.action == "reserve":
                return ticket
            return save_ticket(
                actor,
                pub,
                account,
                ticket,
                {
                    "status": "in_handoff",
                    "reason": body.reason,
                    "handoff_started": instant.isoformat(),
                    "side_effect_status": "unknown_until_receipt",
                },
                [ref(policy)],
            )
        if body.action == "handoff":
            raise DomainError(
                info["block"] or "QUOTA_RESERVATION_REQUIRED",
                "交接需要当前有效的预留，并通过限流与权限检查。",
                409,
            )
        if state == "queued":
            earliest = max(earliest, timestamp(ticket["data"]["not_before"]))
        settings = policy["data"]
        pending = info["held_or_unknown"]
        wait = blocked_reason(policy, instant)
        if not wait and earliest > instant:
            wait = "scheduled"
        if not wait and pending >= settings["concurrent_slots"]:
            wait = "concurrency"
        if not wait and pending + info["receipt_count_24h"] >= settings["daily_slots"]:
            wait = "daily_slots"
        if not wait and info["first_ticket"] not in (None, (ticket or {}).get("id")):
            wait = "earlier_request"
        if wait and state == "queued" and ticket["data"].get("wait_reason") == wait:
            return ticket
        queued_at = ticket["data"]["enqueued_at"] if state == "queued" else instant.isoformat()
        data = {
            "status": "queued" if wait else "held",
            "reason": body.reason,
            "wait_reason": wait,
            "enqueued_at": queued_at,
            "not_before": earliest.isoformat(),
            "valid_until": (
                ticket["data"]["valid_until"]
                if wait and state == "queued"
                else (
                    earliest + DAY
                    if wait
                    else instant + timedelta(seconds=settings["hold_seconds"])
                ).isoformat()
            ),
        }
        return save_ticket(actor, pub, account, ticket, data, [ref(policy)])

    return ok(on_request("manual-quota-action", body, actor, apply), request)


class ProviderObservation(Body):
    project_id: Label
    account_id: Label
    request_id: Label
    expected_revision: StrictInt = Field(ge=1)
    outcome: Literal["rate_limited", "reviewed"]
    retry_after_seconds: StrictInt | None = Field(default=None, ge=1, le=7 * 86400)
    evidence: Note


@router.post("/provider-observation")
def observe(body: ProviderObservation, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        account = record(actor, body.account_id, "Account", project=body.project_id)
        policy = policy_for(actor, account)
        check_revision(policy, body.expected_revision)
        instant = clock()
        if body.outcome == "rate_limited":
            if not body.retry_after_seconds:
                raise DomainError("BACKOFF_REQUIRED", "需提供保守等待时长。")
            new = instant + timedelta(seconds=body.retry_after_seconds)
            old = policy["data"].get("retry_after")
            retry = max(new, timestamp(old)) if old else new
        else:
            if (
                body.retry_after_seconds
                or blocked_reason(policy, instant) != "CAPABILITY_REFRESH_REQUIRED"
            ):
                raise DomainError("BACKOFF_ACTIVE", "仅在现有限流期结束后才能提交恢复核对。", 409)
            retry = None
        from .production import save

        snapshot = save(
            actor,
            "Capability",
            body.project_id,
            {
                "name": "人工核对 · 账号限流",
                "provider": account["data"].get("provider") or "manual",
                "account_id": account["id"],
                "reported_outcome": body.outcome,
                "observation_basis": "manual_attestation",
                "evidence": body.evidence,
                "api_verified": False,
                "observed_at": instant.isoformat(),
            },
            [ref(account)],
            "登记带来源的限流状态，平台真实额度待核",
        )
        return revise(
            actor,
            policy,
            {
                "refresh_required": body.outcome == "rate_limited",
                "retry_after": retry.isoformat() if retry else None,
                "capability_observation": ref(snapshot),
            },
            body.evidence,
            [ref(account), ref(snapshot)],
        )

    return ok(on_request("manual-quota-observation", body, actor, apply), request)


def settle_receipt(actor, pub, post):
    account = record(actor, pub["data"]["account_id"], "Account", project=pub["project_id"])
    ticket = ticket_for(actor, pub)
    if ticket and ticket["data"]["account_id"] == account["id"]:
        save_ticket(
            actor,
            pub,
            account,
            ticket,
            {
                "status": "settled",
                "reason": "按已登记的人工回执对账",
                "settled_at": clock().isoformat(),
                "receipt": ref(post),
                "actual_slots": 1,
            },
            [ref(post)],
        )
