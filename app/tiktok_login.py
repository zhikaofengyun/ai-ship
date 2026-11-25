"""TikTok Login Kit for Web. Credentials stay in an encrypted, account-bound server vault."""

from hashlib import sha256
import json
from datetime import datetime, timezone
import logging
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
import httpx
from pydantic import Field

from .api import Body, ok
from .auth import Actor, actor_still_authorized, current_actor, require_approval
from .errors import DomainError
from .store import canonical, digest, get_store
from .tiktok_metrics import tiktok_account

router = APIRouter(prefix="/production/tiktok/login", tags=["TikTok Login Kit"])
CALLBACK = "/api/v1/production/tiktok/login/callback"
AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
PLATFORM = "https://open.tiktokapis.com/v2/oauth/"
SCOPES = {"user.info.basic", "video.list"}
COOKIE = "__Host-ai_ship_tiktok"
TTL = 600
DDL = """CREATE TABLE IF NOT EXISTS tiktok_login_vault(
 tenant TEXT NOT NULL,project TEXT NOT NULL,account TEXT NOT NULL,revision INTEGER NOT NULL,
 state TEXT NOT NULL,secret BLOB,access_until REAL,refresh_until REAL,updated REAL NOT NULL,
 PRIMARY KEY(tenant,project,account)) WITHOUT ROWID"""
STATES = """CREATE TABLE IF NOT EXISTS tiktok_login_states(
 state TEXT PRIMARY KEY,browser TEXT NOT NULL,expires REAL NOT NULL,principal TEXT NOT NULL,
 project TEXT NOT NULL,account TEXT NOT NULL,generation INTEGER NOT NULL,binding TEXT NOT NULL)"""


class Choice(Body):
    project_id: str
    expected_revision: int = Field(ge=0)


class CallbackLog(logging.Filter):
    def filter(self, record):
        # Uvicorn access tuples contain the original request-target with its query.
        if (
            isinstance(record.args, tuple)
            and len(record.args) >= 3
            and isinstance(record.args[2], str)
            and record.args[2].split("?")[0].rstrip("/") == CALLBACK
        ):
            args = list(record.args)
            args[2] = CALLBACK
            record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(CallbackLog())


def hash_value(value):
    return sha256(value.encode()).hexdigest()


def cipher():
    try:
        from cryptography.fernet import Fernet

        return Fernet(os.environ["AI_SHIP_TIKTOK_FERNET_KEY"])
    except (ValueError, KeyError, ImportError):
        raise DomainError(
            "TIKTOK_VAULT_UNAVAILABLE", "服务端尚未配置受保护的 TikTok 授权凭据库。", 503
        ) from None


def assignment(key):
    try:
        rows = json.loads(os.getenv("AI_SHIP_TIKTOK_LOGIN_ACCOUNTS", "[]"))
        if not isinstance(rows, list):
            raise ValueError()
        matches = [r for r in rows if (r["tenant_id"], r["project_id"], r["account_id"]) == key]
        if len(matches) > 1:
            raise ValueError()
        if not matches:
            return None
        open_id = matches[0]["open_id"]
        if not isinstance(open_id, str) or not open_id.strip():
            raise ValueError()
        app = os.environ["AI_SHIP_TIKTOK_CLIENT_KEY"]
        secret = os.environ["AI_SHIP_TIKTOK_CLIENT_SECRET"]
        redirect = os.environ["AI_SHIP_TIKTOK_REDIRECT_URI"]
        url = urlsplit(redirect)
        if (
            not app
            or not secret
            or len(redirect) >= 512
            or not url.hostname
            or url.username
            or url.password
            or url.scheme != "https"
            or url.query
            or url.fragment
            or url.path != CALLBACK
        ):
            raise ValueError()
        return {
            "open_id": open_id,
            "client_key": app,
            "client_secret": secret,
            "redirect_uri": redirect,
        }
    except (TypeError, KeyError, ValueError):
        raise DomainError(
            "TIKTOK_LOGIN_CONFIG", "TikTok Login Kit 的账号分配或静态 HTTPS 回调配置无效。", 503
        ) from None


def tables():
    with get_store().connection() as db:
        db.execute(DDL)
        db.execute(STATES)
        db.execute("""CREATE TABLE IF NOT EXISTS tiktok_login_history(
            tenant TEXT, project TEXT, account TEXT, revision INTEGER, state TEXT, changed_at REAL,
            PRIMARY KEY(tenant,project,account,revision)) WITHOUT ROWID""")


def row_for(key):
    tables()
    with get_store().connection() as db:
        return db.execute(
            "SELECT * FROM tiktok_login_vault WHERE tenant=? AND project=? AND account=?", key
        ).fetchone()


def public(row):
    if not row:
        return {"state": "not_connected", "revision": 0}
    result = {k: row[k] for k in ("state", "revision", "access_until", "refresh_until")}
    if row["state"] == "active" and row["refresh_until"] <= time.time():
        result["state"] = "expired"
    return result


def configured(key, request=None):
    cfg = assignment(key)
    if not cfg:
        raise DomainError("TIKTOK_LOGIN_UNASSIGNED", "服务器尚未分配此 TikTok 账号。", 403)
    cipher()
    if (
        request is not None
        and str(request.base_url).rstrip("/") != "https://" + urlsplit(cfg["redirect_uri"]).netloc
    ):
        raise DomainError("TIKTOK_CALLBACK_ORIGIN", "请从登记回调所在的 HTTPS 站点进入。", 403)
    return cfg


def fingerprint(cfg):
    return digest(
        [os.getenv("AI_SHIP_MODE", "local"), cfg["open_id"], cfg["client_key"], cfg["redirect_uri"]]
    )


def expect(row, rev):
    if (row["revision"] if row else 0) != rev:
        raise DomainError("REVISION_CONFLICT", "账号授权已变化，请重新读取。", 409)


def set_state(db, key, revision, state, secret=None, access=None, refresh=None):
    db.execute(
        """INSERT INTO tiktok_login_vault VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant,project,account) DO UPDATE SET revision=excluded.revision,state=excluded.state,
        secret=excluded.secret,access_until=excluded.access_until,refresh_until=excluded.refresh_until,updated=excluded.updated""",
        (*key, revision, state, secret, access, refresh, time.time()),
    )
    db.execute(
        "INSERT INTO tiktok_login_history VALUES(?,?,?,?,?,?)", (*key, revision, state, time.time())
    )


def invalidate_read_check(db, key, blocked=False):
    previous = db.execute(
        "SELECT revision,state FROM tiktok_connections WHERE tenant=? AND project=? AND account=?",
        key,
    ).fetchone()
    if not blocked and (not previous or previous["state"] == "suspended"):
        return
    revision = (previous["revision"] if previous else 0) + 1
    db.execute(
        """INSERT INTO tiktok_connections VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant,project,account)
                  DO UPDATE SET revision=excluded.revision,state=excluded.state,observed_at=excluded.observed_at,capabilities=excluded.capabilities""",
        (
            *key,
            revision,
            "suspended" if blocked else "unverified",
            datetime.now(timezone.utc).isoformat(),
            "[]",
        ),
    )


def keep_secret(db, key, rev, state, old):
    set_state(
        db,
        key,
        rev,
        state,
        old["secret"] if old else None,
        old["access_until"] if old else None,
        old["refresh_until"] if old else None,
    )


def release(key, rev, state, old=None):
    with get_store().transaction(), get_store().connection() as db:
        row = row_for(key)
        expect(row, rev)
        keep_secret(db, key, rev + 1, state, old)


def post_platform(endpoint, data):
    try:
        # The provider host is fixed in server code. Never follow redirects with credentials.
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", PLATFORM + endpoint, data=data) as response:
                buf = bytearray()
                for chunk in response.iter_bytes(8192):
                    if len(buf) + len(chunk) > 65536:
                        raise ValueError()
                    buf.extend(chunk)
                if (
                    endpoint == "revoke/"
                    and response.status_code in (200, 204)
                    and (not buf.strip() or json.loads(buf) == {})
                ):
                    return {}
                if endpoint == "revoke/" or response.status_code != 200:
                    raise ValueError()
                payload = json.loads(buf)
                if not isinstance(payload, dict) or payload.get("error"):
                    raise ValueError()
                return payload
    except (httpx.HTTPError, ValueError):
        raise DomainError("TIKTOK_GRANT_UNCONFIRMED", "平台未确认本次授权操作。", 502) from None


def encode_tokens(key, cfg, data):
    if (
        data.get("open_id") != cfg["open_id"]
        or str(data.get("token_type", "")).lower() != "bearer"
        or set(str(data.get("scope", "")).split(",")) != SCOPES
    ):
        raise DomainError("TIKTOK_GRANT_SCOPE", "平台账号或授予的只读权限与配置不符。", 409)
    if any(
        not isinstance(data.get(k), str) or not 0 < len(data[k]) <= 16384
        for k in ["access_token", "refresh_token"]
    ):
        raise DomainError("TIKTOK_TOKEN_RESPONSE", "授权服务未提供完整令牌。", 502)
    if any(
        type(data.get(k)) is not int or not 0 < data[k] <= 10 * 365 * 86400
        for k in ["expires_in", "refresh_expires_in"]
    ):
        raise DomainError("TIKTOK_TOKEN_RESPONSE", "授权服务未提供有效的到期时间。", 502)
    secret = cipher().encrypt(
        canonical(
            {
                "key": key,
                "binding": fingerprint(cfg),
                "access": data["access_token"],
                "refresh": data["refresh_token"],
            }
        ).encode()
    )
    return secret, time.time() + data["expires_in"], time.time() + data["refresh_expires_in"]


def decode_tokens(key, cfg, raw):
    from cryptography.fernet import InvalidToken

    try:
        clear = json.loads(cipher().decrypt(bytes(raw)))
        if clear["key"] != list(key) or clear["binding"] != fingerprint(cfg):
            raise ValueError()
        return clear
    except (InvalidToken, ValueError, TypeError, KeyError):
        raise DomainError(
            "TIKTOK_VAULT_MISMATCH", "授权凭据与当前账号分配或加密密钥不同。", 409
        ) from None


def key_for(actor, project, account):
    tiktok_account(actor, project, account)
    return actor.tenant_id, project, account


@router.get("/accounts/{account}")
def status(account: str, project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    key = key_for(actor, project_id, account)
    state = public(row_for(key))
    with get_store().connection() as db:
        history = [
            dict(r)
            for r in db.execute(
                "SELECT revision,state,changed_at FROM tiktok_login_history WHERE tenant=? AND project=? AND account=? ORDER BY revision DESC LIMIT 20",
                key,
            )
        ]
    return ok({**state, "assigned": bool(assignment(key)), "history": history}, request)


@router.post("/accounts/{account}/start")
def start(account: str, body: Choice, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    key = key_for(actor, body.project_id, account)
    cfg = configured(key, request)
    state = secrets.token_urlsafe(36)
    browser = secrets.token_urlsafe(36)
    tables()
    with get_store().transaction(), get_store().connection() as db:
        row = row_for(key)
        expect(row, body.expected_revision)
        if (
            row
            and row["secret"]
            and public(row)["state"] != "expired"
            and row["state"]
            not in {
                "refresh_unknown",
                "refreshing",
                "authorization_failed",
                "denied",
                "authorizing",
                "exchanging",
            }
        ):
            raise DomainError("TIKTOK_ALREADY_LINKED", "请先解除现有授权。", 409)
        if (
            row
            and row["state"] in {"refreshing", "exchanging", "revoking"}
            and row["updated"] > time.time() - 60
        ):
            raise DomainError("TIKTOK_GRANT_BUSY", "另一项授权操作正在进行。", 409)
        rev = body.expected_revision + 1
        keep_secret(db, key, rev, "authorizing", row)
        db.execute("DELETE FROM tiktok_login_states WHERE expires<=?", (time.time(),))
        db.execute(
            "INSERT INTO tiktok_login_states VALUES(?,?,?,?,?,?,?,?)",
            (
                hash_value(state),
                hash_value(browser),
                time.time() + TTL,
                canonical(vars(actor)),
                body.project_id,
                account,
                rev,
                fingerprint(cfg),
            ),
        )
    result = JSONResponse(
        ok(
            {
                "url": AUTHORIZE
                + "?"
                + urlencode(
                    {
                        "client_key": cfg["client_key"],
                        "scope": ",".join(sorted(SCOPES)),
                        "response_type": "code",
                        "redirect_uri": cfg["redirect_uri"],
                        "state": state,
                        "disable_auto_auth": 1,
                    }
                )
            },
            request,
        )
    )
    result.set_cookie(
        COOKIE, browser, secure=True, httponly=True, samesite="lax", path="/", max_age=TTL
    )
    return result


@router.get("/callback")
def callback(request: Request):
    p = request.query_params
    state = p.get("state", "")
    browser = request.cookies.get(COOKIE, "")
    if (
        len(state) > 256
        or not state
        or not browser
        or len(browser) > 256
        or len(p.getlist("state")) != 1
    ):
        raise DomainError("TIKTOK_STATE_INVALID", "授权会话无效或已过期。", 403)
    tables()
    with get_store().transaction(), get_store().connection() as db:
        saved = db.execute(
            "SELECT * FROM tiktok_login_states WHERE state=? AND browser=? AND expires>?",
            (hash_value(state), hash_value(browser), time.time()),
        ).fetchone()
        if not saved:
            raise DomainError("TIKTOK_STATE_INVALID", "授权会话无效或已过期。", 403)
        actor = Actor(**json.loads(saved["principal"]))
        key = key_for(actor, saved["project"], saved["account"])
        cfg = configured(key, request)
        rev = saved["generation"]
        if not actor_still_authorized(actor, {"owner", "reviewer"}) or saved[
            "binding"
        ] != fingerprint(cfg):
            raise DomainError("TIKTOK_GRANT_CHANGED", "开始授权后的身份或账号分配已变化。", 403)
        prior = row_for(key)
        expect(prior, rev)
        db.execute("DELETE FROM tiktok_login_states WHERE state=?", (hash_value(state),))
        keep_secret(db, key, rev + 1, "exchanging", prior)
    code = p.get("code", "")
    if p.get("error") or len(p.getlist("code")) != 1 or not 0 < len(code) <= 4096:
        release(key, rev + 1, "denied", prior)
    else:
        try:
            data = post_platform(
                "token/",
                {
                    "client_key": cfg["client_key"],
                    "client_secret": cfg["client_secret"],
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": cfg["redirect_uri"],
                },
            )
            secret, a, r = encode_tokens(key, cfg, data)
            with get_store().transaction(), get_store().connection() as db:
                expect(row_for(key), rev + 1)
                if configured(key) != cfg or not actor_still_authorized(
                    actor, {"owner", "reviewer"}
                ):
                    raise DomainError("TIKTOK_GRANT_CHANGED", "授权环境已变化。", 409)
                set_state(db, key, rev + 2, "active", secret, a, r)
                invalidate_read_check(db, key)
        except DomainError:
            release(key, rev + 1, "authorization_failed", prior)
    response = RedirectResponse("/#module/account/J01", status_code=303)
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return response


def rotate(key, expected, actor=None):
    cfg = configured(key)
    with get_store().transaction(), get_store().connection() as db:
        row = row_for(key)
        expect(row, expected)
        if row["state"] != "active" or row["refresh_until"] <= time.time():
            raise DomainError("TIKTOK_REAUTHORIZE", "无法刷新该账号授权，请重新连接。", 409)
        clear = decode_tokens(key, cfg, row["secret"])
        set_state(
            db,
            key,
            expected + 1,
            "refreshing",
            row["secret"],
            row["access_until"],
            row["refresh_until"],
        )
    try:
        data = post_platform(
            "token/",
            {
                "client_key": cfg["client_key"],
                "client_secret": cfg["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": clear["refresh"],
            },
        )
        secret, a, r = encode_tokens(key, cfg, data)
        with get_store().transaction(), get_store().connection() as db:
            expect(row_for(key), expected + 1)
            if configured(key) != cfg or (
                actor
                and not actor_still_authorized(actor, {"owner", "reviewer", "editor", "worker"})
            ):
                raise DomainError("TIKTOK_GRANT_CHANGED", "刷新期间授权环境已变化。", 409)
            set_state(db, key, expected + 2, "active", secret, a, r)
    except DomainError:
        release(key, expected + 1, "refresh_unknown", row)
        raise


def managed_binding(actor, project, account, refresh_if_due=True):
    key = (actor.tenant_id, project, account)
    row = row_for(key)
    if not row:
        return None
    if row["state"] != "active":
        raise DomainError("TIKTOK_REAUTHORIZE", "当前托管授权不能用于读取。", 409)
    if refresh_if_due and row["access_until"] <= time.time() + 60:
        rotate(key, row["revision"], actor)
        row = row_for(key)
    if row["state"] != "active" or row["access_until"] <= time.time():
        raise DomainError("TIKTOK_REAUTHORIZE", "当前凭据已过期或授权状态已改变。", 409)
    cfg = configured(key)
    return cfg["open_id"], decode_tokens(key, cfg, row["secret"])["access"]


@router.post("/accounts/{account}/refresh")
def refresh(account: str, body: Choice, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    key = key_for(actor, body.project_id, account)
    configured(key, request)
    if not row_for(key):
        raise DomainError("TIKTOK_NOT_LINKED", "账号尚未授权。", 409)
    rotate(key, body.expected_revision, actor)
    return ok(public(row_for(key)), request)


@router.post("/accounts/{account}/revoke")
def revoke(account: str, body: Choice, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    key = key_for(actor, body.project_id, account)
    cfg = configured(key, request)
    rev = body.expected_revision
    with get_store().transaction(), get_store().connection() as db:
        row = row_for(key)
        expect(row, rev)
        if row and row["state"] == "exchanging" and row["updated"] > time.time() - 60:
            raise DomainError("TIKTOK_GRANT_BUSY", "正在核对新授权，请稍后重读。", 409)
        if not row:
            return ok(public(None), request)
        if not row["secret"]:
            set_state(db, key, rev + 1, "disconnected")
            return ok(public(row_for(key)), request)
        if row["state"] in {"revoking", "refreshing"} and row["updated"] > time.time() - 60:
            raise DomainError("TIKTOK_GRANT_BUSY", "令牌操作正在进行，请稍后重读。", 409)
        token = decode_tokens(key, cfg, row["secret"])["access"]
        invalidate_read_check(db, key, blocked=True)
        set_state(
            db, key, rev + 1, "revoking", row["secret"], row["access_until"], row["refresh_until"]
        )
    try:
        post_platform(
            "revoke/",
            {
                "client_key": cfg["client_key"],
                "client_secret": cfg["client_secret"],
                "token": token,
            },
        )
    except DomainError:
        release(key, rev + 1, "revoke_unconfirmed", row)
        raise
    release(key, rev + 1, "disconnected")
    return ok(public(row_for(key)), request)
