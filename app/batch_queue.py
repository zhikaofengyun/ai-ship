"""SQLite-backed fan-out. Immutable batch recipes live in the normal Run ledger."""

import json
import time
from datetime import datetime, timedelta, timezone

from .errors import DomainError
from .store import now

DDL = """
CREATE TABLE IF NOT EXISTS production_batches(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL,
 actor TEXT NOT NULL, role TEXT NOT NULL, credential_digest TEXT, auth_mode TEXT NOT NULL,
 created TEXT NOT NULL, name TEXT NOT NULL, total INTEGER NOT NULL, output TEXT NOT NULL,
 control TEXT NOT NULL, has_generation INTEGER NOT NULL, intent TEXT NOT NULL, last_claim REAL NOT NULL DEFAULT 0,
 generation_limit INTEGER NOT NULL, generation_window INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS batch_event_range ON events(tenant_id,aggregate_id,event_type,created_at);
CREATE INDEX IF NOT EXISTS batch_project ON production_batches(tenant,project,created DESC);
CREATE TABLE IF NOT EXISTS production_items(
 batch TEXT NOT NULL REFERENCES production_batches(id), ordinal INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', due REAL NOT NULL DEFAULT 0,
 token INTEGER NOT NULL DEFAULT 0, timeline_id TEXT, artifact_id TEXT, generation_id TEXT, workflow_run_id TEXT,
 error TEXT, needs_generation INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(batch,ordinal));
CREATE TABLE IF NOT EXISTS production_generation_slots(
 batch TEXT NOT NULL, ordinal INTEGER NOT NULL, slot INTEGER NOT NULL,
 generation_id TEXT NOT NULL, status TEXT NOT NULL, asset_id TEXT, asset_version INTEGER,
 PRIMARY KEY(batch,ordinal,slot), UNIQUE(generation_id),
 FOREIGN KEY(batch,ordinal) REFERENCES production_items(batch,ordinal));
CREATE TABLE IF NOT EXISTS production_acceptance_results(
 batch TEXT NOT NULL,ordinal INTEGER NOT NULL,stage TEXT NOT NULL,audit_id TEXT NOT NULL,status TEXT NOT NULL,
 PRIMARY KEY(batch,ordinal,stage),FOREIGN KEY(batch,ordinal) REFERENCES production_items(batch,ordinal));
CREATE INDEX IF NOT EXISTS batch_ready ON production_items(batch,status,due,ordinal);
CREATE INDEX IF NOT EXISTS batch_states ON production_items(batch,status,ordinal);
"""
STATES = ("queued", "running", "waiting", "succeeded", "failed", "cancelled")
LEASE_SECONDS = 120


def initialize(store):
    # DDL is run during lifespan. API entry points also work in ASGI clients without lifespan.
    if getattr(store, "_batches_ready", False):
        return
    with store.connection() as db:
        db.executescript(DDL)
    with store.transaction(), store.connection() as db:
        if "needs_generation" not in {
            r["name"] for r in db.execute("PRAGMA table_info(production_items)")
        }:
            # A previous recipe had either fixed generation for every item, or none.
            # has_generation=0 already bypasses admission control for all legacy source-only batches.
            db.execute(
                "ALTER TABLE production_items ADD COLUMN needs_generation INTEGER NOT NULL DEFAULT 1"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS batch_local_ready ON production_items(batch,due,ordinal) WHERE status='queued' AND needs_generation=0"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS batch_generation_active ON production_items(batch,status) WHERE needs_generation=1"
        )
    store._batches_ready = True


def get_batch(db, tenant, bid):
    row = db.execute(
        "SELECT * FROM production_batches WHERE tenant=? AND id=?", (tenant, bid)
    ).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "找不到这个批次。", 404)
    return row


def summary(db, row):
    counts = dict.fromkeys(STATES, 0)
    counts.update(
        dict(
            db.execute(
                "SELECT status,count(*) FROM production_items WHERE batch=? GROUP BY status",
                (row["id"],),
            )
        )
    )
    pending = counts["queued"] + counts["running"] + counts["waiting"]
    if row["control"] == "cancelled":
        status = "cancelling" if pending else "cancelled"
    elif not pending:
        status = "completed_with_errors" if counts["failed"] else "completed"
    elif row["control"] == "paused":
        status = "paused"
    else:
        status = "queued" if counts["queued"] == row["total"] else "running"
    return {
        "id": row["id"],
        "project_id": row["project"],
        "name": row["name"],
        "total": row["total"],
        "created_at": row["created"],
        "output": row["output"],
        "status": status,
        "counts": counts,
        "generation_limit": row["generation_limit"],
        "generation_window": row["generation_window"],
    }


def page(db, row, after=0, limit=30, state=None):
    result = summary(db, row)
    clauses, args = ["batch=?", "ordinal>?"], [row["id"], after]
    if state:
        clauses.append("status=?")
        args.append(state)
    rows = db.execute(
        "SELECT ordinal,status,timeline_id,artifact_id,generation_id,workflow_run_id,error FROM production_items WHERE "
        + " AND ".join(clauses)
        + " ORDER BY ordinal LIMIT ?",
        (*args, limit + 1),
    ).fetchall()
    items = [
        {**dict(r), "error": json.loads(r["error"]) if r["error"] else None} for r in rows[:limit]
    ]
    by_ordinal = {r["ordinal"]: r for r in items}
    for r in items:
        r["generation_jobs"] = []
        r["acceptance"] = []
    if items:
        ordinals = list(by_ordinal)
        audits = db.execute(
            "SELECT ordinal,stage,audit_id,status FROM production_acceptance_results WHERE batch=? AND ordinal IN ("
            + ",".join("?" for _ in ordinals)
            + ") ORDER BY ordinal,CASE stage WHEN 'timeline' THEN 0 ELSE 1 END",
            (row["id"], *ordinals),
        )
        for a in audits:
            by_ordinal[a["ordinal"]]["acceptance"].append(dict(a))
    if items and row["has_generation"]:
        ordinals = list(by_ordinal)
        jobs = db.execute(
            """SELECT s.ordinal,s.slot,s.generation_id,s.status,s.asset_id,s.asset_version,
                 (json_extract(v.data_json,'$.stage')='ready' AND
                  coalesce(json_extract(v.data_json,'$.reference_input.png_sha256'), json_extract(v.data_json,'$.reference_input.segment_sha256')) IS NOT NULL AND
                  json_extract(v.data_json,'$.asset_ref.id') IS NOT NULL) AS visual_review_available
               FROM production_generation_slots s
               LEFT JOIN records r ON r.id=s.generation_id AND r.tenant_id=? AND r.project_id=? AND r.kind='ToolCall'
               LEFT JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
               WHERE s.batch=? AND s.ordinal IN ("""
            + ",".join("?" for _ in ordinals)
            + ") ORDER BY ordinal,slot",
            (row["tenant"], row["project"], row["id"], *ordinals),
        )
        for job in jobs:
            by_ordinal[job["ordinal"]]["generation_jobs"].append(
                {**dict(job), "visual_review_available": bool(job["visual_review_available"])}
            )
    return {
        **result,
        "items": items,
        "next_after": items[-1]["ordinal"] if len(rows) > limit else None,
    }


def expiry():
    return (
        (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS))
        .isoformat()
        .replace("+00:00", "Z")
    )


def resource(bid, ordinal):
    return f"{bid}-{ordinal}"


# Resolve in-flight work before opening further provider jobs. Each active generation batch
# has a fixed window of submitted jobs, independent of requested output count.
PICK = """
SELECT b.*, (:state='queued' AND b.has_generation=1 AND
  (SELECT COUNT(*) FROM (SELECT 1 FROM production_items p
   WHERE p.batch=b.id AND p.needs_generation=1 AND p.status IN ('running','waiting') LIMIT 16))
  >=b.generation_window) AS provider_full
FROM production_batches b
WHERE (b.control='active' OR (b.control='cancelled' AND :state IN ('running','waiting')))
 AND EXISTS(SELECT 1 FROM production_items i WHERE i.batch=b.id AND i.status=:state AND i.due<=:stamp)
 AND (NOT provider_full OR EXISTS(SELECT 1 FROM production_items p
   WHERE p.batch=b.id AND p.status='queued' AND p.needs_generation=0 AND p.due<=:stamp))
ORDER BY b.last_claim,b.created LIMIT 1
"""


def candidate(db):
    stamp = time.time()
    for state in ("running", "waiting", "queued"):
        batch = db.execute(PICK, {"state": state, "stamp": stamp}).fetchone()
        if batch:
            local_only = " AND needs_generation=0" if batch["provider_full"] else ""
            row = db.execute(
                "SELECT * FROM production_items WHERE batch=? AND status=? AND due<=?"
                + local_only
                + " ORDER BY due,ordinal LIMIT 1",
                (batch["id"], state, stamp),
            ).fetchone()
            if row:
                return {**dict(batch), **dict(row)}
    return None


def claim(store):
    with store.connection() as db:
        if not candidate(db):
            return None
    with store.transaction(), store.connection() as db:
        row = candidate(db)
        if not row:
            return None
        item = dict(row)
        bid, ordinal = item["batch"], item["ordinal"]
        item["token"] += 1
        db.execute("UPDATE production_batches SET last_claim=? WHERE id=?", (time.time(), bid))
        db.execute(
            "UPDATE production_items SET token=?,due=?,status='running' WHERE batch=? AND ordinal=?",
            (item["token"], time.time() + LEASE_SECONDS, bid, ordinal),
        )
        db.execute(
            """INSERT INTO leases VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,resource_id)
                   DO UPDATE SET owner_id=excluded.owner_id,fencing_token=excluded.fencing_token,expires_at=excluded.expires_at""",
            (item["tenant"], resource(bid, ordinal), item["actor"], item["token"], expiry()),
        )
        return item


def heartbeat(store, item):
    with store.transaction(), store.connection() as db:
        matched = db.execute(
            "UPDATE production_items SET due=? WHERE batch=? AND ordinal=? AND token=? AND status='running' AND due>?",
            (
                time.time() + LEASE_SECONDS,
                item["batch"],
                item["ordinal"],
                item["token"],
                time.time(),
            ),
        )
        if matched.rowcount:
            db.execute(
                "UPDATE leases SET expires_at=? WHERE tenant_id=? AND resource_id=? AND fencing_token=?",
                (expiry(), item["tenant"], resource(item["batch"], item["ordinal"]), item["token"]),
            )


def update(store, item, *, status=None, delay=0, error=None, **pins):
    if pins.keys() - {"artifact_id", "timeline_id", "generation_id", "workflow_run_id"}:
        raise ValueError("unexpected batch checkpoint")
    data = dict(pins)
    if status:
        data.update(
            status=status,
            due=time.time() + delay,
            error=json.dumps(error, ensure_ascii=False) if error else None,
        )
    with store.transaction(), store.connection() as db:
        matched = db.execute(
            "UPDATE production_items SET "
            + ",".join(k + "=?" for k in data)
            + " WHERE batch=? AND ordinal=? AND token=? AND status='running' AND due>?",
            (*data.values(), item["batch"], item["ordinal"], item["token"], time.time()),
        )
        if not matched.rowcount:
            raise DomainError("STALE_FENCE", "工作项已经交由其他工作进程。", 409)
        if status:
            db.execute(
                "UPDATE leases SET expires_at=? WHERE tenant_id=? AND resource_id=? AND fencing_token=?",
                (now(), item["tenant"], resource(item["batch"], item["ordinal"]), item["token"]),
            )
            store.append_event(
                tenant_id=item["tenant"],
                project_id=item["project"],
                actor_id=item["actor"],
                aggregate_id=item["batch"],
                aggregate_version=1,
                event_type="batch.item." + status,
                payload={"ordinal": item["ordinal"], **pins, **({"error": error} if error else {})},
            )
        item.update(pins)
