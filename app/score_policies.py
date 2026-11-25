"""Project-scoped release of soft asset-scoring weights. No external side effects."""

from datetime import datetime, timezone
from uuid import uuid4
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
    number,
    record,
    ref,
    revise,
    timestamp,
)
from .selection import ChosenVersion, Label, Note, on_request
from .store import get_store

router = APIRouter(prefix="/production/score-policies", tags=["score policy releases"])
DIMENSIONS = ("quality", "relevance", "fatigue", "confidence", "tag_completeness", "performance")
DEFAULT_WEIGHTS = dict.fromkeys(DIMENSIONS, 1.0)
PREFIX = "score_release_"


def checked_weights(weights):
    if not weights or set(weights) - set(DIMENSIONS):
        raise DomainError("INVALID_DIMENSION", "请选择可用的评分维度。")
    weights = {k: number(v, k, 0, 1e6) for k, v in weights.items()}
    if not sum(weights.values()):
        raise DomainError("INVALID_WEIGHT", "权重之和必须大于零。")
    return weights


def weighted_score(dimensions, weights):
    if set(dimensions) - set(DIMENSIONS):
        raise DomainError("INVALID_DIMENSION", "素材含未知评分维度。")
    values = {k: number(v, k, 0, 1) for k, v in dimensions.items() if v is not None}
    observed = sum(weights.get(k, 0) for k in values)
    score = sum(v * weights.get(k, 0) for k, v in values.items()) / observed if observed else None
    return score, observed / sum(weights.values())


def managed(actor, kind, project, data, refs=(), record_id=None):
    return get_store().create(
        kind,
        data,
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        project_id=project,
        record_id=record_id or PREFIX + uuid4().hex,
        refs=list(refs),
        reason=data["name"],
    )


def pointer(actor, project, initialize=False):
    rid = PREFIX + digest([actor.tenant_id, project, "asset_scoring"])
    try:
        return record(actor, rid, "Policy", project=project)
    except DomainError as e:
        if e.code != "NOT_FOUND":
            raise
    if not initialize:
        return None
    baseline = managed(
        actor,
        "Policy",
        project,
        {
            "name": "素材评分 · 等权基线",
            "supported_schema": "asset_score_weights_v1",
            "typed_config": {"weights": DEFAULT_WEIGHTS},
            "release_state": "active",
        },
    )
    return managed(
        actor,
        "Policy",
        project,
        {
            "name": "素材评分 · 项目活动指针",
            "release_state": "active",
            "active": ref(baseline),
            "previous": None,
            "active_evaluation": None,
            "previous_evaluation": None,
        },
        [ref(baseline)],
        rid,
    )


def production_weights(actor, project):
    with get_store().transaction():
        ptr = pointer(actor, project)
        if not ptr:
            return DEFAULT_WEIGHTS, []
        if ptr["data"]["release_state"] != "paused":
            try:
                active = admissible(
                    actor, project, ptr["data"]["active"], ptr["data"]["active_evaluation"]
                )
            except DomainError as problem:
                if problem.code not in GUARD_ERRORS:
                    raise
                failure_ref = ptr["data"]["active_evaluation"]
                evidence = (
                    record(
                        actor,
                        failure_ref["id"],
                        "Experiment",
                        version=failure_ref["version"],
                        project=project,
                    )
                    if failure_ref
                    else None
                )
                ptr = compensate(
                    actor,
                    project,
                    ptr,
                    problem.message,
                    evidence,
                    pause_only=False,
                    trigger="release_evidence_changed",
                )["pointer"]
                if ptr["data"]["release_state"] != "paused":
                    active = current(actor, project, ptr["data"]["active"], "Policy")
            if ptr["data"]["release_state"] != "paused":
                return active["data"]["typed_config"]["weights"], [ref(active), ref(ptr)]
    # A pause discovered by the guard must commit even though this score is rejected.
    raise DomainError("SCORING_PAUSED", "项目策略已暂停，请先恢复策略或显式使用人工权重。", 409)


def current(actor, project, item_ref, kind):
    item = record(actor, item_ref["id"], kind, project=project)
    if item["version"] != item_ref["version"] or current_dependency_problem(item):
        raise DomainError(
            "STALE_RELEASE_EVIDENCE",
            "证据或策略已修改、撤回或过期。",
            409,
            {"kind": kind, "id": item["id"]},
        )
    return item


def release_record(actor, project, item_ref, kind):
    if not item_ref.id.startswith(PREFIX):
        raise DomainError("RELEASE_RECEIPT_REQUIRED", "请选择由项目策略流程产生的记录。", 409)
    return current(actor, project, item_ref.model_dump(), kind)


class ProjectRequest(Body):
    project_id: Label
    request_id: Label


class Proposal(ProjectRequest):
    name: Label
    hypothesis: Note
    weights: dict[str, float]
    expected_pointer_version: int = Field(ge=0)


@router.get("/{project_id}")
def read_pointer(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    record(actor, project_id, "Project")
    ptr = pointer(actor, project_id)
    return ok({"pointer": ptr, "default_weights": DEFAULT_WEIGHTS}, request)


@router.post("/propose")
def propose(body: Proposal, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        before = pointer(actor, body.project_id)
        if (before["version"] if before else 0) != body.expected_pointer_version:
            raise DomainError("REVISION_CONFLICT", "活动策略已更新，请刷新后重新提出候选。", 409)
        ptr = before or pointer(actor, body.project_id, initialize=True)
        old = current(actor, body.project_id, ptr["data"]["active"], "Policy")
        weights = checked_weights(body.weights)
        prior_weights = old["data"]["typed_config"]["weights"]
        if all(
            weights.get(k, 0) * sum(prior_weights.values())
            == prior_weights.get(k, 0) * sum(weights.values())
            for k in DIMENSIONS
        ):
            raise DomainError("NO_POLICY_CHANGE", "候选与基线的相对权重相同。")
        return managed(
            actor,
            "Policy",
            body.project_id,
            {
                "name": body.name,
                "hypothesis": body.hypothesis,
                "parent": ref(old),
                "release_state": "draft",
                "typed_config": {"weights": weights},
                "supported_schema": "asset_score_weights_v1",
                "base_pointer_version": ptr["version"],
            },
            [ref(old), ref(ptr)],
        )

    return ok(on_request("score-policy-propose", body, actor, apply), request)


class ReviewCase(Body):
    preferred: ChosenVersion
    comparison: ChosenVersion
    reason: Note


class Evaluation(ProjectRequest):
    candidate: ChosenVersion
    cases: list[ReviewCase] = Field(min_length=2, max_length=100)


def versioned_score(actor, project, chosen):
    item = current(actor, project, chosen.model_dump(), "AssetScore")
    asset_ref = next(
        (
            r
            for r in item["refs"]
            if r["kind"] == "AssetVersion" and r["id"] == item["data"].get("asset_version_ref")
        ),
        None,
    )
    if not asset_ref:
        raise DomainError("MISSING_SCORE_EVIDENCE", "每个评分必须引用其素材版本。", 409)
    asset = current(actor, project, asset_ref, "AssetVersion")
    return item, asset


def candidate_for_pointer(actor, project, chosen, ptr):
    candidate = release_record(actor, project, chosen, "Policy")
    if (
        not ptr
        or candidate["data"].get("base_pointer_version") != ptr["version"]
        or (candidate["data"].get("parent") != ptr["data"]["active"])
    ):
        raise DomainError("STALE_BASELINE", "候选基线或指针版本已变化，请重新提案。", 409)
    return candidate


class FitCandidate(ProjectRequest):
    name: Label
    hypothesis: Note
    expected_pointer_version: int = Field(ge=0)
    training_cases: list[ReviewCase] = Field(min_length=2, max_length=100)


def sample_keys(asset):
    keys = {"asset:" + (asset["data"].get("asset_id") or asset["id"])}
    if asset["data"].get("content_hash"):
        keys.add("bytes:" + asset["data"]["content_hash"])
    return keys


def examples(actor, project, cases, exclude=()):
    seen = set(exclude)
    for case in cases:
        a, aa = versioned_score(actor, project, case.preferred)
        b, ba = versioned_score(actor, project, case.comparison)
        for asset in (aa, ba):
            ids = sample_keys(asset)
            if seen & ids:
                raise DomainError("DUPLICATE_SAMPLE", "训练与验收样本须使用不重复的素材和原件。")
            seen.update(ids)
        yield case, a, aa, b, ba


@router.post("/optimize")
def fit_candidate(body: FitCandidate, request: Request, actor: Actor = Depends(current_actor)):
    """Learn only a scoring configuration. Existing evaluation and release guards own promotion."""
    require_approval(actor)

    def apply():
        ptr = pointer(actor, body.project_id)
        if (ptr["version"] if ptr else 0) != body.expected_pointer_version:
            raise DomainError("REVISION_CONFLICT", "活动策略已变化，请重新核对。", 409)
        ptr = ptr or pointer(actor, body.project_id, initialize=True)
        baseline = current(actor, body.project_id, ptr["data"]["active"], "Policy")
        weights = baseline["data"]["typed_config"]["weights"]
        total = sum(weights.values())
        base = {d: weights.get(d, 0) / total for d in DIMENSIONS}
        diffs, pins, used = [], [], set()
        for _, a, aa, b, ba in examples(actor, body.project_id, body.training_cases):
            pair = [s["data"].get("dimension_scores", {}) for s in (a, b)]
            if any(any(v.get(d) is None for d in DIMENSIONS) for v in pair):
                raise DomainError(
                    "INCOMPLETE_TRAINING_CASE", "冻结训练样本需要完整维度，缺测保持未知。"
                )
            diffs.append(
                {d: number(pair[0][d], d, 0, 1) - number(pair[1][d], d, 0, 1) for d in DIMENSIONS}
            )
            pins.extend(ref(v) for v in (a, aa, b, ba))
            used.update(sample_keys(aa) | sample_keys(ba))

        def correct(candidate):
            return sum(
                sum(weights * row[d] for d, weights in candidate.items()) > 1e-12 for row in diffs
            )

        # Prefer the smallest change from the active baseline that improves the
        # number of correctly ordered training pairs. The grid is deterministic.
        best_weights, best_correct = base, correct(base)
        evaluations = 1
        for share in (0.25, 0.5, 0.75, 1.0):
            for axis in DIMENSIONS:
                trial = {d: base[d] * (1 - share) + share * (axis == d) for d in DIMENSIONS}
                observed = correct(trial)
                evaluations += 1
                if observed > best_correct:
                    best_weights, best_correct = trial, observed
        if best_correct <= correct(base):
            raise DomainError(
                "NO_TRAINING_IMPROVEMENT", "在冻结样本上未找到排序优于基线的权重。", 409
            )
        return managed(
            actor,
            "Policy",
            body.project_id,
            {
                "name": body.name,
                "hypothesis": body.hypothesis,
                "parent": ref(baseline),
                "release_state": "draft",
                "supported_schema": "asset_score_weights_v1",
                "typed_config": {"weights": best_weights},
                "base_pointer_version": ptr["version"],
                "optimizer": {
                    "method": "bounded_pairwise_coordinate_grid_v1",
                    "evaluations": evaluations,
                    "baseline_correct": correct(base),
                    "candidate_correct": best_correct,
                    "cases": [c.model_dump() for c in body.training_cases],
                    "asset_keys": sorted(used),
                    "training_refs": pins,
                    "objective": "strict_pairwise_preference",
                    "causal_claim": False,
                },
            },
            [ref(baseline), ref(ptr), *pins],
        )

    return ok(on_request("score-policy-optimize", body, actor, apply), request)


@router.post("/evaluate")
def evaluate(body: Evaluation, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        ptr = pointer(actor, body.project_id)
        candidate = candidate_for_pointer(actor, body.project_id, body.candidate, ptr)
        baseline = current(actor, body.project_id, ptr["data"]["active"], "Policy")
        policies = {"baseline": baseline, "candidate": candidate}
        optimizer = candidate["data"].get("optimizer") or {}
        training_refs = optimizer.get("training_refs", [])
        for link in training_refs:
            current(actor, body.project_id, link, link["kind"])
        cases, refs = [], [ref(candidate), ref(baseline), *training_refs]
        for case, a, aa, b, ba in examples(
            actor, body.project_id, body.cases, optimizer.get("asset_keys", [])
        ):
            verdicts = {}
            for role, policy in policies.items():
                weights = policy["data"]["typed_config"]["weights"]
                scores = [
                    weighted_score(item["data"].get("dimension_scores", {}), weights)
                    for item in (a, b)
                ]
                covered = all(
                    score is not None and abs(1 - coverage) <= 1e-12 for score, coverage in scores
                )
                verdicts[role] = {
                    "values": [score for score, _ in scores],
                    "complete": covered,
                    "ordered": covered and scores[0][0] > scores[1][0],
                }
            cases.append({**case.model_dump(), **verdicts})
            refs.extend(ref(item) for item in (a, b, aa, ba))
        complete = all(c[role]["complete"] for c in cases for role in policies)
        passed = (
            complete
            and all(c["candidate"]["ordered"] for c in cases)
            and any(not c["baseline"]["ordered"] for c in cases)
        )
        return managed(
            actor,
            "Experiment",
            body.project_id,
            {
                "name": "素材评分 · 冻结样本回归",
                "baseline": ref(baseline),
                "candidate": ref(candidate),
                "status": "passed" if passed else "failed",
                "reviewer": actor.actor_id,
                "result": {
                    "passed": passed,
                    "cases": cases,
                    "causal_claim": False,
                    "rule": "全部样本维度齐全，候选通过每对人工顺序，且至少纠正一项基线排序",
                },
            },
            refs,
        )

    return ok(on_request("score-policy-evaluate", body, actor, apply), request)


def eligible(actor, project, evaluation, candidate):
    if evaluation["data"].get("candidate") != ref(candidate) or not (
        evaluation["data"].get("result", {}).get("passed") is True
    ):
        raise DomainError("REGRESSION_GATE", "需有绑定该候选的通过回归记录。", 409)
    for link in evaluation["refs"]:
        current(actor, project, link, link["kind"])


class ApprovalRequest(ProjectRequest):
    candidate: ChosenVersion
    evaluation: ChosenVersion
    expires_at: str
    reason: Note


@router.post("/approve")
def approve(body: ApprovalRequest, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        ptr = pointer(actor, body.project_id)
        candidate = candidate_for_pointer(actor, body.project_id, body.candidate, ptr)
        exp = release_record(actor, body.project_id, body.evaluation, "Experiment")
        eligible(actor, body.project_id, exp, candidate)
        expiry = timestamp(body.expires_at)
        if expiry <= datetime.now(timezone.utc):
            raise DomainError("APPROVAL_EXPIRED", "批准到期时间须在未来。", 409)
        return managed(
            actor,
            "Approval",
            body.project_id,
            {
                "name": "素材评分 · 晋升批准",
                "decision": "approved",
                "status": "approved",
                "subject_kind": "Policy",
                "subject_id": candidate["id"],
                "subject_version": candidate["version"],
                "subject_hash": candidate["hash"],
                "operation": "activate_asset_scoring_policy",
                "approved_guard": "recheck_frozen_evidence_before_each_project_score",
                "evaluation_ref": ref(exp),
                "scope": {"project_id": body.project_id, "consumer": "asset_score"},
                "principal": actor.actor_id,
                "actor_role": actor.role,
                "evidence": body.reason,
                "expires_at": expiry.isoformat(),
                "pointer_version": ptr["version"],
            },
            [ref(candidate), ref(exp)],
        )

    return ok(on_request("score-policy-approve", body, actor, apply), request)


class Activate(ProjectRequest):
    approval: ChosenVersion


def approved_candidate(actor, project, chosen):
    approval = release_record(actor, project, chosen, "Approval")
    data = approval["data"]
    if (
        data.get("operation") != "activate_asset_scoring_policy"
        or data.get("decision") != "approved"
    ):
        raise DomainError("WRONG_APPROVAL", "批准未授权评分策略晋升。", 409)
    ptr = pointer(actor, project)
    cand = candidate_for_pointer(
        actor,
        project,
        ChosenVersion(id=data["subject_id"], version=data["subject_version"]),
        ptr,
    )
    if data["subject_hash"] != cand["hash"] or data["pointer_version"] != ptr["version"]:
        raise DomainError("STALE_APPROVAL", "批准不覆盖当前策略版本。", 409)
    exp = current(actor, project, data["evaluation_ref"], "Experiment")
    eligible(actor, project, exp, cand)
    return approval, ptr, cand, exp


@router.post("/activate")
def activate(body: Activate, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        approval, ptr, cand, exp = approved_candidate(actor, body.project_id, body.approval)
        # Inspect this candidate's latest enrollment even if a different candidate ran afterwards.
        trial_id, _ = trial_head(actor, body.project_id)
        with get_store().connection() as db:
            last = db.execute(
                """SELECT version FROM revisions WHERE tenant_id=? AND record_id=?
                AND json_extract(data_json,'$.candidate.id')=? AND json_extract(data_json,'$.baseline_pointer.version')=?
                ORDER BY version DESC LIMIT 1""",
                (actor.tenant_id, trial_id, cand["id"], ptr["version"]),
            ).fetchone()
        trial = None
        if last:
            trial = record(actor, trial_id, "Experiment", last["version"], body.project_id)
            if (
                trial["data"].get("status") != "stopped"
                or trial["data"].get("review_decision") != "recommend_release"
            ):
                raise DomainError(
                    "TRIAL_REVIEW_REQUIRED",
                    "该候选已进入限量试用，须先审阅两组观察并明确建议推广。",
                    409,
                )
        old = ptr["data"]
        return revise(
            actor,
            ptr,
            {
                "active": ref(cand),
                "previous": old["active"],
                "active_evaluation": ref(exp),
                "previous_evaluation": old["active_evaluation"],
                "release_state": "active",
                "activation_approval": ref(approval),
                "trial_review": ref(trial) if trial else None,
            },
            "项目素材评分策略已生效",
            [ref(cand), old["active"], ref(exp), ref(approval), *([ref(trial)] if trial else [])],
        )

    return ok(on_request("score-policy-activate", body, actor, apply), request)


GUARD_ERRORS = {"STALE_RELEASE_EVIDENCE", "REGRESSION_GATE", "NOT_FOUND"}


def admissible(actor, project, chosen, evaluation):
    policy = current(actor, project, chosen, "Policy")
    if evaluation:
        exp = current(actor, project, evaluation, "Experiment")
        eligible(actor, project, exp, policy)
    return policy


def compensate(actor, project, ptr, reason, evidence, pause_only, trigger):
    old = ptr["data"]
    fallback = old["previous"]
    block_reason = "按人工决定暂停" if pause_only else "无可用的已验证回退版本"
    if fallback and not pause_only:
        try:
            admissible(actor, project, fallback, old["previous_evaluation"])
        except DomainError as error:
            if error.code not in GUARD_ERRORS:
                raise
            fallback, block_reason = None, error.message
    restore = bool(fallback and not pause_only)
    links = [old["active"]] + ([ref(evidence)] if evidence else [])
    decision = managed(
        actor,
        "DecisionRecord",
        project,
        {
            "name": "素材评分 · 回退决定",
            "decision_kind": "recovery",
            "selection_reason": reason,
            "decider_principal": actor.actor_id,
            "trigger": trigger,
            "disposition": "restore_previous" if restore else "pause",
            "failure_evidence": ref(evidence) if evidence else None,
        },
        links,
    )
    changes = {
        "release_state": "active" if restore else "paused",
        "rollback_decision": ref(decision),
        "activation_approval": None,
        "hold_reason": None if restore else block_reason,
    }
    if restore:
        changes.update(
            active=fallback,
            previous=None,
            active_evaluation=old["previous_evaluation"],
            previous_evaluation=None,
        )
    updated = revise(
        actor,
        ptr,
        changes,
        "受控回退项目素材评分策略",
        [*links, ref(decision), *([fallback] if restore else [])],
    )
    return {"pointer": updated, "decision": decision}


class Rollback(ProjectRequest):
    expected_pointer_version: int = Field(ge=1)
    reason: Note
    evidence: ChosenVersion | None = None
    pause_only: bool = False


@router.post("/rollback")
def rollback(body: Rollback, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        ptr = pointer(actor, body.project_id)
        if not ptr or ptr["version"] != body.expected_pointer_version:
            raise DomainError("REVISION_CONFLICT", "活动策略已变化，请重新查看。", 409)
        old = ptr["data"]
        if old["release_state"] == "paused" and body.pause_only:
            raise DomainError("ALREADY_PAUSED", "当前项目的策略已暂停。", 409)
        evidence = (
            record(actor, body.evidence.id, version=body.evidence.version, project=body.project_id)
            if body.evidence
            else None
        )
        return compensate(
            actor, body.project_id, ptr, body.reason, evidence, body.pause_only, "human_review"
        )

    return ok(on_request("score-policy-rollback", body, actor, apply), request)


class StartTrial(Activate):
    percent: StrictInt = Field(ge=1, le=99)
    expected_trial_version: StrictInt = Field(ge=0)
    stop_condition: Note


class StopTrial(ProjectRequest):
    expected_trial_version: StrictInt = Field(ge=1)
    reason: Note
    decision: Literal["no_recommendation", "recommend_release"] = "no_recommendation"


def trial_head(actor, project):
    rid = PREFIX + "trial_" + digest([actor.tenant_id, project])
    try:
        return rid, record(actor, rid, "Experiment", project=project)
    except DomainError as exc:
        if exc.code != "NOT_FOUND":
            raise
        return rid, None


def live_trial(actor, project, trial, ptr):
    if not trial or trial["data"]["status"] != "running":
        return None
    spec = trial["data"]
    if not ptr or ref(ptr) != spec["baseline_pointer"] or ptr["data"]["release_state"] != "active":
        return "项目活动策略已变更或暂停"
    try:
        approved_candidate(
            actor,
            project,
            ChosenVersion(id=spec["approval"]["id"], version=spec["approval"]["version"]),
        )
    except DomainError as exc:
        return exc.message
    return None


@router.post("/trials/start")
def start_trial(body: StartTrial, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        approval, ptr, cand, exp = approved_candidate(actor, body.project_id, body.approval)
        if ptr["data"]["release_state"] != "active":
            raise DomainError("SCORING_PAUSED", "项目评分已暂停。", 409)
        rid, previous = trial_head(actor, body.project_id)
        if (previous["version"] if previous else 0) != body.expected_trial_version:
            raise DomainError("REVISION_CONFLICT", "试用版本已更新。", 409)
        if (
            previous
            and previous["data"]["status"] == "running"
            and not live_trial(actor, body.project_id, previous, ptr)
        ):
            raise DomainError("TRIAL_RUNNING", "请先结束正在进行的试用。", 409)
        data = {
            "name": "素材评分 · 限量试用",
            "status": "running",
            "candidate": ref(cand),
            "baseline": ptr["data"]["active"],
            "baseline_pointer": ref(ptr),
            "approval": ref(approval),
            "evaluation": ref(exp),
            "percent": body.percent,
            "stop_condition": body.stop_condition,
            "stop_reason": None,
            "review_decision": None,
            "reviewed_arms": None,
            "assignment_salt": digest([rid, body.request_id, cand["hash"]]),
        }
        pins = [ref(cand), ref(ptr), data["baseline"], ref(approval), ref(exp)]
        if previous:
            return revise(actor, previous, data, "开始新一轮限量评分", pins)
        return managed(actor, "Experiment", body.project_id, data, pins, rid)

    return ok(on_request("score-trial-start", body, actor, apply), request)


def trial_weights(actor, project, item, weights, policy_refs):
    _, trial = trial_head(actor, project)
    if not trial or trial["data"]["status"] != "running":
        return weights, policy_refs, None
    ptr = pointer(actor, project)
    problem = live_trial(actor, project, trial, ptr)
    if problem:
        revise(actor, trial, {"status": "stopped", "stop_reason": problem}, "依据变化，停止试用")
        return weights, policy_refs, None
    data = trial["data"]
    # All versions under the same asset stay together. A standalone imported version falls back
    # to its byte fingerprint, or finally its immutable record ID if there is no measured hash.
    unit = item["data"].get("asset_id") or item["data"].get("content_hash") or item["id"]
    unit_hash = digest([data["assignment_salt"], unit])
    bucket = int(unit_hash[:16], 16) % 100
    candidate = bucket < data["percent"]
    if candidate:
        cand = current(actor, project, data["candidate"], "Policy")
        weights = cand["data"]["typed_config"]["weights"]
        policy_refs = [ref(cand), *policy_refs[1:]]
    assignment = {
        "trial": ref(trial),
        "arm": "candidate" if candidate else "baseline",
        "unit_hash": unit_hash,
        "bucket": bucket,
        "percent": data["percent"],
    }
    return weights, [*policy_refs, ref(trial)], assignment


def trial_counts(db, actor, rid, enrollment):
    sql = """SELECT json_extract(v.data_json,'$.trial_assignment.arm') arm,
                count(DISTINCT json_extract(v.data_json,'$.trial_assignment.unit_hash')) units,
                count(*) scores, count(json_extract(v.data_json,'$.observed_score')) measured,
                count(DISTINCT CASE WHEN json_type(v.data_json,'$.observed_score') IN ('real','integer') THEN
                    json_extract(v.data_json,'$.trial_assignment.unit_hash') END) measured_units
                FROM reference_index i JOIN revisions v ON v.tenant_id=i.tenant_id
                AND v.record_id=i.source_id AND v.version=i.source_version
                WHERE i.tenant_id=? AND i.target_id=? AND i.target_version=? AND v.version=1
                AND i.source_id GLOB 'score_release_observation_*'
                GROUP BY arm"""
    return {row["arm"]: dict(row) for row in db.execute(sql, (actor.tenant_id, rid, enrollment))}


@router.post("/trials/stop")
def stop_trial(body: StopTrial, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        _, trial = trial_head(actor, body.project_id)
        if (
            not trial
            or trial["version"] != body.expected_trial_version
            or trial["data"]["status"] != "running"
        ):
            raise DomainError("REVISION_CONFLICT", "试用已变化，请刷新。", 409)
        with get_store().connection() as db:
            counts = trial_counts(db, actor, trial["id"], trial["version"])
        if body.decision == "recommend_release":
            if live_trial(actor, body.project_id, trial, pointer(actor, body.project_id)):
                raise DomainError("TRIAL_STALE", "试用的批准或回归依据已变化。", 409)
            if not all(
                counts.get(arm, {}).get("measured_units", 0) for arm in ("candidate", "baseline")
            ):
                raise DomainError(
                    "TRIAL_OBSERVATIONS_REQUIRED", "两组均须有实际素材评分，才能建议推广。", 409
                )
        return revise(
            actor,
            trial,
            {
                "status": "stopped",
                "stop_reason": body.reason,
                "review_decision": body.decision,
                "reviewed_arms": counts,
            },
            "保存限量评分观察与人工处置",
        )

    return ok(on_request("score-trial-stop", body, actor, apply), request)


@router.get("/{project_id}/trial")
def read_trial(
    project_id: str,
    request: Request,
    version: int | None = None,
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    with get_store().read_snapshot() as db:
        rid, latest = trial_head(actor, project_id)
        trial = (
            record(actor, rid, "Experiment", version=version, project=project_id)
            if version
            else latest
        )
        arms = {}
        reason = None
        if trial:
            enrollment = (
                trial["version"] if trial["data"]["status"] == "running" else trial["version"] - 1
            )
            arms = trial_counts(db, actor, rid, enrollment)
            if trial == latest:
                reason = live_trial(actor, project_id, trial, pointer(actor, project_id))
    return ok({"trial": trial, "arms": arms, "ineligible_reason": reason}, request)
