"""F05/F07/F09. Project-scoped handoff reconciliation, cancellation and intent migration."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt, model_validator

from . import account_quota
from .api import ok
from .auth import Actor, current_actor, require_approval, require_write
from .errors import DomainError
from .media import check_revision
from .production import Body, digest, now, record, ref, resolve_schedule, revise, save, timestamp
from .selection import ChosenVersion
from .store import get_store

router = APIRouter(prefix="/production/publication-lifecycle", tags=["publication lifecycle"])
CLOSED = {"superseded", "cancelled_before_handoff"}
SCHEDULE = ("requested_local_datetime", "timezone", "fold", "scheduled_at", "utc_offset")


class Mutation(Body):
    expected_revision: StrictInt = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=110)
    reason: str = Field(min_length=1, max_length=1000)


class Change(Mutation):
    local_datetime: str | None = None
    timezone: str | None = None
    fold: Literal[0, 1] | None = None
    clear_schedule: bool = False
    artifact: ChosenVersion | None = None
    account: ChosenVersion | None = None
    title: str | None = Field(default=None, min_length=1, max_length=2200)

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.clear_schedule
            and self.local_datetime
            or (self.timezone or self.fold is not None)
            and not self.local_datetime
        ):
            raise ValueError("时区与 fold 随新的排程时间填写，清除排程时不应同时提交新时间。")
        return self


class Observation(Mutation):
    observation: Literal["outcome_unknown", "confirmed_not_sent", "post_removed"]
    observed_at: str
    evidence: str = Field(min_length=1, max_length=4000)
    evidence_source: ChosenVersion | None = None
    post: ChosenVersion | None = None

    @model_validator(mode="after")
    def check_post(self):
        if (self.observation == "post_removed") != bool(self.post):
            raise ValueError("登记撤下观测必须提供精确帖子版本。")
        return self


def lifecycle_block(actor, publication):
    d = publication["data"]
    if d.get("lifecycle_state") in CLOSED:
        return {
            "code": "PUBLICATION_CLOSED",
            "message": "此意图已在本地关闭。请查看变更历史及后续意图。",
        }
    if d.get("outcome") == "outcome_unknown":
        return {"code": "RECONCILE_FIRST", "message": "请核对原交付回执，当前意图禁止再次提交。"}
    predecessor = d.get("predecessor_intent")
    seen = set()
    while predecessor:
        if not isinstance(predecessor, dict) or not isinstance(predecessor.get("id"), str):
            return {
                "code": "PUBLICATION_LINEAGE_INVALID",
                "message": "前序意图引用不完整，需核对迁移记录。",
            }
        key = predecessor["id"]
        if key in seen:
            return {
                "code": "PUBLICATION_LINEAGE_INVALID",
                "message": "意图迁移记录存在循环，需人工核对。",
            }
        seen.add(key)
        try:
            old = record(actor, key, "Publication", project=publication["project_id"])
        except DomainError:
            return {
                "code": "PUBLICATION_LINEAGE_INVALID",
                "message": "无法在本项目核实前序意图，不能继续交付。",
            }
        previous = old["data"]
        if previous.get("outcome") not in {
            "not_sent",
            "superseded_before_handoff",
            "cancelled_before_handoff",
        } or previous.get("post_refs"):
            return {
                "code": "PREDECESSOR_RECEIPT",
                "message": "此前意图出现了在途结果或帖子回执，请核对后再决定是否另行发布。",
                "publication_id": key,
            }
        predecessor = previous.get("predecessor_intent")
    return None


def perform(actor, pid, action, body, fn):
    key = f"publication:{action}:{digest([pid,body.request_id])}"
    result = get_store().idempotent(
        actor.tenant_id, key, key, {"principal": actor.actor_id, **body.model_dump()}, fn
    )
    return result


def fetch(actor, pid, body):
    pub = record(actor, pid, "Publication")
    check_revision(pub, body.expected_revision)
    return pub


def unsent(actor, pub):
    data = pub["data"]
    if (
        data.get("lifecycle_state") in CLOSED
        or data.get("post_refs")
        or data.get("outcome") != "not_sent"
    ):
        raise DomainError(
            "RECONCILE_FIRST", "只有尚未交付的意图可以取消或迁移；已交付的结果须先核对。", 409
        )
    ticket = account_quota.ticket_for(actor, pub)
    if ticket and ticket["data"]["status"] in {"in_handoff", "settled"}:
        raise DomainError(
            "RECONCILE_FIRST", "此席位已有交付记录或帖子回执，请先核对原发布意图。", 409
        )
    return ticket


def release(actor, pub, ticket, reason, proof=None):
    if ticket and ticket["data"]["status"] not in {"released", "settled"}:
        account = record(actor, pub["data"]["account_id"], "Account", project=pub["project_id"])
        return account_quota.save_ticket(
            actor,
            pub,
            account,
            ticket,
            {
                "status": "released",
                "reason": reason,
                "closed_at": now(),
                "resolution": "manual_not_submitted" if proof else "unused_released",
            },
            [ref(proof)] if proof else [],
        )


@router.post("/{pid}/cancel")
def cancel(pid: str, body: Mutation, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pub = fetch(actor, pid, body)
        ticket = unsent(actor, pub)
        released = release(actor, pub, ticket, body.reason)
        updated = revise(
            actor,
            pub,
            {
                "status": "cancelled",
                "lifecycle_state": "cancelled_before_handoff",
                "outcome": "cancelled_before_handoff",
                "closed_at": now(),
                "closure_reason": body.reason,
            },
            body.reason,
        )
        return {
            "publication": updated,
            "released_ticket": ref(released) if released else None,
            "external_request_sent": False,
        }

    return ok(perform(actor, pid, "cancel", body, apply), request)


@router.post("/{pid}/change")
def change(pid: str, body: Change, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pub = fetch(actor, pid, body)
        ticket = unsent(actor, pub)
        if issue := lifecycle_block(actor, pub):
            raise DomainError(issue["code"], issue["message"], 409)
        d, project = pub["data"], pub["project_id"]
        old_schedule = {k: d[k] for k in SCHEDULE if k in d}
        schedule = (
            resolve_schedule(
                body.local_datetime, body.timezone or d.get("timezone", "Asia/Shanghai"), body.fold
            )
            if body.local_datetime
            else ({} if body.clear_schedule else old_schedule)
        )
        artifact = record(
            actor,
            body.artifact.id if body.artifact else d["artifact_id"],
            "Artifact",
            project=project,
        )
        check_revision(artifact, body.artifact.version if body.artifact else d["artifact_version"])
        account = record(
            actor, body.account.id if body.account else d["account_id"], "Account", project=project
        )
        original_account_version = next(
            (
                r["version"]
                for r in pub["refs"]
                if r["id"] == d["account_id"] and r["kind"] == "Account"
            ),
            None,
        )
        check_revision(account, body.account.version if body.account else original_account_version)
        title = body.title or d["title"]
        if (
            artifact["id"],
            artifact["version"],
            account["id"],
            account["version"],
            title,
            schedule,
        ) == (
            d["artifact_id"],
            d["artifact_version"],
            d["account_id"],
            original_account_version,
            d["title"],
            old_schedule,
        ):
            raise DomainError("PUBLICATION_UNCHANGED", "请改变排程、标题、成片版本或目标账号。")
        from .batch_publications import current_intent, membership

        batch_fields = membership(actor, pub)
        if batch_fields and account["id"] != d["account_id"]:
            if d["channel"] == "tiktok_inbox" and account["data"].get("provider") != "tiktok":
                raise DomainError("TIKTOK_ACCOUNT_SCOPE", "收件箱交付需要 TikTok 账号。", 422)
            with get_store().connection() as db:
                dest, _ = current_intent(
                    db,
                    actor,
                    project,
                    batch_fields["batch_id"],
                    batch_fields["batch_ordinal"],
                    account["id"],
                    d["channel"],
                )
            if dest:
                raise DomainError(
                    "BATCH_PUBLICATION_ACCOUNT_OCCUPIED",
                    "目标账号已为同一批次作品登记了发布意图。",
                    409,
                )
        metadata = {**deepcopy(d.get("metadata", {})), "title": title}
        new_data = {
            "title": title,
            "status": "draft",
            "outcome": "not_sent",
            "artifact_id": artifact["id"],
            "artifact_version": artifact["version"],
            "artifact_hash": artifact["hash"],
            "account_id": account["id"],
            "metadata": metadata,
            "metadata_hash": digest(metadata),
            "channel": d["channel"],
            "post_refs": [],
            "expected_post_count": d.get("expected_post_count"),
            "predecessor_intent": ref(pub),
            "migration_reason": body.reason,
            **schedule,
            **batch_fields,
        }
        # The verifier will flag mismatches with this old binding until an explicit new review.
        if d.get("commerce_binding"):
            new_data["commerce_binding"] = d["commerce_binding"]
        successor = save(
            actor,
            "Publication",
            project,
            new_data,
            [ref(pub), ref(account), ref(artifact)],
            body.reason,
        )
        release(actor, pub, ticket, body.reason)
        retired = revise(
            actor,
            pub,
            {
                "status": "superseded",
                "lifecycle_state": "superseded",
                "outcome": "superseded_before_handoff",
                "successor_intent": ref(successor),
                "closed_at": now(),
            },
            body.reason,
        )
        return {"publication": retired, "successor": successor, "external_request_sent": False}

    return ok(perform(actor, pid, "change", body, apply), request)


@router.post("/{pid}/observe")
def observe(pid: str, body: Observation, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    if not body.evidence.strip() or not body.reason.strip():
        raise DomainError("OBSERVATION_EVIDENCE", "请填写实际观察的凭据和原因。")
    when = timestamp(body.observed_at)
    if when > datetime.now(timezone.utc):
        raise DomainError("FUTURE_OBSERVATION", "不能登记未来的发布观察。")

    def apply():
        pub = fetch(actor, pid, body)
        data, project = pub["data"], pub["project_id"]
        refs = [ref(pub)]
        if body.evidence_source:
            source = record(actor, body.evidence_source.id, "Source", project=project)
            check_revision(source, body.evidence_source.version)
            refs.append(ref(source))
        ticket = account_quota.ticket_for(actor, pub)
        post = None
        if body.observation == "post_removed":
            post = record(actor, body.post.id, "Post", project=project)
            check_revision(post, body.post.version)
            if (
                post["data"].get("publication_id") != pid
                or post["data"].get("account_id") != data["account_id"]
                or not post["data"].get("platform_post_id")
            ):
                raise DomainError(
                    "POST_BINDING_MISMATCH", "所选帖子与此意图的账号和回执不匹配。", 409
                )
            if post["data"].get("visibility") == "removed":
                raise DomainError("POST_ALREADY_REMOVED", "该帖子已记录为撤下。", 409)
            if post["data"].get("last_observed_at") and when < timestamp(
                post["data"]["last_observed_at"]
            ):
                raise DomainError("STALE_OBSERVATION", "撤下观察早于该帖已有观测。", 409)
            refs.append(ref(post))
        else:
            if (
                data.get("post_refs")
                or data.get("outcome") in {"published", "manual_reported"}
                or (ticket and ticket["data"]["status"] == "settled")
            ):
                raise DomainError(
                    "POST_RECEIPT_EXISTS", "已有实际帖子回执，不能将原意图恢复为未发送。", 409
                )
            if (
                (
                    ticket
                    and ticket["data"].get("handoff_started")
                    and when < timestamp(ticket["data"]["handoff_started"])
                )
                or when < timestamp(pub["created_at"])
                or data.get("lifecycle_observed_at")
                and when < timestamp(data["lifecycle_observed_at"])
            ):
                raise DomainError("STALE_OBSERVATION", "观察早于意图或已有核对记录。", 409)
            if (
                body.observation == "confirmed_not_sent"
                and data.get("outcome") != "outcome_unknown"
                and not (ticket and ticket["data"]["status"] == "in_handoff")
            ):
                raise DomainError("NO_UNKNOWN_RESULT", "当前意图没有待核对的交付。", 409)
        event = save(
            actor,
            "PlatformEvent",
            project,
            {
                "provider": "manual",
                "publication_id": pid,
                "event_type": body.observation,
                "event_time": body.observed_at,
                "received_at": now(),
                "source_kind": "manual",
                "evidence": body.evidence,
                "signature_result": "not_applicable",
                "independently_verified": False,
                "post_id": post["id"] if post else None,
            },
            refs,
            body.reason,
        )
        changes = {"last_lifecycle_event": ref(event), "lifecycle_observed_at": body.observed_at}
        if post:
            post = revise(
                actor,
                post,
                {
                    "visibility": "removed",
                    "last_observed_at": body.observed_at,
                    "removal_observation": ref(event),
                    "removal_verification": "operator_attestation",
                },
                body.reason,
                [*post["refs"], ref(event)],
            )
        elif body.observation == "outcome_unknown":
            changes.update(outcome="outcome_unknown", status="awaiting_reconciliation")
        else:
            release(actor, pub, ticket, body.reason, event)
            if data.get("lifecycle_state") in CLOSED:
                changes.update(
                    outcome=(
                        "superseded_before_handoff"
                        if data["lifecycle_state"] == "superseded"
                        else "cancelled_before_handoff"
                    )
                )
            else:
                changes.update(outcome="not_sent", status="draft")
        updated = revise(actor, pub, changes, body.reason, [*pub["refs"], ref(event)])
        return {
            "publication": updated,
            "post": post,
            "event": event,
            "external_request_sent": False,
        }

    return ok(perform(actor, pid, "observe", body, apply), request)
