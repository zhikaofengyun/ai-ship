"""Permission-limited manual comment observations and version-bound human classifications."""

from datetime import datetime, timezone
from typing import Literal
import json

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import Body, current_dependency_problem, digest, record, ref, timestamp
from .selection import ChosenVersion, Label, on_request
from .store import get_store

router = APIRouter(prefix="/production/comment-feedback", tags=["comment feedback"])
PREFIX = "feedback_"


class Observation(Body):
    request_id: Label
    project_id: Label
    account_id: Label
    platform_post_id: Label
    platform_comment_id: Label
    post_record_id: Label | None = None
    observed_at: str
    expected_revision: int = Field(ge=0)
    provenance: Label
    retention_basis: Label
    visibility: Literal["visible", "unavailable", "deleted"]
    excerpt: str | None = Field(default=None, max_length=4000)
    retain_until: str | None = None
    unavailable_reason: Label | None = None


def managed_item(actor, project, item_id, kind):
    if not item_id.startswith(PREFIX):
        raise DomainError("OBSERVATION_REQUIRED", "须选择通过评论采集流程保存的证据。")
    return record(actor, item_id, kind, project=project)


def vault(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS comment_excerpts(
        tenant_id TEXT NOT NULL, record_id TEXT NOT NULL, body_version INTEGER NOT NULL,
        expires TEXT NOT NULL, excerpt TEXT NOT NULL, PRIMARY KEY(tenant_id,record_id,body_version))"""
    )
    db.execute(
        "DELETE FROM comment_excerpts WHERE expires<=?", (datetime.now(timezone.utc).isoformat(),)
    )


def source_gate(actor, project, account_id):
    account = record(actor, account_id, "Account", project=project)
    if current_dependency_problem(account):
        raise DomainError("SOURCE_UNAVAILABLE", "来源账号已撤回或失效。", 409)
    return account


@router.post("/observe")
def observe(body: Observation, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        account = record(actor, body.account_id, "Account", project=body.project_id)
        if body.visibility == "visible":
            source_gate(actor, body.project_id, body.account_id)
        refs = [ref(account)]
        if body.post_record_id:
            post = record(actor, body.post_record_id, "Post", project=body.project_id)
            if (
                post["data"].get("account_id") != account["id"]
                or post["data"].get("platform_post_id") != body.platform_post_id
            ):
                raise DomainError(
                    "COMMENT_POST_SCOPE", "评论与所选帖子的账号或平台帖子标识不一致。"
                )
            refs.append(ref(post))
        seen = timestamp(body.observed_at)
        if seen > datetime.now(timezone.utc):
            raise DomainError("FUTURE_OBSERVATION", "观察时间不能晚于当前时间。")
        excerpt = (body.excerpt or "").strip()
        expires = timestamp(body.retain_until) if body.retain_until else None
        if body.visibility == "visible":
            if (
                not excerpt
                or body.unavailable_reason
                or not expires
                or expires <= datetime.now(timezone.utc)
            ):
                raise DomainError("EXCERPT_SCOPE", "保存节选须有正文、保留依据及未来的保留期限。")
        elif excerpt or expires or not body.unavailable_reason:
            raise DomainError("UNAVAILABLE_BODY", "不可见或已删除的评论只能记录原因。")
        rid = PREFIX + digest(
            [body.project_id, account["id"], body.platform_post_id, body.platform_comment_id]
        )
        try:
            old = record(actor, rid, "Comment", project=body.project_id)
        except DomainError as e:
            if e.code != "NOT_FOUND":
                raise
            old = None
        if body.expected_revision != (old["version"] if old else 0):
            raise DomainError("REVISION_CONFLICT", "评论已有新版本，请刷新后核对。", 409)
        if old and timestamp(old["data"]["observed_at"]) > seen:
            raise DomainError("OUT_OF_ORDER_OBSERVATION", "旧观察不能替换新版本。", 409)
        if old and old["data"]["visibility_state"] == "deleted" and body.visibility == "visible":
            raise DomainError("DELETED_COMMENT", "已删除的评论不能重新导入节选。", 409)
        version = body.expected_revision + 1
        data = {
            "name": "评论 · " + body.platform_comment_id[:120],
            "account_id": account["id"],
            "provider": account["data"].get("provider") or "manual",
            "platform_comment_id": body.platform_comment_id,
            "platform_post_id": body.platform_post_id,
            "post_record_id": body.post_record_id,
            "capture_method": "human_reported_observation",
            "provenance": body.provenance,
            "body_retention_basis": body.retention_basis,
            "observed_at": seen.isoformat(),
            "visibility_state": body.visibility,
            "body_completeness": "excerpt" if excerpt else "unavailable",
            "raw_ref": None,
            "body_version": version,
            "body_unavailable_reason": body.unavailable_reason,
            "retention": {"expires_at": expires.isoformat()} if expires else None,
            "deleted_at": seen.isoformat() if body.visibility == "deleted" else None,
        }
        store = get_store()
        with store.connection() as db:
            vault(db)
            # The current permission window governs every stored copy, including a formerly visible excerpt.
            db.execute(
                "DELETE FROM comment_excerpts WHERE tenant_id=? AND record_id=?",
                (actor.tenant_id, rid),
            )
            if excerpt:
                db.execute(
                    "INSERT INTO comment_excerpts VALUES (?,?,?,?,?)",
                    (actor.tenant_id, rid, version, expires.isoformat(), excerpt),
                )
        if old:
            return store.update(
                rid,
                data,
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                expected_revision=body.expected_revision,
                refs=refs,
                reason="更新评论可见性和保留范围",
            )
        return store.create(
            "Comment",
            data,
            tenant_id=actor.tenant_id,
            project_id=body.project_id,
            actor_id=actor.actor_id,
            record_id=rid,
            refs=refs,
            reason="录入有界人工节选",
        )

    return ok(on_request("observe-comment", body, actor, apply), request)


def body_for(actor, item):
    source_gate(actor, item["project_id"], item["data"]["account_id"])
    with get_store().connection() as db:
        vault(db)
        row = db.execute(
            "SELECT excerpt FROM comment_excerpts WHERE tenant_id=? AND record_id=? AND body_version=?",
            (actor.tenant_id, item["id"], item["version"]),
        ).fetchone()
    return row["excerpt"] if row else None


@router.get("/{project_id}/{comment_id}/body")
def get_body(
    project_id: str, comment_id: str, request: Request, actor: Actor = Depends(current_actor)
):
    item = managed_item(actor, project_id, comment_id, "Comment")
    text = body_for(actor, item)
    return ok(
        {
            "body_version": item["version"],
            "excerpt": text,
            "available": text is not None,
            "coverage": item["data"]["body_completeness"] if text else "unavailable",
        },
        request,
    )


class Classification(Body):
    request_id: Label
    project_id: Label
    comment: ChosenVersion
    topic: Literal["product", "usage", "price", "delivery", "content", "other"]
    intent: Literal["question", "request", "complaint", "praise", "other"]
    sentiment: Literal["positive", "neutral", "negative", "uncertain"]
    expected_classification_version: int = Field(ge=0)


def class_id(comment_id):
    return PREFIX + "class_" + comment_id


@router.post("/classify")
def classify(body: Classification, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        item = managed_item(actor, body.project_id, body.comment.id, "Comment")
        if item["version"] != body.comment.version or not body_for(actor, item):
            raise DomainError(
                "COMMENT_BODY_CHANGED", "节选已变、不可见或超出保留期限，请重新核实。", 409
            )
        rid = class_id(item["id"])
        data = {
            "name": "评论人工分类",
            "classification_method": "human_interpretation",
            "reviewer": actor.actor_id,
            "body_version": item["version"],
            "comment_ref": ref(item),
            "topic": body.topic,
            "intent": body.intent,
            "sentiment": body.sentiment,
        }
        store = get_store()
        if body.expected_classification_version:
            return store.update(
                rid,
                data,
                tenant_id=actor.tenant_id,
                expected_revision=body.expected_classification_version,
                actor_id=actor.actor_id,
                refs=[ref(item)],
                reason="修订对节选的人工判断",
            )
        return store.create(
            "Insight",
            data,
            tenant_id=actor.tenant_id,
            project_id=body.project_id,
            actor_id=actor.actor_id,
            refs=[ref(item)],
            record_id=rid,
            reason="记录对节选的人工判断",
        )

    return ok(on_request("classify-comment", body, actor, apply), request)


@router.get("/{project_id}/summary")
def summary(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    record(actor, project_id, "Project")
    counts = {"observations": 0, "classified": 0, "unavailable": 0, "unclassified": 0}
    topics, intents, sentiment = {}, {}, {}
    store = get_store()
    # Work from a consistent snapshot. No excerpt text or removed judgment is returned.
    with store.transaction(), store.connection() as db:
        vault(db)
        rows = db.execute(
            """SELECT r.id, r.latest_version, v.data_json,
                 e.record_id AS available, c.data_json AS classification, av.data_json AS account
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            LEFT JOIN comment_excerpts e ON e.tenant_id=r.tenant_id AND e.record_id=r.id AND e.body_version=r.latest_version
            LEFT JOIN records cr ON cr.tenant_id=r.tenant_id AND cr.project_id=r.project_id AND cr.kind='Insight'
                 AND cr.id='feedback_class_' || r.id
            LEFT JOIN revisions c ON c.tenant_id=cr.tenant_id AND c.record_id=cr.id AND c.version=cr.latest_version
            JOIN records ar ON ar.tenant_id=r.tenant_id AND ar.id=json_extract(v.data_json,'$.account_id')
            JOIN revisions av ON av.tenant_id=ar.tenant_id AND av.record_id=ar.id AND av.version=ar.latest_version
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Comment' AND r.id GLOB 'feedback_*'""",
            (actor.tenant_id, project_id),
        )
        for row in rows:
            counts["observations"] += 1
            if not row["available"] or current_dependency_problem(
                {"kind": "Account", "id": row["id"], "data": json.loads(row["account"])}
            ):
                counts["unavailable"] += 1
                continue
            label = json.loads(row["classification"]) if row["classification"] else None
            if not label or label["body_version"] != row["latest_version"]:
                counts["unclassified"] += 1
                continue
            counts["classified"] += 1
            for target, key in ((topics, "topic"), (intents, "intent"), (sentiment, "sentiment")):
                target[label[key]] = target.get(label[key], 0) + 1
    return ok(
        {
            **counts,
            "topics": topics,
            "intents": intents,
            "sentiments": sentiment,
            "population": "本项目已录入且仍可用的人工节选",
            "classification_method": "human_interpretation",
        },
        request,
    )
