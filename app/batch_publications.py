"""Connect produced batch items to versioned publication intents in bounded pages."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictInt

from . import batch_queue
from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import (
    Body,
    PublicationInput,
    digest,
    new_publication,
    publication_preflight,
    record,
)
from .store import get_store

router = APIRouter(prefix="/production/batches", tags=["batch publication"])


class PreparePage(Body):
    account_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=2200)
    channel: Literal["manual", "tiktok_inbox"] = "manual"
    privacy: Literal["private", "public"] | None = None
    after: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=25, ge=1, le=100)


def receipt_key(bid, ordinal, account, channel):
    return "batch-publication:" + digest([bid, ordinal, account, channel])


def predecessor_pin(pub):
    pin = pub["data"].get("predecessor_intent")
    if (
        isinstance(pin, dict)
        and pin.get("kind") == "Publication"
        and any(
            link["kind"] == "Publication"
            and link["id"] == pin.get("id")
            and link["version"] == pin.get("version")
            for link in pub["refs"]
        )
    ):
        return pin


def membership(actor, pub):
    """Old successors can precede the explicit batch fields. Trust only reciprocal links."""
    seen = set()
    while pub["id"] not in seen:
        seen.add(pub["id"])
        data = pub["data"]
        if data.get("batch_id") and type(data.get("batch_ordinal")) is int:
            return {"batch_id": data["batch_id"], "batch_ordinal": data["batch_ordinal"]}
        pin = predecessor_pin(pub)
        if not pin:
            return {}
        parent = record(actor, pin["id"], "Publication", project=pub["project_id"])
        if (parent["data"].get("successor_intent") or {}).get("id") != pub["id"]:
            return {}
        pub = parent
    raise DomainError("PUBLICATION_LINEAGE_INVALID", "批次发布迁移记录存在循环。", 409)


def current_intent(db, actor, project, bid, ordinal, account, channel):
    roots = db.execute(
        """SELECT r.id FROM revisions v CROSS JOIN records r ON r.tenant_id=v.tenant_id AND r.id=v.record_id
           WHERE v.tenant_id=? AND v.version=1 AND json_extract(v.data_json,'$.batch_ordinal') IS NOT NULL
             AND json_extract(v.data_json,'$.batch_id')=? AND json_extract(v.data_json,'$.batch_ordinal')=?
             AND json_extract(v.data_json,'$.channel')=? AND json_extract(v.data_json,'$.predecessor_intent') IS NULL
             AND r.kind='Publication' AND r.project_id=?""",
        (actor.tenant_id, bid, ordinal, channel, project),
    ).fetchall()
    head = None
    moved = False
    for row in roots:
        pub = record(actor, row["id"], "Publication", project=project)
        seen = set()
        while pub["data"].get("successor_intent"):
            seen.add(pub["id"])
            if pub["data"]["account_id"] == account:
                moved = True
            pin = pub["data"]["successor_intent"]
            if pin["id"] in seen:
                raise DomainError("PUBLICATION_LINEAGE_INVALID", "发布迁移记录存在循环。", 409)
            successor = record(actor, pin["id"], "Publication", project=project)
            parent = predecessor_pin(successor) or {}
            sd = successor["data"]
            if (
                parent.get("id") != pub["id"]
                or sd.get("batch_id", bid) != bid
                or sd.get("batch_ordinal", ordinal) != ordinal
                or sd.get("channel") != channel
            ):
                raise DomainError(
                    "PUBLICATION_LINEAGE_INVALID", "批次作品的发布迁移关系无法核实。", 409
                )
            pub = successor
        if pub["data"]["account_id"] == account:
            if head and head["id"] != pub["id"]:
                raise DomainError(
                    "BATCH_PUBLICATION_AMBIGUOUS", "同一账号已存在多条独立发布意图，请核对。", 409
                )
            head = pub
    return head, moved


def moved_result(ordinal):
    return {
        "ordinal": ordinal,
        "status": "blocked",
        "code": "BATCH_PUBLICATION_MOVED",
        "message": "此作品的发布意图已迁往其他账号。请在目标账号下查看。",
        "can_send": False,
        "can_handoff": False,
    }


def inspect_intent(actor, pub, ordinal):
    checks = publication_preflight(actor, pub)
    return {
        "ordinal": ordinal,
        "publication_id": pub["id"],
        "version": pub["version"],
        "can_handoff": checks["can_handoff"],
        "can_send": checks["can_send"] and pub["data"].get("inbox_stage") in (None, "init_refused"),
        "artifact_id": pub["data"]["artifact_id"],
        "problems": checks["problems"],
    }


@router.get("/{bid}/publications")
def existing_page(
    bid: str,
    account_id: str,
    channel: Literal["manual", "tiktok_inbox"],
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    actor: Actor = Depends(current_actor),
):
    store = get_store()
    batch_queue.initialize(store)
    with store.read_snapshot() as db:
        batch = batch_queue.get_batch(db, actor.tenant_id, bid)
        record(actor, account_id, "Account", project=batch["project"])
        items = []
        for i in range(after + 1, min(after + limit, batch["total"]) + 1):
            head, moved = current_intent(db, actor, batch["project"], bid, i, account_id, channel)
            if head:
                items.append({"status": "existing", **inspect_intent(actor, head, i)})
            elif moved:
                items.append(moved_result(i))
            else:
                items.append(
                    {"ordinal": i, "status": "unprepared", "message": "尚未建立发布草稿。"}
                )
    return ok({"items": items}, request)


@router.post("/{bid}/publications/prepare")
def prepare(bid: str, body: PreparePage, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    store = get_store()
    batch_queue.initialize(store)
    with store.read_snapshot() as db:
        batch = batch_queue.get_batch(db, actor.tenant_id, bid)
        if batch["output"] != "render":
            raise DomainError(
                "RENDER_REQUIRED", "此批次的交付目标是时间线，发布需要已编码的成片。", 409
            )
        run = record(actor, bid, "Run", version=1, project=batch["project"])
        account = record(actor, body.account_id, "Account", project=batch["project"])
        if body.channel == "tiktok_inbox" and account["data"].get("provider") != "tiktok":
            raise DomainError("TIKTOK_ACCOUNT_SCOPE", "收件箱交付需要 TikTok 账号。", 422)
        # Page across every ordinal; unfinished items remain visible to the operator.
        items = db.execute(
            "SELECT ordinal,status,artifact_id FROM production_items WHERE batch=? AND ordinal>? ORDER BY ordinal LIMIT ?",
            (bid, body.after, body.limit + 1),
        ).fetchall()
    results = []
    for item in items[: body.limit]:
        ordinal = item["ordinal"]
        result = {"ordinal": ordinal}
        if item["status"] != "succeeded" or not item["artifact_id"]:
            result.update(status="skipped", item_status=item["status"])
        else:
            title = body.title.replace("{n}", str(ordinal)).strip()
            if not title or len(title) > 2200:
                result.update(
                    status="blocked",
                    code="INVALID_TITLE",
                    message="展开序号后的标题须为 1 至 2200 字。",
                )
                results.append(result)
                continue
            settings = PublicationInput(
                project_id=batch["project"],
                artifact_id=item["artifact_id"],
                account_id=account["id"],
                title=title,
                privacy=body.privacy,
                channel=body.channel,
            )
            key = receipt_key(bid, ordinal, account["id"], body.channel)
            try:

                def create():
                    publication = new_publication(actor, settings, batch_run=run, ordinal=ordinal)
                    return {"publication_id": publication["id"]}

                # Serialize destination selection with operator migrations and other page requests.
                with store.transaction(), store.connection() as db:
                    head, moved = current_intent(
                        db, actor, batch["project"], bid, ordinal, account["id"], body.channel
                    )
                    status = "existing"
                    if not head and moved:
                        results.append(moved_result(ordinal))
                        continue
                    if not head or not head["data"].get("predecessor_intent"):
                        receipt = store.idempotent(
                            actor.tenant_id, key, key, settings.model_dump(), create
                        )
                        status = "existing" if receipt.get("idempotent_replay") else "created"
                        head = record(
                            actor,
                            receipt["publication_id"],
                            "Publication",
                            project=batch["project"],
                        )
                result.update(inspect_intent(actor, head, ordinal), status=status)
            except DomainError as exc:
                result.update(status="blocked", code=exc.code, message=exc.message)
        results.append(result)
    return ok(
        {
            "items": results,
            "next_after": results[-1]["ordinal"] if len(items) > body.limit else None,
            "external_request_sent": False,
        },
        request,
    )
