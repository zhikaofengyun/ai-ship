"""Attach each output to a typed input of an existing project workflow."""

from . import batch_queue as queue
from . import execution, generation
from .commands import dispatch
from .errors import DomainError
from .media import check_revision
from .production import record, ref


def blueprint(actor, recipe):
    tail = recipe.workflow_tail
    workflow = record(actor, tail.id, "Workflow", project=recipe.project_id)
    check_revision(workflow, tail.version)
    graph = execution.execution_spec(workflow["data"])
    execution._validate_execution(graph)
    node = next((n for n in graph["nodes"] if n["id"] == tail.node_id), None)
    handler = execution.HANDLERS.get(node["route_id"]) if node else None
    port = (
        execution.INPUT_SCHEMAS.get(handler["operation"], {}).get(tail.input_path)
        if handler
        else None
    )
    kind = "Artifact" if recipe.output == "render" else "Timeline"
    if (
        not port
        or kind not in port["kinds"]
        or node.get("input_bindings", {}).get(tail.input_path, {}).get("node_id")
    ):
        raise DomainError(
            "BATCH_WORKFLOW_PORT", "请选择与当前批次产物类型兼容、且未连接上游节点的工作流输入。"
        )
    return workflow, graph, node, port


def prepare(store, actor, recipe, item, product):
    workflow, graph, node, port = blueprint(actor, recipe)
    node.setdefault("input_bindings", {})[recipe.workflow_tail.input_path] = {
        "record_id": product["id"],
        "version": product["version"],
        "value": port["value"],
    }
    with store.transaction():
        bound = store.create(
            "Workflow",
            {
                "name": f"{recipe.name} · {item['ordinal']}",
                "DAG": graph,
                "batch_id": item["batch"],
                "batch_ordinal": item["ordinal"],
            },
            tenant_id=actor.tenant_id,
            project_id=recipe.project_id,
            actor_id=actor.actor_id,
            refs=[ref(workflow), ref(product)],
            reason="将单项的精确产物版本绑定到冻结的流程端口",
        )
        result = execution.start_workflow(
            store,
            actor,
            {
                "workflow_id": bound["id"],
                "expected_revision": bound["version"],
                "idempotency_key": queue.resource(item["batch"], item["ordinal"]) + ":tail",
            },
            prepare_only=True,
        )
        run = result["run"]
        queue.update(store, item, workflow_run_id=run["id"])
    return run


def cancel(store, actor, item, run):
    still_waiting = False
    for step in run["data"]["checkpoint"]["nodes"].values():
        pin = (step.get("provider_receipt") or {}).get("job")
        if step["status"] == "waiting_receipt" and pin:
            receipt = generation.cancel(pin["id"], None, actor)["data"]["job"]
            still_waiting |= receipt["data"]["stage"] not in generation.FINAL
    if still_waiting:
        queue.update(store, item, status="waiting", delay=5)
        return
    if run["data"]["status"] != "cancelled":
        key = f'{item["batch"]}:{item["ordinal"]}:cancel:{run["version"]}'
        result = dispatch(
            store,
            actor,
            {
                "command_type": "RequestCancel",
                "parameters": {},
                "trace_id": key,
                "idempotency_key": key,
                "command_id": key,
                "target_id": run["id"],
                "expected_revision": run["version"],
                "actor_id": actor.actor_id,
                "actor_role": actor.role,
                "project_id": run["project_id"],
                "reason": "用户取消了所属生产批次",
            },
        )
        if result["run"]["data"]["status"] != "cancelled":
            queue.update(
                store,
                item,
                status="failed",
                error={
                    "code": "WORKFLOW_RECONCILE",
                    "message": "子流程仍有未知副作用，请核对原运行。",
                },
            )
            return
    queue.update(store, item, status="cancelled")


def finish(store, actor, recipe, item, product):
    if not recipe.workflow_tail:
        queue.update(store, item, status="succeeded")
        return
    run = (
        record(actor, item["workflow_run_id"], "Run", project=recipe.project_id)
        if item["workflow_run_id"]
        else None
    )
    with store.connection() as db:
        control = queue.get_batch(db, actor.tenant_id, item["batch"])["control"]
    if run and run["data"]["status"] == "completed":
        queue.update(store, item, status="succeeded")
        return
    if control == "cancelled":
        if run:
            cancel(store, actor, item, run)
        else:
            queue.update(store, item, status="cancelled")
        return
    if control == "paused":
        queue.update(store, item, status="waiting")
        return
    if not run:
        run = prepare(store, actor, recipe, item, product)
    try:
        result = execution.execute_run(store, actor, run["id"])
    except DomainError as exc:
        if exc.code != "EXECUTION_BUSY":
            raise
        queue.update(store, item, status="waiting", delay=5)
        return
    status = result["status"]
    if status == "waiting_receipt":
        queue.update(store, item, status="waiting", delay=3)
    elif status in {"completed", "cancelled"}:
        queue.update(store, item, status="succeeded" if status == "completed" else "cancelled")
    else:
        failures = [
            (k, v.get("error"))
            for k, v in result["run"]["data"]["checkpoint"]["nodes"].items()
            if v.get("error")
        ]
        error = failures[0][1] if failures else {}
        queue.update(
            store,
            item,
            status="failed",
            error={
                "code": error.get("code", "WORKFLOW_BLOCKED"),
                "message": error.get("message")
                or f"子流程状态为 {status}，请查看原运行的节点检查点。",
            },
        )
