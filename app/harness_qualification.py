"""Version a registered recipe after real, bounded runtime and failure-path checks."""

import copy
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import shutil
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from jsonschema import Draft202012Validator

from .api import ok
from .auth import Actor, current_actor, require_approval, require_write
from .errors import DomainError
from .execution import (
    HANDLERS,
    OPERATION_SCHEMAS,
    INPUT_SCHEMAS,
    _validate_execution,
    execution_spec,
    start_workflow,
)
from .media import probe_media, run_process
from .production import Body, digest, record, ref, save
from .selection import ChosenVersion, on_request
from .store import get_store

router = APIRouter(prefix="/harness", tags=["runtime qualification"])


@lru_cache(maxsize=16)
def binary_version(binary, executable, stamp):
    return run_process([executable, "-version"], 5)[0].splitlines()[0]


def runtime():
    versions = {"python": platform.python_version()}
    for binary in ("ffmpeg", "ffprobe"):
        exe = shutil.which(binary)
        try:
            versions[binary] = (
                binary_version(binary, exe, Path(exe).stat().st_mtime_ns) if exe else None
            )
        except (OSError, DomainError):
            versions[binary] = None
    for package in ("opentimelineio", "jsonschema", "pydantic"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    routes = [
        {
            "route_id": r,
            "operation": v["operation"],
            "contract_hash": digest(
                [v, OPERATION_SCHEMAS[v["operation"]], INPUT_SCHEMAS[v["operation"]]]
            ),
        }
        for r, v in sorted(HANDLERS.items())
    ]
    return {"versions": versions, "routes": routes, "fingerprint": digest([versions, routes])}


@router.get("/runtime")
def catalog(request: Request, actor: Actor = Depends(current_actor)):
    return ok(runtime(), request)


class Qualify(Body):
    request_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=180)
    project_id: str
    workflow: ChosenVersion


class Dispatch(Body):
    request_id: str = Field(min_length=1, max_length=120)
    project_id: str
    skill: ChosenVersion


def check_workflow(workflow, actor, report):
    spec = execution_spec(workflow["data"])
    _validate_execution(spec)
    for node in spec["nodes"]:
        if node["route_id"] not in HANDLERS:
            raise DomainError("UNREGISTERED_ROUTE", node["route_id"])
        op = HANDLERS[node["route_id"]]["operation"]
        schema = OPERATION_SCHEMAS[op]
        Draft202012Validator.check_schema(schema)
        bindings = node.get("input_bindings", {})
        for pin in bindings.values():
            if pin.get("record_id"):
                current = record(actor, pin["record_id"], project=workflow["project_id"])
                if current["version"] != pin["version"]:
                    raise DomainError("INPUT_VERSION_CHANGED", current["id"])
        args = {**node.get("operation_args", {}), "project_id": workflow["project_id"]}
        # Dynamic ports are materialized by the execution engine. Check unrelated
        # literal arguments now, and check the complete input at real dispatch.
        bound = {k.strip("/").replace("/", ".").split(".")[0] for k in bindings}
        if "asset_id" in bindings or "timeline_id" in bindings:
            bound.add("expected_revision")
        for name in bound:
            args.pop(name, None)
        static = copy.deepcopy(schema)
        static["required"] = [k for k in schema.get("required", []) if k not in bound]
        messages = list(Draft202012Validator(static).iter_errors(args))
        if messages:
            raise DomainError("OPERATION_ARGUMENTS", messages[0].message[:500])
        if op == "generate_media":
            from .generation import profile

            selected = profile(args.get("profile_id", ""))
            kind = "image" if node["route_id"] == "G02-R2" else "video"
            if selected.get("output_kind", "video") != kind:
                raise DomainError("GENERATOR_KIND_MISMATCH", "配置类型与生成路线不匹配。")
            report["profiles"][node["id"]] = digest(selected)
    return f"{len(spec['nodes'])} 个节点的合同及已绑定输入通过检查"


def rehearsal(spec):
    bad = copy.deepcopy(spec)
    bad["edges"] = [
        *bad["edges"],
        {"source": bad["nodes"][0]["id"], "target": bad["nodes"][0]["id"], "type": "control"},
    ]
    try:
        _validate_execution(bad)
    except DomainError as issue:
        if issue.code == "WORKFLOW_INVALID":
            return "自环输入被编译器拒绝"
        raise
    raise DomainError("FAULT_NOT_CAUGHT", "故障样例通过了编译器。")


def codec_trial():
    with TemporaryDirectory(prefix="ai-ship-codec-check-") as td:
        output = Path(td) / "probe.mp4"
        run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=64x64:r=25:d=0.2",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(output),
            ],
            10,
        )
        probe = probe_media(output)
        if not probe.get("has_video") or not 0 < probe.get("duration", 0) < 2:
            raise DomainError("CODEC_PROBE_FAILED", "输出文件未通过解码时长核验。")
        run_process(
            ["ffmpeg", "-nostdin", "-xerror", "-v", "error", "-i", str(output), "-f", "null", "-"],
            10,
        )
        return "64×64 H.264 已编码、探测并完整解码"


def stable_skill(body, actor):
    flow = record(actor, body.workflow.id, "Workflow", project=body.project_id)
    if flow["version"] != body.workflow.version:
        raise DomainError("REVISION_CONFLICT", "工作流已经修改，请重新核验。", 409)
    return flow


@router.post("/qualify")
def qualify(body: Qualify, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    store = get_store()
    flow = stable_skill(body, actor)
    manifest = runtime()
    report = {"profiles": {}}
    spec = execution_spec(flow["data"])
    tests = [("工作流合同", lambda: check_workflow(flow, actor, report))]
    if spec.get("nodes"):
        tests.append(("编译器故障注入", lambda: rehearsal(spec)))
    if any(
        HANDLERS.get(n.get("route_id"), {}).get("operation")
        in {"generate_media", "transform", "render", "extract_audio", "detect_shots", "narration"}
        for n in spec.get("nodes", [])
    ):
        tests.append(("媒体后端采样", codec_trial))
    checks = []
    for name, fn in tests:
        try:
            checks.append({"name": name, "passed": True, "detail": fn()})
        except (DomainError, OSError) as exc:
            checks.append(
                {"name": name, "passed": False, "detail": getattr(exc, "message", str(exc))[:600]}
            )

    def commit():
        stable_skill(body, actor)
        passed = all(c["passed"] for c in checks)
        suite = save(
            actor,
            "EvaluationSuite",
            body.project_id,
            {
                "name": body.name,
                "status": "frozen",
                "checks": checks,
                "runtime_manifest": manifest,
                "profile_hashes": report["profiles"],
                "passed": passed,
            },
            [ref(flow)],
        )
        skill = None
        if passed:
            skill = save(
                actor,
                "SkillVersion",
                body.project_id,
                {
                    "name": body.name,
                    "status": "validated",
                    "workflow_pin": ref(flow),
                    "suite_pin": ref(suite),
                    "runtime_fingerprint": manifest["fingerprint"],
                    "profile_hashes": report["profiles"],
                },
                [ref(flow), ref(suite)],
            )
            with store.connection() as db:
                db.execute(
                    "INSERT INTO qualified_workflows(tenant,project,skill,version,record_hash) VALUES(?,?,?,?,?)",
                    (
                        actor.tenant_id,
                        body.project_id,
                        skill["id"],
                        skill["version"],
                        skill["hash"],
                    ),
                )
        return {
            "suite": ref(suite),
            "skill": ref(skill) if skill else None,
            "passed": passed,
            "checks": checks,
        }

    return ok(on_request("qualify-workflow", body, actor, commit), request)


@router.post("/dispatch")
def dispatch(body: Dispatch, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store = get_store()
    skill = record(actor, body.skill.id, "SkillVersion", project=body.project_id)
    with store.connection() as db:
        accepted = db.execute(
            "SELECT 1 FROM qualified_workflows WHERE tenant=? AND project=? AND skill=? AND version=? AND record_hash=?",
            (actor.tenant_id, body.project_id, skill["id"], body.skill.version, skill["hash"]),
        ).fetchone()
    if (
        not accepted
        or skill["version"] != body.skill.version
        or skill["data"].get("status") != "validated"
    ):
        raise DomainError(
            "SKILL_QUALIFICATION_REQUIRED", "必须选择通过现场评测的精确配方版本。", 409
        )
    data = skill["data"]
    if runtime()["fingerprint"] != data["runtime_fingerprint"]:
        raise DomainError(
            "HARNESS_RUNTIME_CHANGED", "运行时或路线合同已变更，请重新核验配方。", 409
        )
    flow = record(actor, data["workflow_pin"]["id"], "Workflow", project=body.project_id)
    if flow["version"] != data["workflow_pin"]["version"]:
        raise DomainError("WORKFLOW_CHANGED", "配方对应的工作流版本已经更改。", 409)
    verified = {"profiles": {}}
    check_workflow(flow, actor, verified)
    if verified["profiles"] != data["profile_hashes"]:
        raise DomainError("GENERATOR_PROFILE_CHANGED", "本地生成器配置已变化。", 409)
    return ok(
        start_workflow(
            store,
            actor,
            {
                "workflow_id": flow["id"],
                "expected_revision": flow["version"],
                "idempotency_key": "qualified:"
                + digest([actor.actor_id, skill["id"], body.request_id]),
            },
        ),
        request,
    )
