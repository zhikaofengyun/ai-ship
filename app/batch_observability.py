"""Atlas O04/O05. Read-only project health and reproducible comparisons of stored results."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request

from . import batch_queue
from .api import ok
from .auth import Actor, current_actor
from .errors import DomainError
from .production import record, timestamp
from .store import get_store

router = APIRouter(prefix="/observability", tags=["batch operations"])


@router.get("/health")
def health(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    record(actor, project_id, "Project")
    store = get_store()
    batch_queue.initialize(store)
    scope = (actor.tenant_id, project_id)
    check_time = datetime.now(timezone.utc)
    with store.read_snapshot() as db:
        scoped = """ FROM production_batches b JOIN production_items i ON i.batch=b.id
                 WHERE b.tenant=? AND b.project=? """
        counts = dict.fromkeys(batch_queue.STATES, 0)
        counts.update(
            dict(db.execute("SELECT i.status,count(*)" + scoped + " GROUP BY i.status", scope))
        )
        oldest = db.execute(
            "SELECT MIN(CASE WHEN i.due>0 THEN i.due ELSE (julianday(b.created)-2440587.5)*86400 END)"
            + scoped
            + " AND i.status='queued' AND b.control='active'",
            scope,
        ).fetchone()[0]
        lease_row = db.execute(
            """SELECT count(*) FILTER(WHERE l.resource_id IS NULL) missing,
                         count(*) FILTER(WHERE l.resource_id IS NOT NULL AND
                         (julianday(l.expires_at)<=julianday(?) OR i.due<=?)) expired
                FROM production_batches b JOIN production_items i ON i.batch=b.id
                LEFT JOIN leases l ON l.tenant_id=b.tenant AND l.resource_id=b.id||'-'||i.ordinal
                WHERE b.tenant=? AND b.project=? AND i.status='running'""",
            (check_time.isoformat(), check_time.timestamp(), *scope),
        ).fetchone()
        expired, missing = lease_row["expired"], lease_row["missing"]
        paused_batches = db.execute(
            "SELECT COUNT(*) FROM production_batches b WHERE tenant=? AND project=? AND control='paused' AND EXISTS (SELECT 1 FROM production_items i WHERE batch=b.id AND i.status IN ('queued','running','waiting','failed'))",
            scope,
        ).fetchone()[0]
        # Separate provider receipts from other parked work (a paused worker or a busy DAG).
        direct = db.execute(
            """SELECT count(*) """
            + scoped
            + """ AND i.status IN ('waiting','running','failed') AND EXISTS
              (SELECT 1 FROM production_generation_slots g WHERE g.batch=i.batch AND g.ordinal=i.ordinal
                AND g.status NOT IN ('ready','cancelled','failed','reserved'))""",
            scope,
        ).fetchone()[0]
        dag = db.execute(
            """SELECT count(*) FROM production_batches b JOIN production_items i ON i.batch=b.id
               JOIN records r ON r.tenant_id=b.tenant AND r.id=i.workflow_run_id JOIN revisions v ON v.tenant_id=r.tenant_id
               AND v.record_id=r.id AND v.version=r.latest_version WHERE b.tenant=? AND b.project=?
               AND i.status IN ('running','waiting','failed') AND json_extract(v.data_json,'$.status')='waiting_receipt'""",
            scope,
        ).fetchone()[0]
        failures = [
            dict(r)
            for r in db.execute(
                "SELECT COALESCE(json_extract(i.error,'$.code'),'UNKNOWN') AS code,COUNT(*) AS count"
                + scoped
                + " AND i.status='failed' GROUP BY code ORDER BY count DESC,code LIMIT 20",
                scope,
            )
        ]
    return ok(
        {
            "project_id": project_id,
            "observed_at": check_time.isoformat(),
            "counts": counts,
            "queued_oldest_seconds": (
                None if oldest is None else round(max(0, check_time.timestamp() - oldest), 3)
            ),
            "expired_leases": expired,
            "missing_leases": missing,
            "waiting_receipts": direct + dag,
            "receipt_items": {"media_slots": direct, "workflow": dag},
            "paused_batches": paused_batches,
            "failures": failures,
            "alerts": [
                {"code": code, "count": number}
                for code, number in (
                    ("LEASE_EXPIRED", expired),
                    ("LEASE_MISSING", missing),
                    ("ITEM_FAILED", counts["failed"]),
                )
                if number
            ],
        },
        request,
    )


def duration_of_timeline(data):
    ends = []
    for clip in [*data.get("clips", []), *data.get("audio_tracks", [])]:
        if not isinstance(clip, dict):
            return None
        start, end = clip.get("source_in"), clip.get("source_out")
        if not all(isinstance(v, (int, float)) for v in (start, end)) or not end > start:
            return None
        speed = clip.get("speed", 1)
        pos = clip.get("timeline_start", clip.get("start", 0))
        if not isinstance(speed, (float, int)) or speed <= 0 or not isinstance(pos, (float, int)):
            return None
        ends.append(pos + (end - start) / speed)
    return round(max(ends), 6) if ends else None


def duration_bounds(actor, recipe):
    if template := recipe.get("template"):
        data = record(
            actor,
            template["id"],
            "Timeline",
            version=template["version"],
            project=recipe["project_id"],
        )["data"]
        exact = duration_of_timeline(data)
        return {"min_seconds": exact, "max_seconds": exact, "basis": "frozen_template"}
    low = high = 0
    for slot in recipe["slots"]:
        lengths = [c["source_out"] - c["source_in"] for c in slot["choices"]]
        if slot.get("generate"):
            lengths.append(slot["generate"]["duration"])
        low += min(lengths)
        high += max(lengths)
    return {
        "min_seconds": round(low, 6),
        "max_seconds": round(high, 6),
        "basis": "frozen_source_ranges",
    }


def measured_batch(actor, db, row, instant):
    info = batch_queue.summary(db, row)
    recipe = record(actor, row["id"], "Run", version=1, project=row["project"])["data"]["recipe"]
    closed = info["status"] in {"completed", "completed_with_errors", "cancelled"}
    ended = None
    if closed:
        # Last processing/control completion persisted for this batch; later redundant API
        # reads/retries/pause calls must not lower the throughput of an already closed batch.
        last = db.execute(
            """SELECT MAX(created_at) FROM events WHERE tenant_id=? AND aggregate_id=?
                         AND event_type IN ('batch.item.succeeded','batch.item.failed','batch.item.cancelled')""",
            (actor.tenant_id, row["id"]),
        ).fetchone()[0]
        if info["status"] == "cancelled":
            cancellation = db.execute(
                "SELECT MIN(created_at) FROM events WHERE tenant_id=? AND aggregate_id=? AND event_type='batch.control.cancel'",
                (actor.tenant_id, row["id"]),
            ).fetchone()[0]
            last = max((t for t in (last, cancellation) if t), default=None)
        ended = timestamp(last) if last else None
    elapsed = (
        ((ended or instant) - timestamp(row["created"])).total_seconds()
        if ended or not closed
        else None
    )
    success = info["counts"]["succeeded"]
    return {
        **info,
        "format": {k: recipe[k] for k in ("width", "height", "fps")},
        "duration": duration_bounds(actor, recipe),
        "slots": len(recipe["slots"]),
        "workflow_tail": recipe.get("workflow_tail"),
        "ended_at": ended.isoformat() if ended else None,
        "elapsed_seconds": round(elapsed, 6) if elapsed is not None else None,
        "throughput_per_hour": (
            round(3600 * success / elapsed, 4) if elapsed and elapsed > 0 and success else None
        ),
        "throughput_basis": "completed_items_per_elapsed_wall_hour_including_queue_and_wait",
        "cost": {
            "actual_gpu_cost": None,
            "currency": None,
            "reason": "GPU device time and billing were not metered per item",
        },
        "generation_slot_count": sum(bool(s.get("generate")) for s in recipe["slots"]),
    }


def sample_indices(last, requested):
    n = min(last, requested)
    return [1] if n == 1 else [1 + (j * (last - 1)) // (n - 1) for j in range(n)]


def inspect_item(store, actor, db, bid, i):
    row = db.execute(
        "SELECT status,timeline_id,artifact_id,workflow_run_id FROM production_items WHERE batch=? AND ordinal=?",
        (bid, i),
    ).fetchone()
    data = {}
    if row["timeline_id"]:
        data = store.get(row["timeline_id"], actor.tenant_id, version=1)["data"]
    movie = {}
    if row["artifact_id"]:
        movie = store.get(row["artifact_id"], actor.tenant_id, version=1)["data"]
    cuts = [
        (
            c.get("asset_version_id") or c.get("asset_id") or c.get("source_id"),
            c.get("source_in"),
            c.get("source_out"),
            c.get("timeline_start", 0),
            c.get("track", 0),
        )
        for c in data.get("clips", [])
    ]
    qc = movie.get("qc", {}).get("checks", {}).get("full_decode")
    result = {
        **dict(row),
        "clip_count": len(cuts) if data else None,
        "duration_seconds": (
            movie.get("probe", {}).get("duration") if movie else duration_of_timeline(data)
        ),
        "full_decode": qc,
        "file_sha256": movie.get("content_hash"),
        "file_bytes": movie.get("size_bytes"),
    }
    return result, cuts


@router.get("/compare")
def compare(
    project_id: str,
    left: str,
    right: str,
    request: Request,
    sample_size: int = Query(8, ge=1, le=32),
    actor: Actor = Depends(current_actor),
):
    if left == right:
        raise DomainError("BATCH_COMPARISON_DUPLICATE", "请选择两个不同批次。")
    record(actor, project_id, "Project")
    store = get_store()
    batch_queue.initialize(store)
    instant = datetime.now(timezone.utc)
    with store.read_snapshot() as db:
        rows = [batch_queue.get_batch(db, actor.tenant_id, b) for b in (left, right)]
        if any(r["project"] != project_id for r in rows):
            raise DomainError("PROJECT_MISMATCH", "批次必须属于同一项目。", 409)
        a, b = [measured_batch(actor, db, r, instant) for r in rows]
        reasons = []
        if a["output"] != b["output"]:
            reasons.append("output_kind")
        if a["format"] != b["format"]:
            reasons.append("frame_format")
        durations = [(x["duration"]["min_seconds"], x["duration"]["max_seconds"]) for x in (a, b)]
        if durations[0] != durations[1] or any(lo is None or lo != hi for lo, hi in durations):
            reasons.append("duration_not_identical_fixed")
        if a["status"] != "completed" or b["status"] != "completed":
            reasons.append("not_both_fully_completed")
        if not a["throughput_per_hour"] or not b["throughput_per_hour"]:
            reasons.append("throughput_unavailable")
        sample = []
        for index in sample_indices(min(a["total"], b["total"]), sample_size):
            ar, ac = inspect_item(store, actor, db, left, index)
            br, bc = inspect_item(store, actor, db, right, index)
            hashes = ar["file_sha256"], br["file_sha256"]
            sample.append(
                {
                    "ordinal": index,
                    "left": ar,
                    "right": br,
                    "same_clip_sequence": ac == bc if ac and bc else None,
                    "same_file": hashes[0] == hashes[1] if all(hashes) else None,
                }
            )
    return ok(
        {
            "project_id": project_id,
            "observed_at": instant.isoformat(),
            "left": a,
            "right": b,
            "comparison": {
                "comparable": not reasons,
                "reasons": reasons,
                "throughput_ratio": (
                    None
                    if reasons
                    else round(b["throughput_per_hour"] / a["throughput_per_hour"], 6)
                ),
                "ratio_direction": "right_over_left",
                "scope": "descriptive end-to-end output rate; hardware load, retries and sample sizes may differ",
            },
            "sample": sample,
            "sample_method": "same evenly spaced ordinals within common range; both endpoints when sample size exceeds one",
            "additional_inference_calls": 0,
            "additional_renders": 0,
        },
        request,
    )
