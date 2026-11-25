"""C10-R1. Replace a not-yet-started node contract at a durable local DAG checkpoint."""

import copy

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt, model_validator

from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_write
from .errors import DomainError
from .production import current_dependency_problem, record, ref, revise, save
from .selection import Label, Note, SelectionBody, on_request
from .store import digest, get_store, now

router = APIRouter()


class NodeChange(SelectionBody):
    project_id: Label
    request_id: Label
    expected_revision: StrictInt = Field(ge=1)
    base_spec_hash: str = Field(pattern="^[a-f0-9]{64}$")
    node_id: Label
    route_id: Label | None = None
    operation_args: dict | None = None
    input_bindings: dict | None = None
    reason: Note

    @model_validator(mode="after")
    def has_change(self):
        if all(getattr(self, k) is None for k in ("route_id", "operation_args", "input_bindings")):
            raise ValueError("请指定新的同功能路线、执行参数或输入绑定。")
        return self


@router.post("/{run_id}/revise-node")
def revise_node(
    run_id: str, body: NodeChange, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    from .execution import HANDLERS, OPERATION_SCHEMAS, _inputs, _validate_execution, execution_spec
    from jsonschema import Draft202012Validator

    def apply():
        if not actor_still_authorized(actor):
            raise DomainError("IDENTITY_REVOKED", "当前身份已撤回。", 403)
        store = get_store()
        run = record(actor, run_id, "Run", project=body.project_id)
        d = run["data"]
        if run["version"] != body.expected_revision or d.get("spec_hash") != body.base_spec_hash:
            raise DomainError("REVISION_CONFLICT", "运行版本或原执行图已变化。", 409)
        cp = d.get("checkpoint") or {}
        steps = cp.get("nodes", {})
        lease = store._local.conn.execute(
            "SELECT expires_at FROM leases WHERE tenant_id=? AND resource_id=?",
            (actor.tenant_id, run_id),
        ).fetchone()
        if (
            d.get("execution_kind") != "local_dag"
            or d.get("status") not in {"queued", "blocked", "paused"}
            or d.get("worker_active")
            or d.get("outcome_unknown")
            or (lease and lease[0] > now())
            or d.get("control_request") == "RequestCancel"
            or any(
                n.get("status") in {"running", "unknown", "waiting_receipt"}
                or (n.get("provider_receipt") and n.get("status") != "completed")
                for n in steps.values()
            )
        ):
            raise DomainError(
                "GRAPH_NOT_PARKED",
                "运行没有可修改的安全检查点，先暂停工作进程并结清未决回执。",
                409,
            )
        if d.get("deadline_at", "") <= now():
            raise DomainError("DEADLINE_EXCEEDED", "原任务时限已到，须另建任务。", 409)
        step = steps.get(body.node_id)
        if (
            not step
            or step.get("status") not in {"pending", "blocked"}
            or step.get("attempts") != 0
        ):
            raise DomainError("NODE_ALREADY_ATTEMPTED", "只能调整尚未尝试执行的节点。", 409)
        old_pin = d["workflow_version"]
        workflow = record(actor, old_pin["id"], "Workflow", old_pin["version"], body.project_id)
        graph = record(actor, d["run_graph_ref"], "RunGraph", project=body.project_id)
        spec = execution_spec(workflow["data"])
        if (
            digest(spec) != body.base_spec_hash
            or graph["data"].get("compiled_ir_hash") != body.base_spec_hash
        ):
            raise DomainError("FROZEN_GRAPH_CHANGED", "工作流或运行图摘要不一致。", 409)
        index = next((i for i, n in enumerate(spec["nodes"]) if n["id"] == body.node_id), None)
        if index is None:
            raise DomainError("NODE_CHECKPOINT_MISMATCH", "图节点与检查点不一致。", 409)
        node = spec["nodes"][index]
        if any(
            obj.get("locked") or obj.get("locks")
            for obj in (node, spec, graph["data"], workflow["data"], d)
        ):
            raise DomainError("NODE_LOCKED", "该节点或执行图有人工作业锁。", 409)
        route = body.route_id or node["route_id"]
        if HANDLERS.get(route, {}).get("feature_id") != node["feature_id"]:
            raise DomainError(
                "INVALID_REPLACEMENT_ROUTE", "只能使用同一功能已注册的执行路线。", 422
            )
        changes = {
            k: v
            for k, v in body.model_dump().items()
            if k in {"route_id", "operation_args", "input_bindings"}
            and v is not None
            and v != node.get(k)
        }
        if not changes:
            raise DomainError("NO_CHANGE", "节点合同与原图相同。")
        before = copy.deepcopy(node)
        node.update(changes)
        report, _ = _validate_execution(spec)
        refs = []
        for pin in node.get("input_bindings", {}).values():
            if pin.get("record_id"):
                source = record(actor, pin["record_id"], project=body.project_id)
                if source["version"] != pin["version"] or current_dependency_problem(source):
                    raise DomainError("INPUT_VERSION_CHANGED", "新节点输入版本已过期。", 409)
                refs.append(ref(source))
        ready = all(
            not binding.get("node_id") or steps[binding["node_id"]]["status"] == "completed"
            for binding in node.get("input_bindings", {}).values()
        )
        if ready:
            args, _ = _inputs(store, actor, run, node)
            schema = OPERATION_SCHEMAS[HANDLERS[route]["operation"]]
            problems = sorted(
                Draft202012Validator(schema).iter_errors(args), key=lambda error: str(error.path)
            )
            if problems:
                raise DomainError(
                    "OPERATION_ARGUMENTS",
                    "修改后的节点未通过实际执行输入合同。",
                    422,
                    [{"path": list(e.path), "message": e.message} for e in problems[:20]],
                )
        wd = copy.deepcopy(workflow["data"])
        if "DAG" in wd:
            wd["DAG"] = spec
        else:
            wd = spec
        new_workflow = save(
            actor, "Workflow", body.project_id, wd, [ref(workflow), *refs], body.reason
        )
        new_graph = save(
            actor,
            "RunGraph",
            body.project_id,
            {
                "status": "parked",
                "branch_kind": "live",
                "graph_revision": graph["data"]["graph_revision"] + 1,
                "workflow_version": ref(new_workflow),
                "nodes": spec["nodes"],
                "edges": spec["edges"],
                "compiled_order": report["order"],
                "compiled_ir_hash": digest(spec),
                "parent_graph": ref(graph),
                "checkpoint_run_ref": ref(run),
            },
            [ref(graph), ref(run), ref(new_workflow)],
            body.reason,
        )
        change_set = save(
            actor,
            "ChangeSet",
            body.project_id,
            {
                "feature_id": "C10",
                "route_id": "C10-R1",
                "commit_status": "committed",
                "base_object_refs": [ref(graph), ref(workflow), ref(run)],
                "expected_revisions": {run_id: body.expected_revision},
                "node_id": body.node_id,
                "argument_validation": "resolved" if ready else "deferred_until_upstream",
                "operations": [
                    {
                        "op": "replace" if k in before else "add",
                        "path": f"/nodes/{index}/{k}",
                        "value": v,
                    }
                    for k, v in changes.items()
                ],
                "inverse_snapshot_ref": ref(graph),
                "result_ref": ref(new_graph),
                "retained_refs": d.get("output_refs", []),
                "base_hash": body.base_spec_hash,
                "new_hash": digest(spec),
            },
            [ref(run), ref(workflow), ref(new_workflow), ref(graph), ref(new_graph)],
            body.reason,
        )
        pins = [p for p in run["refs"] if p["kind"] not in {"Workflow", "RunGraph"}]
        revised = revise(
            actor,
            run,
            {
                "workflow_version": ref(new_workflow),
                "run_graph_ref": new_graph["id"],
                "spec_hash": digest(spec),
                "compiled_order": report["order"],
                "graph_change": ref(change_set),
                "state": "暂停",
                "status": "paused",
                "control_request": "SuspendTask",
            },
            body.reason,
            [*pins, ref(new_workflow), ref(new_graph), ref(change_set)],
        )
        return {
            "run": revised,
            "workflow": new_workflow,
            "change_set": change_set,
            "graph": new_graph,
        }

    # Include the path in the scope, so the same key and request cannot replay another run.
    return ok(on_request("revise-node-" + run_id, body, actor, apply), request)
