"""C08/P04/N05. Frozen requirements, evidence checks on actual batch outputs."""

from fractions import Fraction
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictBool, StrictInt, model_validator

from . import batch_observability
from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .media import check_revision
from .production import Body, record, ref, revise, save
from .selection import ChosenVersion, Label
from .store import digest, get_store

router = APIRouter(prefix="/contracts", tags=["batch output contracts"])
KIND = "batch_output_v1"


class Requirements(Body):
    width: StrictInt | None = Field(default=None, ge=64, le=4096, multiple_of=2)
    height: StrictInt | None = Field(default=None, ge=64, le=4096, multiple_of=2)
    fps: Literal[24, 25, 30, 50, 60] | None = None
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    duration_tolerance_seconds: float | None = Field(default=None, ge=0, le=3600)
    min_distinct_sources: StrictInt | None = Field(default=None, ge=1, le=64)
    min_editorial_roles: StrictInt | None = Field(default=None, ge=1, le=64)
    min_clip_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_clip_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_generation_calls: StrictInt | None = Field(default=None, ge=0, le=6400000)
    subtitle_language: str | None = Field(
        default=None, min_length=2, max_length=40, pattern=r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$"
    )
    require_full_decode: StrictBool | None = None

    @model_validator(mode="after")
    def valid(self):
        if not self.model_dump(exclude_none=True) or self.require_full_decode is False:
            raise ValueError("请声明至少一项硬门槛；不要求全片解码时省略该字段。")
        if self.duration_tolerance_seconds is not None and self.duration_seconds is None:
            raise ValueError("时长容差须与目标时长一同填写。")
        if (
            self.max_clip_seconds is not None
            and self.min_clip_seconds is not None
            and self.min_clip_seconds > self.max_clip_seconds
        ):
            raise ValueError("最短镜头不能长于最长镜头。")
        return self


class ContractInput(Body):
    project_id: str
    name: Label
    request_id: Label
    requirements: Requirements
    previous: ChosenVersion | None = None


def load(actor, project, pin):
    rec = record(actor, pin.id, "TaskContract", version=pin.version, project=project)
    if rec["data"].get("contract_type") != KIND:
        raise DomainError("BATCH_CONTRACT_KIND", "该任务契约没有批量输出验收规则。")
    return rec, Requirements.model_validate(rec["data"]["output_requirements"])


@router.post("")
def create_contract(body: ContractInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        data = {
            "name": body.name,
            "goal": body.name,
            "contract_type": KIND,
            "output_requirements": body.requirements.model_dump(exclude_none=True),
            "scope": {"project_id": body.project_id, "subject": "batch_primary_output"},
        }
        if body.previous:
            old, _ = load(actor, body.project_id, body.previous)
            check_revision(
                record(actor, old["id"], "TaskContract", project=body.project_id),
                body.previous.version,
            )
            out = revise(actor, old, data, "修订批量输出验收契约")
        else:
            out = save(actor, "TaskContract", body.project_id, data, reason="保存批量输出验收契约")
        return {"contract": out}

    key = "batch-contract:" + digest([body.project_id, body.request_id])
    return ok(
        get_store().idempotent(
            actor.tenant_id, key, key, {"actor": actor.actor_id, **body.model_dump()}, apply
        ),
        request,
    )


@router.get("")
def list_contracts(
    project_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    with get_store().read_snapshot() as db:
        rows = db.execute(
            """SELECT r.id FROM records r JOIN revisions v ON r.tenant_id=v.tenant_id AND r.id=v.record_id
            AND r.latest_version=v.version WHERE r.tenant_id=? AND r.project_id=? AND r.kind='TaskContract'
            AND json_extract(v.data_json,'$.contract_type')=? ORDER BY r.created_at DESC LIMIT ?""",
            (actor.tenant_id, project_id, KIND, limit),
        )
        return ok(
            {
                "contracts": [
                    record(actor, r["id"], "TaskContract", project=project_id) for r in rows
                ]
            },
            request,
        )


def preflight(actor, recipe, generation_limit):
    contract, rules = load(actor, recipe.project_id, recipe.acceptance_contract)
    issues = []
    for k in ("width", "height", "fps"):
        if getattr(rules, k) is not None and getattr(rules, k) != getattr(recipe, k):
            issues.append(k)
    if rules.max_generation_calls is not None and generation_limit > rules.max_generation_calls:
        issues.append("max_generation_calls")
    if rules.require_full_decode and recipe.output != "render":
        issues.append("require_full_decode")
    if rules.min_distinct_sources and rules.min_distinct_sources > len(recipe.slots):
        issues.append("min_distinct_sources")
    if rules.duration_seconds:
        bounds = batch_observability.duration_bounds(actor, recipe.model_dump())
        low, high = bounds["min_seconds"], bounds["max_seconds"]
        tol = rules.duration_tolerance_seconds or 0
        if low is not None and (
            low > rules.duration_seconds + tol + 1e-8 or high < rules.duration_seconds - tol - 1e-8
        ):
            issues.append("duration_seconds")
    if rules.min_clip_seconds or rules.max_clip_seconds:
        reference_clips = (
            record(
                actor,
                recipe.template.id,
                "Timeline",
                version=recipe.template.version,
                project=recipe.project_id,
            )["data"]["clips"]
            if recipe.template
            else []
        )
        for index, slot in enumerate(recipe.slots):
            speed = reference_clips[index].get("speed", 1) if reference_clips else 1
            if not isinstance(speed, (int, float)) or not 0 < speed:
                raise DomainError("BATCH_TEMPLATE_SPEED", "模板镜头速度无法用于节奏验收。")
            ranges = [(c.source_out - c.source_in) / speed for c in slot.choices] or [
                slot.generate.duration / speed
            ]
            if not any(
                (rules.min_clip_seconds is None or length + 1e-8 >= rules.min_clip_seconds)
                and (rules.max_clip_seconds is None or length <= rules.max_clip_seconds + 1e-8)
                for length in ranges
            ):
                issues.append("clip_duration_range")
                break
    if rules.subtitle_language and not recipe.template:
        issues.append("subtitle_language")
    if issues:
        raise DomainError(
            "BATCH_ACCEPTANCE_IMPOSSIBLE",
            "配方在入队前已经违反固定验收条件。",
            422,
            {"contract": ref(contract), "failed": issues},
        )
    return contract


def role_tags(store, actor, project, asset, start, end):
    project_args = (actor.tenant_id, project, asset["id"])
    # Tags are attached to an AssetVersion; an Asset can use the matching original bytes only.
    version_ids = [asset["id"]] if asset["kind"] == "AssetVersion" else []
    with store.connection() as db:
        if asset["kind"] == "Asset":
            version_ids.extend(
                r[0]
                for r in db.execute(
                    """SELECT DISTINCT v.record_id FROM reference_index x JOIN records r
                      ON r.tenant_id=x.tenant_id AND r.id=x.source_id AND r.latest_version=x.source_version
                      JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
                      WHERE x.tenant_id=? AND r.project_id=? AND x.target_id=? AND r.kind='AssetVersion'
                      AND json_extract(v.data_json,'$.variant')='original' AND json_extract(v.data_json,'$.content_hash')=?""",
                    (*project_args, asset["data"].get("content_hash")),
                )
            )
        tags = {}
        for vid in version_ids:
            version = store.get(vid, actor.tenant_id)
            rows = db.execute(
                """SELECT r.id FROM reference_index x JOIN records r ON r.tenant_id=x.tenant_id AND r.id=x.source_id
               AND r.latest_version=x.source_version JOIN revisions v ON r.tenant_id=v.tenant_id AND r.id=v.record_id AND r.latest_version=v.version
               WHERE x.tenant_id=? AND r.project_id=? AND x.target_id=? AND x.target_version=? AND r.kind='Tag'
               AND json_extract(v.data_json,'$.reviewer_lock')=1 AND json_extract(v.data_json,'$.entity')='editorial_role'
               AND json_extract(v.data_json,'$.status')='human_assertion' AND length(trim(json_extract(v.data_json,'$.evidence')))>0
               AND json_extract(v.data_json,'$.interval[0]')<=? AND json_extract(v.data_json,'$.interval[1]')>=?""",
                (actor.tenant_id, project, vid, version["version"], start, end),
            )
            for row in rows:
                tag = store.get(row["id"], actor.tenant_id)
                tags[tag["id"]] = (tag["data"]["label"], ref(tag))
    return tags


def actual_fps(probe):
    try:
        return float(Fraction(str(probe["fps"])))
    except (KeyError, ZeroDivisionError, ValueError):
        return None


def evaluate(store, actor, recipe, item, timeline, artifact, contract, rules):
    checks = []
    evidence_refs = {}

    def check(name, expected, observed, predicate, *, deferred=False):
        status = (
            "deferred"
            if deferred
            else "unknown" if observed is None else "passed" if predicate(observed) else "failed"
        )
        checks.append({"name": name, "status": status, "expected": expected, "observed": observed})

    data = timeline["data"]
    probe = artifact["data"].get("probe", {}) if artifact else {}
    fmt = data.get("format", {})
    for name in ("width", "height", "fps"):
        expected = getattr(rules, name)
        if expected is not None:
            value = (
                (actual_fps(probe) if name == "fps" else probe.get(name))
                if artifact
                else fmt.get(name)
            )
            check(name, expected, value, lambda n: abs(n - expected) < 1e-6)
    if rules.duration_seconds is not None:
        tol = rules.duration_tolerance_seconds or 0
        value = (
            probe.get("duration") if artifact else batch_observability.duration_of_timeline(data)
        )
        check(
            "duration_seconds",
            {"target": rules.duration_seconds, "tolerance": tol},
            value,
            lambda n: abs(n - rules.duration_seconds) <= tol + 1e-8,
        )
    if rules.max_generation_calls is not None:
        with store.connection() as db:
            limit = db.execute(
                "SELECT generation_limit FROM production_batches WHERE id=?", (item["batch"],)
            ).fetchone()[0]
        check(
            "max_generation_calls",
            rules.max_generation_calls,
            limit,
            lambda n: n <= rules.max_generation_calls,
        )
    clips = data["clips"]
    lengths = [(c["source_out"] - c["source_in"]) / c.get("speed", 1) for c in clips]
    if rules.min_clip_seconds:
        check(
            "min_clip_seconds",
            rules.min_clip_seconds,
            min(lengths),
            lambda n: n + 1e-8 >= rules.min_clip_seconds,
        )
    if rules.max_clip_seconds:
        check(
            "max_clip_seconds",
            rules.max_clip_seconds,
            max(lengths),
            lambda n: n <= rules.max_clip_seconds + 1e-8,
        )
    if rules.min_distinct_sources or rules.min_editorial_roles:
        hashes = set()
        roles = set()
        missing_hash = missing_tag = False
        for clip in clips:
            asset = record(actor, clip["asset_id"], project=recipe.project_id)
            sha = asset["data"].get("content_hash")
            if sha:
                hashes.add(sha)
            else:
                missing_hash = True
            if rules.min_editorial_roles:
                tags = role_tags(
                    store, actor, recipe.project_id, asset, clip["source_in"], clip["source_out"]
                )
                missing_tag |= not tags
                for key, (label, pin) in tags.items():
                    roles.add(label)
                    evidence_refs[key] = pin
        if rules.min_distinct_sources:
            n = len(hashes)
            check(
                "min_distinct_sources",
                rules.min_distinct_sources,
                None if missing_hash and n < rules.min_distinct_sources else n,
                lambda v: v >= rules.min_distinct_sources,
            )
        if rules.min_editorial_roles:
            value = (
                None if missing_tag and len(roles) < rules.min_editorial_roles else sorted(roles)
            )
            check(
                "min_editorial_roles",
                rules.min_editorial_roles,
                value,
                lambda v: len(v) >= rules.min_editorial_roles,
            )
    if rules.subtitle_language:
        cues = [c for c in data.get("captions", []) if str(c.get("text", "")).strip()]
        locales = {
            c["language"].lower()
            for c in cues
            if isinstance(c.get("language"), str) and c["language"].strip()
        }
        expected = rules.subtitle_language.lower()
        missing = any(not c.get("language") for c in cues)
        check(
            "subtitle_language",
            expected,
            None if missing and expected not in locales else sorted(locales),
            lambda tags: expected in tags,
        )
    if rules.require_full_decode:
        decoded = (
            artifact["data"].get("qc", {}).get("checks", {}).get("full_decode")
            if artifact
            else None
        )
        check(
            "require_full_decode",
            True,
            decoded,
            lambda v: v is True,
            deferred=artifact is None and recipe.output == "render",
        )
    return checks, list(evidence_refs.values())


def validate_output(store, actor, recipe, item, timeline, artifact=None):
    if not recipe.acceptance_contract:
        return
    stage = "encoded" if artifact else "timeline"
    with store.connection() as db:
        saved = db.execute(
            "SELECT * FROM production_acceptance_results WHERE batch=? AND ordinal=? AND stage=?",
            (item["batch"], item["ordinal"], stage),
        ).fetchone()
    if saved and saved["status"] != "blocked":
        return
    contract, rules = load(actor, recipe.project_id, recipe.acceptance_contract)
    checks, pins = evaluate(store, actor, recipe, item, timeline, artifact, contract, rules)
    failing = any(c["status"] in {"failed", "unknown"} for c in checks)
    status = "blocked" if failing else "passed"
    if not failing and any(c["status"] == "deferred" for c in checks):
        status = "pending_encode"
    if saved:
        previous = record(actor, saved["audit_id"], "Audit", project=recipe.project_id)
        if previous["data"]["checks"] == checks:
            if failing:
                raise DomainError(
                    "BATCH_ACCEPTANCE_FAILED", "该项未满足输出契约，请核对验收记录。", 409
                )
            return
    with store.transaction(), store.connection() as db:
        evidence = [ref(contract), ref(timeline), *pins]
        if artifact:
            evidence.append(ref(artifact))
        result = save(
            actor,
            "Audit",
            recipe.project_id,
            {
                "name": "批次输出验收",
                "status": status,
                "stage": stage,
                "contract": ref(contract),
                "batch_id": item["batch"],
                "batch_ordinal": item["ordinal"],
                "checks": checks,
                "basis": {
                    "min_editorial_roles": "human-reviewed tags covering each source range",
                    "subtitle_language": "declared subtitle cue locale; semantic translation quality unmeasured",
                    "max_generation_calls": "frozen worst-case new inference requests for this batch",
                },
            },
            evidence,
            "按冻结版本核对实际时间线或成片",
        )
        db.execute(
            "INSERT INTO production_acceptance_results VALUES(?,?,?,?,?) ON CONFLICT(batch,ordinal,stage) DO UPDATE SET audit_id=excluded.audit_id,status=excluded.status",
            (item["batch"], item["ordinal"], stage, result["id"], status),
        )
    if failing:
        raise DomainError("BATCH_ACCEPTANCE_FAILED", "该项的硬门槛失败或缺少可核验数据。", 409)
