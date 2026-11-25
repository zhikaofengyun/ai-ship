"""Bounded deterministic workflow execution; no dynamic code or external writes.

Each node calls a named local handler, freezes inputs, persists attempts/checkpoints,
and writes through a fencing lease. Unsupported routes stay blocked while
independent branches may proceed. A crashed running node must be reconciled.
"""

from datetime import datetime, timezone, timedelta
from typing import Any
import copy
import json
import time
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ConfigDict, StrictInt
from .api import ok
from .auth import Actor, current_actor, require_write, actor_still_authorized
from .catalog import FEATURES
from .errors import DomainError
from .store import get_store, digest, now
from .workflow import validate_workflow

router = APIRouter(prefix="/execution", tags=["Bounded local workflows"])
HANDLERS = {
    "G02-R2": {
        "operation": "generate_media",
        "feature_id": "G02",
        "outputs": {"asset": "Asset", "job": "ToolCall"},
        "boundary": "本地生成图片，核对原任务并解码文件后交给下游；商品一致性由审阅环节确认。",
    },
    "G03-R2": {
        "operation": "generate_media",
        "feature_id": "G03",
        "outputs": {"asset": "Asset", "job": "ToolCall"},
        "boundary": "本地文生或参考图生视频，以解码后的实际视频和任务凭据继续。",
    },
    "E01-R3": {
        "operation": "import_otio",
        "feature_id": "E01",
        "outputs": {"timeline": "Timeline"},
        "boundary": "导入手剪 OTIO 清单，保留来源关系，损失项必须经确认。",
    },
    "I03-R2": {
        "operation": "transform",
        "feature_id": "I03",
        "outputs": {"version": "AssetVersion", "run": "TransformRun"},
        "boundary": "实际烘焙衍生，非破坏式效果栈另行保存",
    },
    "I04-R1": {
        "operation": "transform",
        "feature_id": "I04",
        "outputs": {"version": "AssetVersion", "run": "TransformRun"},
        "boundary": "显式配方的标准媒体处理",
    },
    "I07-R2": {
        "operation": "transform",
        "feature_id": "I07",
        "outputs": {"version": "AssetVersion", "run": "TransformRun"},
        "boundary": "固定配方实际执行；无自动推荐推断",
    },
    "L01-R1": {
        "operation": "detect_shots",
        "feature_id": "L01",
        "outputs": {"shots": "Shot"},
        "boundary": "视觉场景候选；未自动证明语义标签",
    },
    "L07-R1": {
        "operation": "extract_audio",
        "feature_id": "L07",
        "outputs": {"audio": "Audio"},
        "boundary": "实际解码音轨；无音轨明确阻塞",
    },
    "V03-R2": {
        "operation": "narration",
        "feature_id": "V03",
        "outputs": {"audio": "Audio", "call": "ToolCall"},
        "boundary": "以项目内已审核音色和冻结英文脚本进行本地 Flite 合成；试听后另行打点",
    },
    "V01-R3": {
        "operation": "script",
        "feature_id": "V01",
        "outputs": {"script": "Script"},
        "boundary": "人工脚本与显式事实引用",
    },
    "E01-R1": {
        "operation": "timeline",
        "feature_id": "E01",
        "outputs": {"timeline": "Timeline"},
        "boundary": "内部时间线数据与引用校验",
    },
    "E08-R1": {
        "operation": "lock_timeline",
        "feature_id": "E08",
        "outputs": {"timeline": "Timeline"},
        "boundary": "用户显式确认的版本冻结；不代替内容/版权审批",
    },
    "Y03-R1": {
        "operation": "render",
        "feature_id": "Y03",
        "outputs": {"artifact": "Artifact", "job": "RenderJob", "manifest": "RenderManifest"},
        "boundary": "真实FFmpeg与技术QC，publish_ready仍为false",
    },
    "Y06-R2": {
        "operation": "package",
        "feature_id": "Y06",
        "outputs": {"package": "DeliveryPackage"},
        "boundary": "实际离线交付包；发布能力另行准入",
    },
}

_REF_INPUT = {"type": "string", "minLength": 1, "title": "选择已保存的版本"}
_RECIPE_PROPERTIES = {
    "name": {"type": "string", "title": "配方名称"},
    "trim_start": {"type": "number", "minimum": 0, "title": "保留起点（秒）"},
    "trim_end": {"type": "number", "exclusiveMinimum": 0, "title": "保留终点（秒）"},
    "scale": {"type": "number", "minimum": 1, "maximum": 4, "title": "画面缩放"},
    "brightness": {"type": "number", "minimum": -1, "maximum": 1, "title": "亮度"},
    "contrast": {"type": "number", "minimum": 0, "maximum": 3, "title": "对比度"},
    "saturation": {"type": "number", "minimum": 0, "maximum": 3, "title": "饱和度"},
    "gamma": {"type": "number", "minimum": 0.1, "maximum": 10, "title": "Gamma"},
    "sharpness": {"type": "number", "minimum": 0, "maximum": 2, "title": "锐化"},
    "speed": {"type": "number", "minimum": 0.25, "maximum": 4, "title": "播放速度"},
    "volume": {"type": "number", "minimum": 0, "maximum": 4, "title": "原声音量"},
    "rotation": {"enum": [0, 90, 180, 270], "title": "旋转"},
    "flip_horizontal": {"type": "boolean", "title": "水平翻转"},
    "flip_vertical": {"type": "boolean", "title": "垂直翻转"},
    "loudness_lufs": {"type": "number", "minimum": -70, "maximum": -5, "title": "目标响度 LUFS"},
    "true_peak_db": {"type": "number", "minimum": -9, "maximum": 0, "title": "峰值上限 dBTP"},
    "width": {"type": "integer", "minimum": 16, "maximum": 4096, "multipleOf": 2, "title": "宽度"},
    "height": {"type": "integer", "minimum": 16, "maximum": 4096, "multipleOf": 2, "title": "高度"},
    "template": {"type": "string", "title": "配方模板"},
    "origin": {"type": "string"},
    "skip_reasons": {"type": "object"},
}
_COMMON_ARGS = {"project_id": _REF_INPUT, "expected_revision": {"type": "integer", "minimum": 1}}
OPERATION_SCHEMAS = {
    "generate_media": {
        "type": "object",
        "additionalProperties": False,
        "required": ["profile_id", "prompt", "seed"],
        "properties": {
            "project_id": _REF_INPUT,
            "profile_id": {"type": "string", "minLength": 1, "title": "本地生成配置"},
            "prompt": {"type": "string", "minLength": 1, "maxLength": 6000, "title": "镜头提示词"},
            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647, "title": "种子"},
            "reference_time_seconds": {
                "type": "number",
                "minimum": 0,
                "exclusiveMaximum": 3600,
                "title": "视频参考帧源时码（秒）",
            },
            "reference": {
                "type": "object",
                "required": ["id", "version"],
                "properties": {"id": _REF_INPUT, "version": {"type": "integer", "minimum": 1}},
                "title": "项目参考图片或视频版本",
            },
        },
    },
    "import_otio": {
        "type": "object",
        "additionalProperties": False,
        "required": ["content"],
        "properties": {
            "project_id": _REF_INPUT,
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1500000,
                "title": "OTIO 剪辑清单",
            },
            "bindings": {
                "type": "object",
                "maxProperties": 80,
                "additionalProperties": {"type": "string"},
                "title": "媒体来源绑定",
            },
            "acknowledge_metadata": {"type": "boolean", "title": "已核对元数据损失"},
        },
    },
    "narration": {
        "type": "object",
        "required": ["script", "approval"],
        "properties": {
            **_COMMON_ARGS,
            **{
                k: {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "version": {"type": "integer", "minimum": 1},
                        "kind": {"const": kind},
                    },
                    "required": ["id", "version"],
                    "additionalProperties": False,
                }
                for k, kind in (("script", "Script"), ("approval", "Approval"))
            },
            "tempo": {"type": "number", "minimum": 0.5, "maximum": 2, "title": "语速倍率"},
        },
        "additionalProperties": False,
    },
    "transform": {
        "type": "object",
        "required": ["asset_id", "recipe"],
        "properties": {
            **_COMMON_ARGS,
            "asset_id": _REF_INPUT,
            "recipe": {
                "type": "object",
                "properties": _RECIPE_PROPERTIES,
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    },
    "extract_audio": {
        "type": "object",
        "required": ["asset_id"],
        "properties": {**_COMMON_ARGS, "asset_id": _REF_INPUT},
        "additionalProperties": False,
    },
    "detect_shots": {
        "type": "object",
        "required": ["asset_id"],
        "properties": {
            **_COMMON_ARGS,
            "asset_id": _REF_INPUT,
            "threshold": {"type": "number", "minimum": 0.01, "maximum": 1, "title": "场景变化阈值"},
        },
        "additionalProperties": False,
    },
    "script": {
        "type": "object",
        "required": ["data"],
        "properties": {
            **_COMMON_ARGS,
            "data": {
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "title": "人工脚本"},
                    "language": {"type": "string", "title": "语言"},
                    "claims": {"type": "array"},
                    "product_bindings": {"type": ["array", "object"]},
                    "name": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "additionalProperties": False,
    },
    "timeline": {
        "type": "object",
        "required": ["data"],
        "properties": {
            **_COMMON_ARGS,
            "data": {"type": "object", "title": "从剪辑台绑定已有时间线或明确的内部时间线数据"},
        },
        "additionalProperties": False,
    },
    "lock_timeline": {
        "type": "object",
        "required": ["timeline_id", "expected_revision", "confirmed_hash"],
        "properties": {
            **_COMMON_ARGS,
            "timeline_id": _REF_INPUT,
            "confirmed_hash": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
                "title": "人工确认的时间线 hash",
            },
        },
        "additionalProperties": False,
    },
    "render": {
        "type": "object",
        "required": ["timeline_id", "expected_revision"],
        "properties": {
            **_COMMON_ARGS,
            "timeline_id": _REF_INPUT,
            "profile": {
                "type": "object",
                "properties": {
                    "width": _RECIPE_PROPERTIES["width"],
                    "height": _RECIPE_PROPERTIES["height"],
                    "fps": {
                        "anyOf": [
                            {"type": "number", "minimum": 1, "maximum": 60},
                            {
                                "type": "object",
                                "required": ["num", "den"],
                                "properties": {
                                    "num": {"type": "integer", "minimum": 1, "maximum": 100000},
                                    "den": {"type": "integer", "minimum": 1, "maximum": 100000},
                                },
                                "additionalProperties": False,
                            },
                        ],
                        "title": "输出帧率",
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    },
    "package": {
        "type": "object",
        "required": ["artifact_id"],
        "properties": {**_COMMON_ARGS, "artifact_id": _REF_INPUT},
        "additionalProperties": False,
    },
}
INPUT_SCHEMAS = {
    "generate_media": {"reference": {"kinds": ["Asset"], "value": "ref"}},
    "import_otio": {},
    "narration": {
        "script": {"kinds": ["Script"], "value": "ref"},
        "approval": {"kinds": ["Approval"], "value": "ref"},
    },
    "transform": {"asset_id": {"kinds": ["Asset", "AssetVersion"], "value": "id"}},
    "extract_audio": {"asset_id": {"kinds": ["Asset", "AssetVersion", "Audio"], "value": "id"}},
    "detect_shots": {"asset_id": {"kinds": ["Asset", "AssetVersion"], "value": "id"}},
    "script": {},
    "timeline": {"data": {"kinds": ["Timeline"], "value": "data"}},
    "lock_timeline": {"timeline_id": {"kinds": ["Timeline"], "value": "id"}},
    "render": {"timeline_id": {"kinds": ["Timeline"], "value": "id"}},
    "package": {"artifact_id": {"kinds": ["Artifact"], "value": "id"}},
}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StartInput(Body):
    workflow_id: str
    expected_revision: StrictInt = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ResumeInput(Body):
    expected_revision: StrictInt = Field(ge=1)


class ReconcileInput(ResumeInput):
    node_id: str
    outputs: dict[str, dict[str, Any] | list[dict[str, Any]]]
    reason: str = Field(min_length=1, max_length=2000)


def ref(record):
    return {"id": record["id"], "version": record["version"], "kind": record["kind"]}


def _require_record(store, actor, rid, kind=None, version=None, project=None):
    record = store.get(rid, actor.tenant_id, version)
    if kind and record["kind"] != kind:
        raise DomainError("REFERENCE_KIND", f"对象必须是 {kind}。")
    if project and record["project_id"] != project:
        raise DomainError("PROJECT_MISMATCH", "执行输入必须来自显式选定项目。", 403)
    return record


def execution_spec(data):
    spec = copy.deepcopy(data.get("DAG", data))
    # Older clients kept bounds beside DAG; preserve explicit declarations.
    for key in ("max_steps", "max_attempts", "budget", "max_seconds"):
        if key not in spec and key in data:
            spec[key] = data[key]
    return spec


def _validate_execution(spec):
    report = validate_workflow(spec)
    if not report["valid"]:
        raise DomainError("WORKFLOW_INVALID", "流程未通过类型与有界编译。", details=report)
    seconds = spec.get("max_seconds", 600)
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not 1 <= seconds <= 3600
    ):
        raise DomainError("TIME_BOUND", "本地运行时限必须为 1–3600 秒。")
    node_ids = {n["id"] for n in spec["nodes"]}
    ancestors = {n: set() for n in node_ids}
    for nid in report["order"]:
        for edge in spec["edges"]:
            source = edge.get("source", edge.get("from"))
            target = edge.get("target", edge.get("to"))
            if target == nid:
                ancestors[nid].add(source)
                ancestors[nid].update(ancestors[source])
    for node in spec["nodes"]:
        if not isinstance(node.get("operation_args", {}), dict) or not isinstance(
            node.get("input_bindings", {}), dict
        ):
            raise DomainError("NODE_CONTRACT", "operation_args 与 input_bindings 必须为对象。")
        for path, binding in node.get("input_bindings", {}).items():
            if not isinstance(path, str) or not path or not isinstance(binding, dict):
                raise DomainError("INPUT_BINDING", "输入绑定必须明确目标参数路径与来源对象。")
            if "node_id" in binding:
                if binding["node_id"] not in ancestors[node["id"]]:
                    raise DomainError(
                        "NONDEPENDENCY_BINDING",
                        "绑定只能读取执行图中的上游节点，不能以导航或布局绕过依赖。",
                    )
                if not binding.get("output"):
                    raise DomainError("OUTPUT_BINDING", "上游绑定缺少 output 名称。")
            elif (
                not binding.get("record_id")
                or not isinstance(binding.get("version"), int)
                or isinstance(binding.get("version"), bool)
                or binding["version"] < 1
            ):
                raise DomainError(
                    "VERSION_REFERENCE_REQUIRED", "直接输入必须固定 record_id 与 version。"
                )
    return report, seconds


def start_workflow(store, actor, body, *, prepare_only=False):
    require_write(actor)
    payload = body.model_dump() if isinstance(body, BaseModel) else body
    key = "execution:" + payload["idempotency_key"]

    def prepare():
        workflow = _require_record(store, actor, payload["workflow_id"], "Workflow")
        if workflow["version"] != payload["expected_revision"]:
            raise DomainError("REVISION_CONFLICT", "流程已被修订，请核对后重新启动。", 409)
        spec = execution_spec(workflow["data"])
        report, seconds = _validate_execution(spec)
        for node in spec["nodes"]:
            for binding in node.get("input_bindings", {}).values():
                if binding.get("record_id"):
                    _require_record(
                        store,
                        actor,
                        binding["record_id"],
                        version=binding["version"],
                        project=workflow["project_id"],
                    )
        graph = store.create(
            "RunGraph",
            {
                "status": "running",
                "branch_kind": "live",
                "graph_revision": 1,
                "workflow_version": ref(workflow),
                "nodes": spec["nodes"],
                "edges": spec["edges"],
                "compiled_order": report["order"],
                "compiled_ir_hash": digest(spec),
            },
            tenant_id=actor.tenant_id,
            project_id=workflow["project_id"],
            actor_id=actor.actor_id,
            refs=[ref(workflow)],
            reason="冻结已验证本地执行图",
        )
        checkpoint = {
            "nodes": {
                n["id"]: {"status": "pending", "attempts": 0, "outputs": {}} for n in spec["nodes"]
            },
            "completed_nodes": [],
            "steps_started": 0,
        }
        run = store.create(
            "Run",
            {
                "state": "待执行",
                "status": "queued",
                "execution_kind": "local_dag",
                "workflow_version": ref(workflow),
                "run_graph_ref": graph["id"],
                "compiled_order": report["order"],
                "spec_hash": digest(spec),
                "checkpoint": checkpoint,
                "attempt_id": 1,
                "max_steps": spec["max_steps"],
                "max_attempts": spec["max_attempts"],
                "budget_limit": spec["budget"],
                "paid_calls_allowed": False,
                "max_seconds": seconds,
                "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=seconds))
                .isoformat()
                .replace("+00:00", "Z"),
                "outcome_unknown": False,
                "worker_active": False,
                "output_refs": [],
            },
            tenant_id=actor.tenant_id,
            project_id=workflow["project_id"],
            actor_id=actor.actor_id,
            refs=[ref(workflow), ref(graph)],
            reason="保存工作流检查点与资源边界",
        )
        return {"run_id": run["id"]}

    prepared = store.idempotent(actor.tenant_id, key, "start:" + digest(key), payload, prepare)
    run = store.get(prepared["run_id"], actor.tenant_id)
    if prepare_only or prepared.get("idempotent_replay"):
        return {
            "run": run,
            "status": run["data"]["status"],
            "replayed": bool(prepared.get("idempotent_replay")),
            "boundary": "返回既有执行；不会因重发 HTTP 请求重复节点。",
        }
    return execute_run(store, actor, run["id"])


def _lease(store, actor, run_id, seconds, *, expected_revision=None):
    with store.transaction():
        if (
            expected_revision is not None
            and store.get(run_id, actor.tenant_id)["version"] != expected_revision
        ):
            raise DomainError(
                "REVISION_CONFLICT", "检查点在获取工作租约前已变化，请重新读取。", 409
            )
        row = store._local.conn.execute(
            "SELECT * FROM leases WHERE tenant_id=? AND resource_id=?", (actor.tenant_id, run_id)
        ).fetchone()
        stamp = now()
        if row and row["expires_at"] > stamp:
            raise DomainError("EXECUTION_BUSY", "运行已有工作进程持有租约。", 409)
        token = (row["fencing_token"] if row else 0) + 1
        owner = "worker_" + uuid.uuid4().hex
        expiry = (
            (datetime.now(timezone.utc) + timedelta(seconds=seconds + 30))
            .isoformat()
            .replace("+00:00", "Z")
        )
        store._local.conn.execute(
            "INSERT INTO leases VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,resource_id) DO UPDATE SET owner_id=excluded.owner_id,fencing_token=excluded.fencing_token,expires_at=excluded.expires_at",
            (actor.tenant_id, run_id, owner, token, expiry),
        )
        return owner, token


def _mutate(store, actor, run_id, token, fn, reason, refs=None):
    with store.transaction():
        lease = store._local.conn.execute(
            "SELECT * FROM leases WHERE tenant_id=? AND resource_id=?", (actor.tenant_id, run_id)
        ).fetchone()
        if not lease or lease["fencing_token"] != token or lease["expires_at"] <= now():
            raise DomainError("STALE_FENCE", "工作进程租约已经失效。", 409)
        current = store.get(run_id, actor.tenant_id)
        data = copy.deepcopy(current["data"])
        fn(data)
        return store.update(
            run_id,
            data,
            tenant_id=actor.tenant_id,
            expected_revision=current["version"],
            actor_id=actor.actor_id,
            refs=current["refs"] + (refs or []),
            reason=reason,
        )


def _bind_path(args, path, value):
    if any(
        part in ("__proto__", "prototype", "constructor")
        for part in path.replace("/", ".").split(".")
    ):
        raise DomainError("INPUT_PATH", "输入参数路径无效。")
    parts = path.strip("/").replace("/", ".").split(".")
    parent = args
    for part in parts[:-1]:
        if isinstance(parent, list):
            if not part.isdigit() or int(part) >= len(parent):
                raise DomainError("INPUT_PATH", "数组绑定路径不存在。")
            parent = parent[int(part)]
        elif isinstance(parent, dict):
            if part not in parent:
                parent[part] = {}
            parent = parent[part]
        else:
            raise DomainError("INPUT_PATH", "绑定路径不能进入标量。")
    if isinstance(parent, list):
        if not parts[-1].isdigit() or int(parts[-1]) >= len(parent):
            raise DomainError("INPUT_PATH", "数组绑定路径不存在。")
        parent[int(parts[-1])] = value
    elif isinstance(parent, dict):
        parent[parts[-1]] = value
    else:
        raise DomainError("INPUT_PATH", "绑定路径不可写。")


def _inputs(store, actor, run, node):
    args = copy.deepcopy(node.get("operation_args", {}))
    input_refs = []
    for path, binding in node.get("input_bindings", {}).items():
        if binding.get("node_id"):
            upstream = run["data"]["checkpoint"]["nodes"][binding["node_id"]]
            if upstream["status"] != "completed":
                raise DomainError("UPSTREAM_UNAVAILABLE", "上游节点尚无可用真实产物。", 409)
            output = upstream["outputs"].get(binding["output"])
            if isinstance(output, list):
                index = binding.get("index")
                if (
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or not 0 <= index < len(output)
                ):
                    raise DomainError("OUTPUT_INDEX", "集合产物需要明确有效 index。")
                output = output[index]
            if not isinstance(output, dict) or not output.get("id"):
                raise DomainError("OUTPUT_UNAVAILABLE", "所选上游产物不存在。", 409)
            record = _require_record(
                store, actor, output["id"], version=output["version"], project=run["project_id"]
            )
        else:
            record = _require_record(
                store,
                actor,
                binding["record_id"],
                version=binding["version"],
                project=run["project_id"],
            )
        latest = store.get(record["id"], actor.tenant_id)
        if latest["data"].get("status") in ("revoked", "deleted", "restricted"):
            raise DomainError("INPUT_REVOKED", "运行输入已撤权或受限。", 403)
        if latest["version"] != record["version"]:
            raise DomainError(
                "INPUT_VERSION_CHANGED",
                "输入当前版本已改变，冻结工作流不会静默读取新版本；请显式修订输入绑定。",
                409,
            )
        mode = binding.get("value", "id")
        if mode not in ("id", "ref", "data", "version"):
            raise DomainError("BINDING_MODE", "绑定值只能是 id/ref/data/version。")
        value = {
            "id": record["id"],
            "ref": ref(record),
            "data": record["data"],
            "version": record["version"],
        }[mode]
        _bind_path(args, path, value)
        input_refs.append(ref(record))
        # Pin revisions for media arguments that consume an object id.
        if path == "asset_id":
            args.setdefault("expected_revision", record["version"])
        if path == "timeline_id":
            args.setdefault("expected_revision", record["version"])
    args["project_id"] = run["project_id"]
    return args, input_refs


def _call_handler(store, actor, run, node, args, input_refs):
    from . import media

    spec = HANDLERS[node["route_id"]]
    op = spec["operation"]
    from jsonschema import Draft202012Validator

    errors = list(Draft202012Validator(OPERATION_SCHEMAS[op]).iter_errors(args))
    if errors:
        raise DomainError(
            "OPERATION_ARGUMENTS",
            "实际执行参数不符合该处理器的明确合同。",
            details=[
                {"path": ".".join(map(str, e.path)), "message": e.message} for e in errors[:20]
            ],
        )
    if op == "generate_media":
        from . import generation

        expected_kind = "image" if node["feature_id"] == "G02" else "video"
        if generation.profile(args["profile_id"]).get("output_kind", "video") != expected_kind:
            raise DomainError("GENERATOR_KIND_MISMATCH", "配置输出类型与所选生成路线不同。")
        reference = args.get("reference") or {}
        body = generation.Generate(
            project_id=run["project_id"],
            profile_id=args["profile_id"],
            prompt=args["prompt"],
            seed=args["seed"],
            idempotency_key="dag:" + digest([run["id"], node["id"]]),
            reference_asset_id=reference.get("id"),
            reference_asset_version=reference.get("version"),
            reference_time_seconds=args.get("reference_time_seconds"),
        )
        result = generation.generate(body, None, actor)["data"]
        job = result["job"]
        if job["data"]["stage"] not in generation.FINAL:
            result = generation.refresh(job["id"], None, actor)["data"]
            job = result["job"]
        if job["data"]["stage"] not in generation.FINAL:
            raise DomainError(
                "GENERATION_WAITING",
                "等待原生成任务的可核验产物。",
                409,
                {"job": ref(job), "stage": job["data"]["stage"]},
            )
        if job["data"]["stage"] != "ready" or not result.get("asset"):
            raise DomainError(
                "GENERATION_INCOMPLETE",
                "生成任务未交付可用素材。",
                409,
                {"job": ref(job), "issue": job["data"].get("issue")},
            )
        media.resolve_media(store, actor, result["asset"]["id"], project_id=run["project_id"])
    elif op == "import_otio":
        from .timeline_exchange import ImportInput, import_timeline

        arguments = {k: v for k, v in args.items() if k != "project_id"}
        body = ImportInput(**arguments, request_id="dag:" + digest([run["id"], node["id"]]))
        result = {"timeline": import_timeline(run["project_id"], body, None, actor)["data"]}
    elif op == "transform":
        if not args.get("asset_id"):
            raise DomainError("INPUT_REQUIRED", "变换节点需要 asset_id 版本绑定。")
        source = store.get(args["asset_id"], actor.tenant_id)
        if source["kind"] not in ("Asset", "AssetVersion"):
            raise DomainError(
                "ROUTE_MEDIA_KIND",
                "此工作流路线产出 AssetVersion；独立音频变换请使用音频处理接口。",
            )
        result = media.transform_asset(store, actor, args["asset_id"], args)
    elif op == "narration":
        from .narration import produce
        from .selection import ChosenVersion

        def pinned(key):
            return ChosenVersion(id=args[key]["id"], version=args[key]["version"])

        with store.transaction():
            result = produce(
                actor, run["project_id"], pinned("script"), pinned("approval"), args.get("tempo", 1)
            )
    elif op == "extract_audio":
        if not args.get("asset_id"):
            raise DomainError("INPUT_REQUIRED", "提音节点需要 asset_id 版本绑定。")
        result = media.extract_audio(store, actor, args["asset_id"], args)
    elif op == "detect_shots":
        if not args.get("asset_id"):
            raise DomainError("INPUT_REQUIRED", "切镜头节点需要 asset_id 版本绑定。")
        result = media.detect_shots(store, actor, args["asset_id"], args.get("threshold", 0.3))
    elif op in ("script", "timeline"):
        data = args.get("data", args.get("timeline") if op == "timeline" else None)
        if not isinstance(data, dict):
            raise DomainError("INPUT_REQUIRED", "节点需要显式 data 对象。")
        data = copy.deepcopy(data)
        if op == "timeline":
            data["status"] = "draft"
            data["locked"] = False
        kind = "Script" if op == "script" else "Timeline"
        record = store.create(
            kind,
            data,
            tenant_id=actor.tenant_id,
            project_id=run["project_id"],
            actor_id=actor.actor_id,
            refs=input_refs,
            reason="本地工作流显式数据节点",
        )
        result = {op: record}
    elif op == "lock_timeline":
        timeline = _require_record(
            store, actor, args.get("timeline_id"), "Timeline", project=run["project_id"]
        )
        if (
            args.get("expected_revision") != timeline["version"]
            or args.get("confirmed_hash") != timeline["hash"]
        ):
            raise DomainError(
                "EXPLICIT_REVIEW_REQUIRED",
                "锁版节点必须有用户确认的精确 revision 与 confirmed_hash。动态新时间线须先审阅后继续。",
                409,
            )
        if not (timeline["data"].get("clips") or timeline["data"].get("clip_uses")):
            raise DomainError("EMPTY_TIMELINE", "空时间线不能锁版。")
        updated = store.update(
            timeline["id"],
            {
                **timeline["data"],
                "locked": True,
                "status": "locked",
                "locked_by": actor.actor_id,
                "locked_at": now(),
            },
            tenant_id=actor.tenant_id,
            expected_revision=timeline["version"],
            actor_id=actor.actor_id,
            reason="用户已确认精确hash的工作流锁版",
        )
        result = {"timeline": updated}
    elif op == "render":
        result = media.render_timeline(store, actor, args)
    elif op == "package":
        package, _ = media.create_package(store, actor, args.get("artifact_id"))
        result = {"package": package}
    else:
        raise DomainError("HANDLER_UNAVAILABLE", "本地处理器未实现。", 501)
    outputs = {}
    for key, kind in spec["outputs"].items():
        value = result.get(key)
        if isinstance(value, dict) and value.get("kind") == kind:
            outputs[key] = ref(value)
        elif (
            key == "shots"
            and isinstance(value, list)
            and len(value) > 0
            and all(isinstance(x, dict) and x.get("kind") == kind for x in value)
        ):
            outputs[key] = [ref(x) for x in value]
    if not set(spec["outputs"]) <= set(outputs):
        raise DomainError("OUTPUT_CONTRACT", "处理器没有返回合同中的完整真实产物。", 500)
    return outputs, result


def execute_run(store, actor, run_id):
    run = _require_record(store, actor, run_id, "Run")
    if run["data"].get("execution_kind") != "local_dag":
        raise DomainError("EXECUTION_KIND", "该运行不是本地 DAG。")
    workflow = _require_record(
        store,
        actor,
        run["data"]["workflow_version"]["id"],
        "Workflow",
        version=run["data"]["workflow_version"]["version"],
    )
    spec = execution_spec(workflow["data"])
    report, _ = _validate_execution(spec)
    if digest(spec) != run["data"]["spec_hash"]:
        raise DomainError("FROZEN_GRAPH_CHANGED", "冻结图摘要不匹配。", 409)
    with store.connection() as conn:
        active = conn.execute(
            "SELECT expires_at FROM leases WHERE tenant_id=? AND resource_id=?",
            (actor.tenant_id, run_id),
        ).fetchone()
    if active and active["expires_at"] > now():
        raise DomainError("EXECUTION_BUSY", "运行已有有效工作进程租约，不可并发恢复或核对。", 409)
    if run["data"].get("outcome_unknown"):
        raise DomainError("RECONCILIATION_REQUIRED", "旧尝试结果未知，先核对再恢复。", 409)
    if any(n["status"] == "running" for n in run["data"]["checkpoint"]["nodes"].values()):
        # A process disappeared after its start checkpoint. Never assume its
        # side effects did not happen merely because no response was recorded.
        data = {
            **run["data"],
            "state": "待核对",
            "status": "awaiting_reconciliation",
            "outcome_unknown": True,
            "worker_active": False,
        }
        run = store.update(
            run_id,
            data,
            tenant_id=actor.tenant_id,
            expected_revision=run["version"],
            actor_id=actor.actor_id,
            reason="发现未结节点，禁止自动重做",
        )
        return {"run": run, "status": "awaiting_reconciliation"}
    deadline = datetime.fromisoformat(run["data"]["deadline_at"].replace("Z", "+00:00"))
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise DomainError(
            "DEADLINE_EXCEEDED", "本次冻结运行时限已到；请保留成果并修订新任务。", 409
        )
    owner, token = _lease(store, actor, run_id, remaining, expected_revision=run["version"])
    try:
        run = _mutate(
            store,
            actor,
            run_id,
            token,
            lambda d: d.update(
                {
                    "state": "执行中",
                    "status": "running",
                    "worker_active": True,
                    "worker_id": owner,
                    "fencing_token": token,
                    "control_request": None,
                }
            ),
            "获取本地运行租约",
        )
        nodes = {n["id"]: n for n in spec["nodes"]}
        incoming = {
            nid: [
                e.get("source", e.get("from"))
                for e in spec["edges"]
                if e.get("target", e.get("to")) == nid
            ]
            for nid in nodes
        }
        for nid in report["order"]:
            run = store.get(run_id, actor.tenant_id)
            state = run["data"]
            checkpoint = state["checkpoint"]
            step = checkpoint["nodes"][nid]
            node = nodes[nid]
            if state.get("control_request") in ("SuspendTask", "RequestCancel") or state.get(
                "state"
            ) in ("停止中", "暂停", "取消"):
                break
            if step["status"] == "completed":
                continue
            polling = step["status"] == "waiting_receipt"
            problem = None
            if not actor_still_authorized(actor):
                problem = {"code": "AUTHORIZATION_REVOKED"}
            elif datetime.now(timezone.utc) >= deadline:
                problem = {"code": "DEADLINE_EXCEEDED"}
            elif not polling and checkpoint["steps_started"] >= state["max_steps"]:
                problem = {"code": "STEP_LIMIT"}
            elif not polling and step["attempts"] >= state["max_attempts"]:
                problem = {"code": "ATTEMPT_LIMIT"}
            elif any(checkpoint["nodes"][up]["status"] != "completed" for up in incoming[nid]):
                problem = {"code": "UPSTREAM_UNAVAILABLE"}
            elif node["route_id"] not in HANDLERS:
                problem = {"code": "HANDLER_UNAVAILABLE", "route_id": node["route_id"]}
            if problem:

                def block(d):
                    d["checkpoint"]["nodes"][nid].update({"status": "blocked", "error": problem})

                _mutate(store, actor, run_id, token, block, "节点准入未通过；独立分支继续")
                continue
            try:
                args, input_refs = _inputs(store, actor, run, node)
            except DomainError as exc:

                def blocked(d):
                    d["checkpoint"]["nodes"][nid].update(
                        {"status": "blocked", "error": {"code": exc.code, "message": exc.message}}
                    )

                _mutate(store, actor, run_id, token, blocked, "输入版本或来源未通过准入")
                continue

            def start(d):
                s = d["checkpoint"]["nodes"][nid]
                s.update(
                    {
                        "status": "running",
                        "attempts": s["attempts"] + (0 if polling else 1),
                        "started_at": now(),
                        "input_refs": input_refs,
                        "arguments_hash": digest(args),
                        "outputs": {},
                        "error": None,
                    }
                )
                d["checkpoint"]["steps_started"] += 0 if polling else 1

            run = _mutate(store, actor, run_id, token, start, "节点开始前持久保存检查点")
            try:
                with store.execution_context(
                    run_id,
                    nid,
                    token,
                    actor.tenant_id,
                    actor=actor,
                    deadline_at=run["data"]["deadline_at"],
                ):
                    outputs, result = _call_handler(store, actor, run, node, args, input_refs)
                refs = []
                for output in outputs.values():
                    refs.extend(output if isinstance(output, list) else [output])

                def complete(d):
                    d["checkpoint"]["nodes"][nid].update(
                        {
                            "status": "completed",
                            "outputs": outputs,
                            "finished_at": now(),
                            "boundary": HANDLERS[node["route_id"]]["boundary"],
                        }
                    )
                    if nid not in d["checkpoint"]["completed_nodes"]:
                        d["checkpoint"]["completed_nodes"].append(nid)
                    d["output_refs"].extend(x for x in refs if x not in d["output_refs"])

                _mutate(store, actor, run_id, token, complete, "节点真实产物通过本地输出合同", refs)
            except DomainError as exc:
                if exc.code == "GENERATION_WAITING":

                    def park(d):
                        d["checkpoint"]["nodes"][nid].update(
                            status="waiting_receipt", error=None, provider_receipt=exc.details
                        )

                    _mutate(
                        store, actor, run_id, token, park, "生成任务已记录，下次恢复仅核对原任务"
                    )
                    continue
                unknown = exc.code in ("STALE_FENCE", "OUTPUT_CONTRACT")

                def fail(d):
                    d["checkpoint"]["nodes"][nid].update(
                        {
                            "status": "unknown" if unknown else "failed",
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            },
                            "finished_at": now(),
                        }
                    )
                    if unknown:
                        d.update(
                            {
                                "outcome_unknown": True,
                                "state": "待核对",
                                "status": "awaiting_reconciliation",
                            }
                        )

                _mutate(store, actor, run_id, token, fail, "节点未通过；保留证据，不伪报成功")
                if unknown:
                    break
            except Exception:

                def unknown(d):
                    d["checkpoint"]["nodes"][nid].update(
                        {"status": "unknown", "error": {"code": "WORKER_INTERRUPTION"}}
                    )
                    d.update(
                        {
                            "outcome_unknown": True,
                            "state": "待核对",
                            "status": "awaiting_reconciliation",
                        }
                    )

                _mutate(store, actor, run_id, token, unknown, "异常中断，需要核对本地已提交事实")
                break

        def finalize(d):
            d["worker_active"] = False
            if d.get("outcome_unknown"):
                d.update({"state": "待核对", "status": "awaiting_reconciliation"})
                return
            if d.get("control_request") == "RequestCancel":
                d.update({"state": "取消", "status": "cancelled", "stopped_at": now()})
                return
            if d.get("control_request") == "SuspendTask":
                d.update({"state": "暂停", "status": "paused", "stopped_at": now()})
                return
            if any(n["status"] == "waiting_receipt" for n in d["checkpoint"]["nodes"].values()):
                d.update({"state": "待核对", "status": "waiting_receipt"})
                return
            complete = all(n["status"] == "completed" for n in d["checkpoint"]["nodes"].values())
            d.update(
                {
                    "state": "完成" if complete else "需补充",
                    "status": "completed" if complete else "blocked",
                    "finished_at": now(),
                }
            )

        run = _mutate(
            store, actor, run_id, token, finalize, "工作流达到安全检查点，确认工作进程停止"
        )
        graph = store.get(run["data"]["run_graph_ref"], actor.tenant_id)
        gs = {
            "completed": "completed",
            "paused": "paused",
            "awaiting_reconciliation": "awaiting_reconciliation",
            "cancelled": "withdrawn",
        }.get(run["data"]["status"], "parked")
        store.update(
            graph["id"],
            {**graph["data"], "status": gs, "checkpoint_run_ref": ref(run)},
            tenant_id=actor.tenant_id,
            expected_revision=graph["version"],
            actor_id=actor.actor_id,
            reason="真实执行状态投影",
        )
        return {
            "run": run,
            "status": run["data"]["status"],
            "replayed": False,
            "outputs": run["data"]["output_refs"],
            "boundary": "完成仅指选定本地操作及其声明输出，不代表外部平台发布或商品语义质检已完成。",
        }
    finally:
        with store.transaction():
            store._local.conn.execute(
                "UPDATE leases SET expires_at=? WHERE tenant_id=? AND resource_id=? AND fencing_token=?",
                (now(), actor.tenant_id, run_id, token),
            )


@router.get("/capabilities")
def get_capabilities(request: Request, actor: Actor = Depends(current_actor)):
    return ok(
        {
            "handlers": {
                route: {
                    **spec,
                    "route_id": route,
                    "handler": spec["operation"],
                    "operation_schema": OPERATION_SCHEMAS[spec["operation"]],
                    "input_bindings": INPUT_SCHEMAS[spec["operation"]],
                }
                for route, spec in HANDLERS.items()
            },
            "paid_calls_allowed": False,
            "max_nodes": 1000,
            "resume_policy": "completed nodes retained; unknown node must reconcile",
            "cancellation": "finish current local safe boundary before stop",
        },
        request,
    )


@router.post("/start")
def start_endpoint(body: StartInput, request: Request, actor: Actor = Depends(current_actor)):
    return ok(start_workflow(get_store(), actor, body), request)


@router.post("/{run_id}/resume")
def resume_endpoint(
    run_id: str, body: ResumeInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    store = get_store()
    run = _require_record(store, actor, run_id, "Run")
    if run["version"] != body.expected_revision:
        raise DomainError("REVISION_CONFLICT", "运行状态已改变。", 409)
    if run["data"].get("status") in ("completed", "cancelled"):
        raise DomainError(
            "TERMINAL_RUN", "已完成或取消的运行不能重启；可新建保留产物的新流程。", 409
        )
    return ok(execute_run(store, actor, run_id), request)


@router.get("/{run_id}/recovery")
def recovery(run_id: str, request: Request, actor: Actor = Depends(current_actor)):
    store = get_store()
    run = _require_record(store, actor, run_id, "Run")
    candidates = [
        r
        for r in store.list(actor.tenant_id, project_id=run["project_id"], q=run_id, limit=2000)
        if r["data"].get("execution_context", {}).get("run_id") == run_id
    ]
    return ok(
        {
            "run": run,
            "candidates": candidates,
            "boundary": "候选事实不是自动完成证据，核对适用产物与实际字节后再显式接受。",
        },
        request,
    )


def _verify_delivery_package(store, actor, package, project_id):
    """An orphan package is usable only while its exact bytes and rights hold."""
    import zipfile
    from .media import blob_path, hash_file, check_current_media_rights, resolve_media

    current = _require_record(store, actor, package["id"], "DeliveryPackage", project=project_id)
    if package["data"].get("status") != "available" or current["data"].get("status") != "available":
        raise DomainError("PACKAGE_NOT_AVAILABLE", "交付包当前不可用，不能恢复为完成。", 409)
    if current["version"] != package["version"] or current["hash"] != package["hash"]:
        raise DomainError(
            "PACKAGE_VERSION_CHANGED", "候选交付包已被修订，不能用旧版本绕过当前状态。", 409
        )

    def linked(field, kind):
        rid = package["data"].get(field)
        pinned = next((r for r in package["refs"] if r["id"] == rid and r["kind"] == kind), None)
        if not pinned:
            raise DomainError(
                "PACKAGE_CONTRACT", "交付包缺少实际冻结的产物或字节引用。", 409, {"field": field}
            )
        return _require_record(store, actor, rid, kind, pinned["version"], project_id)

    artifact = linked("artifact_id", "Artifact")
    blob = linked("blob_location_id", "BlobLocation")
    current_blob = _require_record(store, actor, blob["id"], "BlobLocation", project=project_id)
    if (
        blob["data"].get("availability") != "available"
        or current_blob["data"].get("availability") != "available"
    ):
        raise DomainError("PACKAGE_NOT_AVAILABLE", "交付包字节落点当前不可用。", 409)
    check_current_media_rights(store, actor, package, purpose="export")
    current_artifact, _, _ = resolve_media(
        store, actor, artifact["id"], project_id=project_id, purpose="export"
    )
    if (
        current_artifact["version"] != artifact["version"]
        or current_artifact["hash"] != artifact["hash"]
    ):
        raise DomainError(
            "PACKAGE_ARTIFACT_CHANGED", "交付包绑定的冻结成片已变化，需要重新核对。", 409
        )
    checksum = package["data"].get("checksum")
    blob_checksum = blob["data"].get("checksum") or blob["data"].get("content_hash")
    size = blob["data"].get("size_bytes")
    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or checksum != blob_checksum
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        raise DomainError("PACKAGE_CONTRACT", "交付包缺少一致的 SHA-256 与真实字节大小。", 409)
    try:
        path = blob_path(blob, actor.tenant_id)
        if path.stat().st_size != size or hash_file(path) != checksum:
            raise DomainError(
                "PACKAGE_CORRUPTION", "交付包字节大小或 SHA-256 与冻结记录不一致。", 409
            )
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            declared = package["data"].get("included")
            required = {
                "video.mp4",
                "timeline.json",
                "render-manifest.json",
                "time-map.json",
                "README.txt",
            }
            if (
                not isinstance(declared, list)
                or any(not isinstance(name, str) for name in declared)
                or len(set(names)) != len(names)
                or set(names) != set(declared)
                or not required <= set(names)
            ):
                raise DomainError(
                    "PACKAGE_CONTRACT", "ZIP 内容与交付清单不一致，不能宣称可交付。", 409
                )
            if archive.testzip() is not None:
                raise DomainError("PACKAGE_CORRUPTION", "ZIP 内部条目校验失败。", 409)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise DomainError(
            "PACKAGE_UNVERIFIABLE", "交付包无法读取或验证，保持待核对状态。", 409
        ) from exc


@router.post("/{run_id}/reconcile")
def reconcile(
    run_id: str, body: ReconcileInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    store = get_store()
    with store.transaction():
        run = _require_record(store, actor, run_id, "Run")
        if run["version"] != body.expected_revision:
            raise DomainError("REVISION_CONFLICT", "运行版本改变。", 409)
        lease = store._local.conn.execute(
            "SELECT * FROM leases WHERE tenant_id=? AND resource_id=?", (actor.tenant_id, run_id)
        ).fetchone()
        if lease and lease["expires_at"] > now():
            raise DomainError("WORKER_STILL_RUNNING", "原工作进程租约仍有效，不得人工覆盖。", 409)
        if not run["data"].get("outcome_unknown"):
            raise DomainError("NO_UNKNOWN_OUTCOME", "本运行没有待核对结果。")
        workflow = store.get(
            run["data"]["workflow_version"]["id"],
            actor.tenant_id,
            run["data"]["workflow_version"]["version"],
        )
        node = next(
            (n for n in execution_spec(workflow["data"])["nodes"] if n["id"] == body.node_id), None
        )
        if not node or node["route_id"] not in HANDLERS:
            raise DomainError("UNKNOWN_NODE", "节点不支持本地结果核对。")
        step = run["data"]["checkpoint"]["nodes"][body.node_id]
        if step["status"] not in ("running", "unknown"):
            raise DomainError("NODE_NOT_UNKNOWN", "节点没有待核对的尝试。")
        contract = HANDLERS[node["route_id"]]["outputs"]
        outputs = {}
        refs = []
        for key, value in body.outputs.items():
            if key not in contract:
                raise DomainError("OUTPUT_CONTRACT", "核对产物名不属于处理器合同。")
            if key == "shots":
                if not isinstance(value, list) or not value:
                    raise DomainError(
                        "OUTPUT_CARDINALITY", "镜头产物必须是至少一个真实 Shot 的集合。"
                    )
            elif not isinstance(value, dict):
                raise DomainError(
                    "OUTPUT_CARDINALITY", "该产物必须是单个真实对象，不能以空数组或集合替代。"
                )
            values = value if isinstance(value, list) else [value]
            verified = []
            for item in values:
                rec = _require_record(
                    store,
                    actor,
                    item.get("id"),
                    contract[key],
                    item.get("version"),
                    run["project_id"],
                )
                ctx = rec["data"].get("execution_context", {})
                if ctx.get("run_id") != run_id or ctx.get("node_id") != body.node_id:
                    raise DomainError("RESULT_PROVENANCE", "产物不属于这个运行节点。", 409)
                if rec["data"].get("status") in ("running", "failed", "rejected"):
                    raise DomainError("RESULT_NOT_VALID", "未通过处理的候选不能恢复为完成。", 409)
                if (
                    rec["kind"] in ("TransformRun", "RenderJob")
                    and rec["data"].get("status") != "succeeded"
                ):
                    raise DomainError("RESULT_NOT_VALID", "任务须有真实成功状态才能核对完成。", 409)
                if (
                    rec["kind"] in ("AssetVersion", "Audio")
                    and rec["data"].get("status") != "available"
                ):
                    raise DomainError("RESULT_NOT_VALID", "媒体版本尚不可用。", 409)
                if rec["kind"] in ("AssetVersion", "Audio", "Artifact"):
                    from .media import resolve_media

                    resolve_media(store, actor, rec["id"], project_id=run["project_id"])
                if rec["kind"] == "DeliveryPackage":
                    _verify_delivery_package(store, actor, rec, run["project_id"])
                verified.append(ref(rec))
                refs.append(ref(rec))
            outputs[key] = verified if isinstance(value, list) else verified[0]
        if not outputs or not set(contract) <= set(outputs):
            raise DomainError("OUTPUT_CONTRACT", "须核对该处理器完整的必需输出。")
        data = copy.deepcopy(run["data"])
        data["checkpoint"]["nodes"][body.node_id].update(
            {
                "status": "completed",
                "outputs": outputs,
                "reconciled_at": now(),
                "reconciliation_reason": body.reason,
            }
        )
        data["checkpoint"]["completed_nodes"].append(body.node_id)
        data["output_refs"].extend(refs)
        data["outcome_unknown"] = any(
            n["status"] in ("running", "unknown") for n in data["checkpoint"]["nodes"].values()
        )
        data.update(
            {
                "state": "待核对" if data["outcome_unknown"] else "需补充",
                "status": "awaiting_reconciliation" if data["outcome_unknown"] else "blocked",
                "worker_active": False,
            }
        )
        updated = store.update(
            run_id,
            data,
            tenant_id=actor.tenant_id,
            expected_revision=run["version"],
            actor_id=actor.actor_id,
            refs=run["refs"] + refs,
            reason=body.reason,
        )
        return ok({"run": updated, "status": updated["data"]["status"]}, request)
