"""Compare like-for-like first-play observations at a user-selected position in the final cut."""

from collections import defaultdict
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from .api import ok
from .auth import Actor, current_actor
from .errors import DomainError
from .media import check_current_media_rights
from .production import current_dependency_problem, record, timestamp
from .store import get_store

router = APIRouter()

# Restrict evidence to verified retention-writing operations and the same current
# post revision. Multiple imports of the same curve must not multiply exposures.
CURVES = """
SELECT i.id AS insight_id, v.data_json AS data, p.id AS post_id,
       json_extract(pv.data_json,'$.actual_published_at') AS published
FROM command_results c
JOIN records i ON i.tenant_id=c.tenant_id AND i.id=json_extract(c.response_json,'$.insight.id')
JOIN revisions v ON v.tenant_id=i.tenant_id AND v.record_id=i.id AND v.version=i.latest_version
    AND v.content_hash=json_extract(c.response_json,'$.insight.hash')
JOIN records p ON p.tenant_id=i.tenant_id AND p.id=json_extract(v.data_json,'$.observed_curve.post.id')
    AND p.latest_version=json_extract(v.data_json,'$.observed_curve.post.version') AND p.kind='Post'
JOIN revisions pv ON pv.tenant_id=p.tenant_id AND pv.record_id=p.id AND pv.version=p.latest_version
WHERE c.tenant_id=? AND c.idempotency_key GLOB 'retention-mapping:*'
  AND i.project_id=? AND p.project_id=i.project_id AND i.kind='Insight'
  AND json_extract(pv.data_json,'$.account_id')=? AND json_extract(v.data_json,'$.observed_curve.traffic')=?
  AND json_extract(pv.data_json,'$.final_media_status')='verified_same'
ORDER BY julianday(json_extract(v.data_json,'$.observed_curve.observed_at')) DESC,
         v.created_at DESC, i.id DESC
"""


@router.get("/placement-performance")
def performance(
    project_id: str,
    account_id: str,
    request: Request,
    start_seconds: float = Query(ge=0, le=86400),
    end_seconds: float = Query(gt=0, le=86400),
    traffic: Literal["organic", "paid", "mixed", "unknown"] = "organic",
    cohort_after_hours: int = Query(0, ge=0, le=8760),
    cohort_hours: int = Query(24, ge=1, le=8760),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    actor: Actor = Depends(current_actor),
):
    lo, hi = round(start_seconds * 1e6), round(end_seconds * 1e6)
    if lo >= hi:
        raise DomainError("POSITION_RANGE", "终点必须晚于起点。")
    account = record(actor, account_id, "Account", project=project_id)
    if current_dependency_problem(account):
        raise DomainError("ACCOUNT_NOT_CURRENT", "该账号当前不可用于复盘。", 409)
    records = defaultdict(list)
    seen = set()
    no_curve, no_entrants = 0, 0
    store = get_store()
    with store.read_snapshot() as db:
        for row in db.execute(CURVES, (actor.tenant_id, project_id, account_id, traffic)):
            post = row["post_id"]
            if post in seen:
                continue
            data = json.loads(row["data"])
            curve = data["observed_curve"]
            start, end = map(timestamp, (curve["window_start"], curve["window_end"]))
            if (end - start).total_seconds() != cohort_hours * 3600 or (
                start - timestamp(row["published"])
            ).total_seconds() != cohort_after_hours * 3600:
                continue
            seen.add(post)
            points = {round(p["at_seconds"] * 1e6): p["retained"] for p in curve["points"]}
            if lo not in points or hi not in points:
                no_curve += 1
                continue
            if not points[lo]:
                no_entrants += 1
                continue
            occurrences = defaultdict(set)
            for interval in data["intervals"]:
                for layer in interval["contributors"]:
                    a, b = (round(s * 1e6) for s in layer["output_interval_seconds"])
                    if b <= lo or a >= hi:
                        continue
                    pin = layer["source_ref"]
                    occurrences[(pin["id"], pin["version"], layer["role"])].add(
                        layer["clip_use_id"]
                    )
            multiple = len({k[0] for k in occurrences}) > 1
            for key, uses in occurrences.items():
                records[key].append(
                    {
                        "post": post,
                        "receipt": row["insight_id"],
                        "conditional_retention": points[hi] / points[lo],
                        "multiple_sources_in_interval": multiple,
                        "uses": len(uses),
                    }
                )
        rows = []
        visible = {}
        for (asset_id, version, role), samples in records.items():
            pin = (asset_id, version)
            if pin not in visible:
                asset = record(actor, asset_id, version=version, project=project_id)
                try:
                    check_current_media_rights(store, actor, asset)
                    visible[pin] = (
                        asset["data"].get("name") or asset["data"].get("filename") or asset_id
                    )
                except DomainError:
                    visible[pin] = None
            if visible[pin] is None:
                continue
            rows.append(
                {
                    "source_id": asset_id,
                    "source_version": version,
                    "name": visible[pin],
                    "role": role,
                    "posts": len(samples),
                    "clip_uses": sum(x["uses"] for x in samples),
                    "multi_source_posts": sum(x["multiple_sources_in_interval"] for x in samples),
                    "mean_post_retention": sum(x["conditional_retention"] for x in samples)
                    / len(samples),
                    "example_evidence": [
                        {"post": x["post"], "insight": x["receipt"]} for x in samples[:3]
                    ],
                }
            )
        rows.sort(
            key=lambda r: (
                -r["mean_post_retention"],
                -r["posts"],
                r["source_id"],
                r["source_version"],
                r["role"],
            )
        )
    return ok(
        {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "matching_posts": len(seen),
            "missing_boundary_points": no_curve,
            "zero_entrants": no_entrants,
            "statistic": "equal_post_mean_of_end_retained_over_start_retained",
            "cohort_after_hours": cohort_after_hours,
            "cohort_hours": cohort_hours,
            "source_position_seconds": [lo / 1e6, hi / 1e6],
            "traffic": traffic,
            "causal_claim": False,
        },
        request,
    )
