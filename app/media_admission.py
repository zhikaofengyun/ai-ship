"""Source admission and retention reconciliation for I05 and D06."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictInt, model_validator

from .api import ok
from .auth import Actor, current_actor, require_approval
from .errors import DomainError
from .production import record, ref, revise, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, on_request
from .store import get_store

router = APIRouter()
Purpose = Literal["inspect", "process", "export"]
MEDIA = {"Asset", "AssetVersion", "BlobLocation", "Audio", "Artifact", "DeliveryPackage", "Source"}
REMOTE = {"Publication", "Post"}


class SourceCommand(SelectionBody):
    project_id: Label
    request_id: Label
    source: ChosenVersion
    expected_revision: StrictInt = Field(ge=0)
    reason: Note


class Admission(SourceCommand):
    action: Literal["review", "approve", "restrict", "reject", "quarantine"]
    allowed_purposes: list[Purpose] = Field(default_factory=list, max_length=3)
    retain_until: str | None = Field(default=None, max_length=80)
    evidence: list[ChosenVersion] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_purposes(self):
        if (self.action in {"approve", "restrict"}) != bool(self.allowed_purposes):
            raise ValueError("批准或限制时须明确实际用途，其他操作不接受用途。")
        if len(set(self.allowed_purposes)) != len(self.allowed_purposes):
            raise ValueError("用途重复。")
        return self


def head(store, source):
    with store.connection() as conn:
        row = conn.execute(
            """SELECT a.revision, v.data_json, a.decision_id FROM media_admission_log a
            JOIN revisions v ON v.tenant_id=a.tenant AND v.record_id=a.decision_id AND v.version=1
            WHERE a.tenant=? AND a.project=? AND a.source=? ORDER BY a.revision DESC LIMIT 1""",
            (source["tenant_id"], source["project_id"] or "", source["id"]),
        ).fetchone()
        return (
            (row["revision"], row["decision_id"], json.loads(row["data_json"]))
            if row
            else (0, None, {})
        )


def effective(decision, source, instant):
    if not decision:
        return "legacy_unreviewed"
    action = decision["action"]
    if action in {"quarantine", "reconciled"}:
        return action
    if decision.get("retain_until") and timestamp(decision["retain_until"]) <= instant:
        return "expired"
    if action in {"approve", "restrict"} and source["version"] != decision["subject_version"]:
        return "review"
    return action


def check_admission(store, actor, source, purpose, instant):
    _, _, decision = head(store, source)
    state = effective(decision, source, instant)
    if state == "legacy_unreviewed":
        return
    if state in {"quarantine", "expired", "reconciled"}:
        raise DomainError(
            "MEDIA_RETENTION_BLOCK",
            "来源已隔离、留存到期或完成隔离核销。",
            403,
            {"source_id": source["id"], "scope_status": state},
        )
    allowed = ["inspect"] if state in {"review", "reject"} else decision["allowed_purposes"]
    if purpose not in allowed:
        raise DomainError(
            "MEDIA_ADMISSION_DENIED",
            "入库决定未批准该来源用于此操作。",
            403,
            {"source_id": source["id"], "scope_status": state, "purpose": purpose},
        )


def canonical_import_source(actor, source):
    """A deduplicated import entry is a receipt for the already registered original."""
    with get_store().connection() as conn:
        row = conn.execute(
            """SELECT json_extract(v.data_json,'$.asset_id') FROM reference_index x
            JOIN records r ON r.tenant_id=x.tenant_id AND r.id=x.source_id AND r.latest_version=x.source_version
            JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE x.tenant_id=? AND x.target_id=? AND r.kind='ImportEntry' AND r.project_id=?
            AND json_extract(v.data_json,'$.source_id')=? AND json_extract(v.data_json,'$.status')='deduplicated' LIMIT 1""",
            (actor.tenant_id, source["id"], source["project_id"], source["id"]),
        ).fetchone()
    if row:
        origin = record(actor, row[0], "Asset", version=1, project=source["project_id"])
        return record(actor, origin["data"]["source_id"], "Source", project=source["project_id"])
    return source


def source_state(actor, body):
    source = record(actor, body.source.id, "Source", project=body.project_id)
    canonical = canonical_import_source(actor, source)
    if canonical["id"] != source["id"]:
        raise DomainError(
            "ADMISSION_CANONICAL_SOURCE",
            "该次导入复用了已有素材，请核对其原来源。",
            409,
            {"source": ref(canonical)},
        )
    revision, decision_id, decision = head(get_store(), source)
    if source["version"] != body.source.version or revision != body.expected_revision:
        raise DomainError("ADMISSION_CONFLICT", "来源或准入版本已改变，请重新载入。", 409)
    return source, revision, decision_id, decision


def append_head(source, revision, item):
    with get_store().connection() as conn:
        conn.execute(
            "INSERT INTO media_admission_log VALUES(?,?,?,?,?)",
            (source["tenant_id"], source["project_id"] or "", source["id"], revision, item["id"]),
        )


def response(source, revision, item):
    return {
        "source": ref(source),
        "revision": revision,
        "decision": item,
        "scope_status": effective(item["data"] if item else {}, source, datetime.now(timezone.utc)),
    }


@router.get("/admission")
def read_admission(
    project_id: str, source_id: str, request: Request, actor: Actor = Depends(current_actor)
):
    with get_store().read_snapshot():
        requested = record(actor, source_id, "Source", project=project_id)
        source = canonical_import_source(actor, requested)
        revision, decision_id, _ = head(get_store(), source)
        decision = record(actor, decision_id, version=1) if decision_id else None
        return ok(
            {**response(source, revision, decision), "requested_source": ref(requested)}, request
        )


def record_pool_transition(actor, source, decision):
    # Keep creative lineage immutable. The separate Event binds the new origin revision
    # to the approval; prior governance decisions are not inputs to media rendering.
    with get_store().connection() as conn:
        ids = conn.execute(
            """SELECT DISTINCT r.id FROM reference_index x JOIN records r
            ON r.tenant_id=x.tenant_id AND r.id=x.source_id
            JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=1
            WHERE x.tenant_id=? AND x.target_id=? AND x.source_version=1
            AND r.project_id=? AND r.kind='AssetVersion' AND json_extract(v.data_json,'$.variant')='original'""",
            (actor.tenant_id, source["id"], source["project_id"]),
        ).fetchall()
    state = effective(decision["data"], source, datetime.now(timezone.utc))
    versions = []
    for row in ids:
        item = record(actor, row[0], "AssetVersion", project=source["project_id"])
        revised = revise(
            actor,
            item,
            {
                "admission_projection": {
                    "decision_id": decision["id"],
                    "decision_version": decision["version"],
                    "scope_status": state,
                    "allowed_purposes": decision["data"]["allowed_purposes"],
                    "retain_until": decision["data"].get("retain_until"),
                }
            },
            decision["data"]["reason"],
        )
        versions.append(ref(revised))
    event = save(
        actor,
        "Event",
        source["project_id"],
        {
            "feature_id": "I05",
            "route_id": decision["data"]["route_id"],
            "name": "来源入库判定",
            "pool": state,
            "source_ref": ref(source),
            "decision_ref": ref(decision),
            "asset_versions": versions,
        },
        [ref(source), ref(decision), *versions],
        decision["data"]["reason"],
    )
    return {"asset_versions": versions, "event": event}


@router.post("/admission")
def decide(body: Admission, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        source, revision, previous_id, previous = source_state(actor, body)
        if previous.get("retention_restore_hold"):
            raise DomainError(
                "RESTORE_ADMISSION_HOLD", "恢复来源与现行授权不一致，须先以现行事实重建来源。", 409
            )
        if previous.get("action") == "reconciled":
            raise DomainError("RETENTION_RECONCILED", "已核销的来源永久保留隔离记录。", 409)
        if previous.get("action") == "reject" and body.action in {"approve", "restrict"}:
            raise DomainError("SOURCE_REVIEW_REQUIRED", "退件后须重新开启审核并核对来源。", 409)
        deadline = timestamp(body.retain_until) if body.retain_until else None
        # Keeping the previous deadline avoids losing a retention boundary on a review or correction.
        retain_until = deadline.isoformat() if deadline else previous.get("retain_until")
        evidence = []
        for pin in body.evidence:
            item = record(actor, pin.id, project=body.project_id)
            if item["version"] != pin.version or item["kind"] not in {
                "Audit",
                "Grant",
                "DecisionRecord",
                "Source",
            }:
                raise DomainError("ADMISSION_EVIDENCE", "证据类型或版本不符合本次审核。", 409)
            evidence.append(ref(item))
        if previous_id and all(
            (
                previous.get("subject_hash") == source["hash"],
                previous.get("action") == body.action,
                previous.get("allowed_purposes") == sorted(body.allowed_purposes),
                previous.get("retain_until") == retain_until,
                previous.get("reason") == body.reason,
                previous.get("evidence") == evidence,
            )
        ):
            return {
                **response(source, revision, record(actor, previous_id, version=1)),
                "reused": True,
            }
        refs = [ref(source), *evidence]
        if previous_id:
            refs.append(ref(record(actor, previous_id, version=1)))
        status = {
            "approve": "approved",
            "restrict": "approved",
            "reject": "rejected",
            "review": "proposed",
            "quarantine": "revoked",
        }[body.action]
        item = save(
            actor,
            "Approval",
            body.project_id,
            {
                "feature_id": "I05",
                "route_id": "I05-R3" if body.action == "restrict" else "I05-R2",
                "kind": "rights_check",
                "status": status,
                "action": body.action,
                "subject_kind": "Source",
                "subject_id": source["id"],
                "subject_version": source["version"],
                "subject_hash": source["hash"],
                "principal": actor.actor_id,
                "allowed_purposes": sorted(body.allowed_purposes),
                "retain_until": retain_until,
                "reason": body.reason,
                "evidence": evidence,
                "admission_revision": revision + 1,
            },
            refs,
            body.reason,
        )
        append_head(source, revision + 1, item)
        return {
            **response(source, revision + 1, item),
            **record_pool_transition(actor, source, item),
        }

    return ok(on_request("media-admission", body, actor, apply), request)


# Follow historical target versions used in actual work, including intermediates whose
# latest revision changed. Once an object is found, retain its affected revision in the audit.
IMPACT = """
WITH RECURSIVE impacted(id,version) AS (
 SELECT record_id,version FROM revisions WHERE tenant_id=:tenant AND record_id=:source
 UNION
 SELECT x.source_id,x.source_version FROM impacted i JOIN reference_index x
  ON x.tenant_id=:tenant AND x.target_id=i.id AND x.target_version=i.version
)
SELECT r.id,r.kind,i.version,
 json_extract(v.data_json,'$.object_key') AS object_key
FROM impacted i JOIN records r ON r.tenant_id=:tenant AND r.id=i.id
 JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=i.id AND v.version=i.version
WHERE r.project_id=:project AND r.kind IN
 ('Source','Asset','AssetVersion','BlobLocation','Audio','Artifact','Post','Publication',
  'Timeline','RenderManifest','DeliveryPackage','TimeMap','RenderJob','Usage','ImportEntry')
ORDER BY r.id,i.version
"""


@router.post("/retention-reconcile")
def reconcile(body: SourceCommand, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        source, revision, previous_id, previous = source_state(actor, body)
        if effective(previous, source, datetime.now(timezone.utc)) not in {"expired", "quarantine"}:
            raise DomainError("RETENTION_NOT_DUE", "仅到期或明确隔离的来源可以核销。", 409)
        # Create an independent, immutable decision before inserting potentially many targets.
        decision = save(
            actor,
            "Approval",
            body.project_id,
            {
                **previous,
                "feature_id": "D06",
                "route_id": "D06-R3",
                "status": "revoked",
                "action": "reconciled",
                "subject_version": source["version"],
                "subject_hash": source["hash"],
                "admission_revision": revision + 1,
                "allowed_purposes": [],
                "reason": body.reason,
                "principal": actor.actor_id,
            },
            [ref(source), ref(record(actor, previous_id, version=1))],
            body.reason,
        )
        args = {"tenant": actor.tenant_id, "source": source["id"], "project": body.project_id}
        counts, dispositions = Counter(), Counter()
        checksum = hashlib.sha256()
        with get_store().connection() as conn:
            # Reconciliation records all versions; the paginated detail route serves large projects.
            for row in conn.execute(IMPACT, args):
                kind = row["kind"]
                disposition = (
                    "external_verification_pending"
                    if kind in REMOTE
                    else "logical_quarantine" if kind in MEDIA else "trace_preserved"
                )
                counts[kind] += 1
                dispositions[disposition] += 1
                item = [row["id"], row["version"], kind, disposition]
                checksum.update(
                    (json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
                )
                conn.execute(
                    "INSERT INTO media_retention_items VALUES(?,?,?,?,?,?)",
                    (actor.tenant_id, decision["id"], *item),
                )
            shared = conn.execute(
                """
                WITH affected(blob_id, object_key) AS (
                  SELECT i.target,json_extract(v.data_json,'$.object_key')
                  FROM media_retention_items i JOIN revisions v ON v.tenant_id=i.tenant
                    AND v.record_id=i.target AND v.version=i.version
                  WHERE i.tenant=? AND i.audit=? AND i.kind='BlobLocation'
                )
                SELECT COUNT(DISTINCT a.object_key) FROM affected a WHERE EXISTS(
                    SELECT 1 FROM revisions v JOIN records r
                    ON r.tenant_id=v.tenant_id AND r.id=v.record_id
                    WHERE v.tenant_id=? AND json_extract(v.data_json,'$.object_key')=a.object_key
                    AND r.kind='BlobLocation' AND NOT EXISTS(
                       SELECT 1 FROM affected b WHERE b.blob_id=r.id)
                )""",
                (actor.tenant_id, decision["id"], actor.tenant_id),
            ).fetchone()[0]
        summary = {
            "counts_by_kind": dict(counts),
            "logical_quarantine_count": dispositions["logical_quarantine"],
            "external_verification_pending": dispositions["external_verification_pending"],
            "physical_objects_removed": 0,
            "shared_objects_retained": shared,
            "total_versions": sum(counts.values()),
            "manifest_sha256": checksum.hexdigest(),
            "storage_disposition": "retain_bytes",
            "backup_disposition": "verification_pending",
            "detail_id": decision["id"],
        }
        audit = save(
            actor,
            "Audit",
            body.project_id,
            {
                "feature_id": "D06",
                "route_id": "D06-R3",
                "name": "来源与衍生版本隔离核销",
                "source_ref": ref(source),
                "decision_ref": ref(decision),
                "summary": summary,
                "reason": body.reason,
            },
            [ref(source), ref(decision)],
            body.reason,
        )
        append_head(source, revision + 1, decision)
        return {"audit": audit, "summary": summary, "revision": revision + 1}

    return ok(on_request("retention-reconcile", body, actor, apply), request)


@router.get("/retention-reconcile/{audit_id}")
def read_targets(
    audit_id: str,
    project_id: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    actor: Actor = Depends(current_actor),
):
    audit = record(actor, audit_id, "Audit", version=1, project=project_id)
    if audit["data"].get("feature_id") != "D06":
        raise DomainError("RETENTION_AUDIT_REQUIRED", "需要来源核销审计记录。", 422)
    receipt = audit["data"].get("decision_ref") or {}
    with get_store().connection() as conn:
        rows = conn.execute(
            """SELECT target AS id,version,kind,disposition FROM media_retention_items
           WHERE tenant=? AND audit=? ORDER BY target,version LIMIT ? OFFSET ?""",
            (actor.tenant_id, receipt.get("id"), limit + 1, offset),
        ).fetchall()
    return ok(
        {
            "items": [dict(r) for r in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "audit": ref(audit),
        },
        request,
    )
