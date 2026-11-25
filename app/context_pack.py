"""H04-R3. Human-pinned, bounded evidence for a local task or workflow run."""

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import current_dependency_problem, record, ref, revise, save
from .selection import ChosenVersion, Label, Note, SelectionBody, on_request
from .store import canonical, get_store
from .edit_memory import router as memory_router
from .graph_revision import router as graph_revision_router

router = APIRouter(prefix="/execution", tags=["context"])
router.include_router(memory_router)
router.include_router(graph_revision_router)
EVIDENCE_FIELDS = {
    "Product": ("SKU", "quantity", "price", "currency", "market", "validity"),
    "DecisionRecord": ("name", "decision", "reason"),
}
TASK_FIELDS = (
    "goal",
    "success_criteria",
    "required_outputs",
    "stop_conditions",
    "locks",
    "hard_constraints",
    "authorization_requirements",
    "budget_caps",
    "deadline",
)
RUN_FIELDS = (
    "max_steps",
    "max_attempts",
    "budget_limit",
    "max_seconds",
    "deadline_at",
    "paid_calls_allowed",
    "outcome_unknown",
    "worker_active",
    "control_request",
    "control_reason",
    "locks",
    "stop_conditions",
)


class PackInput(SelectionBody):
    project_id: Label
    run: ChosenVersion
    request_id: Label
    reason: Note
    byte_limit: StrictInt = Field(default=16384, ge=512, le=131072)
    evidence: list[ChosenVersion] = Field(default_factory=list, max_length=24)


def selected(data, fields):
    return {key: data[key] for key in fields if key in data}


def read_inputs(actor, run, choices):
    project = run["project_id"]
    inputs = [record(actor, project, "Project")]
    for pointer in run["refs"]:
        if pointer["kind"] in {"Workflow", "TaskContract"}:
            if any(
                item["id"] == pointer["id"] and item["version"] == pointer["version"]
                for item in inputs
            ):
                continue
            inputs.append(
                record(actor, pointer["id"], pointer["kind"], pointer["version"], project)
            )
    required_task = run["data"].get("task_contract_ref")
    if required_task and not any(
        item["kind"] == "TaskContract" and item["id"] == required_task for item in inputs
    ):
        raise DomainError("TASK_EVIDENCE_MISSING", "任务缺少可核对的合同版本。", 409)
    scope = []
    for choice in choices:
        item = record(actor, choice.id, project=project)
        if item["kind"] not in EVIDENCE_FIELDS or item["version"] != choice.version:
            raise DomainError("EVIDENCE_MISMATCH", "请选择本项目当前的商品或决定版本。", 409)
        if issue := current_dependency_problem(item):
            raise DomainError(issue["code"], issue["message"], 409)
        scope.append(item)
    if len({item["id"] for item in scope}) != len(scope):
        raise DomainError("DUPLICATE_EVIDENCE", "同一证据只能固定一次。", 422)
    return inputs, scope


def bound_inputs(actor, run, authorities):
    """Freeze references from the task, workflow and completed or pending node attempts."""
    state = run["data"]
    groups = [state.get("input_refs", [])]
    for item in authorities:
        data = item["data"]
        if item["kind"] == "TaskContract":
            groups.append(data.get("input_bindings", []))
        if item["kind"] == "Workflow":
            groups.extend(
                node.get("input_bindings", {})
                for node in (data.get("DAG") or data).get("nodes", [])
            )
    groups.extend(
        node.get("input_refs", [])
        for node in (state.get("checkpoint") or {}).get("nodes", {}).values()
    )
    pins = {}
    pending = list(groups)
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            rid = value.get("record_id") or value.get("id")
            if rid is None:
                pending.extend(value.values())
                continue
            version = value.get("version")
            if not isinstance(rid, str) or type(version) is not int or version < 1:
                raise DomainError(
                    "BOUND_VERSION_MISSING", "必需输入缺少明确版本，请补取原始任务依据。", 409
                )
            if (rid, version) not in pins:
                item = record(actor, rid, value.get("kind"), version, run["project_id"])
                pins[rid, version] = ref(item)
    return [pins[key] for key in sorted(pins)]


def build_pack(actor, run, inputs, scope, limit):
    state = run["data"]
    nodes = (state.get("checkpoint") or {}).get("nodes", {})
    done = {
        key: value.get("outputs", {})
        for key, value in nodes.items()
        if value.get("status") == "completed"
    }
    pending = {
        key: selected(value, ("status", "attempts", "error", "input_refs"))
        for key, value in nodes.items()
        if value.get("status") != "completed"
    }
    calls = get_store()._local.conn.execute(
        """SELECT r.id, r.latest_version FROM records r JOIN revisions v
        ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
        WHERE r.tenant_id=? AND r.project_id=? AND r.kind='ToolCall'
        AND (json_extract(v.data_json,'$.run_id')=? OR json_extract(v.data_json,'$.execution_context.run_id')=?)
        ORDER BY r.id""",
        (actor.tenant_id, run["project_id"], run["id"], run["id"]),
    )
    status = state.get("status")
    if state.get("outcome_unknown"):
        action = "reconcile_side_effect"
    elif state.get("control_request") in {"RequestCancel", "SuspendTask"}:
        action = "inspect_stop_request"
    elif state.get("worker_active") or status == "running":
        action = "observe_worker"
    elif status in {"completed", "cancelled"}:
        action = "inspect_final_record"
    elif state.get("blockers") or pending:
        action = "inspect_blockers"
    else:
        action = "inspect_checkpoint"
    authorities = []
    goal = inputs[0]["data"].get("goals") or inputs[0]["data"].get("name", "")
    for item in inputs:
        data = item["data"]
        if item["kind"] == "TaskContract":
            fields = selected(data, TASK_FIELDS)
            goal = data.get("goal") or goal
        elif item["kind"] == "Workflow":
            fields = selected(data, ("guards", "budgets", "policy_version"))
            fields["node_locks"] = [
                {"node_id": n.get("id"), **selected(n, ("locks", "hard_constraints"))}
                for n in (data.get("DAG") or data).get("nodes", [])
                if n.get("locks") or n.get("hard_constraints")
            ]
        else:
            fields = selected(data, ("goals", "policy_version", "brand_rules"))
        authorities.append({"source": ref(item), "facts": fields})
    refs = [ref(row) for row in scope]
    packet = {
        "goal": goal,
        "source_run": ref(run),
        "bound_inputs": bound_inputs(actor, run, inputs),
        "status": status,
        "authorities": authorities,
        "limits_and_stops": selected(state, RUN_FIELDS),
        "completed": done,
        "output_refs": state.get("output_refs", []),
        "unresolved_nodes": pending,
        "blockers": state.get("blockers", []),
        "calls": [
            {"id": row["id"], "version": row["latest_version"], "kind": "ToolCall"} for row in calls
        ],
        "next_read_or_review": action,
        "evidence_refs": refs,
        "omitted_details": list(refs),
        "data_excerpts": [],
        "grants_execution": False,
    }

    def byte_count():
        return len(canonical(packet).encode("utf-8"))

    if byte_count() > limit:
        raise DomainError(
            "CONTEXT_WINDOW_TOO_SMALL",
            "窗口不足以保留约束、未决事项和来源，请扩大窗口或拆分任务。",
            422,
            {"required_bytes": byte_count(), "limit": limit},
        )
    for item, pointer in zip(scope, refs):
        excerpt = {
            "source": pointer,
            "fields": selected(item["data"], EVIDENCE_FIELDS[item["kind"]]),
            "role": "evidence_data",
        }
        packet["omitted_details"].remove(pointer)
        packet["data_excerpts"].append(excerpt)
        if byte_count() > limit:
            packet["data_excerpts"].pop()
            packet["omitted_details"].append(pointer)
    return packet, byte_count()


@router.post("/context-packs")
def pin_context(body: PackInput, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        run = record(actor, body.run.id, "Run", project=body.project_id)
        if run["version"] != body.run.version:
            raise DomainError("STALE_RUN", "运行已更新，请先读取最新检查点。", 409)
        if not (
            run["data"].get("execution_kind") == "local_dag" or run["data"].get("task_contract_ref")
        ):
            raise DomainError(
                "CONTEXT_RUN_UNSUPPORTED", "当前支持本地工作流和持有任务合同的运行。", 422
            )
        inputs, scope = read_inputs(actor, run, body.evidence)
        packet, size = build_pack(actor, run, inputs, scope, body.byte_limit)
        audit = save(
            actor,
            "Audit",
            body.project_id,
            {
                "feature_id": "H04",
                "route_id": "H04-R3",
                "action": "pin_context",
                "actor": actor.actor_id,
                "reason": body.reason,
                "context_packet": packet,
                "payload_bytes": size,
                "byte_limit": body.byte_limit,
            },
            [
                ref(run),
                *[ref(x) for x in inputs + scope],
                *packet["bound_inputs"],
                *packet["calls"],
            ],
        )
        prior = run["data"].get("context_ref")
        refs = [x for x in run["refs"] if not isinstance(prior, dict) or x["id"] != prior.get("id")]
        updated = revise(
            actor, run, {"context_ref": ref(audit)}, "人工固定最小证据包", refs + [ref(audit)]
        )
        return {"audit": audit, "run": updated}

    return ok(on_request("pin-run-context", body, actor, apply), request)


@router.get("/{run_id}/context-pack")
def read_context(run_id: str, request: Request, actor: Actor = Depends(current_actor)):
    run = record(actor, run_id, "Run")
    pin = run["data"].get("context_ref")
    if not isinstance(pin, dict) or pin.get("kind") != "Audit":
        raise DomainError("NO_CONTEXT_PACK", "该任务尚未固定证据包。", 404)
    audit = record(actor, pin["id"], "Audit", pin["version"], run["project_id"])
    packet = audit["data"].get("context_packet", {})
    if (
        audit["data"].get("action") != "pin_context"
        or packet.get("source_run", {}).get("id") != run_id
    ):
        raise DomainError("INVALID_CONTEXT_REF", "审计证据与本任务不匹配。", 409)
    changes = []
    seen = set()
    for p in [
        *(row["source"] for row in packet["authorities"]),
        *packet["evidence_refs"],
        *packet["calls"],
        *packet.get("bound_inputs", []),
    ]:
        key = (p["id"], p["version"])
        if key in seen:
            continue
        seen.add(key)
        current = record(actor, p["id"], p["kind"], project=run["project_id"])
        issue = current_dependency_problem(current)
        if current["version"] != p["version"] or issue:
            changes.append(
                {
                    "pinned": p,
                    "current": ref(current),
                    "reason": issue["code"] if issue else "VERSION_CHANGED",
                }
            )
    return ok(
        {
            "audit": audit,
            "source_changes": changes,
            "run_advanced": run["version"] != packet["source_run"]["version"] + 1,
        },
        request,
    )
