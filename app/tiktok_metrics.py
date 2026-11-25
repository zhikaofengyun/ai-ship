"""Read TikTok Display API video counters for an explicitly bound project account."""

from datetime import datetime, timezone
import json
import os
import re

from fastapi import APIRouter, Depends, Request
import httpx
from pydantic import Field, StrictInt
from typing import Literal

from .api import ok
from .auth import Actor, current_actor, require_write, require_approval, actor_still_authorized
from .production import Body, MetricImport, MetricRow, digest, record, ref, save, store_metric_rows
from .errors import DomainError
from .store import get_store

router = APIRouter(prefix="/production/tiktok", tags=["TikTok observations"])
ROOT = "https://open.tiktokapis.com/v2/"
COUNTERS = {
    "view_count": "views",
    "like_count": "likes",
    "comment_count": "comments",
    "share_count": "shares",
}


class SyncMetrics(Body):
    project_id: str
    post_ids: list[str] = Field(min_length=1, max_length=20)
    request_id: str = Field(min_length=1, max_length=100)


def binding(actor, project, account):
    try:
        config = json.loads(os.getenv("AI_SHIP_TIKTOK_ACCOUNTS") or "[]")
        if not isinstance(config, list):
            raise ValueError()
        matches = [
            r
            for r in config
            if (r["tenant_id"], r["project_id"], r["account_id"])
            == (actor.tenant_id, project, account)
        ]
        if len(matches) > 1:
            raise ValueError()
        if matches:
            match = matches[0]
            if (
                not re.fullmatch(r"AI_SHIP_TIKTOK_TOKEN_[A-Z0-9_]+", match["token_env"])
                or not isinstance(match["open_id"], str)
                or not match["open_id"]
            ):
                raise ValueError()
            token = os.getenv(match["token_env"])
            if token:
                return match["open_id"], token
    except (ValueError, KeyError, TypeError):
        raise DomainError("TIKTOK_CONFIG_INVALID", "服务端账号绑定配置无效。", 503) from None
    raise DomainError("TIKTOK_AUTH_REQUIRED", "此项目账号尚未配置 TikTok 只读授权。", 503)


def read_result(client, method, path, params, body=None):
    try:
        response = client.request(method, path, params=params, json=body)
        response.raise_for_status()
        data = response.json()
        if data.get("error", {}).get("code") != "ok" or not isinstance(data.get("data"), dict):
            raise ValueError()
        return data["data"], data["error"].get("log_id")
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise DomainError(
            "TIKTOK_READ_FAILED", "TikTok 未返回可验证的结果，请检查只读授权、限额和连接。", 503
        ) from None


def tiktok_client(token):
    return httpx.Client(
        base_url=ROOT,
        headers={"Authorization": "Bearer " + token},
        follow_redirects=False,
        timeout=15,
    )


def verify_identity(client, open_id):
    user, _ = read_result(client, "GET", "user/info/", {"fields": "open_id"})
    if not isinstance(user.get("user"), dict) or user["user"].get("open_id") != open_id:
        raise DomainError("TIKTOK_ACCOUNT_MISMATCH", "当前授权主体与绑定账号不一致。", 409)


def observation(open_id, token, ids):
    with tiktok_client(token) as client:
        verify_identity(client, open_id)
        data, log_id = read_result(
            client,
            "POST",
            "video/query/",
            {"fields": ",".join(["id", "create_time", *COUNTERS])},
            {"filters": {"video_ids": ids}},
        )
    videos = data.get("videos")
    if not isinstance(videos, list) or len(videos) > len(ids):
        raise DomainError("TIKTOK_VIDEO_EVIDENCE", "平台返回视频列表无法核对。", 502)
    return videos, log_id


class ConnectionAction(Body):
    project_id: str
    expected_revision: StrictInt = Field(ge=0)
    action: Literal["verify", "suspend"]


def connection_state(store, actor, project, account):
    with store.connection() as db:
        row = db.execute(
            "SELECT revision,state,observed_at,capabilities FROM tiktok_connections WHERE tenant=? AND project=? AND account=?",
            (actor.tenant_id, project, account),
        ).fetchone()
    if not row:
        return {"state": "unverified", "revision": 0, "capabilities": [], "observed_at": None}
    return {**dict(row), "capabilities": json.loads(row["capabilities"])}


def check_connection(store, actor, project, account):
    state = connection_state(store, actor, project, account)
    if state["state"] == "suspended":
        raise DomainError("TIKTOK_CONNECTION_SUSPENDED", "此账号的自动采集已暂停。", 409)
    return state["revision"]


def tiktok_account(actor, project, account_id):
    account = record(actor, account_id, "Account", project=project)
    if str(account["data"].get("provider", "")).casefold() != "tiktok":
        raise DomainError("TIKTOK_ACCOUNT_SCOPE", "帐号的平台类型必须为 TikTok。")
    return account


@router.get("/accounts/{account_id}/connection")
def show_connection(
    account_id: str, project_id: str, request: Request, actor: Actor = Depends(current_actor)
):
    tiktok_account(actor, project_id, account_id)
    return ok(connection_state(get_store(), actor, project_id, account_id), request)


@router.post("/accounts/{account_id}/connection")
def change_connection(
    account_id: str, body: ConnectionAction, request: Request, actor: Actor = Depends(current_actor)
):
    require_approval(actor)
    account = tiktok_account(actor, body.project_id, account_id)
    store = get_store()

    def check_revision():
        old = connection_state(store, actor, body.project_id, account_id)
        if old["revision"] != body.expected_revision:
            raise DomainError("REVISION_CONFLICT", "账号连接已变化，请重读。", 409)

    check_revision()
    caps = []
    evidence = {}
    if body.action == "verify":
        open_id, token = binding(actor, body.project_id, account_id)
        with tiktok_client(token) as client:
            verify_identity(client, open_id)
            result, log_id = read_result(
                client, "POST", "video/list/", {"fields": "id"}, {"max_count": 1}
            )
        if not isinstance(result.get("videos"), list):
            raise DomainError("TIKTOK_READ_FAILED", "账号视频列表回执格式有误。", 502)
        caps = ["user.info.basic", "video.list"]
        evidence = {
            "list_log_id": log_id,
            "identity_matches_binding": True,
            "list_count": len(result["videos"]),
        }
    stamp = datetime.now(timezone.utc).isoformat()
    with store.transaction(), store.connection() as db:
        check_revision()
        if not actor_still_authorized(actor) or (
            body.action == "verify"
            and binding(actor, body.project_id, account_id) != (open_id, token)
        ):
            raise DomainError("TIKTOK_AUTH_CHANGED", "核验期间授权已变化。", 409)
        state = "verified_read_only" if caps else "suspended"
        db.execute(
            "INSERT INTO tiktok_connections VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant,project,account) DO UPDATE SET revision=excluded.revision,state=excluded.state,observed_at=excluded.observed_at,capabilities=excluded.capabilities",
            (
                actor.tenant_id,
                body.project_id,
                account_id,
                body.expected_revision + 1,
                state,
                stamp,
                json.dumps(caps),
            ),
        )
        event = save(
            actor,
            "PlatformEvent",
            body.project_id,
            {
                "provider": "tiktok",
                "event_type": "account.read_connection." + body.action,
                "event_time": stamp,
                "received_at": stamp,
                "source_kind": "poll" if caps else "manual",
                "evidence": evidence,
            },
            [ref(account)],
        )
        return ok(
            {
                **connection_state(store, actor, body.project_id, account_id),
                "evidence_id": event["id"],
            },
            request,
        )


@router.post("/metrics")
def sync_metrics(body: SyncMetrics, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    record(actor, body.project_id, "Project")
    posts = [record(actor, k, "Post", project=body.project_id) for k in body.post_ids]
    accounts = {p["data"].get("account_id") for p in posts}
    if len(accounts) != 1 or None in accounts:
        raise DomainError("TIKTOK_ACCOUNT_SCOPE", "一次同步只能选择同一已绑定账号的帖子。")
    account = record(actor, accounts.pop(), "Account", project=body.project_id)
    if str(account["data"].get("provider", "")).casefold() != "tiktok":
        raise DomainError("TIKTOK_ACCOUNT_SCOPE", "所选帖子不属于 TikTok 账号。")
    by_id = {p["data"].get("platform_post_id"): p for p in posts}
    if len(by_id) != len(posts) or any(
        not isinstance(i, str) or not re.fullmatch(r"[0-9]{1,24}", i) for i in by_id
    ):
        raise DomainError("TIKTOK_POST_ID", "平台视频 ID 缺失、重复或有误。")
    store = get_store()
    intent = {"actor": actor.actor_id, "posts": body.post_ids, "project": body.project_id}
    key = "tiktok-read:" + digest([body.project_id, actor.actor_id, body.request_id])

    def prior():
        with store.connection() as db:
            return db.execute(
                "SELECT 1 FROM command_results WHERE tenant_id=? AND idempotency_key=?",
                (actor.tenant_id, key),
            ).fetchone()

    if prior():
        return ok(store.idempotent(actor.tenant_id, key, key, intent, lambda: {}), request)
    connection_revision = check_connection(store, actor, body.project_id, account["id"])
    open_id, token = binding(actor, body.project_id, account["id"])
    videos, log_id = observation(open_id, token, list(by_id))
    observed = datetime.now(timezone.utc)

    def persist():
        if check_connection(store, actor, body.project_id, account["id"]) != connection_revision:
            raise DomainError("TIKTOK_AUTH_CHANGED", "平台读取期间账号连接已变化。", 409)
        if not actor_still_authorized(actor) or binding(actor, body.project_id, account["id"]) != (
            open_id,
            token,
        ):
            raise DomainError("TIKTOK_AUTH_CHANGED", "采集期间授权状态已变化。", 409)
        seen = set()
        rows = []
        for v in videos:
            if (
                not isinstance(v, dict)
                or not isinstance(v.get("id"), str)
                or v["id"] not in by_id
                or v["id"] in seen
            ):
                raise DomainError("TIKTOK_VIDEO_EVIDENCE", "平台回包未能与所选视频逐项匹配。", 502)
            seen.add(v["id"])
            try:
                if type(v["create_time"]) is not int:
                    raise ValueError()
                start = datetime.fromtimestamp(v["create_time"], timezone.utc)
                if start >= observed:
                    raise ValueError()
            except (ValueError, KeyError, OverflowError, OSError):
                raise DomainError(
                    "TIKTOK_VIDEO_EVIDENCE", "缺少可验证的视频发布时间。", 502
                ) from None
            for key_name, name in COUNTERS.items():
                value = v.get(key_name)
                if value is not None and (type(value) is not int or not 0 <= value <= 2**53):
                    raise DomainError("TIKTOK_VIDEO_EVIDENCE", "计数超出可验证范围。", 502)
                rows.append(
                    MetricRow(
                        account_id=account["id"],
                        post_record_id=by_id[v["id"]]["id"],
                        metric_name=name,
                        value=value,
                        missing_reason=None if value is not None else "TikTok 未提供此字段",
                        unit="count",
                        window_start=start.isoformat(),
                        window_end=observed.isoformat(),
                        observed_at=observed.isoformat(),
                        counting_mode="cumulative",
                        source="TikTok Display API /v2/video/query/",
                    )
                )
        archive = save(
            actor,
            "Source",
            body.project_id,
            {
                "origin_kind": "tiktok_display_api",
                "provenance": {
                    "api": ROOT + "video/query/",
                    "observed_at": observed.isoformat(),
                    "request_log_id": log_id,
                    "response_sha256": digest(videos),
                    "requested_ids": list(by_id),
                    "observations": videos,
                },
            },
            [ref(account), *[ref(p) for p in posts]],
        )
        result = (
            store_metric_rows(MetricImport(project_id=body.project_id, rows=rows), actor, archive)
            if rows
            else {"count": 0}
        )
        return {
            "count": result["count"],
            "source_id": archive["id"],
            "observed_at": observed.isoformat(),
            "missing_posts": [p["id"] for i, p in by_id.items() if i not in seen],
        }

    return ok(store.idempotent(actor.tenant_id, key, key, intent, persist), request)
