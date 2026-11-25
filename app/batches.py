"""Atlas N04/E01/Y02/Y05/G03. Durable project-scoped bulk edit and render recipes."""

import logging
import math
import os
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictInt, model_validator

from . import batch_queue as queue
from . import batch_acceptance, batch_market, batch_observability, batch_workflows, generation
from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_approval, require_write
from .errors import DomainError
from .media import MAX_DURATION, MAX_PIXELS, check_revision, render_timeline, resolve_media
from .production import Body, record, ref
from .selection import ChosenVersion
from .store import digest, get_store, now

router = APIRouter(prefix="/production/batches", tags=["bulk production"])
router.include_router(batch_observability.router)
router.include_router(batch_acceptance.router)
router.include_router(batch_market.router)
logger = logging.getLogger("ai_ship.batch")
signal = threading.Event()


class Cut(Body):
    asset_id: str = Field(min_length=1, max_length=180)
    version: StrictInt = Field(ge=1)
    source_in: float = Field(default=0, ge=0)
    source_out: float = Field(gt=0, le=MAX_DURATION)


class GeneratedSlot(Body):
    profile_id: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1, max_length=6000)
    duration: float = Field(ge=0.04, le=MAX_DURATION)
    reference_asset_id: str | None = None
    reference_asset_version: StrictInt | None = Field(default=None, ge=1)


class Slot(Body):
    choices: list[Cut] = Field(default_factory=list, max_length=64)
    generate: GeneratedSlot | None = None

    @model_validator(mode="after")
    def source(self):
        if bool(self.choices) == bool(self.generate):
            raise ValueError("每个镜头须选择一个素材池或一个生成任务。")
        if len({(c.asset_id, c.source_in, c.source_out) for c in self.choices}) != len(
            self.choices
        ):
            raise ValueError("同一镜头候选区间重复。")
        return self


class WorkflowTail(ChosenVersion):
    node_id: str = Field(min_length=1, max_length=180)
    input_path: str = Field(min_length=1, max_length=180)


class Recipe(Body):
    market_brief: ChosenVersion | None = None
    acceptance_contract: ChosenVersion | None = None
    workflow_tail: WorkflowTail | None = None
    project_id: str
    name: str = Field(min_length=1, max_length=150)
    idempotency_key: str = Field(min_length=8, max_length=150)
    count: StrictInt = Field(ge=1, le=100000)
    slots: list[Slot] = Field(min_length=1, max_length=64)
    template: ChosenVersion | None = None
    output: Literal["timeline", "render"] = "timeline"
    width: StrictInt = Field(ge=64, le=4096, multiple_of=2, default=1280)
    height: StrictInt = Field(ge=64, le=4096, multiple_of=2, default=720)
    fps: Literal[24, 25, 30, 50, 60] = 30
    seed: StrictInt = Field(ge=0, le=2147483647, default=0)

    max_generation_calls: StrictInt | None = Field(default=None, ge=0, le=6400000)
    generation_window: StrictInt = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def bounds(self):
        gen_count = sum(s.generate is not None for s in self.slots)
        if self.width * self.height > MAX_PIXELS:
            raise ValueError("画面像素超过编码器限制。")
        if (
            self.max_generation_calls is not None
            and gen_count * self.count > self.max_generation_calls
        ):
            raise ValueError("所有生成镜头的推理次数超过本批次允许的调用数。")
        if not gen_count and self.count > capacity(self.slots):
            raise ValueError("批次数量超出来源区间的不重复组合数。")
        return self


def capacity(slots):
    return math.prod(len(s.choices) or 1 for s in slots)


def choices_for(recipe, ordinal):
    # A coprime affine permutation of mixed-radix indices avoids materializing products.
    # Every combination is visited exactly once per cycle, with reproducible seeded ordering.
    size = capacity(recipe.slots)
    step = digest([recipe.seed, "stride"])
    stride = int(step[:16], 16) % size or 1
    while math.gcd(stride, size) != 1:
        stride += 1
    rank = (recipe.seed + (ordinal - 1) * stride) % size
    result = []
    for slot in recipe.slots:
        rank, pos = divmod(rank, len(slot.choices) or 1)
        result.append(slot.choices[pos] if slot.choices else None)
    return result


def source(actor, project, cut, cache):
    if cut.asset_id not in cache:
        item, _, _ = resolve_media(get_store(), actor, cut.asset_id, project_id=project)
        cache[cut.asset_id] = item
    item = cache[cut.asset_id]
    check_revision(item, cut.version)
    probe = item["data"].get("probe", {}) or item["data"].get("qc", {}).get("probe", {})
    if not probe.get("has_video") or not (
        0 <= cut.source_in < cut.source_out <= (probe.get("duration") or 0) + 1e-8
    ):
        raise DomainError("BATCH_SOURCE_RANGE", "来源必须有可验证的视频轨，且区间在原片时长内。")
    return item


def template_for(actor, recipe):
    if not recipe.template:
        return None
    item = record(actor, recipe.template.id, "Timeline", project=recipe.project_id)
    check_revision(item, recipe.template.version)
    data = item["data"]
    first = record(actor, item["id"], "Timeline", version=1)
    if data.get("locks") or any(x.get("voice_alignment_plan") for x in [data, first["data"]]):
        raise DomainError(
            "BATCH_TEMPLATE_LOCK", "本模板含人工锁或逐帧配音绑定，需另存可换镜头的模板。", 409
        )
    if first["data"].get("opening_text") != data.get("opening_text"):
        raise DomainError("BATCH_TEMPLATE_CONSTRAINT", "首镜校验条件在模板历史中不一致。", 409)
    clips = data.get("clips", [])
    if len(clips) != len(recipe.slots):
        raise DomainError("BATCH_TEMPLATE_SLOTS", "模板画面片段数与配方镜头数不一致。")
    for clip, slot in zip(clips, recipe.slots):
        if not all(isinstance(clip.get(k), (float, int)) for k in ("source_in", "source_out")):
            raise DomainError("BATCH_TEMPLATE_RANGE", "请先在模板中固定每个源片段的入点与出点。")
        original = clip["source_out"] - clip["source_in"]
        durations = [k.source_out - k.source_in for k in slot.choices] or [slot.generate.duration]
        if any(abs(length - original) > 1e-6 for length in durations):
            raise DomainError("BATCH_TEMPLATE_TIMING", "替代源区间须保持人工样片中的镜头时长。")
    return item


def freeze(actor, recipe):
    refs, assets = {}, {}
    template = template_for(actor, recipe)
    if template:
        refs[template["id"]] = ref(template)
    cfg_hash = {}
    ceiling = 0
    for slot in recipe.slots:
        if slot.generate:
            gen = slot.generate
            cfg = generation.profile(gen.profile_id)
            if cfg.get("output_kind", "video") != "video":
                raise DomainError("BATCH_VIDEO_PROFILE", "生成镜头需要视频输出配置。")
            if ("image_input" in cfg) != bool(gen.reference_asset_id) or bool(
                gen.reference_asset_id
            ) != bool(gen.reference_asset_version):
                raise DomainError(
                    "BATCH_REFERENCE_REQUIRED", "参考图及其版本须与生成配置要求一致。"
                )
            if gen.reference_asset_id:
                item, _, _ = generation.pinned_reference(
                    actor,
                    recipe.project_id,
                    {"id": gen.reference_asset_id, "version": gen.reference_asset_version},
                )
                if not item["data"].get("probe", {}).get("is_still"):
                    raise DomainError("REFERENCE_IMAGE_REQUIRED", "请选择单帧参考图。")
                refs[item["id"]] = ref(item)
            cfg_hash[gen.profile_id] = digest(cfg)
            ceiling += gen.duration
        else:
            for cut in slot.choices:
                item = source(actor, recipe.project_id, cut, assets)
                refs[item["id"]] = ref(item)
            ceiling += max(k.source_out - k.source_in for k in slot.choices)
    if not template and ceiling > MAX_DURATION:
        raise DomainError("MEDIA_RESOURCE_LIMIT", "批次中的最长时间线超过本地编码上限。")
    dag_generations = 0
    if recipe.workflow_tail:
        workflow, graph, _, _ = batch_workflows.blueprint(actor, recipe)
        refs[workflow["id"]] = ref(workflow)
        dag_generations = sum(n["route_id"] in {"G02-R2", "G03-R2"} for n in graph["nodes"])
    limit = recipe.count * (
        sum(slot.generate is not None for slot in recipe.slots) + dag_generations
    )
    if recipe.max_generation_calls is not None and limit > recipe.max_generation_calls:
        raise DomainError("BATCH_GENERATION_BUDGET", "工作流和生成镜头所需的调用次数超过提交预算。")
    if recipe.market_brief:
        brief, _ = batch_market.check(actor, recipe)
        refs[brief["id"]] = ref(brief)
    if recipe.acceptance_contract:
        requirement = batch_acceptance.preflight(actor, recipe, limit)
        refs[requirement["id"]] = ref(requirement)
    return list(refs.values()), cfg_hash, limit


def authorize(actor, output):
    require_write(actor)
    if output == "render":
        require_approval(actor)
    if not actor_still_authorized(actor):
        raise DomainError("IDENTITY_REVOKED", "提交本次批量工作的身份授权已失效。", 403)


@router.post("")
def enqueue(body: Recipe, request: Request, actor: Actor = Depends(current_actor)):
    authorize(actor, body.output)
    project = record(actor, body.project_id, "Project")
    store = get_store()
    queue.initialize(store)
    spec = body.model_dump()
    bid = "batch_" + digest([actor.tenant_id, body.project_id, body.idempotency_key])[:40]
    intent = digest(spec)

    def existing(db):
        row = db.execute(
            "SELECT * FROM production_batches WHERE id=? AND tenant=?", (bid, actor.tenant_id)
        ).fetchone()
        if row and row["intent"] != intent:
            raise DomainError("IDEMPOTENCY_CONFLICT", "相同批次请求键用于不同的配方。", 409)
        return queue.page(db, row) if row else None

    with store.connection() as db:
        old = existing(db)
    if old:
        return ok(old, request)
    pins, cfg_hash, generation_limit = freeze(actor, body)
    with store.transaction(), store.connection() as db:
        old = existing(db)
        if old:
            return ok(old, request)
        run = store.create(
            "Run",
            {
                "name": body.name,
                "run_type": "production_batch",
                "recipe": spec,
                "generator_hashes": cfg_hash,
                "output_scope": body.output,
            },
            project_id=body.project_id,
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            record_id=bid,
            refs=[
                ref(project),
                *(
                    [
                        {
                            "id": body.acceptance_contract.id,
                            "kind": "TaskContract",
                            "version": body.acceptance_contract.version,
                        }
                    ]
                    if body.acceptance_contract
                    else []
                ),
            ],
            reason="冻结批次全部镜头区间、组合序号与输出范围",
        )
        store.create(
            "Plan",
            {"name": body.name, "plan_type": "batch_input_scope", "recipe_hash": intent},
            project_id=body.project_id,
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            refs=[ref(run), *pins],
            reason="记录批次候选输入范围，产物仅依赖实际选用的镜头",
        )
        db.execute(
            "INSERT INTO production_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
            (
                bid,
                actor.tenant_id,
                body.project_id,
                actor.actor_id,
                actor.role,
                actor.credential_digest,
                os.getenv("AI_SHIP_MODE", "local"),
                now(),
                body.name,
                body.count,
                body.output,
                "active",
                bool(generation_limit),
                intent,
                generation_limit,
                body.generation_window,
            ),
        )
        db.executemany(
            "INSERT INTO production_items(batch,ordinal) VALUES(?,?)",
            ((bid, i) for i in range(1, body.count + 1)),
        )
        result = queue.page(db, queue.get_batch(db, actor.tenant_id, bid))
    signal.set()
    return ok(result, request)


@router.get("")
def list_batches(
    project_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    store = get_store()
    queue.initialize(store)
    with store.read_snapshot() as db:
        rows = db.execute(
            "SELECT * FROM production_batches WHERE tenant=? AND project=? ORDER BY created DESC LIMIT ?",
            (actor.tenant_id, project_id, limit),
        )
        return ok({"batches": [queue.summary(db, row) for row in rows]}, request)


@router.get("/{bid}")
def get_batch(
    bid: str,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=200),
    state: (
        Literal["queued", "running", "waiting", "succeeded", "failed", "cancelled"] | None
    ) = None,
    actor: Actor = Depends(current_actor),
):
    store = get_store()
    queue.initialize(store)
    with store.read_snapshot() as db:
        return ok(
            queue.page(db, queue.get_batch(db, actor.tenant_id, bid), after, limit, state), request
        )


@router.post("/{bid}/{action}")
def control(
    bid: str,
    action: Literal["pause", "resume", "cancel", "retry"],
    request: Request,
    actor: Actor = Depends(current_actor),
):
    store = get_store()
    queue.initialize(store)
    with store.transaction(), store.connection() as db:
        row = queue.get_batch(db, actor.tenant_id, bid)
        authorize(actor, row["output"])
        if row["control"] == "cancelled" and action != "cancel":
            raise DomainError("BATCH_CANCELLED", "已取消的批次不能重新执行。", 409)
        if action == "retry":
            db.execute(
                "UPDATE production_items SET status='queued',error=NULL,due=? WHERE batch=? AND status='failed'",
                (time.time(), bid),
            )
        elif action == "cancel":
            db.execute(
                "UPDATE production_items SET status='cancelled' WHERE batch=? AND status='queued' AND generation_id IS NULL AND workflow_run_id IS NULL",
                (bid,),
            )
            db.execute(
                "UPDATE production_items SET status='waiting',due=0 WHERE batch=? AND status IN ('queued','waiting','failed') AND (generation_id IS NOT NULL OR workflow_run_id IS NOT NULL)",
                (bid,),
            )
        state = {
            "cancel": "cancelled",
            "pause": "paused",
            "resume": "active",
            "retry": row["control"],
        }[action]
        db.execute("UPDATE production_batches SET control=? WHERE id=?", (state, bid))
        store.append_event(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            project_id=row["project"],
            aggregate_id=bid,
            aggregate_version=1,
            event_type="batch.control." + action,
            payload={"control": state},
        )
        result = queue.page(db, queue.get_batch(db, actor.tenant_id, bid))
    signal.set()
    return ok(result, request)


def made_artifact(store, actor, timeline):
    # Exact immutable revision only. This covers a crash after a successful render but before
    # the queue receipt was committed, without reading every Artifact in a large project.
    with store.connection() as db:
        row = db.execute(
            """SELECT r.id FROM reference_index x JOIN records r
            ON r.tenant_id=x.tenant_id AND r.id=x.source_id AND r.latest_version=x.source_version
            JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=1
            WHERE x.tenant_id=? AND x.target_id=? AND x.target_version=? AND r.kind='Artifact'
            AND r.project_id=? AND json_extract(v.data_json,'$.execution_context.run_id')=?
            AND json_extract(v.data_json,'$.execution_context.node_id')='batch_item' LIMIT 1""",
            (
                actor.tenant_id,
                timeline["id"],
                timeline["version"],
                timeline["project_id"],
                queue.resource(timeline["data"]["batch_id"], timeline["data"]["batch_ordinal"]),
            ),
        ).fetchone()
    if row:
        artifact, _, _ = resolve_media(
            store, actor, row["id"], project_id=timeline["project_id"], purpose="export"
        )
        if (
            artifact["data"].get("timeline_id") == timeline["id"]
            and artifact["data"].get("timeline_version") == timeline["version"]
        ):
            return artifact["id"]
    return None


def generation_checkpoint(store, item, key, gid, stage, asset=None):
    asset = asset or {}
    with store.transaction(), store.connection() as db:
        queue.update(store, item, generation_id=gid)
        db.execute(
            "UPDATE production_generation_slots SET status=?,asset_id=?,asset_version=? WHERE batch=? AND ordinal=? AND slot=?",
            (stage, asset.get("id"), asset.get("version"), *key),
        )


def cancel_slots(store, item, actor):
    # New provider submissions need a reservation first. Only reconcile existing jobs.
    with store.connection() as db:
        outstanding = db.execute(
            "SELECT slot,generation_id FROM production_generation_slots WHERE batch=? AND ordinal=? AND status NOT IN ('ready','cancelled','failed')",
            (item["batch"], item["ordinal"]),
        ).fetchall()
    pending = False
    for row in outstanding:
        gid = row["generation_id"]
        try:
            receipt = generation.cancel(gid, None, actor)["data"]
            stage = receipt["job"]["data"]["stage"]
        except DomainError as exc:
            if exc.code != "NOT_FOUND":
                raise
            # A reserved slot with no locally recorded provider intent was never submitted.
            stage = "cancelled"
        generation_checkpoint(
            store, item, (item["batch"], item["ordinal"], row["slot"]), gid, stage
        )
        pending |= stage not in generation.FINAL
    queue.update(
        store, item, status="waiting" if pending else "cancelled", delay=5 if pending else 0
    )


def generated_cut(store, item, actor, recipe, index, cfg_hash):
    slot = recipe.slots[index].generate
    job_key = f'{item["batch"]}-{item["ordinal"]}-{index+1}-gen'
    gid = "gen_" + digest([actor.tenant_id, recipe.project_id, job_key])[:36]
    key = (item["batch"], item["ordinal"], index + 1)
    with store.connection() as db:
        saved = db.execute(
            "SELECT * FROM production_generation_slots WHERE batch=? AND ordinal=? AND slot=?", key
        ).fetchone()
    if saved and saved["status"] == "ready":
        return Cut(
            asset_id=saved["asset_id"], version=saved["asset_version"], source_out=slot.duration
        )
    try:
        job = generation.load(actor, gid) if saved else None
    except DomainError as exc:
        if exc.code != "NOT_FOUND":
            raise
        job = None
    if not job:
        if digest(generation.profile(slot.profile_id)) != cfg_hash:
            raise DomainError(
                "GENERATOR_PROFILE_CHANGED", "生成器配置已变化，不能沿用原批次执行。", 409
            )
        # Only one reservation can occupy each pre-authorized (ordinal,slot). A crash
        # before/after sending reopens the same provider request and the same seed.
        with store.transaction(), store.connection() as db:
            queue.update(store, item, generation_id=gid)
            db.execute(
                "INSERT OR IGNORE INTO production_generation_slots(batch,ordinal,slot,generation_id,status) VALUES(?,?,?,?,'reserved')",
                (*key, gid),
            )
        body = generation.Generate(
            project_id=recipe.project_id,
            slot=f"{recipe.name[:120]} · {item['ordinal']}/{index+1}",
            profile_id=slot.profile_id,
            prompt=slot.prompt,
            seed=(recipe.seed + (item["ordinal"] - 1) * len(recipe.slots) + index) % 2147483648,
            reference_asset_id=slot.reference_asset_id,
            reference_asset_version=slot.reference_asset_version,
            idempotency_key=job_key,
        )
        receipt = generation.generate(body, None, actor)["data"]
    elif job["data"]["stage"] in generation.FINAL:
        receipt = generation.result(actor, job)
    else:
        receipt = generation.refresh(gid, None, actor)["data"]
    stage = receipt["job"]["data"]["stage"]
    asset = receipt.get("asset") or {}
    generation_checkpoint(store, item, key, gid, stage, asset)
    if stage == "ready":
        return Cut(asset_id=asset["id"], version=asset["version"], source_out=slot.duration)
    if stage in generation.FINAL:
        issue = receipt["job"]["data"].get("issue") or {
            "code": "GENERATION_STOPPED",
            "message": "生成器未返回可用素材。",
        }
        queue.update(store, item, status="failed", error=issue)
    elif stage == "outcome_unknown":
        # This slot already owns a stable provider submission. Pause future sends until the
        # original receipt is reconciled, including after an interrupted submit.
        with store.transaction(), store.connection() as db:
            db.execute(
                "UPDATE production_batches SET control='paused' WHERE id=? AND control='active'",
                (item["batch"],),
            )
            queue.update(
                store,
                item,
                status="failed",
                error={
                    "code": "GENERATION_RECONCILE_FIRST",
                    "message": "供应商回执未知；请核对原生成任务后重试并恢复批次。",
                },
            )
    else:
        queue.update(store, item, status="waiting", delay=3)
    return None


def materialize(store, actor, recipe, run, item, cuts):
    template = template_for(actor, recipe)
    data = deepcopy(template["data"]) if template else {"audio_tracks": [], "captions": []}
    for key in (
        "execution_context",
        "origin_execution_context",
        "locked_at",
        "locked_by",
        "clip_uses",
    ):
        data.pop(key, None)
    old_clips = data.get("clips", [])
    clips, sources = [], {}
    cursor = 0.0
    for index, cut in enumerate(cuts):
        source(actor, recipe.project_id, cut, sources)
        clip = (
            dict(old_clips[index])
            if template
            else {
                "id": f"cut-{index+1}",
                "track": 0,
                "speed": 1,
                "volume": 1,
                "timeline_start": cursor,
            }
        )
        for key in ("asset_version_id", "source_ref", "source_id", "source_range"):
            clip.pop(key, None)
        clip.update(
            asset_id=cut.asset_id,
            source_in=cut.source_in,
            source_out=cut.source_out,
            name=sources[cut.asset_id]["data"].get("name") or cut.asset_id,
        )
        clips.append(clip)
        cursor = round(cursor + cut.source_out - cut.source_in, 8)
    locked = recipe.output == "render"
    data.update(
        name=f"{recipe.name} · {item['ordinal']}",
        clips=clips,
        locked=locked,
        status="locked" if locked else "draft",
        format={
            **data.get("format", {}),
            "width": recipe.width,
            "height": recipe.height,
            "fps": recipe.fps,
        },
        batch_id=run["id"],
        batch_ordinal=item["ordinal"],
    )
    if locked:
        data.update(locked_at=now(), locked_by=actor.actor_id)
    dependencies = [ref(run), *map(ref, sources.values())]
    if recipe.market_brief:
        local_brief, checked = batch_market.check(actor, recipe, {"data": data})
        data["localization_evidence"] = checked
        dependencies.append(ref(local_brief))
    if template:
        dependencies.append(ref(template))
        for clip in data.get("audio_tracks", []):
            key = clip.get("asset_version_id") or clip.get("asset_id") or clip.get("source_id")
            if not key:
                pin = clip.get("source_ref") or {}
                key = pin.get("id") if isinstance(pin, dict) else pin
            item_record, _, _ = resolve_media(store, actor, key, project_id=recipe.project_id)
            dependencies.append(ref(item_record))
    if item.get("generation_id"):
        with store.connection() as db:
            generated = db.execute(
                "SELECT generation_id FROM production_generation_slots WHERE batch=? AND ordinal=? AND status='ready' ORDER BY slot",
                (item["batch"], item["ordinal"]),
            )
            dependencies.extend(ref(generation.load(actor, r["generation_id"])) for r in generated)
    with store.transaction():
        # Recheck frozen record revisions immediately before commit.
        for asset in sources.values():
            check_revision(store.get(asset["id"], actor.tenant_id), asset["version"])
        timeline = store.create(
            "Timeline",
            data,
            project_id=recipe.project_id,
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            refs=dependencies,
            reason="批次冻结配方按确定组合生成时间线",
        )
        queue.update(store, item, timeline_id=timeline["id"])
    return timeline


def control_state(store, item):
    with store.connection() as db:
        return queue.get_batch(db, item["tenant"], item["batch"])["control"]


def execute_item(store, item):
    actor = Actor(item["tenant"], item["actor"], item["role"], item["credential_digest"])
    run = record(actor, item["batch"], "Run", version=1)
    recipe = Recipe.model_validate(run["data"]["recipe"])
    if item["auth_mode"] != os.getenv("AI_SHIP_MODE", "local"):
        raise DomainError(
            "AUTH_MODE_CHANGED", "服务端认证模式已切换，原授权范围不能继续执行。", 403
        )
    authorize(actor, recipe.output)
    state = control_state(store, item)
    if state == "paused":
        queue.update(store, item, status="waiting" if item["generation_id"] else "queued")
        return
    if recipe.market_brief and state == "active":
        batch_market.check(actor, recipe)
    cuts = choices_for(recipe, item["ordinal"])
    if not item["timeline_id"]:
        for index, slot in enumerate(recipe.slots):
            if not slot.generate:
                continue
            state = control_state(store, item)
            if state == "paused":
                queue.update(store, item, status="waiting" if item["generation_id"] else "queued")
                return
            if state == "cancelled":
                cancel_slots(store, item, actor)
                return
            cuts[index] = generated_cut(
                store,
                item,
                actor,
                recipe,
                index,
                run["data"]["generator_hashes"][slot.generate.profile_id],
            )
            if not cuts[index]:
                return
    if state == "cancelled" and not item["timeline_id"]:
        queue.update(store, item, status="cancelled")
        return
    if item["timeline_id"]:
        timeline = record(actor, item["timeline_id"], "Timeline", project=recipe.project_id)
        check_revision(timeline, 1)
    else:
        timeline = materialize(store, actor, recipe, run, item, cuts)
    batch_acceptance.validate_output(store, actor, recipe, item, timeline)
    if recipe.market_brief:
        batch_market.check(actor, recipe, timeline)
    if recipe.output == "timeline":
        batch_workflows.finish(store, actor, recipe, item, timeline)
        return
    artifact_id = item.get("artifact_id") or made_artifact(store, actor, timeline)
    if artifact_id:
        artifact, _, _ = resolve_media(
            store, actor, artifact_id, project_id=recipe.project_id, purpose="export"
        )
        queue.update(store, item, artifact_id=artifact_id)
        if recipe.market_brief:
            batch_market.check(actor, recipe, timeline)
        batch_acceptance.validate_output(store, actor, recipe, item, timeline, artifact)
        batch_workflows.finish(store, actor, recipe, item, artifact)
        return
    state = control_state(store, item)
    if state != "active":
        queue.update(store, item, status="cancelled" if state == "cancelled" else "queued")
        return
    for cut in cuts:
        if cut:
            source(actor, recipe.project_id, cut, {})
    artifact = render_timeline(
        store,
        actor,
        {
            "project_id": recipe.project_id,
            "timeline_id": timeline["id"],
            "expected_revision": timeline["version"],
        },
    )["artifact"]
    queue.update(store, item, artifact_id=artifact["id"])
    if recipe.market_brief:
        batch_market.check(actor, recipe, timeline)
    batch_acceptance.validate_output(store, actor, recipe, item, timeline, artifact)
    batch_workflows.finish(store, actor, recipe, item, artifact)


def work_once(store):
    item = queue.claim(store)
    if not item:
        return False
    stopped = threading.Event()

    def renew():
        while not stopped.wait(queue.LEASE_SECONDS / 4):
            try:
                queue.heartbeat(store, item)
            except Exception:
                logger.exception("批次续租失败 batch=%s ordinal=%s", item["batch"], item["ordinal"])
                return

    renewer = threading.Thread(target=renew, daemon=True)
    renewer.start()
    try:
        actor = Actor(item["tenant"], item["actor"], item["role"], item["credential_digest"])
        with store.execution_context(
            queue.resource(item["batch"], item["ordinal"]),
            "batch_item",
            item["token"],
            item["tenant"],
            actor,
        ):
            execute_item(store, item)
    except Exception as exc:
        if not isinstance(exc, DomainError):
            logger.exception("批次工作项失败 batch=%s ordinal=%s", item["batch"], item["ordinal"])
        if not isinstance(exc, DomainError) or exc.code != "STALE_FENCE":
            issue = (
                {"code": exc.code, "message": exc.message}
                if isinstance(exc, DomainError)
                else {"code": "BATCH_INTERNAL", "message": "工作项未完成，请核对后台记录后重试。"}
            )
            try:
                queue.update(store, item, status="failed", error=issue)
            except DomainError as lost:
                if lost.code != "STALE_FENCE":
                    raise
    finally:
        stopped.set()
        renewer.join()
    return True


@contextmanager
def batch_workers():
    store = get_store()
    queue.initialize(store)
    count = int(os.getenv("AI_SHIP_BATCH_WORKERS", "2"))
    if not 0 <= count <= 16:
        raise ValueError("AI_SHIP_BATCH_WORKERS须在0到16之间")
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                if work_once(store):
                    continue
            except Exception:
                logger.exception("批次调度存储暂不可用")
            signal.wait(1)
            signal.clear()

    threads = [
        threading.Thread(target=worker, name=f"batch-worker-{i}", daemon=True) for i in range(count)
    ]
    for thread in threads:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        signal.set()
        for thread in threads:
            thread.join()
