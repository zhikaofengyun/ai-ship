"""A06-R3 manual source amendments and version-aware notification of dependent releases."""

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import digest, now, record, ref, revise, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody
from .store import get_store

router = APIRouter(prefix="/production/acquisition", tags=["source changes"])


class Signal(SelectionBody):
    source: ChosenVersion
    event_id: Label
    occurred_at: Label
    change: Literal["missing", "revised", "origin_deleted", "rights_expired", "rights_revoked"]
    confirmed: bool = False
    evidence: Note
    origin_revision: Label | None = None


class Manifest(SelectionBody):
    project_id: Label
    events: list[Signal] = Field(min_length=1, max_length=100)


def release_consumers(actor, source):
    """Follow exact historical input pins, then retain only each current release head."""
    with get_store().connection() as c:
        rows = c.execute(
            """
            WITH RECURSIVE consumers(id,version) AS (
                SELECT source_id,source_version FROM reference_index WHERE tenant_id=? AND target_id=?
                UNION
                SELECT x.source_id,x.source_version FROM consumers AS p
                JOIN reference_index AS x ON x.tenant_id=? AND x.target_id=p.id AND x.target_version=p.version
            )
            SELECT r.id,r.kind,r.latest_version FROM consumers p JOIN records r
              ON r.tenant_id=? AND r.project_id=? AND r.id=p.id AND r.latest_version=p.version
            WHERE r.kind IN ('Publication','Post') ORDER BY r.kind,r.id
        """,
            (actor.tenant_id, source["id"], actor.tenant_id, actor.tenant_id, source["project_id"]),
        ).fetchall()
    pending, history, unknown = [], [], []
    for row in rows:
        item = record(actor, row["id"])
        state = item["data"].get("outcome") or item["data"].get("status")
        if row["kind"] == "Post" or state in {
            "published",
            "manual_reported",
            "confirmed",
            "removed",
            "cancelled",
        }:
            history.append(ref(item))
        elif state in {"outcome_unknown", "submitted", "accepted"}:
            unknown.append(ref(item))
        else:
            pending.append(ref(item))
    return {"release_review": pending, "reconcile_in_flight": unknown, "historical": history}


def observe(actor, project, signal):
    occurred = timestamp(signal.occurred_at)
    if occurred > datetime.now(timezone.utc):
        raise DomainError("FUTURE_OBSERVATION", "来源事件发生时间不能在未来。")
    if signal.change == "revised" and signal.confirmed and not signal.origin_revision:
        raise DomainError("ORIGIN_REVISION_REQUIRED", "请给出已核实的来源版本或校验值。")
    if signal.change == "missing" and signal.confirmed:
        raise DomainError("MISSING_IS_OBSERVATION", "只观察到缺失时不能确认为删除或撤权。")
    store = get_store()
    event_key = "source-amendment:" + digest([project, signal.source.id, signal.event_id])
    fingerprint = {**signal.model_dump(exclude={"source"}), "occurred_at": occurred.isoformat()}

    def apply():
        source = record(actor, signal.source.id, "Source", project=project)
        baseline = record(
            actor, signal.source.id, "Source", version=signal.source.version, project=project
        )
        channel = "rights" if signal.change.startswith("rights_") else "origin"
        state = source["data"].get("sync_watermarks") or {}
        if not isinstance(state, dict):
            raise DomainError("SOURCE_STATE_UNVERIFIABLE", "历史来源水位格式不符，须先核对。", 409)
        previous = state.get(channel)
        if previous and (
            not isinstance(previous, dict) or not {"occurred_at", "change"} <= previous.keys()
        ):
            raise DomainError("SOURCE_STATE_UNVERIFIABLE", "历史来源水位不完整，须先核对。", 409)
        disposition = "observed_only"
        if signal.confirmed:
            if previous and occurred < timestamp(previous["occurred_at"]):
                disposition = "late_ignored"
            elif previous and occurred == timestamp(previous["occurred_at"]):
                disposition = (
                    "duplicate_observation"
                    if (previous["change"], previous.get("origin_revision"))
                    == (signal.change, signal.origin_revision)
                    else "needs_reconciliation"
                )
            elif source["version"] != baseline["version"]:
                raise DomainError("STALE_SELECTION", "来源已变化，请核实当前版本再提交。", 409)
            else:
                disposition = "applied"
        impacts = {"release_review": [], "reconcile_in_flight": [], "historical": []}
        if disposition == "applied":
            changes = {
                "sync_watermarks": {
                    **state,
                    channel: {
                        "occurred_at": occurred.isoformat(),
                        "change": signal.change,
                        "event_id": signal.event_id,
                        "origin_revision": signal.origin_revision,
                    },
                }
            }
            if signal.change == "origin_deleted":
                changes["origin_availability"] = "removed_at_origin"
            elif signal.change == "revised":
                changes.update(
                    origin_availability="observed",
                    origin_revision=signal.origin_revision,
                    content_change_version=source["version"] + 1,
                )
            elif signal.change == "rights_revoked":
                changes.update(status="revoked", revoked_at=occurred.isoformat())
            elif signal.change == "rights_expired":
                changes["expires_at"] = occurred.isoformat()
            impacts = release_consumers(actor, source)
            source = revise(actor, source, changes, "人工清单确认来源变化")
        event = save(
            actor,
            "Event",
            project,
            {
                "name": "来源变化记录",
                "feature_id": "A06",
                "route_id": "A06-R3",
                "aggregate_version": source["version"],
                "source_event_id": signal.event_id,
                "change": signal.change,
                "occurred_at": occurred.isoformat(),
                "received_at": now(),
                "disposition": disposition,
                "confirmed": signal.confirmed,
                "origin_revision": signal.origin_revision,
                "evidence": signal.evidence,
                "principal": actor.actor_id,
                "impact": impacts,
            },
            [ref(source)],
            "登记来源清单信号，保留已发布历史",
        )
        return {
            "event": ref(event),
            "source": ref(source),
            "disposition": disposition,
            "impact": impacts,
        }

    return store.idempotent(actor.tenant_id, event_key, event_key, fingerprint, apply)


@router.post("/changes")
def amend_sources(body: Manifest, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    record(actor, body.project_id, "Project")
    results = []
    for signal in body.events:
        try:
            result = observe(actor, body.project_id, signal)
        except DomainError as err:
            result = {"disposition": "failed", "code": err.code, "message": err.message}
        results.append({"event_id": signal.event_id, **result})
    return ok(
        {"results": results, "failed": sum(x["disposition"] == "failed" for x in results)}, request
    )
