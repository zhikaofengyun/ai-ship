"""Explicit creator-inbox delivery and reconciliation of a frozen, reviewed MP4."""

import json
import os
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt

from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_write, require_approval
from .errors import DomainError
from .media import check_revision, resolve_media
from .production import Body, associated_post, now, publication_preflight, record, ref, revise, save
from .store import get_store
from .tiktok_metrics import check_connection, read_result, tiktok_client, verify_identity

router = APIRouter(prefix="/production/tiktok/inbox", tags=["TikTok creator inbox"])


class InboxRevision(Body):
    expected_revision: StrictInt = Field(ge=1)


def upload_binding(actor, project, account):
    try:
        rows = json.loads(os.getenv("AI_SHIP_TIKTOK_UPLOAD_ACCOUNTS") or "[]")
        hits = [
            r
            for r in rows
            if (r["tenant_id"], r["project_id"], r["account_id"])
            == (actor.tenant_id, project, account)
        ]
        if len(hits) == 1:
            item = hits[0]
            name = item["token_env"]
            if (
                re.fullmatch(r"AI_SHIP_TIKTOK_UPLOAD_TOKEN_[A-Z0-9_]+", name)
                and item["open_id"]
                and os.getenv(name)
            ):
                return item["open_id"], os.environ[name]
    except (KeyError, TypeError, ValueError):
        pass
    raise DomainError(
        "TIKTOK_UPLOAD_AUTH_REQUIRED",
        "服务端需要为这个项目账号配置 video.upload 和 user.info.basic 授权。",
        409,
    )


def subject(actor, pid):
    pub = record(actor, pid, "Publication")
    if pub["data"].get("channel") != "tiktok_inbox":
        raise DomainError("INBOX_CHANNEL", "请选择 TikTok 收件箱交付通道。", 409)
    return pub


def chunk_plan(size):
    if not 0 < size <= 4_000_000_000:
        raise DomainError("TIKTOK_FILE_SIZE", "文件大小超出支持范围。", 422)
    chunk = size if size <= 64_000_000 else 16_000_000
    return {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": chunk,
        "total_chunk_count": size // chunk,
    }


def valid_upload_url(raw):
    url = urlsplit(raw)
    return (
        url.scheme == "https"
        and (url.hostname or "").endswith(".tiktokapis.com")
        and url.username is None
        and url.password is None
        and url.port in (None, 443)
        and bool(url.path)
        and not url.fragment
    )


def submission_context(actor, pub):
    if not actor_still_authorized(actor, {"owner"}):
        raise DomainError("FORBIDDEN", "收件箱交付需要项目负责人的当前授权。", 403)
    data, project = pub["data"], pub["project_id"]
    account = record(actor, data["account_id"], "Account", project=project)
    if account["data"].get("provider") != "tiktok":
        raise DomainError("TIKTOK_ACCOUNT_SCOPE", "发布意图未绑定 TikTok 账号。", 409)
    check_connection(get_store(), actor, project, account["id"])
    return upload_binding(actor, project, account["id"])


def eligible(actor, pub):
    check = publication_preflight(actor, pub)
    if not check["can_send"]:
        raise DomainError("INBOX_PREFLIGHT", "当前发布意图未通过交付预检。", 409, check["problems"])
    data = pub["data"]
    art, blob, path = resolve_media(
        get_store(), actor, data["artifact_id"], project_id=pub["project_id"], purpose="export"
    )
    if (
        art["version"] != data["artifact_version"]
        or art["hash"] != data["artifact_hash"]
        or blob["data"].get("mime_type") != "video/mp4"
    ):
        raise DomainError("INBOX_ARTIFACT_CHANGED", "本次交付须使用已冻结的 MP4 原件。", 409)
    return path


def update(actor, pub, stage, outcome, **details):
    return revise(
        actor,
        pub,
        {"inbox_stage": stage, "outcome": outcome, **details},
        "核对 TikTok 收件箱交付进度",
    )


@router.post("/{pid}/send")
def send(pid: str, body: InboxRevision, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    require_approval(actor)
    store = get_store()
    pub = subject(actor, pid)
    check_revision(pub, body.expected_revision)
    if pub["data"].get("inbox_stage") not in (None, "init_refused"):
        raise DomainError("RECONCILE_FIRST", "此作品已有交付尝试，请核对原请求。", 409)
    open_id, token = submission_context(actor, pub)
    path = eligible(actor, pub)
    with tiktok_client(token) as client, path.open("rb") as media:
        verify_identity(client, open_id)
        plan = chunk_plan(os.fstat(media.fileno()).st_size)
        # Commit the attempt before the first non-idempotent provider call.
        with store.transaction():
            pub = subject(actor, pid)
            check_revision(pub, body.expected_revision)
            if submission_context(actor, pub) != (open_id, token):
                raise DomainError("INBOX_CHANGED", "身份核验期间账号授权已变化。", 409)
            eligible(actor, pub)
            pub = update(actor, pub, "initializing", "outcome_unknown", inbox_started_at=now())
        try:
            response = client.post("post/publish/inbox/video/init/", json={"source_info": plan})
            packet = response.json()
            if 400 <= response.status_code < 500 and (
                isinstance(packet, dict)
                and isinstance(packet.get("error"), dict)
                and packet["error"].get("code") not in ("ok", None)
            ):
                code = str(packet["error"]["code"])[:100]
                return ok(update(actor, pub, "init_refused", "not_sent", inbox_error=code), request)
            response.raise_for_status()
            receipt = packet["data"]
            publish_id = receipt["publish_id"]
            if (
                packet["error"]["code"] != "ok"
                or not isinstance(publish_id, str)
                or not 0 < len(publish_id) <= 64
            ):
                raise ValueError()
            pub = update(actor, pub, "transferring", "outcome_unknown", inbox_publish_id=publish_id)
            upload = receipt["upload_url"]
            if not valid_upload_url(upload):
                raise ValueError()
            # The signed upload URL carries its own credential. Never forward the OAuth bearer.
            with httpx.Client(timeout=60, follow_redirects=False) as uploader:
                for index in range(plan["total_chunk_count"]):
                    latest = subject(actor, pid)
                    if latest["version"] != pub["version"] or submission_context(actor, latest) != (
                        open_id,
                        token,
                    ):
                        raise DomainError("INBOX_CHANGED", "交付状态或账号授权已改变。", 409)
                    # Recheck rights and content decisions at each chunk boundary.
                    eligible(actor, {**latest, "data": {**latest["data"], "outcome": "not_sent"}})
                    start = media.tell()
                    last = index == plan["total_chunk_count"] - 1
                    length = plan["video_size"] - start if last else plan["chunk_size"]
                    chunk = media.read(length)
                    if len(chunk) != length:
                        raise ValueError()
                    ack = uploader.put(
                        upload,
                        content=chunk,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Range": f"bytes {start}-{start+length-1}/{plan['video_size']}",
                        },
                    )
                    if ack.status_code != (201 if last else 206):
                        raise ValueError()
            pub = update(actor, pub, "uploaded", "submitted", uploaded_bytes=plan["video_size"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError, DomainError):
            # A lost reply is never permission to submit again. Preserve any known publish_id.
            current = subject(actor, pid)
            if current["version"] == pub["version"]:
                current = update(actor, pub, "needs_reconciliation", "outcome_unknown")
            return ok(current, request)
    return ok(pub, request)


@router.post("/{pid}/status")
def status(pid: str, body: InboxRevision, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    pub = subject(actor, pid)
    check_revision(pub, body.expected_revision)
    pid_external = pub["data"].get("inbox_publish_id")
    if not pid_external:
        raise DomainError("INBOX_ID_REQUIRED", "没有可向平台查询的原始交付编号。", 409)
    open_id, token = submission_context(actor, pub)
    with tiktok_client(token) as client:
        verify_identity(client, open_id)
        data, log_id = read_result(
            client, "POST", "post/publish/status/fetch/", {}, {"publish_id": pid_external}
        )
    stages = {
        "PROCESSING_UPLOAD": "submitted",
        "PROCESSING_DOWNLOAD": "submitted",
        "SEND_TO_USER_INBOX": "accepted",
        "PUBLISH_COMPLETE": "published",
        "FAILED": "provider_failed",
    }
    stage = data.get("status")
    ids = data.get("publicaly_available_post_id", [])
    if (
        stage not in stages
        or not isinstance(ids, list)
        or any(not re.fullmatch(r"[0-9]{1,24}", str(i)) for i in ids)
        or (ids and stage != "PUBLISH_COMPLETE")
    ):
        raise DomainError("INBOX_INVALID_STATUS", "平台回执字段无法核对。", 502)
    with get_store().transaction():
        current = subject(actor, pid)
        check_revision(current, body.expected_revision)
        if submission_context(actor, current) != (open_id, token):
            raise DomainError("INBOX_CHANGED", "核对期间账号授权已变化。", 409)
        # A harmless progress poll must never interrupt an active chunk writer's CAS.
        if stage in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"} and current["data"].get(
            "inbox_stage"
        ) in {"transferring", "SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"}:
            return ok(current, request)
        if (
            current["data"].get("inbox_stage") in ("FAILED", "PUBLISH_COMPLETE")
            and current["data"]["inbox_stage"] != stage
        ):
            raise DomainError(
                "INBOX_STATUS_CONFLICT", "平台新状态与已记录的终态不一致，需复核。", 409
            )
        posts = list(current["data"].get("post_refs", []))
        refs = list(current["refs"])
        observed = now()
        for external in dict.fromkeys(map(str, ids)):
            post = associated_post(actor, current, external)
            if post and post["data"].get("publication_id") != pid:
                raise DomainError("POST_ALREADY_ASSOCIATED", "该帖子已关联其他发布意图。", 409)
            if not post:
                post = save(
                    actor,
                    "Post",
                    current["project_id"],
                    {
                        "provider": "tiktok",
                        "account_id": current["data"]["account_id"],
                        "platform_post_id": external,
                        "publication_id": pid,
                        "submitted_artifact_id": current["data"]["artifact_id"],
                        "association_status": "provider_confirmed",
                        "independently_verified": True,
                        "visibility": "public",
                        "last_observed_at": observed,
                        "final_media_status": "unknown",
                        "inbox_publish_id": pid_external,
                        "inbox_log_id": log_id,
                    },
                    [ref(current)],
                )
            elif post["data"].get("independently_verified") is not True:
                post = revise(
                    actor,
                    post,
                    {
                        "association_status": "provider_confirmed",
                        "independently_verified": True,
                        "visibility": "public",
                        "last_observed_at": observed,
                        "inbox_publish_id": pid_external,
                        "inbox_log_id": log_id,
                    },
                    "平台回执核实此前人工登记的同一帖子",
                )
            if post["id"] not in posts:
                posts.append(post["id"])
            if ref(post) not in refs:
                refs.append(ref(post))
        details = {}
        if stage == "FAILED" and isinstance(data.get("fail_reason"), str):
            details["inbox_fail_reason"] = data["fail_reason"][:400]
        if type(data.get("uploaded_bytes")) is int and data["uploaded_bytes"] >= 0:
            details["inbox_reported_uploaded_bytes"] = data["uploaded_bytes"]
        pub = revise(
            actor,
            current,
            {
                **details,
                "inbox_stage": stage,
                "outcome": stages[stage],
                "inbox_log_id": log_id,
                "platform_post_ids": list(
                    dict.fromkeys([*current["data"].get("platform_post_ids", []), *map(str, ids)])
                ),
                "inbox_checked_at": observed,
                "post_refs": posts,
            },
            "核对平台原始交付回执与公开帖子编号",
            refs,
        )
    return ok(pub, request)
