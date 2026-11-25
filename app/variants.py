"""Atlas N04: project-scoped, versioned storyboard families with measured duplicate checks."""

from collections import Counter
from base64 import b64decode, b64encode
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import itertools
import random
from statistics import fmean
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt, model_validator

from .api import ok
from .auth import Actor, current_actor, require_approval
from .asset_lifecycle import records as project_records
from . import auto_edits
from .errors import DomainError
from .media import check_revision, probe_media, resolve_media
from .opening_check import check_proposal
from .production import Body, current_dependency_problem, digest, record, ref, save, timestamp
from .selection import ChosenVersion
from .store import get_store
from .variant_fingerprints import DIMENSIONS, difference, fingerprint
from .visual_candidates import inspect

router = APIRouter(prefix="/production/variants", tags=["ai-production"])
FAMILY = "storyboard_variant_family"
ROUTES = {"N04-R1": "分镜组合差异优化", "N04-R2": "分层轮换与配额", "N04-R3": "人工创意改写/补拍"}
Dimension = Literal["picture", "text", "sound", "structure"]
MAX_INTERVALS = 64


class Option(ChosenVersion):
    source_in: float = Field(ge=0, default=0)
    source_out: float | None = Field(gt=0, default=None)

    @model_validator(mode="after")
    def ordered(self):
        if self.source_out is not None and self.source_out <= self.source_in:
            raise ValueError("候选区间终点须大于起点")
        return self


class FamilySpec(Body):
    expected_revision: StrictInt = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=120)
    route: Literal["N04-R1", "N04-R2", "N04-R3"] = "N04-R1"
    count: StrictInt = Field(ge=1, le=8, default=3)
    seed: StrictInt = Field(ge=0, le=2147483647, default=1)
    dimensions: list[Dimension] = Field(
        min_length=1, max_length=4, default_factory=lambda: list(DIMENSIONS)
    )
    locked_slots: list[StrictInt] = Field(default_factory=list, max_length=32)
    pool: list[Option] = Field(default_factory=list, max_length=16)
    manual_shots: list[Option] = Field(default_factory=list, max_length=32)
    manual_reason: str = Field(max_length=500, default="")
    history_days: StrictInt = Field(ge=0, le=365, default=30)
    max_uses: StrictInt | None = Field(ge=1, le=1000, default=None)
    products: list[ChosenVersion] = Field(default_factory=list, max_length=8)
    policy: ChosenVersion | None = None

    @model_validator(mode="after")
    def valid(self):
        for collection in (
            self.dimensions,
            self.locked_slots,
            [(x.id, x.source_in, x.source_out) for x in self.pool],
            [x.id for x in self.products],
        ):
            if len(collection) != len(set(collection)):
                raise ValueError("重复的设置或候选")
        if self.route == "N04-R3":
            if not self.manual_shots or not self.manual_reason.strip():
                raise ValueError("请选择新的镜头顺序并写明创意改动")
        elif self.manual_shots or self.manual_reason:
            raise ValueError("手动镜头需选择人工改写路线")
        return self


class SelectVariant(Body):
    expected_revision: StrictInt = Field(ge=1)
    plan: ChosenVersion
    review_note: str = Field(min_length=1, max_length=800)


def live_pin(actor, project, pin, kind=None):
    item = record(actor, pin["id"], kind or pin["kind"], project=project)
    check_revision(item, pin["version"])
    if problem := current_dependency_problem(item):
        raise DomainError(problem["code"], problem["message"], 409)
    return item


def verify_variant_dependencies(actor, run):
    for pin in run["data"].get("variant_dependency_refs", []):
        live_pin(actor, run["project_id"], pin)


def source_context(actor, run, body):
    project = run["project_id"]
    auto_edits.require_live(actor)
    check_revision(run, body.expected_revision)
    if run["data"]["stage"] not in {"review", "draft", "rendered"}:
        raise DomainError("VARIANT_BASE_NOT_READY", "请先取得可以审阅的基础提案。", 409)
    verify_variant_dependencies(actor, run)
    plan = live_pin(actor, project, run["data"]["plan_ref"], "Plan")
    base = plan["data"]["shots"]
    if not 1 <= len(base) <= 32:
        raise DomainError("VARIANT_SLOT_LIMIT", "单次变体比较支持 1 至 32 个镜头。")
    locked = set(body.locked_slots) | set(plan["data"].get("locked_slots", []))
    if any(i < 0 or i >= len(base) for i in locked):
        raise DomainError("INVALID_LOCKED_SLOT", "锁定的镜头不在基础方案中。")
    records = list(run["data"]["evidence"])
    auto_edits.verify_sources(actor, project, records)
    evidence = {s["asset_id"]: s for s in records}
    extras = [o.id for o in [*body.pool, *body.manual_shots] if o.id not in evidence]
    if len(set(extras) | set(evidence)) > 24:
        raise DomainError("VARIANT_POOL_LIMIT", "请将本次比较的来源缩小至 24 段。")
    if extras:
        records = inspect(actor, project, list(dict.fromkeys(extras)))
        evidence.update({s["asset_id"]: s for s in records})
    for option in [*body.pool, *body.manual_shots]:
        s = evidence[option.id]
        if option.version != s["asset_version"]:
            raise DomainError("REVISION_CONFLICT", "候选素材已变化。", 409)
        if option.source_in >= s["duration"] or (option.source_out or 0) > s["duration"] + 1e-6:
            raise DomainError("SHOT_OUT_OF_RANGE", "候选区间超出当前素材。")
    return project, plan, base, locked, evidence


def base_cut(s):
    return (s["asset_id"], s["source_in"], s["source_out"])


def interval_key(cut):
    return digest([cut[0], float(cut[1]), float(cut[2])])[:24]


def family_state(actor, project, evidence, body, plan, run):
    days = body.history_days
    since = datetime.now(timezone.utc) - timedelta(days=days)
    usage = Counter()
    used_refs = []
    clusters = {}
    refs = {p["id"]: p for p in run["data"].get("variant_dependency_refs", [])}
    pins = [p for p in plan["refs"] if p["kind"] == "Product"]
    pins += [ref(live_pin(actor, project, o.model_dump(), "Product")) for o in body.products]
    products = {
        p["id"]: live_pin(actor, project, p, "Product")
        for p in [*refs.values(), *pins]
        if p.get("kind") == "Product"
    }
    refs.update({k: ref(v) for k, v in products.items()})
    quota = body.max_uses
    if body.policy:
        policy = live_pin(actor, project, body.policy.model_dump(), "Policy")
        d = policy["data"]
        if d.get("release_state", d.get("status")) not in {"approved", "active"}:
            raise DomainError("VARIANT_POLICY_UNAPPROVED", "轮换策略须为已批准或生效版本。", 409)
        config = d.get("typed_config", {})
        ceiling = config.get("max_uses") if isinstance(config, dict) else None
        if type(ceiling) is not int or not 1 <= ceiling <= 1000:
            raise DomainError("VARIANT_POLICY_UNSUPPORTED", "该策略没有受支持的数字复用配额。")
        quota = min(ceiling, quota) if quota else ceiling
        refs[policy["id"]] = ref(policy)
    # Only record actual rendered contributions. Reservations and drafts are not exposure.
    for u in (project_records(actor, project, "Usage") if days else []):
        d = u["data"]
        version = d.get("asset_version_ref")
        version_id = version.get("id") if isinstance(version, dict) else version
        matching = {d.get("asset_id"), version_id} & evidence.keys()
        if (
            matching
            and d.get("status") == "rendered"
            and d.get("artifact_id")
            and timestamp(u["created_at"]) >= since
        ):
            usage.update(matching)
            used_refs.append(ref(u))
    for cluster in project_records(actor, project, "Cluster"):
        d = cluster["data"]
        if d.get("comparison_scope") == "segments" or d.get("status") in {
            "retired",
            "proposed",
            "rejected",
        }:
            continue
        if d.get("cluster_level") not in {
            "exact",
            "near",
            "provenance",
        } or current_dependency_problem(cluster):
            continue
        members = [v.get("id") if isinstance(v, dict) else v for v in d.get("members", [])]
        for mid in members:
            if isinstance(mid, str) and mid in evidence:
                clusters.setdefault(mid, set()).add(cluster["id"])
                refs[cluster["id"]] = ref(cluster)
    return refs, products, quota, usage, used_refs, clusters


def product_coverage(actor, project, products, evidence):
    if not products:
        return {}
    by_version = {}
    for item in project_records(actor, project, "AssetVersion"):
        if item["data"].get("variant") == "original":
            by_version[item["id"]] = item["data"].get("asset_id")
    tags = {}
    for tag in project_records(actor, project, "Tag"):
        d = tag["data"]
        if (
            d.get("entity") != "SKU"
            or d.get("reviewer_lock") is not True
            or not d.get("evidence")
            or current_dependency_problem(tag)
        ):
            continue
        matches = [r for r in tag["refs"] if r["kind"] == "AssetVersion"]
        if not matches:
            continue
        pin = matches[0]
        if pin["id"] not in evidence and by_version.get(pin["id"]) not in evidence:
            continue
        version = live_pin(actor, project, pin)
        if version["data"].get("variant") == "original":
            keys = {version["id"], version["data"].get("asset_id")}
        else:
            keys = {version["id"]}
        if not isinstance(d.get("interval"), list) or len(d["interval"]) != 2:
            continue
        for key in keys:
            tags.setdefault(key, []).append(
                (str(d.get("label") or d.get("SKU")), d["interval"], ref(tag), ref(version))
            )
    return tags


def covered_products(cuts, products, tags):
    """Explicit full-range SKU evidence for all changed material in a product-bound variant."""
    if not products:
        return [], True
    required = {p["data"].get("SKU") for p in products.values()}
    refs, seen = {}, set()
    for asset, a, b in cuts:
        matches = [
            (sku, tag, version)
            for sku, (lo, hi), tag, version in tags.get(asset, [])
            if sku in required and lo <= a + 1e-6 and hi >= b - 1e-6
        ]
        conflicts = any(
            sku not in required and lo < b and hi > a for sku, (lo, hi), _, _ in tags.get(asset, [])
        )
        if not matches or conflicts:
            return [], False
        for sku, tag, version in matches:
            seen.add(sku)
            refs[tag["id"]] = tag
            refs[version["id"]] = version
    return list(refs.values()), required <= seen


def options_for(base, evidence, body, locked):
    choices = []
    for index, shot in enumerate(base):
        orig = base_cut(shot)
        length = orig[2] - orig[1]
        if index in locked:
            choices.append([orig])
            continue
        if body.route == "N04-R3":
            if len(body.manual_shots) != len(base):
                raise DomainError("VARIANT_RHYTHM", "此路线保留基础节奏，请为每一段指定镜头。")
            o = body.manual_shots[index]
            end = o.source_out if o.source_out is not None else round(o.source_in + length, 6)
            if abs(end - o.source_in - length) > 1e-6 or end > evidence[o.id]["duration"] + 1e-6:
                raise DomainError("VARIANT_RHYTHM", "手动选段的可用时长不符合当前节奏。")
            choices.append([(o.id, o.source_in, end)])
            continue
        sources = [(s["asset_id"], s["source_in"], s["source_out"]) for s in base]
        sources += [
            (o.id, o.source_in, o.source_out or evidence[o.id]["duration"]) for o in body.pool
        ]
        choices.append(
            list(
                dict.fromkeys(
                    [orig]
                    + [
                        (sid, start, round(start + length, 6))
                        for sid, start, end in sources
                        if end - start >= length - 1e-6
                    ]
                )
            )
        )
    if body.route == "N04-R3":
        if len(body.manual_shots) != len(base):
            raise DomainError("VARIANT_RHYTHM", "手动改写须明确指定每个镜头。")
        for i in locked:
            o = body.manual_shots[i]
            s = base[i]
            if (
                o.id != s["asset_id"]
                or abs(o.source_in - s["source_in"]) > 1e-6
                or abs(
                    (o.source_out if o.source_out is not None else s["source_out"])
                    - s["source_out"]
                )
                > 1e-6
            ):
                raise DomainError("VARIANT_LOCKED", "手动改写与锁定镜头冲突。", 409)
    return choices


def combinations(choices, seed, usage, route):
    rng = random.Random(seed)
    pools = [list(a) for a in choices]
    for pool in pools:
        rng.shuffle(pool)
        if route == "N04-R2":
            pool.sort(key=lambda c: usage[c[0]])
    # Visit stratified rotations even when the Cartesian product exceeds the search budget.
    rotations = [
        tuple(p[(i + j) % len(p)] for j, p in enumerate(pools)) for i in range(max(map(len, pools)))
    ]
    return itertools.islice(
        dict.fromkeys(itertools.chain(rotations, itertools.islice(itertools.product(*pools), 256))),
        256,
    )


def historical_neighbors(actor, project, run, days):
    """Compare compatible, previously issued Plan families, never infer audience exposure."""
    neighbors = []
    if days == 0:
        return neighbors, 0
    since = datetime.now(timezone.utc) - timedelta(days=days)
    skipped = 0
    for old in project_records(actor, project, "Run", run_type=FAMILY):
        d = old["data"]
        if d.get("run_type") != FAMILY or timestamp(old["created_at"]) < since:
            continue
        for candidate in d["candidates"]:
            fps = []
            for shot in candidate["shots"]:
                key = interval_key(base_cut(shot))
                saved = d["fingerprints"].get(key)
                if not saved or not all("shape" in frame for frame in saved["frames"]):
                    break
                frames = [
                    {**f, "shape": [v - 63 for v in b64decode(f["shape"])]} for f in saved["frames"]
                ]
                fps.append(
                    {
                        "frames": frames,
                        "asset_id": shot["asset_id"],
                        "source_hash": saved["source_hash"],
                        "words": saved["words"],
                        "audio": saved["sound"],
                        "duration": shot["source_out"] - shot["source_in"],
                    }
                )
            if len(fps) != len(candidate["shots"]) or len(neighbors) >= 32:
                skipped += 1
                continue
            neighbors.append((candidate["plan"], fps))
    return neighbors, skipped


def timeline_match(left, right):
    """Map a current slot centre to a historical slot on normalized programme time."""
    total_right = sum(s["duration"] for s in right)
    total_left = sum(x[2] - x[1] for x in left)
    cursor = 0
    j = 0
    right_end = right[0]["duration"]
    result = []
    for _, a, b in left:
        sample = (cursor + (b - a) / 2) / total_left * total_right
        while j < len(right) - 1 and sample >= right_end:
            j += 1
            right_end += right[j]["duration"]
        result.append(right[j])
        cursor += b - a
    return result


def build_family(actor, body, run):
    project, plan, base, locked, evidence = source_context(actor, run, body)
    refs, products, quota, usage, usage_refs, clusters = family_state(
        actor, project, evidence, body, plan, run
    )
    tags = product_coverage(actor, project, products, evidence)
    choices = options_for(base, evidence, body, locked)
    initial = tuple(base_cut(s) for s in base)
    receipts, probes = {}, {}
    rejected = Counter()
    samples = []
    history, skipped_history = historical_neighbors(actor, project, run, body.history_days)

    def measured(cut):
        if cut not in receipts:
            if len(receipts) >= MAX_INTERVALS:
                raise DomainError(
                    "VARIANT_ANALYSIS_LIMIT", "本次超出 64 个不同源区间，请减少候选或拆分比较。"
                )
            aid, start, end = cut
            if aid not in probes:
                item, _, path = resolve_media(get_store(), actor, aid, project_id=project)
                probe = probe_media(path)
                probes[aid] = (
                    path,
                    {**probe, "frame_rate": Fraction(probe.get("fps") or "25")},
                    item["data"].get("content_hash") or aid,
                )
            path, probe, _ = probes[aid]
            receipts[cut] = fingerprint(path, start, end, probe)
        return receipts[cut]

    @lru_cache(maxsize=4096)
    def pair(x, y):
        fp1, fp2 = measured(x), measured(y)
        family = (
            x[0] == y[0]
            or probes[x[0]][2] == probes[y[0]][2]
            or bool(clusters.get(x[0], set()) & clusters.get(y[0], set()))
        )
        return difference(fp1, fp2, family)

    def compare(a, b, historical=False):
        scores = {k: [] for k in DIMENSIONS}
        active = [i for i in range(len(a)) if i not in locked]
        for i in active:
            x, y = a[i], b[i]
            if historical:
                fp = measured(x)
                same = (
                    x[0] == y["asset_id"]
                    or probes[x[0]][2] == y["source_hash"]
                    or bool(clusters.get(x[0], set()) & clusters.get(y["asset_id"], set()))
                )
                distances = difference(fp, y, same)
            else:
                distances = pair(x, y)
            for k, v in distances.items():
                if v is not None:
                    scores[k].append(v)
        return {k: round(fmean(v), 4) if v else None for k, v in scores.items()}

    def sufficiently_different(matrix):
        # Structural or audio changes by themselves cannot promote visually identical retints.
        visual_evidence = (matrix["picture"] or 0) >= 0.085 or (matrix["text"] or 0) >= 0.25
        selected = [matrix[k] for k in body.dimensions if matrix[k] is not None]
        return bool(visual_evidence and selected and max(selected) >= 0.15)

    for c in combinations(choices, body.seed, usage, body.route):
        if c == initial:
            rejected["same_sequence"] += 1
            continue
        counts = Counter(x[0] for i, x in enumerate(c) if i not in locked)
        if quota is not None and any(usage[sid] + n > quota for sid, n in counts.items()):
            rejected["reuse_quota"] += 1
            continue
        if any(c.count(slot) > max(1, initial.count(slot)) for slot in set(c)):
            rejected["internal_repetition"] += 1
            continue
        fact_pins, facts_ok = covered_products(c, products, tags)
        if not facts_ok:
            rejected["product_evidence"] += 1
            continue
        matrix = compare(c, initial)
        if not sufficiently_different(matrix):
            rejected["duplicate"] += 1
            continue
        score = fmean(matrix[k] for k in body.dimensions if matrix[k] is not None)
        samples.append((round(score, 4), c, matrix, fact_pins))
    if body.route == "N04-R2":
        samples.sort(key=lambda x: (sum(usage[aid] for aid, _, _ in x[1]), -x[0]))
    else:
        samples.sort(key=lambda x: -x[0])
    accepted = []
    for score, cuts, matrix, fact_pins in samples:
        other = [compare(cuts, item["cuts"]) for item in accepted]
        if not all(sufficiently_different(m) for m in other):
            rejected["duplicates_sibling"] += 1
            continue
        prior = [
            {"plan": pin, "differences": compare(cuts, timeline_match(cuts, past), historical=True)}
            for pin, past in history
        ]
        if any(not sufficiently_different(x["differences"]) for x in prior):
            rejected["duplicates_history"] += 1
            continue
        sources = [evidence[aid] for aid in dict.fromkeys(c[0] for c in cuts)]
        raw = [
            {
                "asset_id": s,
                "source_in": start,
                "source_out": end,
                "reason": (
                    base[i]["reason"] if i in locked else body.manual_reason or ROUTES[body.route]
                ),
            }
            for i, (s, start, end) in enumerate(cuts)
        ]
        pd = auto_edits.validate_plan(
            {
                "title": plan["data"]["title"],
                "approach": ROUTES[body.route],
                "shots": raw,
                "missing": [
                    m
                    for m in plan["data"].get("missing", [])
                    if not m.startswith("当前可用编排比目标短 ")
                ],
            },
            sources,
            run["data"]["target_seconds"],
        )
        opening = check_proposal(
            actor, project, run["data"].get("opening_text"), sources, pd["shots"]
        )
        if opening.get("review_blocker"):
            rejected["opening_requirement"] += 1
            continue
        accepted.append(
            {
                "cuts": cuts,
                "data": {**pd, **opening},
                "score": score,
                "matrix": matrix,
                "previous": other,
                "history": prior,
                "facts": fact_pins,
            }
        )
        if len(accepted) == body.count:
            break
    for cut in initial:
        measured(cut)
    fingerprint_index = {
        interval_key(c): {
            "asset_id": c[0],
            "range": list(c[1:]),
            "frames": [
                {
                    "at": f["at"],
                    "sha256": f["sha256"],
                    "shape": b64encode(bytes(v + 63 for v in f["shape"])).decode("ascii"),
                }
                for f in fp["frames"]
            ],
            "source_hash": probes[c[0]][2],
            "ocr_at": fp["ocr_at"],
            "ocr_status": fp["ocr_status"],
            "words": fp["words"],
            "sound": fp["audio"],
        }
        for c, fp in receipts.items()
    }
    return (
        project,
        plan,
        evidence,
        refs,
        locked,
        accepted,
        {
            "route": body.route,
            "requested": body.count,
            "actual": len(accepted),
            "seed": body.seed,
            "dimensions": body.dimensions,
            "locked_slots": sorted(locked),
            "history_days": body.history_days,
            "history_usage": dict(usage),
            "usage_refs": usage_refs,
            "max_uses": quota,
            "audience_overlap": "unknown",
            "history_source": "rendered_use_only",
            "historical_plans_compared": len(history),
            "historical_plans_outside_comparison": skipped_history,
            "rejected": dict(rejected),
            "fingerprints": fingerprint_index,
            "comparison_scope": "local_sampled_source_intervals",
            "platform_duplicate_verdict": None,
        },
    )


def public_family(family):
    d = family["data"]
    return {
        "id": family["id"],
        "version": family["version"],
        **{
            k: v
            for k, v in d.items()
            if k not in {"fingerprints", "evidence", "intent", "usage_refs", "dependency_refs"}
        },
    }


def load_family(actor, run, fid):
    item = record(actor, fid, "Run", project=run["project_id"])
    if (
        item["data"].get("run_type") != FAMILY
        or item["data"].get("base_session_ref", {}).get("id") != run["id"]
    ):
        raise DomainError("VARIANT_NOT_FOUND", "当前制作任务中没有这组变体。", 404)
    return item


@router.get("/{run_id}")
def list_families(run_id: str, request: Request, actor: Actor = Depends(current_actor)):
    base = auto_edits.load_session(actor, run_id)
    items = project_records(actor, base["project_id"], "Run", run_type=FAMILY, base_session=run_id)
    return ok(
        {
            "families": [
                public_family(f)
                for f in items
                if f["data"].get("run_type") == FAMILY
                and f["data"].get("base_session_ref", {}).get("id") == run_id
            ]
        },
        request,
    )


@router.post("/{run_id}")
def generate(
    run_id: str, body: FamilySpec, request: Request, actor: Actor = Depends(current_actor)
):
    auto_edits.require_live(actor)
    base = auto_edits.load_session(actor, run_id)
    fid = "family_" + digest([actor.tenant_id, actor.actor_id, run_id, body.request_id])[:36]
    intent = digest(body.model_dump(mode="json"))

    def recovered():
        try:
            fam = load_family(actor, base, fid)
        except DomainError as e:
            if e.code == "NOT_FOUND":
                return None
            raise
        if fam["data"]["intent"] != intent:
            raise DomainError("IDEMPOTENCY_CONFLICT", "该请求编号已经用于其他变体设置。", 409)
        return ok(public_family(fam), request)

    if replay := recovered():
        return replay
    project, plan, evidence, refs, locked, accepted, report = build_family(actor, body, base)
    store = get_store()
    with store.transaction():
        if replay := recovered():
            return replay
        auto_edits.require_live(actor)
        check_revision(auto_edits.load_session(actor, run_id), body.expected_revision)
        live_pin(actor, project, ref(plan))
        auto_edits.verify_sources(actor, project, list(evidence.values()))
        for p in refs.values():
            live_pin(actor, project, p)
        outcomes = []
        for i, result in enumerate(accepted):
            pins = list({p["id"]: p for p in [*refs.values(), *result["facts"]]}.values())
            for p in result["facts"]:
                live_pin(actor, project, p)
            pd = result["data"]
            sources = [
                evidence[sid]["record_ref"] for sid in dict.fromkeys(c[0] for c in result["cuts"])
            ]
            variant = save(
                actor,
                "Plan",
                project,
                {
                    **pd,
                    "name": f'{pd["title"]} · 变体 {i+1}',
                    "status": "proposed",
                    "planning_mode": "storyboard_variant",
                    "base_plan_ref": ref(plan),
                    "locked_slots": sorted(locked),
                    "variant_family_id": fid,
                    "variant_dependency_refs": pins,
                    "format": base["data"]["format"],
                },
                [ref(plan), *pins, *sources],
                reason="按多维差异和当前用途保存同父变体",
            )
            outcomes.append(
                {
                    "plan": ref(variant),
                    "name": variant["data"]["name"],
                    "score": result["score"],
                    "vs_base": result["matrix"],
                    "vs_siblings": result["previous"],
                    "vs_history": result["history"],
                    "shots": [
                        {k: s[k] for k in ("asset_id", "name", "source_in", "source_out")}
                        for s in pd["shots"]
                    ],
                }
            )
        family = store.create(
            "Run",
            {
                **report,
                "name": "镜头变体",
                "run_type": FAMILY,
                "intent": intent,
                "status": "review" if outcomes else "insufficient",
                "candidates": outcomes,
                "base_session_ref": ref(base),
                "base_plan_ref": ref(plan),
                "dependency_refs": list(refs.values()),
                "evidence": list(evidence.values()),
            },
            tenant_id=actor.tenant_id,
            project_id=project,
            actor_id=actor.actor_id,
            record_id=fid,
            refs=[ref(base), ref(plan), *[o["plan"] for o in outcomes]],
        )
    return ok(public_family(family), request)


@router.post("/{run_id}/{family_id}/select")
def choose(
    run_id: str,
    family_id: str,
    body: SelectVariant,
    request: Request,
    actor: Actor = Depends(current_actor),
):
    require_approval(actor)
    store = get_store()
    with store.transaction():
        auto_edits.require_live(actor)
        parent = auto_edits.load_session(actor, run_id)
        project = parent["project_id"]
        family = load_family(actor, parent, family_id)
        fd = family["data"]
        candidate = next((c for c in fd["candidates"] if c["plan"]["id"] == body.plan.id), None)
        if not candidate or not body.review_note.strip():
            raise DomainError(
                "VARIANT_SELECTION_INVALID", "请选择本组实际生成的版本并填写复核意见。"
            )
        child_id = "edit_" + digest([family_id, body.plan.id])[:36]
        check_revision(family, body.expected_revision)
        pd_record = live_pin(actor, project, candidate["plan"], "Plan")
        check_revision(pd_record, body.plan.version)
        pins = pd_record["data"]["variant_dependency_refs"]
        for p in pins:
            live_pin(actor, project, p)
        chosen_ids = {s["asset_id"] for s in pd_record["data"]["shots"]}
        sources = [s for s in fd["evidence"] if s["asset_id"] in chosen_ids]
        auto_edits.verify_sources(actor, project, sources)
        try:
            child = auto_edits.load_session(actor, child_id)
        except DomainError as e:
            if e.code != "NOT_FOUND":
                raise
        else:
            return ok(auto_edits.detail(actor, child), request)
        check_revision(parent, fd["base_session_ref"]["version"])
        live_pin(actor, project, fd["base_plan_ref"])
        child = store.create(
            "Run",
            {
                **{k: parent["data"][k] for k in ("mode", "goal", "target_seconds", "format")},
                **(
                    {"opening_text": parent["data"]["opening_text"]}
                    if parent["data"].get("opening_text")
                    else {}
                ),
                "run_type": auto_edits.KIND,
                "status": "review",
                "stage": "review",
                "name": candidate["name"],
                "base_session_ref": ref(parent),
                "plan_ref": ref(pd_record),
                "evidence": sources,
                "variant_dependency_refs": pins,
                "project_preferences": parent["data"].get("project_preferences", []),
                "feedback_evidence": parent["data"].get("feedback_evidence", []),
                "variant_family_ref": ref(family),
                "review_note": body.review_note.strip(),
                "reviewer": actor.actor_id,
                "revision_kind": "storyboard_variant",
            },
            tenant_id=actor.tenant_id,
            project_id=project,
            actor_id=actor.actor_id,
            record_id=child_id,
            refs=[ref(family), ref(parent), ref(pd_record), *pins],
        )
    return ok(auto_edits.detail(actor, child), request)
