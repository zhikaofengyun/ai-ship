from typing import Any, Literal
from datetime import datetime, timezone
import os
import asyncio
import json
import shutil
import uuid
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict, StrictInt
from .auth import Actor, current_actor, require_write, require_approval
from .catalog import catalog, workspace_catalog, COUNTS, SOURCE_SHA256, MACHINES, ENTITIES
from .commands import dispatch, command_capabilities
from .asset_controls import manual_asset_data
from .errors import DomainError
from .schemas import SCHEMAS
from .store import get_store, now
from .workflow import resolve_parameters, validate_workflow

router = APIRouter()


def ok(data, request=None):
    return {
        "data": data,
        "trace_id": (
            getattr(request.state, "trace_id", None) if request is not None else uuid.uuid4().hex
        ),
    }


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProjectCreate(Body):
    name: str = Field(min_length=1, max_length=240)
    goals: str | list | dict = ""
    language: str = Field(default="zh-CN", min_length=1, max_length=32)
    market: str = Field(default="CN", min_length=1, max_length=80)
    content_mode: Literal["voiceover", "music", "mixed", "product", "custom"] = "product"
    duration: float = Field(default=25, gt=0, le=86400)
    width: StrictInt = Field(default=1080, ge=64, le=8192)
    height: StrictInt = Field(default=1920, ge=64, le=8192)
    budget: float = Field(default=0, ge=0)


class RecordCreate(Body):
    kind: str
    project_id: str | None = None
    data: dict[str, Any]
    refs: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = Field(default="用户录入", max_length=2000)


class RecordUpdate(Body):
    expected_revision: StrictInt = Field(ge=1)
    data: dict[str, Any]
    refs: list[dict[str, Any]] | None = None
    reason: str = Field(default="用户修订", max_length=2000)


class CommandEnvelope(Body):
    command_id: str = Field(min_length=1, max_length=200)
    command_type: str = Field(min_length=1, max_length=100)
    project_id: str | None
    actor_id: str = Field(min_length=1, max_length=200)
    actor_role: str
    target_id: str | None
    expected_revision: StrictInt | None = Field(ge=1)
    input_version_refs: list[dict[str, Any]]
    parameters: dict[str, Any]
    policy_version: dict[str, Any] | None
    idempotency_key: str = Field(min_length=1, max_length=240)
    trace_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class ImpactBody(Body):
    changes: list[dict[str, Any]] = Field(default_factory=list)
    refs: list[dict[str, Any]] = Field(default_factory=list)


SYSTEM_TYPES = {
    "Run",
    "ToolCall",
    "RenderJob",
    "Artifact",
    "Event",
    "Audit",
    "ImportBatch",
    "ImportEntry",
    "TransformRun",
    "BlobLocation",
    "TimeMap",
    "Cost",
    "Post",
    "PlatformEvent",
    "ProductionTrace",
    "RenderManifest",
    "DeliveryPackage",
    "ReplayManifest",
}


SOURCE_SYNC_FIELDS = {"sync_watermarks", "origin_availability", "content_change_version"}


def admit_manual_record(kind, data, actor):
    if kind in SYSTEM_TYPES:
        raise DomainError(
            "DEDICATED_ENDPOINT_REQUIRED",
            "该类型由执行器或带证据的专用导入接口写入，不能通用录入执行事实。",
            403,
            {"kind": kind},
        )
    if kind == "Approval":
        require_approval(actor)
        raise DomainError(
            "DEDICATED_ENDPOINT_REQUIRED", "确认记录需要绑定对象 hash 与权限，请使用确认接口。", 403
        )
    require_write(actor)
    if kind == "ModelProfile" and data.get("health_status") not in (None, "unprobed"):
        raise DomainError("PROBE_REQUIRED", "模型健康状态必须由真实探测产生。")
    if kind == "Capability" and any(
        data.get(k) in (True, "verified", "available", "healthy", "passed")
        for k in (
            "availability",
            "publish_probe_state",
            "production_observed_state",
            "read_probe_state",
        )
    ):
        raise DomainError("PROBE_REQUIRED", "能力可用状态必须有真实探测证据。")
    if kind == "Trend" and (
        "source_review" in data
        or data.get("verification")
        in (
            "corroborated_as_reviewed",
            "conflicting",
            "insufficient_independent_sources",
            "expired",
        )
    ):
        raise DomainError("TREND_REVIEW_REQUIRED", "热点交叉核验结论须由核验入口写入。", 403)
    if kind == "TaskContract" and data.get("status") == "satisfied":
        raise DomainError("ACCEPTANCE_REQUIRED", "任务完成必须由执行器验收产物。")
    if kind == "Publication" and (
        any(k.startswith("inbox_") for k in data)
        or "platform_post_ids" in data
        or "batch_ordinal" in data
    ):
        raise DomainError(
            "PUBLISH_ENDPOINT_REQUIRED", "批次交付与平台回执由专用发布入口写入。", 403
        )
    if kind == "Publication" and data.get("status") not in (None, "draft", "草稿"):
        raise DomainError("PUBLISH_ENDPOINT_REQUIRED", "发布状态必须由发布处理器或人工回执更新。")
    if kind == "Account" and data.get("identity_status") == "verified":
        raise DomainError("PROBE_REQUIRED", "账号验证状态必须来自实际身份探测。")


@router.post("/session")
def browser_session(request: Request, actor: Actor = Depends(current_actor)):
    from .auth import create_session, SESSION_COOKIE

    cookie, data = create_session(request, actor)
    response = JSONResponse(ok(data, request))
    if cookie:
        response.set_cookie(
            SESSION_COOKIE,
            cookie["value"],
            max_age=cookie["max_age"],
            httponly=True,
            secure=cookie["secure"],
            samesite="strict",
            path="/",
        )
    return response


@router.delete("/session")
def delete_browser_session(request: Request, actor: Actor = Depends(current_actor)):
    from .auth import revoke_session, SESSION_COOKIE

    revoke_session(request)
    response = JSONResponse(ok({"signed_out": True}, request))
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/health")
def health(request: Request, actor: Actor = Depends(current_actor)):
    import fastapi, pydantic, sqlite3

    return ok(
        {
            "status": "ok",
            "version": "0.1.0",
            "mode": os.getenv("AI_SHIP_MODE", "local"),
            "actor": {"actor_id": actor.actor_id, "role": actor.role, "tenant_id": actor.tenant_id},
            "runtime": {
                "fastapi": fastapi.__version__,
                "pydantic": pydantic.__version__,
                "sqlite": sqlite3.sqlite_version,
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "ffprobe": bool(shutil.which("ffprobe")),
            },
            "counts": COUNTS,
            "source_sha256": SOURCE_SHA256,
            "capabilities": command_capabilities(),
        },
        request,
    )


@router.get("/catalog")
def get_catalog(request: Request, actor: Actor = Depends(current_actor)):
    return ok(catalog(), request)


@router.get("/catalog/workspace")
def get_workspace_catalog(request: Request, actor: Actor = Depends(current_actor)):
    return ok(workspace_catalog(), request)


@router.get("/contracts/entities/{kind}")
def entity_schema(kind: str, request: Request, actor: Actor = Depends(current_actor)):
    if kind not in SCHEMAS:
        raise DomainError("UNKNOWN_ENTITY", "实体类型不存在。", 404)
    return ok(SCHEMAS[kind], request)


@router.get("/projects")
def projects(request: Request, actor: Actor = Depends(current_actor)):
    return ok(get_store().list(actor.tenant_id, "Project"), request)


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    data = body.model_dump()
    data["status"] = "draft"
    return ok(
        get_store().create(
            "Project",
            data,
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            reason="创建项目",
            trace_id=request.state.trace_id,
        ),
        request,
    )


@router.get("/records")
def records(
    request: Request,
    kind: str | None = None,
    project_id: str | None = None,
    q: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=500, ge=1, le=2000),
    actor: Actor = Depends(current_actor),
):
    return ok(get_store().list(actor.tenant_id, kind, project_id, q, limit), request)


@router.post("/records", status_code=201)
def create_record(body: RecordCreate, request: Request, actor: Actor = Depends(current_actor)):
    if body.kind == "Source" and SOURCE_SYNC_FIELDS.intersection(body.data):
        raise DomainError("SOURCE_SYNC_REQUIRED", "来源同步状态由已核实的变更清单产生。", 403)
    manual_asset_data(body.kind, body.data)
    if body.kind == "Publication" and "commerce_binding" in body.data:
        raise DomainError("COMMERCIAL_BINDING_REQUIRED", "商品绑定结果须经专用核对流程生成。", 403)
    admit_manual_record(body.kind, body.data, actor)
    return ok(
        get_store().create(
            body.kind,
            body.data,
            tenant_id=actor.tenant_id,
            project_id=body.project_id,
            actor_id=actor.actor_id,
            refs=body.refs,
            reason=body.reason,
            trace_id=request.state.trace_id,
        ),
        request,
    )


@router.get("/records/{record_id}")
def get_record(
    record_id: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
    actor: Actor = Depends(current_actor),
):
    return ok(get_store().get(record_id, actor.tenant_id, version), request)


@router.get("/records/{record_id}/history")
def record_history(record_id: str, request: Request, actor: Actor = Depends(current_actor)):
    return ok(get_store().history(record_id, actor.tenant_id), request)


@router.patch("/records/{record_id}")
def patch_record(
    record_id: str, body: RecordUpdate, request: Request, actor: Actor = Depends(current_actor)
):
    with get_store().transaction():
        return _patch_record(record_id, body, request, actor)


def _patch_record(record_id, body, request, actor):
    store = get_store()
    old = store.get(record_id, actor.tenant_id)
    from .score_policies import PREFIX as SCORE_RELEASE_PREFIX

    from .comment_feedback import PREFIX as COMMENT_PREFIX

    if record_id.startswith((SCORE_RELEASE_PREFIX, COMMENT_PREFIX, "edit_pref_", "edit_memory_")):
        raise DomainError("DEDICATED_ENDPOINT_REQUIRED", "该记录仅由其专用流程写入。", 403)
    if old["kind"] == "Publication" and (
        old["data"].get("inbox_stage") or old["data"].get("batch_id")
    ):
        raise DomainError("PUBLISH_ENDPOINT_REQUIRED", "请从发布意图的变更或回执入口操作。", 403)
    if old["kind"] == "Publication":
        from .account_quota import ensure_intent_editable

        ensure_intent_editable(actor, old, body.data.get("account_id"))
    admit_manual_record(old["kind"], body.data, actor)
    data = dict(manual_asset_data(old["kind"], body.data, old))
    if old["kind"] == "Publication" and (
        "commerce_binding" in data or "commerce_binding" in old["data"]
    ):
        if "commerce_binding" in data and data["commerce_binding"] != old["data"].get(
            "commerce_binding"
        ):
            raise DomainError("COMMERCIAL_BINDING_REQUIRED", "商品绑定不可通过通用编辑变更。", 403)
        if "commerce_binding" in old["data"]:
            for key in (
                "commerce_binding",
                "artifact_id",
                "artifact_version",
                "artifact_hash",
                "account_id",
                "channel",
                "metadata",
                "metadata_hash",
                "outcome",
                "status",
                "post_refs",
            ):
                if key in data and data[key] != old["data"].get(key):
                    raise DomainError(
                        "COMMERCIAL_BINDING_REQUIRED", "改变已绑定对象或商业信息前请重新核对。", 403
                    )
                data[key] = old["data"][key]
    if old["kind"] == "Source":
        managed = set(SOURCE_SYNC_FIELDS)
        if "rights" in (old["data"].get("sync_watermarks") or {}):
            managed.update(("expires_at", "revoked_at", "status"))
        for key in managed:
            if key in data and data[key] != old["data"].get(key):
                raise DomainError("SOURCE_SYNC_REQUIRED", "已核实的来源变化须经专用流程复核。", 403)
            if key in old["data"]:
                data[key] = old["data"][key]
    if old["kind"] == "Timeline" and data != old["data"]:
        data["status"] = "draft"
        data["locked"] = False
        data.pop("locked_at", None)
        data.pop("locked_by", None)
    return ok(
        store.update(
            record_id,
            data,
            tenant_id=actor.tenant_id,
            expected_revision=body.expected_revision,
            actor_id=actor.actor_id,
            refs=body.refs,
            reason=body.reason,
            trace_id=request.state.trace_id,
        ),
        request,
    )


@router.get("/events")
def events(
    request: Request,
    project_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    after: int = Query(default=0, ge=0),
    actor: Actor = Depends(current_actor),
):
    return ok(get_store().events(actor.tenant_id, project_id, limit, after), request)


@router.get("/events/stream")
async def event_stream(
    request: Request,
    project_id: str | None = None,
    after: int = Query(default=0, ge=0),
    actor: Actor = Depends(current_actor),
):
    """Reconnectable outbox projection. Event IDs are durable SQLite sequence IDs."""
    header = request.headers.get("last-event-id")
    if header:
        try:
            header_cursor = int(header)
        except ValueError:
            raise DomainError("INVALID_EVENT_CURSOR", "Last-Event-ID 必须为非负整数。")
        if header_cursor < 0:
            raise DomainError("INVALID_EVENT_CURSOR", "事件游标不能为负数。")
        after = max(after, header_cursor)
    store = get_store()
    if project_id:
        store.get(project_id, actor.tenant_id)

    async def generate():
        cursor = after
        idle = 0
        while not await request.is_disconnected():
            # Long-lived streams re-check identity so revoked team keys stop.
            try:
                if current_actor(request) != actor:
                    raise DomainError("IDENTITY_CHANGED", "会话身份已改变。", 401)
            except DomainError:
                yield 'event: revoked\ndata: {"reason":"identity_revoked"}\n\n'
                return
            batch = await asyncio.to_thread(
                store.events, actor.tenant_id, project_id, 100, cursor, True
            )
            for event in batch:
                cursor = event["sequence"]
                yield f"id: {cursor}\nevent: fact\ndata: " + json.dumps(
                    event, ensure_ascii=False, separators=(",", ":")
                ) + "\n\n"
            if batch:
                idle = 0
            else:
                idle += 1
                if idle >= 15:
                    yield ": heartbeat\n\n"
                    idle = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/runs")
def runs(request: Request, project_id: str | None = None, actor: Actor = Depends(current_actor)):
    return ok(get_store().list(actor.tenant_id, "Run", project_id), request)


@router.post("/commands")
def run_command(body: CommandEnvelope, request: Request, actor: Actor = Depends(current_actor)):
    return ok(dispatch(get_store(), actor, body.model_dump()), request)


@router.post("/workflows/validate")
def compile_workflow(body: dict[str, Any], request: Request, actor: Actor = Depends(current_actor)):
    return ok(validate_workflow(body), request)


@router.post("/parameters/resolve")
def resolve(body: dict[str, Any], request: Request, actor: Actor = Depends(current_actor)):
    return ok(resolve_parameters(body), request)


@router.post("/dependencies/impact")
def impact(body: ImpactBody, request: Request, actor: Actor = Depends(current_actor)):
    changes = body.changes or body.refs
    for change in changes:
        if change.get("range") is not None:
            from .schemas import _interval

            change["range"] = list(_interval(change["range"], "change range"))
        if not isinstance(change.get("fields", []), list):
            raise DomainError("INVALID_FIELDS", "修改字段必须为数组。")
    return ok(get_store().impact(actor.tenant_id, changes), request)


@router.get("/machines/{machine_id}")
def machine(machine_id: str, request: Request, actor: Actor = Depends(current_actor)):
    if machine_id not in MACHINES:
        raise DomainError("UNKNOWN_MACHINE", "状态机不存在。", 404)
    return ok(MACHINES[machine_id], request)


@router.post("/machines/{machine_id}/validate")
def machine_transition(
    machine_id: str, body: dict[str, Any], request: Request, actor: Actor = Depends(current_actor)
):
    m = MACHINES.get(machine_id)
    if not m:
        raise DomainError("UNKNOWN_MACHINE", "状态机不存在。", 404)
    matches = [e for e in m["edges"] if e["from"] == body.get("from") and e["to"] == body.get("to")]
    return ok(
        {
            "declared_transition": bool(matches),
            "transitions": matches,
            "execution_authorized": False,
            "boundary": "声明的迁移存在不代表守卫已满足；执行命令需核实真实状态与证据。",
        },
        request,
    )


@router.get("/summary")
def summary(request: Request, project_id: str | None = None, actor: Actor = Depends(current_actor)):
    store = get_store()
    all_records = store.list(actor.tenant_id, project_id=project_id, limit=2000)
    counts = {kind: sum(r["kind"] == kind for r in all_records) for kind in ENTITIES}
    costs = [r for r in all_records if r["kind"] == "Cost"]
    totals = {}
    for r in costs:
        d = r["data"]
        currency = d.get("currency", "unknown")
        if d.get("actual") is not None:
            totals[currency] = totals.get(currency, 0) + d["actual"]
    return ok(
        {
            "counts": counts,
            "actual_costs": totals,
            "record_count": len(all_records),
            "truncated_at": 2000 if len(all_records) == 2000 else None,
            "blocked_runs": sum(
                r["kind"] == "Run" and r["data"].get("state") in ("需补充", "待核对")
                for r in all_records
            ),
            "latest_events": store.events(actor.tenant_id, project_id, 10),
        },
        request,
    )
