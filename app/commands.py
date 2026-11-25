"""Explicit local commands; unsupported production work stays blocked."""

from datetime import datetime, timezone, timedelta
import copy
import uuid
from .auth import require_write, require_approval
from .catalog import FEATURES, MACHINES
from .asset_controls import manual_asset_data
from .errors import DomainError
from .store import now, new_id, digest
from .workflow import validate_controls, validate_workflow
from .budget import project_commitments, money_amount, project_cap

# This table describes actual handlers, not the 178-item design catalogue.
LOCAL_CREATE = {
    "CreateProjectVersion": "Project",
    "BindProductFacts": "Product",
    "CreateFactBoundScript": "Script",
    "SaveScriptVersion": "Script",
    "CommitPlanVersion": "Plan",
    "ImportTimelineIR": "Timeline",
    "CreateTaskContract": "TaskContract",
    "SaveWorkflowVersion": "Workflow",
    "SaveModelProfile": "ModelProfile",
    "SaveAgentRole": "AgentRole",
    "SaveAgentDeployment": "AgentDeployment",
    "SaveView": "View",
    "RecordDecision": "DecisionRecord",
    "CreateEvaluationSuite": "EvaluationSuite",
    "CreateExperiment": "Experiment",
}
LOCAL_UPDATE = {
    "AmendTaskContract": "TaskContract",
    "UpdateProjectVersion": "Project",
    "UpdateScriptVersion": "Script",
    "UpdateTimeline": "Timeline",
    "UpdateWorkflow": "Workflow",
    "UpdateView": "View",
    "UpdatePlan": "Plan",
}


def command_capabilities():
    return {
        "local_commands": sorted(
            set(LOCAL_CREATE)
            | set(LOCAL_UPDATE)
            | {
                "ConfigureFeature",
                "ApplyChangeSet",
                "LockTimelineRevision",
                "FreezeProjectBaseline",
                "SuspendTask",
                "ResumeTask",
                "RequestCancel",
                "ReserveBudget",
                "ReleaseBudget",
                "AcquireLease",
                "ReleaseLease",
                "StartWorkflowRun",
                "ResolveUnknownOutcome",
            }
        ),
        "external_execution": "provider adapters require credential/capability admission; generic commands never report external success",
        "task_machine": "task",
        "workflow_compiler": "bounded acyclic graph",
    }


def _record_data(parameters):
    value = parameters.get("data", parameters.get("record", parameters))
    if not isinstance(value, dict):
        raise DomainError("INVALID_PARAMETERS", "命令数据必须为对象。")
    return value


def dispatch(store, actor, command):
    require_write(actor)
    if command["actor_id"] != actor.actor_id or command["actor_role"] != actor.role:
        raise DomainError("ACTOR_MISMATCH", "命令身份必须与认证身份一致。", 403)
    project = command.get("project_id")
    if project:
        rec = store.get(project, actor.tenant_id)
        if rec["kind"] != "Project":
            raise DomainError("NOT_PROJECT", "project_id 不是项目。")
    refs = command.get("input_version_refs", [])
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("id") or not ref.get("version"):
            raise DomainError("VERSION_REFERENCE_REQUIRED", "执行输入必须固定对象 ID 与版本。")
        store.get(ref["id"], actor.tenant_id, ref["version"])
    if command.get("policy_version"):
        pref = command["policy_version"]
        if not isinstance(pref, dict) or not pref.get("id") or not pref.get("version"):
            raise DomainError("POLICY_REFERENCE", "策略必须绑定固定版本。")
        policy = store.get(pref["id"], actor.tenant_id, pref["version"])
        if policy["kind"] != "Policy":
            raise DomainError("POLICY_REFERENCE", "策略引用类型错误。")
    if command["command_type"] == "StartWorkflowRun":
        from .execution import start_workflow

        target = _target(store, actor, command, "Workflow")
        return start_workflow(
            store,
            actor,
            {
                "workflow_id": target["id"],
                "expected_revision": target["version"],
                "idempotency_key": command["idempotency_key"],
            },
        )
    return store.idempotent(
        actor.tenant_id,
        command["idempotency_key"],
        command["command_id"],
        command,
        lambda: _dispatch(store, actor, command),
    )


def _dispatch(store, actor, command):
    typ = command["command_type"]
    params = command["parameters"]
    project = command.get("project_id")
    args = {
        "tenant_id": actor.tenant_id,
        "project_id": project,
        "actor_id": actor.actor_id,
        "trace_id": command["trace_id"],
        "reason": command["reason"],
    }
    refs = command.get("input_version_refs", [])

    def create(kind, data, more_refs=None):
        return store.create(kind, data, refs=refs + (more_refs or []), **args)

    if typ in LOCAL_CREATE:
        kind = LOCAL_CREATE[typ]
        data = manual_asset_data(kind, copy.deepcopy(_record_data(params)))
        if kind == "Workflow":
            spec = data.get("DAG", data)
            report = validate_workflow(spec)
            if not report["valid"]:
                raise DomainError("WORKFLOW_INVALID", "执行图未通过编译。", details=report)
            data["validation_report"] = report
            data["status"] = "validated"
            data["compiler_version"] = report["compiler_version"]
        if kind == "ModelProfile":
            data["health_status"] = "unprobed"
        if kind == "Project":
            args["project_id"] = None
        record = create(kind, data)
        return {
            "status": "completed",
            "output_refs": [{"id": record["id"], "version": record["version"], "kind": kind}],
            "record": record,
            "omissions": [],
            "unresolved": [],
            "version": 1,
            "trace_id": command["trace_id"],
        }
    if typ in LOCAL_UPDATE:
        old = _target(store, actor, command, LOCAL_UPDATE[typ])
        data = manual_asset_data(old["kind"], copy.deepcopy(_record_data(params)), old)
        if old["kind"] == "Workflow":
            report = validate_workflow(data.get("DAG", data))
            if not report["valid"]:
                raise DomainError("WORKFLOW_INVALID", "执行图未通过编译。", details=report)
            data["validation_report"] = report
        result = store.update(
            old["id"],
            data,
            tenant_id=actor.tenant_id,
            expected_revision=command["expected_revision"],
            refs=refs or None,
            actor_id=actor.actor_id,
            reason=command["reason"],
            trace_id=command["trace_id"],
        )
        return {
            "status": "completed",
            "record": result,
            "output_refs": [
                {"id": result["id"], "version": result["version"], "kind": result["kind"]}
            ],
            "omissions": [],
            "unresolved": [],
            "trace_id": command["trace_id"],
        }
    if typ in ("ConfigureFeature", "RequestFeature"):
        fid = params.get("feature_id")
        f = FEATURES.get(fid)
        if not f:
            raise DomainError("UNKNOWN_FEATURE", "功能不在批准的 HTML 中。")
        route = params.get("route_id")
        if route not in {r["id"] for r in f["routes"]}:
            raise DomainError("UNKNOWN_ROUTE", "路线不属于该功能。")
        controls = params.get("controls", params.get("parameters", {}))
        errors = validate_controls(fid, controls)
        if errors:
            raise DomainError("CONTROL_VALIDATION", "参数不符合该功能控件合同。", details=errors)
        task = create(
            "TaskContract",
            {
                "goal": f["goal"],
                "name": f["label"],
                "feature_id": fid,
                "route_id": route,
                "parameters": controls,
                "success_criteria": f.get("checks", []),
                "input_bindings": refs,
                "required_outputs": f.get("operation", {}).get("outputs", []),
                "status": "draft",
                "hard_constraints": f.get("hard", []),
                "budget_caps": params.get("budget_caps", {}),
                "authorization_requirements": params.get("authorization_requirements", []),
            },
        )
        blockers = [
            {
                "code": "DEDICATED_HANDLER_REQUIRED",
                "message": "功能合同已保存。请从对应业务界面执行真实处理；通用配置请求不会自动完成领域工作。",
            }
        ]
        run = create(
            "Run",
            {
                "state": "需补充",
                "feature_id": fid,
                "route_id": route,
                "task_contract_ref": task["id"],
                "attempt_id": 1,
                "status": "blocked",
                "blockers": blockers,
                "output_refs": [],
                "input_refs": refs,
                "outcome_unknown": False,
            },
            [{"id": task["id"], "version": task["version"], "kind": "TaskContract"}],
        )
        return {
            "status": "configured",
            "task": task,
            "run": run,
            "output_refs": [{"id": task["id"], "version": task["version"], "kind": "TaskContract"}],
            "omissions": [],
            "unresolved": blockers,
            "trace_id": command["trace_id"],
        }
    if typ == "FreezeProjectBaseline":
        old = _target(store, actor, command, "Project")
        data = copy.deepcopy(old["data"])
        missing = [k for k in ("name", "goals", "language", "market") if not data.get(k)]
        if missing:
            raise DomainError("INCOMPLETE_BRIEF", "冻结前需补齐目标与适用范围。", details=missing)
        data["status"] = "frozen"
        data["frozen_at"] = now()
        data["frozen_by"] = actor.actor_id
        result = store.update(
            old["id"],
            data,
            tenant_id=actor.tenant_id,
            expected_revision=command["expected_revision"],
            actor_id=actor.actor_id,
            reason=command["reason"],
            trace_id=command["trace_id"],
        )
        return {
            "status": "completed",
            "record": result,
            "output_refs": [{"id": result["id"], "version": result["version"]}],
            "unresolved": [],
        }
    if typ == "LockTimelineRevision":
        old = _target(store, actor, command, "Timeline")
        if params.get("confirmed_hash") is not None and params["confirmed_hash"] != old["hash"]:
            raise DomainError("REVISION_CONFLICT", "用户确认的时间线 hash 与当前版本不一致。", 409)
        if not (old["data"].get("clip_uses") or old["data"].get("clips")):
            raise DomainError("EMPTY_TIMELINE", "没有实际片段的时间线不能锁版。")
        data = {
            **old["data"],
            "status": "locked",
            "locked": True,
            "locked_at": now(),
            "locked_by": actor.actor_id,
        }
        result = store.update(
            old["id"],
            data,
            tenant_id=actor.tenant_id,
            expected_revision=command["expected_revision"],
            actor_id=actor.actor_id,
            reason=command["reason"],
            trace_id=command["trace_id"],
        )
        return {
            "status": "completed",
            "record": result,
            "output_refs": [{"id": result["id"], "version": result["version"]}],
            "unresolved": [],
        }
    if typ == "ApplyChangeSet":
        return apply_changeset(store, actor, command)
    if typ in ("SuspendTask", "ResumeTask", "RequestCancel", "ResolveUnknownOutcome"):
        old = _target(store, actor, command, "Run")
        data = copy.deepcopy(old["data"])
        state = data.get("state", "待校验")
        unknown = data.get("outcome_unknown", False)
        if typ == "SuspendTask":
            target = "停止中" if state == "执行中" else "暂停"
            if unknown:
                target = "待核对"
        elif typ == "ResumeTask":
            target = "待核对" if unknown else "待校验"
            if data.get("worker_active"):
                raise DomainError(
                    "WORKER_STILL_RUNNING", "工作进程尚未确认停止，不能新建尝试。", 409
                )
        elif typ == "RequestCancel":
            target = "停止中" if state == "执行中" or data.get("worker_active") else "取消"
            if unknown:
                target = "待核对"
        else:
            if not unknown:
                raise DomainError("NO_UNKNOWN_OUTCOME", "该任务没有待核对副作用。")
            # Only a specific provider reconciliation adapter may resolve this;
            # a generic caller-supplied success boolean is not authoritative.
            raise DomainError(
                "RECONCILIATION_REQUIRED",
                "请由原供应商适配器或带证据的人工回执核对结果，不能通用重置未知状态。",
                409,
            )
        if target != state and not any(
            e["from"] == state and e["to"] == target for e in MACHINES["task"]["edges"]
        ):
            raise DomainError("INVALID_TRANSITION", f"不能从 {state} 迁移到 {target}。", 409)
        data.update(
            {
                "state": target,
                "status": (
                    "awaiting_reconciliation"
                    if unknown
                    else (
                        "cancelled"
                        if target == "取消"
                        else (
                            "paused"
                            if target == "暂停"
                            else "stopping" if target == "停止中" else "pending"
                        )
                    )
                ),
                "control_request": typ,
                "control_reason": command["reason"],
            }
        )
        result = store.update(
            old["id"],
            data,
            tenant_id=actor.tenant_id,
            expected_revision=command["expected_revision"],
            actor_id=actor.actor_id,
            reason=command["reason"],
            trace_id=command["trace_id"],
        )
        return {
            "status": "accepted",
            "run": result,
            "output_refs": [{"id": result["id"], "version": result["version"]}],
            "unresolved": [{"code": "UNKNOWN_OUTCOME"}] if unknown else [],
        }
    if typ == "ReserveBudget":
        if not project:
            raise DomainError("PROJECT_REQUIRED", "预算预留必须绑定明确项目。")
        amount = money_amount(params.get("amount"))
        currency = params.get("currency", "USD")
        limit = money_amount(params.get("limit"), "limit")
        cap = project_cap(store.get(project, actor.tenant_id)["data"], currency)
        if cap is not None:
            limit = min(limit, cap)
        committed, unpriced = project_commitments(
            store, actor.tenant_id, project, currency, "COST_RECONCILIATION_REQUIRED"
        )
        if committed + amount > limit:
            raise DomainError(
                "BUDGET_EXCEEDED",
                "费用预留将超过预算。",
                409,
                {
                    "committed": float(committed),
                    "requested": float(amount),
                    "limit": float(limit),
                    "currency": currency,
                },
            )
        cost = create(
            "Cost",
            {
                "reservation_id": new_id("reservation"),
                "reservation_kind": "manual_budget_reservation",
                "side_effect_status": "not_sent",
                "reserved": float(amount),
                "estimated": float(amount),
                "actual": None,
                "calculated_charge": None,
                "currency": currency,
                "limits": float(limit),
                "operation": params.get("operation", "manual"),
                "status": "reserved",
                "budget_scope": "priced_monetary_costs",
                "unpriced_cost_refs": unpriced,
            },
        )
        return {
            "status": "completed",
            "record": cost,
            "remaining": float(limit - committed - amount),
            "budget_scope": "priced_monetary_costs",
            "unpriced_cost_refs": unpriced,
            "output_refs": [{"id": cost["id"], "version": cost["version"]}],
        }
    if typ == "ReleaseBudget":
        old = _target(store, actor, command, "Cost")
        data = old["data"]
        if data.get("actual") is not None or data.get("calculated_charge") is not None:
            raise DomainError("ACTUAL_COST", "已发生或已计量费用不能通过释放预留消除。", 409)
        if (
            data.get("reservation_kind") != "manual_budget_reservation"
            or data.get("side_effect_status") != "not_sent"
            or data.get("status") != "reserved"
            or data.get("provider")
            or data.get("reconciliation_required")
        ):
            raise DomainError(
                "COST_RECONCILIATION_REQUIRED",
                "只能释放明确尚未发出的手工预留；供应商费用、未知结果与历史计量须先核对。",
                409,
            )
        result = store.update(
            old["id"],
            {**data, "status": "released", "reserved": 0, "released_at": now()},
            tenant_id=actor.tenant_id,
            expected_revision=command["expected_revision"],
            actor_id=actor.actor_id,
            reason=command["reason"],
        )
        return {"status": "completed", "record": result}
    if typ in ("AcquireLease", "ReleaseLease"):
        return lease_command(store, actor, command)
    if typ == "StartWorkflowRun":
        old = _target(store, actor, command, "Workflow")
        spec = old["data"].get("DAG", old["data"])
        report = validate_workflow(spec)
        if not report["valid"]:
            raise DomainError("WORKFLOW_INVALID", "流程未通过编译。", details=report)
        # Admission graph is persisted before scheduling. No node is declared
        # completed just because its declared outputs are present in the catalog.
        blockers = [
            {
                "node": n["id"],
                "feature_id": n.get("feature_id"),
                "code": "CAPABILITY_ADMISSION_REQUIRED",
            }
            for n in spec["nodes"]
        ]
        graph = create(
            "RunGraph",
            {
                "graph_revision": 1,
                "workflow_version": {"id": old["id"], "version": old["version"]},
                "nodes": spec["nodes"],
                "edges": spec["edges"],
                "status": "parked",
                "branch_kind": "live",
                "blockers": blockers,
                "max_steps": spec["max_steps"],
                "max_attempts": spec["max_attempts"],
            },
            [{"id": old["id"], "version": old["version"], "kind": "Workflow"}],
        )
        run = create(
            "Run",
            {
                "state": "需补充",
                "status": "blocked",
                "workflow_version": {"id": old["id"], "version": old["version"]},
                "attempt_id": 1,
                "checkpoint": {"completed_nodes": [], "pending_nodes": report["order"]},
                "blockers": blockers,
                "run_graph_ref": graph["id"],
                "outcome_unknown": False,
            },
            [{"id": graph["id"], "version": graph["version"], "kind": "RunGraph"}],
        )
        return {
            "status": "blocked",
            "run": run,
            "graph": graph,
            "unresolved": blockers,
            "output_refs": [],
            "trace_id": command["trace_id"],
        }
    raise DomainError(
        "COMMAND_UNAVAILABLE",
        "此命令尚无可执行处理器；请使用已实现的专用接口。",
        501,
        {"command_type": typ, "capabilities": command_capabilities()},
    )


def _target(store, actor, command, kind):
    if not command.get("target_id") or command.get("expected_revision") is None:
        raise DomainError("TARGET_REQUIRED", "修改命令必须有对象与 expected_revision。", 428)
    record = store.get(command["target_id"], actor.tenant_id)
    if record["kind"] != kind:
        raise DomainError("TARGET_KIND", f"目标必须是 {kind}。")
    if record["version"] != command["expected_revision"]:
        raise DomainError(
            "REVISION_CONFLICT", "目标版本已改变。", 409, {"actual_revision": record["version"]}
        )
    if command.get("project_id") and record["project_id"] != command["project_id"]:
        raise DomainError("PROJECT_MISMATCH", "命令项目与目标不同。", 403)
    return record


def apply_changeset(store, actor, command):
    old = _target(store, actor, command, "Timeline")
    ops = command["parameters"].get("operations", [])
    if not isinstance(ops, list) or not ops or len(ops) > 500:
        raise DomainError("INVALID_PATCH", "需要 1–500 项明确局部变更。")
    data = copy.deepcopy(old["data"])
    changed = []
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in ("replace", "add", "remove"):
            raise DomainError("PATCH_OPERATION", "仅支持显式 add/replace/remove。")
        path = op.get("path", "")
        if not path.startswith("/") or path == "/":
            raise DomainError("PATCH_PATH", "补丁路径必须是非根 JSON Pointer。")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
        if parts[0] in ("tenant_id", "project_id", "locks", "status", "locked_at", "locked_by"):
            raise DomainError("PROTECTED_PATH", "该字段不能由局部补丁修改。")
        parent = data
        try:
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            key = parts[-1]
            if isinstance(parent, list):
                index = len(parent) if key == "-" else int(key)
                if op["op"] == "add":
                    if index < 0 or index > len(parent):
                        raise IndexError()
                    parent.insert(index, op["value"])
                elif op["op"] == "remove":
                    if index < 0:
                        raise IndexError()
                    parent.pop(index)
                else:
                    if index < 0:
                        raise IndexError()
                    parent[index] = op["value"]
            elif isinstance(parent, dict):
                if op["op"] != "add" and key not in parent:
                    raise KeyError()
                if op["op"] == "remove":
                    del parent[key]
                else:
                    parent[key] = op["value"]
            else:
                raise TypeError()
        except (KeyError, IndexError, ValueError, TypeError):
            raise DomainError(
                "PATCH_PATH", "补丁引用路径不存在或类型不符。", details={"path": path}
            )
        changed.append(".".join(parts))
    if data == old["data"]:
        raise DomainError("NO_CHANGE", "补丁没有产生实际变化。")
    data["status"] = "draft"
    data["locked"] = False
    data.pop("locked_at", None)
    data.pop("locked_by", None)
    impact = store.impact(
        actor.tenant_id, [{"id": old["id"], "version": old["version"], "fields": changed}]
    )
    result = store.update(
        old["id"],
        data,
        tenant_id=actor.tenant_id,
        expected_revision=command["expected_revision"],
        actor_id=actor.actor_id,
        reason=command["reason"],
        trace_id=command["trace_id"],
    )
    changeset = store.create(
        "ChangeSet",
        {
            "base_object_refs": [{"id": old["id"], "version": old["version"]}],
            "operations": ops,
            "expected_revisions": {old["id"]: old["version"]},
            "commit_status": "committed",
            "affected_artifact_refs": impact["affected"],
            "retained_refs": impact["retained"],
            "inverse_snapshot_ref": {"id": old["id"], "version": old["version"]},
            "result_ref": {"id": result["id"], "version": result["version"]},
            "lock_conflicts": [],
        },
        tenant_id=actor.tenant_id,
        project_id=old["project_id"],
        actor_id=actor.actor_id,
        refs=[
            {"id": old["id"], "version": old["version"]},
            {"id": result["id"], "version": result["version"]},
        ],
        reason=command["reason"],
        trace_id=command["trace_id"],
    )
    return {
        "status": "completed",
        "record": result,
        "change_set": changeset,
        "impact": impact,
        "output_refs": [
            {"id": result["id"], "version": result["version"]},
            {"id": changeset["id"], "version": 1},
        ],
    }


def lease_command(store, actor, command):
    p = command["parameters"]
    rid = p.get("resource_id")
    if not isinstance(rid, str) or not rid:
        raise DomainError("RESOURCE_REQUIRED", "租约必须绑定资源。")
    store.get(rid, actor.tenant_id)
    c = store._local.conn
    current = c.execute(
        "SELECT * FROM leases WHERE tenant_id=? AND resource_id=?", (actor.tenant_id, rid)
    ).fetchone()
    stamp = now()
    if command["command_type"] == "ReleaseLease":
        if not current:
            return {"status": "completed", "released": False}
        if (
            current["owner_id"] != actor.actor_id
            or p.get("fencing_token") != current["fencing_token"]
        ):
            raise DomainError("STALE_FENCE", "租约所有者或 fencing token 不匹配。", 409)
        c.execute(
            "UPDATE leases SET expires_at=? WHERE tenant_id=? AND resource_id=?",
            (stamp, actor.tenant_id, rid),
        )
        return {"status": "completed", "released": True, "fencing_token": current["fencing_token"]}
    ttl = p.get("ttl_seconds", 60)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 3600:
        raise DomainError("LEASE_TTL", "租约有效期必须为 1–3600 秒。")
    if current and current["expires_at"] > stamp:
        raise DomainError("LEASE_BUSY", "资源已有有效租约。", 409)
    token = (current["fencing_token"] if current else 0) + 1
    expiry = (
        (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    )
    c.execute(
        "INSERT INTO leases VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,resource_id) DO UPDATE SET owner_id=excluded.owner_id,fencing_token=excluded.fencing_token,expires_at=excluded.expires_at",
        (actor.tenant_id, rid, actor.actor_id, token, expiry),
    )
    store.append_event(
        tenant_id=actor.tenant_id,
        project_id=command.get("project_id"),
        event_type="lease.acquired",
        payload={"resource_id": rid, "fencing_token": token, "expires_at": expiry},
        actor_id=actor.actor_id,
        trace_id=command["trace_id"],
    )
    return {"status": "completed", "resource_id": rid, "fencing_token": token, "expires_at": expiry}
