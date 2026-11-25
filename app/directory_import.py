"""Incremental, descriptor-relative intake from an explicitly assigned capture directory."""

from contextlib import contextmanager
from hashlib import sha256
import fcntl
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import time

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from .api import Body, ok
from .auth import Actor, current_actor, require_write, actor_still_authorized
from .errors import DomainError
from .media import MAX_BYTES, PreparedImport, import_saved_file, probe_media, persist_blob
from .production import record
from .store import canonical, digest, get_store

router = APIRouter(prefix="/production/directory-imports", tags=["directory intake"])
DIR = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
DDL = """CREATE TABLE IF NOT EXISTS directory_inputs (
    scope TEXT NOT NULL, path TEXT NOT NULL, signature TEXT NOT NULL, expected TEXT NOT NULL,
    observed REAL NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL,
    PRIMARY KEY(scope,path)) WITHOUT ROWID"""
EXT = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
}


class Entry(Body):
    path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")


class Choice(Body):
    project_id: str
    root: str = Field(min_length=1, max_length=100)
    snapshot: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest: list[Entry] | None = Field(default=None, min_length=1, max_length=50000)


class Advance(Choice):
    limit: int = Field(default=16, ge=1, le=64)
    retry_failed: bool = False


def roots(actor, pid):
    record(actor, pid, "Project")
    try:
        cfg = json.loads(os.getenv("AI_SHIP_CAPTURE_ROOTS", "[]"))
        if not isinstance(cfg, list):
            raise ValueError()
        out = {}
        for item in cfg:
            if item["tenant_id"] != actor.tenant_id or item["project_id"] not in {pid, "*"}:
                continue
            name = item["alias"]
            path = item["path"]
            delay = item.get("stable_seconds", 10)
            if (
                not re.fullmatch(r"[\w.-]{1,100}", name)
                or not os.path.isabs(path)
                or ".." in Path(path).parts
                or not 1 <= delay <= 86400
                or name in out
            ):
                raise ValueError()
            out[name] = (Path(path), delay)
        return out
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DomainError("CAPTURE_CONFIGURATION", "服务器采集目录配置无效。", 503)


def relative(value):
    parts = value.split("/")
    if (
        any(p in {"", ".", ".."} for p in parts)
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or len(parts) > 48
    ):
        raise DomainError("INVALID_CAPTURE_PATH", "清单必须使用根目录内的相对路径。")
    return parts


@contextmanager
def opened_at(base, parts, flags):
    fd = os.dup(base)
    try:
        for i, part in enumerate(parts):
            nxt = os.open(part, flags if i == len(parts) - 1 else DIR, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


@contextmanager
def scope(actor, body):
    require_write(actor)
    conf = roots(actor, body.project_id).get(body.root)
    if not conf:
        raise DomainError("CAPTURE_ROOT_FORBIDDEN", "该项目没有此采集目录。", 403)
    path, delay = conf
    entries = None if body.manifest is None else {}
    for item in body.manifest or []:
        relative(item.path)
        if item.path in entries:
            raise DomainError("DUPLICATE_CAPTURE_PATH", "清单中同一相对路径出现多次。")
        entries[item.path] = item.sha256
    with get_store().connection() as db:
        db.execute(DDL)
        db.execute(
            "CREATE TABLE IF NOT EXISTS directory_scopes (id TEXT PRIMARY KEY, origin TEXT NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_directory_inputs_state ON directory_inputs(scope,state,observed,path)"
        )
    base = os.open("/", DIR)
    try:
        with opened_at(base, path.parts[1:], DIR) as fd:
            st = os.fstat(fd)
            origin = digest(
                [actor.tenant_id, body.project_id, body.root, str(path), st.st_dev, st.st_ino]
            )
            with get_store().connection() as db:
                if body.snapshot:
                    if (
                        entries is not None
                        or not db.execute(
                            "SELECT 1 FROM directory_scopes WHERE id=? AND origin=?",
                            (body.snapshot, origin),
                        ).fetchone()
                    ):
                        raise DomainError(
                            "CAPTURE_SNAPSHOT_SCOPE", "此清单与当前项目目录不匹配。", 403
                        )
                    key = body.snapshot
                else:
                    key = digest([origin, entries])
                    db.execute("INSERT OR IGNORE INTO directory_scopes VALUES (?,?)", (key, origin))
            yield fd, delay, entries, key, origin
    except OSError as e:
        raise DomainError("CAPTURE_UNAVAILABLE", "无法安全读取采集目录。", 409) from e
    finally:
        os.close(base)


def signature(st):
    return canonical([st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns])


def inspect(st, rel):
    sig = signature(st)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        return sig, "blocked", "仅接收普通文件，跳过软链接、硬链接与设备。"
    if PurePosixPath(rel).suffix.lower() not in EXT:
        return sig, "blocked", "扩展名未经支持或文件仍为临时文件。"
    if st.st_size > MAX_BYTES:
        return sig, "blocked", "文件超过单件上限。"
    return sig, "waiting", ""


def one(fd, rel):
    parts = relative(rel)
    try:
        with opened_at(fd, parts[:-1], DIR) as parent:
            return inspect(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False), rel)
    except OSError:
        return "missing", "blocked", "文件不存在、不可读取或父目录为链接。"


def walk(fd, prefix=""):
    with os.scandir(fd) as it:
        for entry in it:
            rel = prefix + entry.name
            try:
                relative(rel)
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    with opened_at(fd, [entry.name], DIR) as sub:
                        yield from walk(sub, rel + "/")
                else:
                    yield rel, "", inspect(st, rel)
            except (OSError, DomainError):
                yield rel, "", ("invalid", "blocked", "无法安全遍历该项目或目录过深。")


def remember(db, key, path, expected, info):
    sig, state, reason = info
    db.execute(
        """INSERT INTO directory_inputs VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(scope,path) DO UPDATE SET signature=excluded.signature,expected=excluded.expected,
        observed=excluded.observed,state=excluded.state,result=excluded.result
        WHERE directory_inputs.signature!=excluded.signature OR directory_inputs.expected!=excluded.expected OR
        (excluded.state='blocked' AND directory_inputs.state!='blocked')""",
        (
            key,
            path,
            sig,
            expected,
            time.time(),
            state,
            canonical({"reason": reason} if reason else {}),
        ),
    )


def summary(key, after="", limit=50):
    with get_store().connection() as db:
        counts = dict(
            db.execute(
                "SELECT state, count(*) FROM directory_inputs WHERE scope=? GROUP BY state", (key,)
            )
        )
        rows = db.execute(
            "SELECT path,state,result FROM directory_inputs WHERE scope=? AND path>? ORDER BY path LIMIT ?",
            (key, after, limit + 1),
        ).fetchall()
    return {
        "snapshot": key,
        "counts": counts,
        "items": [
            {"path": r["path"], "state": r["state"], **json.loads(r["result"])}
            for r in rows[:limit]
        ],
        "next_after": rows[limit - 1]["path"] if len(rows) > limit else None,
    }


@router.get("/roots")
def list_roots(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    return ok(
        {
            "roots": [
                {"alias": k, "stable_seconds": v[1]} for k, v in roots(actor, project_id).items()
            ]
        },
        request,
    )


@router.post("/observe")
def observe(body: Choice, request: Request, actor: Actor = Depends(current_actor)):
    if body.snapshot:
        raise DomainError("CAPTURE_MANIFEST_REQUIRED", "重新观察需使用原始清单。", 422)
    with scope(actor, body) as (fd, delay, entries, key, _):
        rows = walk(fd) if entries is None else ((p, h, one(fd, p)) for p, h in entries.items())
        for group in itertools.batched(rows, 256):
            with get_store().transaction(), get_store().connection() as db:
                for path, expected, info in group:
                    remember(db, key, path, expected, info)
        return ok({**summary(key), "stable_seconds": delay}, request)


def process(actor, body, fd, key, origin, row):
    rel = row["path"]
    before = one(fd, rel)
    store = get_store()
    if before[0] != row["signature"] or before[1] != "waiting":
        with store.connection() as db:
            remember(db, key, rel, row["expected"], before)
        return
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / ("capture" + PurePosixPath(rel).suffix)
        sha = sha256()
        with opened_at(fd, relative(rel), FILE) as source, staged.open("xb") as out:
            first = os.fstat(source)
            if signature(first) != before[0] or inspect(first, rel)[1] != "waiting":
                return
            if first.st_size == 0:
                return
            try:
                fcntl.flock(source, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            size = 0
            while chunk := os.read(source, 1024 * 1024):
                size += len(chunk)
                if size > first.st_size or size > MAX_BYTES:
                    return
                sha.update(chunk)
                out.write(chunk)
            if size != first.st_size or signature(os.fstat(source)) != before[0]:
                return
        if one(fd, rel) != before:
            return
        content_hash = sha.hexdigest()
        if row["expected"] and row["expected"] != content_hash:
            raise DomainError("CAPTURE_HASH_MISMATCH", "实读字节与交付清单的 SHA-256 不一致。", 409)
        # FFprobe and content-addressed writes must not occupy the SQLite writer.
        probe = probe_media(staged)
        blob = persist_blob(staged, actor.tenant_id)
        if blob["content_hash"] != content_hash:
            raise DomainError("CAPTURE_HASH_MISMATCH", "暂存副本的内容校验失败。", 409)
        prepared = PreparedImport(content_hash, probe, blob)
        key_id = "capture:" + digest([origin, rel, before[0], content_hash])
        origin_data = {
            "root_alias": body.root,
            "root_identity": origin,
            "relative_path": rel,
            "content_hash": content_hash,
            "stat_signature": json.loads(before[0]),
            "manifest_sha256": row["expected"] or None,
        }

        def intake():
            result = import_saved_file(
                store,
                actor,
                body.project_id,
                rel,
                staged,
                directory_origin=origin_data,
                prepared=prepared,
            )
            entry = result["import_entry"]
            return {
                "asset_id": result["asset"]["id"],
                "source_id": entry["data"]["source_id"],
                "entry_id": entry["id"],
                "content_hash": content_hash,
                "duplicate": result["duplicate"],
            }

        with store.transaction(), store.connection() as db:
            live = db.execute(
                "SELECT signature,state FROM directory_inputs WHERE scope=? AND path=?", (key, rel)
            ).fetchone()
            if (
                live["signature"] != before[0]
                or live["state"] != "waiting"
                or one(fd, rel) != before
            ):
                return
            if not actor_still_authorized(actor):
                raise DomainError("FORBIDDEN", "当前身份已失去写入授权。", 403)
            receipt = store.idempotent(
                actor.tenant_id,
                key_id,
                key_id,
                {"origin": origin, "path": rel, "signature": before[0]},
                intake,
            )
            # Reopen the configured path from / without following any component links.
            root_path = roots(actor, body.project_id).get(body.root)
            if not root_path:
                raise DomainError("CAPTURE_ROOT_FORBIDDEN", "该采集目录的授权已撤销。", 403)
            top = os.open("/", DIR)
            try:
                with opened_at(top, root_path[0].parts[1:], DIR) as current:
                    if (
                        not os.path.samestat(os.fstat(current), os.fstat(fd))
                        or one(current, rel) != before
                    ):
                        raise DomainError(
                            "CAPTURE_SOURCE_CHANGED", "提交时物理来源已变化，请重新观察。", 409
                        )
            finally:
                os.close(top)
            receipt["manifest_sha256"] = row["expected"] or None
            state = (
                "deduplicated"
                if receipt["duplicate"] or receipt.get("idempotent_replay")
                else "imported"
            )
            db.execute(
                "UPDATE directory_inputs SET state=?,result=? WHERE scope=? AND path=?",
                (state, canonical(receipt), key, rel),
            )


@router.post("/advance")
def advance(body: Advance, request: Request, actor: Actor = Depends(current_actor)):
    with scope(actor, body) as (fd, delay, _, key, origin):
        with get_store().connection() as db:
            if body.retry_failed:
                db.execute(
                    "UPDATE directory_inputs SET state='waiting',observed=? WHERE scope=? AND state='failed'",
                    (time.time(), key),
                )
            rows = db.execute(
                "SELECT * FROM directory_inputs WHERE scope=? AND state='waiting' AND observed<=? ORDER BY observed,path LIMIT ?",
                (key, time.time() - delay, body.limit),
            ).fetchall()
        for row in rows:
            try:
                process(actor, body, fd, key, origin, row)
            except (DomainError, OSError) as err:
                problem = {
                    "code": getattr(err, "code", "CAPTURE_READ_FAILED"),
                    "reason": getattr(err, "message", "读取中断，请重新观察或重试。"),
                }
                with get_store().connection() as db:
                    db.execute(
                        "UPDATE directory_inputs SET state='failed',result=? WHERE scope=? AND path=? AND signature=? AND state='waiting'",
                        (canonical(problem), key, row["path"], row["signature"]),
                    )
        return ok({**summary(key), "stable_seconds": delay}, request)


@router.post("/items")
def items(
    body: Choice,
    request: Request,
    after: str = Query("", max_length=1024),
    limit: int = Query(50, ge=1, le=500),
    actor: Actor = Depends(current_actor),
):
    with scope(actor, body) as (_, _, _, key, _):
        return ok(summary(key, after, limit), request)
