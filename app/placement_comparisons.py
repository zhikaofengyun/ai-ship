"""Descriptive comparisons of two source revisions at the same final-cut position."""

from collections import defaultdict
from datetime import date
from statistics import fmean

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .placement_performance import PlacementCohort, placement_report
from .production import digest, record, ref, save
from .selection import ChosenVersion, Label, Note, SelectionBody, on_request

router = APIRouter()


class PlacementComparison(SelectionBody):
    project_id: Label
    cohort: PlacementCohort
    baseline: ChosenVersion
    candidate: ChosenVersion
    role: Label
    published_from: date | None = None
    published_through: date | None = None


class ComparisonHypothesis(PlacementComparison):
    request_id: Label
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis: Note
    counterevidence: Note
    next_edit: Note
    stop_rule: Note


def example_receipts(samples):
    fields = (
        "post",
        "post_version",
        "receipt",
        "receipt_version",
        "cohort_denominator",
        "entrants",
        "retained",
        "source_note",
    )
    return [{k: x[k] for k in fields} for x in samples[:3]]


def matched_days(base_samples, candidate_samples):
    """Each shared publication day has equal weight; within day each post has equal weight."""
    by_day = defaultdict(lambda: [[], []])
    overlap = {}
    partial = set()
    for arm, samples in enumerate((base_samples, candidate_samples)):
        for row in samples:
            if row["overlaps_comparator"]:
                overlap[row["post"]] = row
            elif not row["covers_interval"]:
                partial.add(row["post"])
            else:
                by_day[row["published_day_utc"]][arm].append(row)
    days = []
    evidence = []
    unmatched = [0, 0]
    totals = [{"posts": 0, "entrants": 0, "retained": 0, "other_source_posts": 0} for _ in range(2)]
    for day, groups in sorted(by_day.items()):
        if not all(groups):
            for arm, samples in enumerate(groups):
                unmatched[arm] += len(samples)
            continue
        entry = {"published_day_utc": day}
        for name, samples, total in zip(("baseline", "candidate"), groups, totals):
            entry[name] = {
                "posts": len(samples),
                "entrants": sum(x["entrants"] for x in samples),
                "retained": sum(x["retained"] for x in samples),
                "other_source_posts": sum(x["multiple_sources_in_interval"] for x in samples),
                "mean_post_retention": fmean(x["conditional_retention"] for x in samples),
            }
            entry[name]["example_receipts"] = example_receipts(samples)
            for k in total:
                total[k] += entry[name][k]
            evidence.extend(samples)
        entry["difference_pp"] = 100 * (
            entry["candidate"]["mean_post_retention"] - entry["baseline"]["mean_post_retention"]
        )
        days.append(entry)
    differences = [d["difference_pp"] for d in days]
    total_difference = sum(differences)
    # Descriptive sensitivity on observed publication days, not a confidence interval.
    omit_one = (
        [(total_difference - diff) / (len(days) - 1) for diff in differences]
        if len(days) >= 2
        else []
    )
    shared = list(overlap.values())
    return {
        "co_occurring_observations": {
            "posts": len(shared),
            "entrants": sum(x["entrants"] for x in shared),
            "retained": sum(x["retained"] for x in shared),
            "mean_post_retention": (
                fmean(x["conditional_retention"] for x in shared) if shared else None
            ),
            "example_receipts": example_receipts(shared),
        },
        "days": days,
        "matched_days": len(days),
        "matched_baseline": totals[0],
        "matched_candidate": totals[1],
        "difference_pp": fmean(differences) if days else None,
        "leave_one_day_out_range_pp": [min(omit_one), max(omit_one)] if omit_one else None,
        "co_occurring_posts_excluded": len(overlap),
        "partial_interval_posts_excluded": len(partial),
        "unmatched_baseline_posts": unmatched[0],
        "unmatched_candidate_posts": unmatched[1],
    }, evidence


def compare(actor, query):
    if query.baseline == query.candidate:
        raise DomainError("DISTINCT_PLACEMENTS_REQUIRED", "请选择两条不同的素材或版本。")
    if (
        query.published_from
        and query.published_through
        and query.published_from > query.published_through
    ):
        raise DomainError("PUBLISHED_RANGE", "发布结束日必须等于或晚于起始日。")
    keys = [(p.id, p.version, query.role) for p in (query.baseline, query.candidate)]
    report = placement_report(
        actor,
        query.project_id,
        **query.cohort.model_dump(),
        selected=set(keys),
        published_from=query.published_from,
        published_through=query.published_through,
    )
    rows = {(r["source_id"], r["source_version"], r["role"]): r for r in report["items"]}
    if not all(key in rows for key in keys):
        raise DomainError("PLACEMENT_SAMPLE_MISSING", "所选素材在当前口径下没有可用观测。", 409)
    baseline, candidate = [rows[k] for k in keys]
    result, evidence = matched_days(baseline["evidence_records"], candidate["evidence_records"])
    scope = query.model_dump(mode="json", include=set(PlacementComparison.model_fields))
    return {
        **result,
        "scope": scope,
        "baseline_name": baseline["name"],
        "candidate_name": candidate["name"],
        "evidence_hash": digest(
            {
                "method": "exclusive_full_interval_matched_utc_day_v1",
                "scope": scope,
                "baseline": baseline["evidence_hash"],
                "candidate": candidate["evidence_hash"],
            }
        ),
        "statistic": "equal_day_mean_of_candidate_minus_baseline_post_means",
        "cohort_diagnostics": {
            k: report[k]
            for k in (
                "matching_posts",
                "missing_boundary_points",
                "zero_entrants",
                "conflicting_latest_posts",
                "revised_latest_receipts",
            )
        },
        "causal_claim": False,
    }, evidence


@router.post("/placement-comparisons")
def compare_placements(
    body: PlacementComparison,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    actor: Actor = Depends(current_actor),
):
    result, _ = compare(actor, body)
    return ok(
        {**result, "days": result["days"][offset : offset + limit], "offset": offset}, request
    )


def checked_comparison(actor, query, expected):
    result, evidence = compare(actor, query)
    if not result["matched_days"] or result["evidence_hash"] != expected:
        raise DomainError(
            "PLACEMENT_COMPARISON_CHANGED", "请重新核对两个素材的同日观测，再固定或复用假设。", 409
        )
    return result, evidence


@router.post("/placement-comparison-hypotheses")
def freeze_comparison(
    body: ComparisonHypothesis, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)

    def apply():
        result, evidence = checked_comparison(actor, body, body.evidence_hash)
        if len(evidence) > 1000:
            raise DomainError(
                "HYPOTHESIS_SAMPLE_LIMIT", "本次快照最多固定 1000 篇帖子，请缩小观察范围。", 422
            )
        pins = [
            ref(record(actor, p.id, version=p.version, project=body.project_id))
            for p in (body.baseline, body.candidate)
        ]
        pins.append(ref(record(actor, body.cohort.account_id, "Account", project=body.project_id)))
        for row in evidence:
            pins.extend(
                [
                    {"id": row["post"], "kind": "Post", "version": row["post_version"]},
                    {"id": row["receipt"], "kind": "Insight", "version": row["receipt_version"]},
                ]
            )
        insight = save(
            actor,
            "Insight",
            body.project_id,
            {
                "name": "同日素材对照 · " + result["candidate_name"][:100],
                "scope": {
                    "level": "matched_placement_hypothesis",
                    "clip_attribution": "co_occurrence_only",
                },
                "observation": result,
                "reuse_hypothesis": body.model_dump(
                    include={"hypothesis", "counterevidence", "next_edit", "stop_rule"}
                ),
                "uncertainty": "按同账号、流量、观察窗、位置和 UTC 发布日对照；受众、发布时间、其他素材及投放仍可能影响结果。",
                "causal_claim": False,
            },
            pins,
            "固定同日素材对照与待验证的选材假设",
        )
        return {"insight": insight}

    return ok(on_request("placement-comparison-hypothesis", body, actor, apply), request)


def recheck_comparison(actor, project, observation):
    query = PlacementComparison(**observation["scope"])
    if query.project_id != project:
        raise DomainError("PROJECT_MISMATCH", "素材对照所属项目不一致。", 409)
    checked_comparison(actor, query, observation["evidence_hash"])
