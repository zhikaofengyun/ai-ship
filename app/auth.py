"""Server identities and short-lived HttpOnly sessions for same-origin media.

Sessions keep only a digest of the backing API key and re-check the current
server key map on every request. No credential enters business records or URLs.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from fastapi import Request
from .errors import DomainError


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    actor_id: str
    role: str
    credential_digest: str | None = None


LOCAL_ACTOR = Actor("local", "local-owner", "owner")
ROLES = {"owner", "editor", "reviewer", "viewer", "worker"}
SESSION_COOKIE = "ai_ship_session"
_SESSIONS = {}
_SESSION_LOCK = threading.Lock()


def _key_map():
    try:
        keys = json.loads(os.getenv("AI_SHIP_API_KEYS", "{}"))
    except json.JSONDecodeError:
        raise DomainError("AUTH_CONFIGURATION", "身份配置无效。", 503)
    if not isinstance(keys, dict):
        raise DomainError("AUTH_CONFIGURATION", "身份配置无效。", 503)
    return keys


def _identity(identity, credential_digest=None):
    if (
        not isinstance(identity, dict)
        or not all(identity.get(k) for k in ("tenant_id", "actor_id", "role"))
        or identity["role"] not in ROLES
    ):
        raise DomainError("AUTH_CONFIGURATION", "身份配置无效。", 503)
    return Actor(identity["tenant_id"], identity["actor_id"], identity["role"], credential_digest)


def _bearer(request):
    authorization = request.headers.get("authorization", "")
    return authorization[7:] if authorization.lower().startswith("bearer ") else ""


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def current_actor(request: Request) -> Actor:
    mode = os.getenv("AI_SHIP_MODE", "local")
    if mode == "local":
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise DomainError(
                "LOCAL_ONLY", "本地模式只接受回环地址；团队访问请配置服务端身份。", 403
            )
        allowed = {"127.0.0.1", "localhost", "::1", "testserver"}
        allowed.update(
            x.strip() for x in os.getenv("AI_SHIP_TRUSTED_HOSTS", "").split(",") if x.strip()
        )
        if (request.url.hostname or "").lower() not in allowed:
            raise DomainError("UNTRUSTED_HOST", "未受信任的主机名。", 403)
        actor = LOCAL_ACTOR
    elif mode == "team":
        keys = _key_map()
        token = _bearer(request)
        identity = None
        credential_digest = None
        if token:
            identity = next(
                (
                    v
                    for k, v in keys.items()
                    if isinstance(k, str) and hmac.compare_digest(k.encode(), token.encode())
                ),
                None,
            )
            credential_digest = _digest(token)
        elif request.cookies.get(SESSION_COOKIE):
            cookie_hash = _digest(request.cookies[SESSION_COOKIE])
            with _SESSION_LOCK:
                session = _SESSIONS.get(cookie_hash)
                if session and session["expires"] <= time.time():
                    _SESSIONS.pop(cookie_hash, None)
                    session = None
            if session:
                credential_digest = session["key_digest"]
                identity = next(
                    (
                        v
                        for k, v in keys.items()
                        if isinstance(k, str)
                        and hmac.compare_digest(_digest(k), session["key_digest"])
                    ),
                    None,
                )
        if not identity:
            raise DomainError("UNAUTHENTICATED", "需要有效的 API 身份或未过期会话。", 401)
        actor = _identity(identity, credential_digest)
    else:
        raise DomainError("AUTH_CONFIGURATION", "AI_SHIP_MODE 仅支持 local 或 team。", 503)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            allowed_origins = {f"{request.url.scheme}://{request.url.netloc}"}
            allowed_origins.update(
                v.strip().rstrip("/")
                for v in os.getenv("AI_SHIP_ALLOWED_ORIGINS", "").split(",")
                if v.strip()
            )
            if origin.rstrip("/") not in allowed_origins:
                raise DomainError("ORIGIN_REJECTED", "拒绝跨站写入请求。", 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise DomainError("ORIGIN_REJECTED", "拒绝跨站写入请求。", 403)
    return actor


def create_session(request: Request, actor: Actor):
    if os.getenv("AI_SHIP_MODE", "local") == "local":
        return None, {
            "mode": "local",
            "actor": {"tenant_id": actor.tenant_id, "actor_id": actor.actor_id, "role": actor.role},
            "expires_at": None,
        }
    token = _bearer(request)
    if not token:
        raise DomainError("BEARER_REQUIRED", "创建浏览器会话必须提供当前 Bearer 身份。", 401)
    # Check exact transport origin. Do not trust client-supplied forwarded headers.
    loopback = request.url.hostname in ("localhost", "127.0.0.1", "::1", "testserver") and (
        not request.client or request.client.host in ("127.0.0.1", "::1", "testclient")
    )
    secure = request.url.scheme == "https"
    if not secure and not loopback:
        raise DomainError("SECURE_TRANSPORT_REQUIRED", "远程团队会话必须使用 HTTPS。", 403)
    try:
        ttl = int(os.getenv("AI_SHIP_SESSION_TTL", "3600"))
    except ValueError:
        raise DomainError("AUTH_CONFIGURATION", "会话有效期配置无效。", 503)
    ttl = min(max(ttl, 60), 3600)
    raw = secrets.token_urlsafe(48)
    stamp = time.time()
    with _SESSION_LOCK:
        for key in [k for k, v in _SESSIONS.items() if v["expires"] <= stamp]:
            _SESSIONS.pop(key, None)
        if len(_SESSIONS) >= 10000:
            raise DomainError("SESSION_LIMIT", "本机活跃会话达到上限。", 503)
        _SESSIONS[_digest(raw)] = {"key_digest": _digest(token), "expires": stamp + ttl}
    expires = (
        (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    )
    return {"value": raw, "secure": secure, "max_age": ttl}, {
        "mode": "team",
        "actor": {"tenant_id": actor.tenant_id, "actor_id": actor.actor_id, "role": actor.role},
        "expires_at": expires,
        "restart_invalidates": True,
    }


def revoke_session(request: Request):
    value = request.cookies.get(SESSION_COOKIE)
    if value:
        with _SESSION_LOCK:
            _SESSIONS.pop(_digest(value), None)


def require_write(actor: Actor):
    if actor.role not in {"owner", "editor", "worker"}:
        raise DomainError("FORBIDDEN", "当前身份没有写入权限。", 403)


def require_approval(actor: Actor):
    if actor.role not in {"owner", "reviewer"}:
        raise DomainError("FORBIDDEN", "当前身份没有确认或审阅权限。", 403)


def actor_still_authorized(actor: Actor):
    """Check a long-running worker at safe boundaries without persisting keys."""
    if actor.role not in {"owner", "editor", "worker"}:
        return False
    if os.getenv("AI_SHIP_MODE", "local") == "local":
        return True
    for key, identity in _key_map().items():
        if not isinstance(identity, dict):
            continue
        if actor.credential_digest and not hmac.compare_digest(
            _digest(key), actor.credential_digest
        ):
            continue
        if (identity.get("tenant_id"), identity.get("actor_id"), identity.get("role")) == (
            actor.tenant_id,
            actor.actor_id,
            actor.role,
        ):
            return True
    return False
