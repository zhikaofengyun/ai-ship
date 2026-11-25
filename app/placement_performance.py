"""Compare like-for-like first-play observations at a user-selected position in the final cut."""

from collections import defaultdict
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .media import check_current_media_rights
from .production import current_dependency_problem, digest, record, ref, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, on_request
from .store import get_store

router = APIRouter()

# Restrict evidence to verified retention-writing operations and the same current
# post revision. Multiple imports of the same curve must not multiply exposures.
CURVES = """
SELECT i.id AS insight_id, i.latest_version AS insight_version, v.content_hash AS receipt_hash, v.data_json AS data, p.id AS post_id,
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
    report = placement_report(
        actor,
        project_id,
        account_id,
        start_seconds,
        end_seconds,
        traffic,
        cohort_after_hours,
        cohort_hours,
    )
    report["items"] = report["items"][offset : offset + limit]
    return ok({**report, "offset": offset}, request)


def placement_report(
    actor,
    project_id,
    account_id,
    start_seconds,
    end_seconds,
    traffic,
    cohort_after_hours,
    cohort_hours,
    selected=None,
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
                if selected is not None and key != selected:
                    continue
                records[key].append(
                    {
                        "post": post,
                        "receipt": row["insight_id"],
                        "receipt_version": row["insight_version"],
                        "receipt_hash": row["receipt_hash"],
                        "post_version": curve["post"]["version"],
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
                    "evidence_hash": digest(
                        sorted(
                            (x["receipt"], x["receipt_hash"], x["post"], x["post_version"])
                            for x in samples
                        )
                    ),
                    **({"evidence_records": samples} if selected else {}),
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
    return {
        "items": rows,
        "total": len(rows),
        "matching_posts": len(seen),
        "missing_boundary_points": no_curve,
        "zero_entrants": no_entrants,
        "statistic": "equal_post_mean_of_end_retained_over_start_retained",
        "cohort_after_hours": cohort_after_hours,
        "cohort_hours": cohort_hours,
        "source_position_seconds": [lo / 1e6, hi / 1e6],
        "traffic": traffic,
        "causal_claim": False,
    }


class PlacementCohort(SelectionBody):
    account_id: Label
    start_seconds: float = Field(ge=0, le=86400)
    end_seconds: float = Field(gt=0, le=86400)
    traffic: Literal["organic", "paid", "mixed", "unknown"]
    cohort_after_hours: int = Field(ge=0, le=8760)
    cohort_hours: int = Field(gt=0, le=8760)


class ReuseHypothesis(SelectionBody):
    project_id: Label
    request_id: Label
    cohort: PlacementCohort
    source: ChosenVersion
    role: Label
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis: Note
    counterevidence: Note
    next_edit: Note
    stop_rule: Note


def audited_sample(actor, project, cohort, source, role, expected):
    report = placement_report(
        actor, project, **cohort.model_dump(), selected=(source.id, source.version, role)
    )
    if not report["items"] or report["items"][0]["evidence_hash"] != expected:
        raise DomainError(
            "PLACEMENT_EVIDENCE_CHANGED", "原片用途或同口径留存证据已变化，请重新查询后登记。", 409
        )
    return report["items"][0]


@router.post("/placement-hypotheses")
def freeze_hypothesis(
    body: ReuseHypothesis, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)

    def apply():
        observation = audited_sample(
            actor, body.project_id, body.cohort, body.source, body.role, body.evidence_hash
        )
        evidence = observation.pop("evidence_records")
        if len(evidence) > 1000:
            raise DomainError(
                "HYPOTHESIS_SAMPLE_LIMIT", "本次快照最多固定 1000 篇帖子，请缩小观察范围。", 422
            )
        source = record(actor, body.source.id, version=body.source.version, project=body.project_id)
        account = record(actor, body.cohort.account_id, "Account", project=body.project_id)
        pins = [ref(source), ref(account)]
        for row in evidence:
            pins.extend(
                [
                    {"id": row["post"], "kind": "Post", "version": row["post_version"]},
                    {"id": row["receipt"], "kind": "Insight", "version": row["receipt_version"]},
                ]
            )
        brief = body.model_dump(include={"hypothesis", "counterevidence", "next_edit", "stop_rule"})
        insight = save(
            actor,
            "Insight",
            body.project_id,
            {
                "name": "素材复用假设 · " + observation["name"][:120],
                "scope": {
                    "level": "placement_reuse_hypothesis",
                    "clip_attribution": "co_occurrence_only",
                },
                "placement_cohort": body.cohort.model_dump(),
                "observation": observation,
                "reuse_hypothesis": brief,
                "causal_claim": False,
                "uncertainty": "同区间素材、受众、时间和投放可能共同影响留存。假设及下一版改法由编辑填写，需要另行验证。",
            },
            pins,
            "固定同口径素材留存及编辑提出的待验证解释",
        )
        return {"insight": insight}

    return ok(on_request("placement-hypothesis", body, actor, apply), request)


def recheck_hypothesis(actor, project, data):
    obs = data["observation"]
    audited_sample(
        actor,
        project,
        PlacementCohort(**data["placement_cohort"]),
        ChosenVersion(id=obs["source_id"], version=obs["source_version"]),
        obs["role"],
        obs["evidence_hash"],
    )
