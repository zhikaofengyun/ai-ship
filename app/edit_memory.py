"""D04-R3. Explicit project preferences with separate observation and effective times."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt, model_validator

from .api import ok
from .auth import Actor, current_actor, require_approval
from .errors import DomainError
from .production import current_dependency_problem, digest, record, ref, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, on_request
from .store import get_store

router = APIRouter()
PROFILE_PREFIX = "edit_pref_"
MEMORY_PREFIX = "edit_memory_"
Mode = Literal["model", "team"]
Scope = Literal["all", "model", "team"]
Key = Literal["tone", "pace", "opening", "framing", "captions", "music"]


def utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class EditPreference(SelectionBody):
    project_id: Label
    request_id: Label
    key: Key
    mode: Scope = "all"
    expected_version: StrictInt = Field(ge=0)
    action: Literal["set", "revoke"]
    value: str | None = Field(default=None, min_length=1, max_length=400)
    reason: Note
    valid_from: str | None = None
    valid_until: str | None = None
    evidence: list[ChosenVersion] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_action(self):
        if self.action == "set" and not (self.value or "").strip():
            raise ValueError("写入偏好须有具体内容。")
        if self.action == "revoke" and (self.value is not None or self.valid_until is not None):
            raise ValueError("撤回只需要生效时刻和原因。")
        if len({pin.id for pin in self.evidence}) != len(self.evidence):
            raise ValueError("偏好来源不能重复。")
        return self


@router.post("/edit-preferences")
def write_preference(body: EditPreference, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        project = record(actor, body.project_id, "Project")
        store = get_store()
        instant = datetime.now(timezone.utc)
        effective = timestamp(body.valid_from) if body.valid_from else instant
        until = timestamp(body.valid_until) if body.valid_until else None
        if until and until <= effective:
            raise DomainError("PREFERENCE_WINDOW", "失效时间必须晚于生效时间。")
        scope = [actor.tenant_id, project["id"], body.mode, body.key]
        pid = PROFILE_PREFIX + digest(scope)[:32]
        try:
            old = record(actor, pid, "PreferenceProfile", project=project["id"])
        except DomainError as error:
            if error.code != "NOT_FOUND":
                raise
            old = None
        if (old["version"] if old else 0) != body.expected_version or (
            not old and body.action == "revoke"
        ):
            raise DomainError("PREFERENCE_CONFLICT", "偏好版本已改变，请读取当前版本。", 409)
        evidence = []
        for pin in body.evidence:
            source = record(actor, pin.id, project=project["id"])
            if (
                source["kind"]
                not in {"DecisionRecord", "Insight", "TaskContract", "Product", "Source"}
                or source["version"] != pin.version
                or current_dependency_problem(source)
            ):
                raise DomainError("PREFERENCE_EVIDENCE", "来源类型、版本或有效性不符合条件。", 409)
            evidence.append(ref(source))
        interval = {"from": utc(effective), "until": utc(until) if until else None}
        values = {
            "owner_scope": "project",
            "owner_ref": project["id"],
            "profile_version": body.expected_version + 1,
            "explicit_preferences": {body.key: body.value.strip()} if body.action == "set" else {},
            "allowed_learning_scope": {"task": "rough_cut", "mode": body.mode},
            "autonomy_band": "assist",
            "revocation_state": "active" if body.action == "set" else "revoked",
            "consent_record": {
                "actor": actor.actor_id,
                "reason": body.reason,
                "observed_at": utc(instant),
            },
            "effective_interval": interval,
            "preference_key": body.key,
            "provenance_refs": evidence,
        }
        if old:
            profile = store.update(
                pid,
                values,
                tenant_id=actor.tenant_id,
                expected_revision=old["version"],
                actor_id=actor.actor_id,
                refs=[ref(project), *evidence],
                reason=body.reason,
            )
        else:
            profile = store.create(
                "PreferenceProfile",
                values,
                record_id=pid,
                tenant_id=actor.tenant_id,
                project_id=project["id"],
                actor_id=actor.actor_id,
                refs=[ref(project), *evidence],
                reason=body.reason,
            )
        memory = store.create(
            "Memory",
            {
                "feature_id": "D04",
                "route_id": "D04-R3",
                "source_record": ref(profile),
                "valid_time": interval,
                "observed_time": utc(instant),
                "episode": {"key": body.key, "mode": body.mode, "action": body.action},
                "correction": {
                    "previous_profile": ref(old) if old else None,
                    "reason": body.reason,
                },
                "verification": "explicit_project_preference",
                "tenant_scope": actor.tenant_id,
            },
            record_id=MEMORY_PREFIX + digest([pid, profile["version"]])[:32],
            tenant_id=actor.tenant_id,
            project_id=project["id"],
            actor_id=actor.actor_id,
            refs=[ref(profile), *evidence],
            reason=body.reason,
        )
        return {"profile": profile, "memory": memory}

    return ok(on_request("edit-preference", body, actor, apply), request)


def query_preferences(actor, project, mode, valid, known):
    """Resolve corrections visible at known time, then valid windows and mode overrides."""
    store = get_store()
    # Immutable index records point to a full, versioned PreferenceProfile authority.
    with store.connection() as conn:
        rows = conn.execute(
            """WITH matching AS (
            SELECT r.id, v.data_json, ROW_NUMBER() OVER(
              PARTITION BY json_extract(v.data_json,'$.source_record.id')
              ORDER BY json_extract(v.data_json,'$.source_record.version') DESC) AS newest
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=1
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Memory' AND r.id GLOB 'edit_memory_*'
            AND json_extract(v.data_json,'$.episode.mode') IN ('all',?)
            AND json_extract(v.data_json,'$.valid_time.from')<=?
            AND json_extract(v.data_json,'$.observed_time')<=?)
            SELECT id FROM matching WHERE newest=1""",
            (actor.tenant_id, project, mode, utc(valid), utc(known)),
        ).fetchall()
        result = {}
        for row in rows:
            memory = record(actor, row["id"], "Memory", version=1, project=project)
            md = memory["data"]
            pin = md["source_record"]
            profile = record(actor, pin["id"], "PreferenceProfile", pin["version"], project)
            pd = profile["data"]
            interval, episode = md["valid_time"], md["episode"]
            if pd["revocation_state"] != "active" or (
                interval["until"] and interval["until"] <= utc(valid)
            ):
                continue
            # Current rights follow the evidence even in a historical inspection.
            if any(
                current_dependency_problem(record(actor, p["id"], project=project))
                for p in pd["provenance_refs"]
            ):
                continue
            key = episode["key"]
            if key in result and result[key]["mode"] == mode:
                continue
            uses = conn.execute(
                """SELECT COUNT(DISTINCT i.source_id) FROM reference_index i JOIN records r
                ON r.id=i.source_id AND r.tenant_id=i.tenant_id AND r.latest_version=i.source_version
                WHERE i.tenant_id=? AND i.target_id=? AND i.target_version=1 AND r.kind='Run' AND r.project_id=?""",
                (actor.tenant_id, memory["id"], project),
            ).fetchone()[0]
            result[key] = {
                "key": key,
                "mode": episode["mode"],
                "value": pd["explicit_preferences"][key],
                "profile": ref(profile),
                "memory": ref(memory),
                "valid_from": interval["from"],
                "valid_until": interval["until"],
                "observed_at": md["observed_time"],
                "source_versions": pd["provenance_refs"],
                "verification": md["verification"],
                "current_run_uses": uses,
            }
    return [result[k] for k in sorted(result)]


@router.get("/edit-preferences")
def read_preferences(
    project_id: str,
    request: Request,
    mode: Mode = "model",
    valid_at: str | None = None,
    known_at: str | None = None,
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    now = datetime.now(timezone.utc)
    valid = timestamp(valid_at) if valid_at else now
    known = timestamp(known_at) if known_at else now
    if known > now:
        raise DomainError("PREFERENCE_OBSERVATION_TIME", "系统观察时间不能在未来。")
    with get_store().read_snapshot() as conn:
        selected = query_preferences(actor, project_id, mode, valid, known)
        # Heads allow correction after expiry or revocation even when no preference is in effect.
        rows = conn.execute(
            """SELECT r.latest_version, json_extract(v.data_json,'$.preference_key') AS key,
            json_extract(v.data_json,'$.allowed_learning_scope.mode') AS mode
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind='PreferenceProfile' AND r.id GLOB 'edit_pref_*'""",
            (actor.tenant_id, project_id),
        )
        heads = [dict(r) for r in rows]
    return ok(
        {
            "preferences": selected,
            "current_heads": heads,
            "valid_at": utc(valid),
            "known_at": utc(known),
        },
        request,
    )


def freeze_preferences(actor, project, mode):
    now = datetime.now(timezone.utc)
    return [
        {k: v for k, v in row.items() if k != "current_run_uses"}
        for row in query_preferences(actor, project, mode, now, now)
    ]


def recheck_preferences(actor, project, mode, pinned):
    if not pinned:
        return
    now = datetime.now(timezone.utc)
    selected = {
        row["key"]: row["memory"] for row in query_preferences(actor, project, mode, now, now)
    }
    if any(selected.get(row["key"]) != row["memory"] for row in pinned):
        raise DomainError(
            "PREFERENCE_CHANGED", "制作开始后偏好已撤回、修订或过期。请用新任务重审。", 409
        )
