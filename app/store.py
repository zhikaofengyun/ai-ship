"""Immutable fact versions and event/outbox ledger, transactionally committed.

SQLite is a single-node control plane. BEGIN IMMEDIATE + foreign keys protects
cross-thread/process writes; no network side effect is held inside a transaction.
"""

from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from .catalog import ENTITIES
from .errors import DomainError
from .schemas import validate_record


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def new_id(kind):
    return kind.lower() + "_" + uuid.uuid4().hex


DDL = """
CREATE TABLE IF NOT EXISTS records(
 tenant_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
 project_id TEXT, latest_version INTEGER NOT NULL CHECK(latest_version>=1),
 created_at TEXT NOT NULL, PRIMARY KEY(tenant_id,id));
CREATE INDEX IF NOT EXISTS ix_records_scope ON records(tenant_id,kind,project_id,created_at DESC);
CREATE TABLE IF NOT EXISTS revisions(
 tenant_id TEXT NOT NULL, record_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1),
 data_json TEXT NOT NULL CHECK(json_valid(data_json)), refs_json TEXT NOT NULL CHECK(json_valid(refs_json)),
 content_hash TEXT NOT NULL, created_at TEXT NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(tenant_id,record_id,version),
 FOREIGN KEY(tenant_id,record_id) REFERENCES records(tenant_id,id));
CREATE TABLE IF NOT EXISTS reference_index(
 tenant_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 target_id TEXT NOT NULL, target_version INTEGER NOT NULL, field_name TEXT NOT NULL DEFAULT '',
 consumed_fields_json TEXT NOT NULL DEFAULT '[]', range_json TEXT,
 PRIMARY KEY(tenant_id,source_id,source_version,target_id,target_version,field_name),
 FOREIGN KEY(tenant_id,source_id,source_version) REFERENCES revisions(tenant_id,record_id,version),
 FOREIGN KEY(tenant_id,target_id,target_version) REFERENCES revisions(tenant_id,record_id,version));
CREATE INDEX IF NOT EXISTS ix_ref_target ON reference_index(tenant_id,target_id,target_version);
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
 tenant_id TEXT NOT NULL,project_id TEXT,aggregate_id TEXT,aggregate_version INTEGER,
 event_type TEXT NOT NULL,payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 actor_id TEXT NOT NULL,trace_id TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_scope ON events(tenant_id,project_id,seq);
CREATE TABLE IF NOT EXISTS command_results(
 tenant_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,command_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL,response_json TEXT NOT NULL CHECK(json_valid(response_json)),
 created_at TEXT NOT NULL,PRIMARY KEY(tenant_id,idempotency_key), UNIQUE(tenant_id,command_id));
CREATE TABLE IF NOT EXISTS leases(
 tenant_id TEXT NOT NULL,resource_id TEXT NOT NULL,owner_id TEXT NOT NULL,
 fencing_token INTEGER NOT NULL CHECK(fencing_token>0),expires_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,resource_id));
CREATE TABLE IF NOT EXISTS consumer_offsets(
 tenant_id TEXT NOT NULL,consumer_id TEXT NOT NULL,last_seq INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant_id,consumer_id));
CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only event'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only event'); END;
CREATE TRIGGER IF NOT EXISTS refs_no_update BEFORE UPDATE ON reference_index BEGIN SELECT RAISE(ABORT,'immutable reference'); END;
CREATE TRIGGER IF NOT EXISTS refs_no_delete BEFORE DELETE ON reference_index BEGIN SELECT RAISE(ABORT,'immutable reference'); END;
PRAGMA user_version=1;
"""

# Shared assets/definitions may be explicitly reused within a tenant. Project
# transaction entities remain scoped to the project that owns them.
SHARED_KINDS = {
    "Source",
    "Asset",
    "AssetVersion",
    "Audio",
    "Music",
    "ModelProfile",
    "AgentRole",
    "SkillVersion",
    "Policy",
    "Capability",
    "BlobLocation",
    "Shot",
    "Tag",
    "PreferenceProfile",
    "Grant",
    "Account",
}


class Store:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        conn = self._connect()
        from .index_schema import SEARCH_SCHEMA

        conn.executescript(DDL + SEARCH_SCHEMA)
        conn.close()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def transaction(self):
        active = getattr(self._local, "conn", None)
        if active is not None:
            # A nested failure is allowed to roll back its own work, even if the
            # caller catches it, without poisoning the enclosing transaction.
            point = "sp_" + uuid.uuid4().hex
            active.execute("SAVEPOINT " + point)
            try:
                yield self
                active.execute("RELEASE SAVEPOINT " + point)
            except BaseException:
                active.execute("ROLLBACK TO SAVEPOINT " + point)
                active.execute("RELEASE SAVEPOINT " + point)
                raise
            return
        conn = self._connect()
        self._local.conn = conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield self
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            self._local.conn = None
            conn.close()

    @contextmanager
    def execution_context(
        self, run_id, node_id, fencing_token, tenant_id, actor=None, deadline_at=None
    ):
        previous = getattr(self._local, "execution", None)
        self._local.execution = {
            "run_id": run_id,
            "node_id": node_id,
            "fencing_token": fencing_token,
            "tenant_id": tenant_id,
            "actor": actor,
            "deadline_at": deadline_at,
        }
        try:
            yield self
        finally:
            self._local.execution = previous

    def _check_execution_fence(self):
        scope = getattr(self._local, "execution", None)
        if scope is None:
            return None
        if scope.get("deadline_at") and scope["deadline_at"] <= now():
            raise DomainError("EXECUTION_DEADLINE", "工作流时限已到，禁止提交新的执行产物。", 409)
        if scope.get("actor") is not None:
            from .auth import actor_still_authorized

            if not actor_still_authorized(scope["actor"]):
                raise DomainError(
                    "AUTHORIZATION_REVOKED", "工作进程身份已撤销或权限已改变，禁止提交新产物。", 403
                )
        lease = self._local.conn.execute(
            "SELECT * FROM leases WHERE tenant_id=? AND resource_id=?",
            (scope["tenant_id"], scope["run_id"]),
        ).fetchone()
        if (
            not lease
            or lease["fencing_token"] != scope["fencing_token"]
            or lease["expires_at"] <= now()
        ):
            raise DomainError("STALE_FENCE", "工作流工作进程租约已失效，拒绝提交产物事实。", 409)
        return scope

    @contextmanager
    def read_snapshot(self):
        active = getattr(self._local, "conn", None)
        if active is not None:
            yield active
            return
        with self.connection() as conn:
            conn.execute("BEGIN")
            self._local.conn = conn
            try:
                yield conn
            finally:
                self._local.conn = None
                conn.rollback()

    @contextmanager
    def connection(self):
        active = getattr(self._local, "conn", None)
        if active is not None:
            yield active
            return
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _row_record(self, row):
        data = json.loads(row["data_json"])
        refs = json.loads(row["refs_json"])
        return {
            "id": row["id"],
            "kind": row["kind"],
            "project_id": row["project_id"],
            "tenant_id": row["tenant_id"],
            "version": row["version"],
            "data": data,
            "refs": refs,
            "created_at": row["created_at"],
            "hash": row["content_hash"],
            "status": data.get("status", data.get("state", "draft")),
        }

    def get(self, record_id, tenant_id, version=None):
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 1
        ):
            raise DomainError("INVALID_VERSION", "版本必须是正整数。")
        with self.connection() as c:
            sql = """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.* FROM records r JOIN revisions v
                ON v.tenant_id=r.tenant_id AND v.record_id=r.id
                WHERE r.tenant_id=? AND r.id=? AND v.version="""
            args = [tenant_id, record_id]
            if version is None:
                sql += "r.latest_version"
            else:
                sql += "?"
                args.append(version)
            row = c.execute(sql, args).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", "对象不存在或不在当前工作区。", 404)
            return self._row_record(row)

    def list(self, tenant_id, kind=None, project_id=None, q=None, limit=500):
        if kind is not None and kind not in ENTITIES:
            raise DomainError("UNKNOWN_ENTITY", "未定义的数据类型。")
        if not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise DomainError("INVALID_LIMIT", "查询条数必须为 1 至 2000。")
        where = ["r.tenant_id=?"]
        args = [tenant_id]
        if kind:
            where.append("r.kind=?")
            args.append(kind)
        if project_id:
            where.append("r.project_id=?")
            args.append(project_id)
        if q:
            where.append("(v.data_json LIKE ? ESCAPE '\\' OR r.id LIKE ? ESCAPE '\\')")
            escaped = str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend(["%" + escaped + "%"] * 2)
        sql = """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.* FROM records r JOIN revisions v
         ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
         WHERE """ + " AND ".join(where) + " ORDER BY r.created_at DESC,r.id LIMIT ?"
        args.append(limit)
        with self.connection() as c:
            return [self._row_record(row) for row in c.execute(sql, args)]

    def history(self, record_id, tenant_id):
        self.get(record_id, tenant_id)
        with self.connection() as c:
            rows = c.execute(
                """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.* FROM records r JOIN revisions v
             ON v.tenant_id=r.tenant_id AND v.record_id=r.id WHERE r.tenant_id=? AND r.id=? ORDER BY v.version DESC""",
                (tenant_id, record_id),
            )
            return [self._row_record(r) for r in rows]

    def _scope(self, kind, project_id, tenant_id, record_id, data):
        if data.get("tenant_id") not in (None, tenant_id):
            raise DomainError("TENANT_MISMATCH", "不能覆盖工作区归属。", 403)
        if data.get("project_id") not in (None, project_id):
            raise DomainError("PROJECT_MISMATCH", "数据与外层项目归属不同。", 422)
        if project_id and kind != "Project":
            p = self.get(project_id, tenant_id)
            if p["kind"] != "Project":
                raise DomainError("NOT_PROJECT", "project_id 不是项目。")
        if kind == "Project" and project_id not in (None, record_id):
            raise DomainError("PROJECT_ID", "项目不能归属于另一个项目。")

    def _references(self, kind, data, refs, tenant_id, project_id, record_id):
        if refs is None:
            refs = []
        if not isinstance(refs, list):
            raise DomainError("INVALID_REFERENCES", "引用必须为数组。")
        candidates = [dict(r) if isinstance(r, dict) else {"id": r} for r in refs]
        # Every declared simple relation is resolved to an immutable current
        # version at commit. A supplied explicit reference can pin an old version.
        relation_groups = {}
        for rel in ENTITIES[kind].get("relations", []):
            field = rel.get("field", "")
            target = rel.get("target")
            if "." in field or "[" in field or field not in data:
                continue
            if field == "subject_id" and data.get("subject_kind") != target:
                continue
            relation_groups.setdefault(field, set()).add(target)
        for field, allowed_targets in relation_groups.items():
            val = data[field]
            if val in (None, "", []):
                continue
            vals = val if isinstance(val, list) else [val]
            for value in vals:
                if isinstance(value, str):
                    candidate = {"id": value, "field": field}
                elif isinstance(value, dict) and ("id" in value or "ref" in value):
                    candidate = {**value, "id": value.get("id", value.get("ref")), "field": field}
                else:
                    continue  # nested aggregate fields require explicit refs
                pinned = next(
                    (r for r in candidates if r.get("id") == candidate["id"] and r.get("version")),
                    None,
                )
                if pinned and not candidate.get("version"):
                    candidate["version"] = pinned["version"]
                actual = self.get(candidate["id"], tenant_id, candidate.get("version"))
                declared = candidate.get("kind", actual["kind"])
                if declared != actual["kind"] or actual["kind"] not in allowed_targets:
                    raise DomainError(
                        "REFERENCE_KIND",
                        "引用对象类型不符合声明关系。",
                        details={
                            "field": field,
                            "actual": actual["kind"],
                            "allowed": sorted(allowed_targets),
                        },
                    )
                candidate["kind"] = actual["kind"]
                candidates.append(candidate)
        normalized = []
        seen = set()
        for ref in candidates:
            rid = ref.get("id")
            if not isinstance(rid, str) or not rid:
                raise DomainError("INVALID_REFERENCE", "引用缺少对象 ID。")
            if rid == record_id:
                raise DomainError("SELF_REFERENCE", "对象不能直接引用自身版本形成环。")
            target = self.get(rid, tenant_id, ref.get("version"))
            if ref.get("kind") and ref["kind"] != target["kind"]:
                raise DomainError(
                    "REFERENCE_KIND",
                    "引用对象类型不匹配。",
                    details={"id": rid, "expected": ref["kind"]},
                )
            if (
                project_id
                and target["project_id"] not in (None, project_id)
                and target["kind"] not in SHARED_KINDS
            ):
                raise DomainError("PROJECT_REFERENCE", "不能引用另一项目的执行事务对象。", 403)
            field = ref.get("field", "")
            fields = ref.get("fields", [])
            if (
                not isinstance(field, str)
                or not isinstance(fields, list)
                or any(not isinstance(f, str) for f in fields)
            ):
                raise DomainError("INVALID_REFERENCE", "引用字段必须为文本。")
            interval = ref.get("range")
            if interval is not None:
                from .schemas import _interval

                interval = list(_interval(interval, "dependency range"))
            item = {
                "id": rid,
                "version": target["version"],
                "kind": target["kind"],
                "field": field,
                "fields": fields,
            }
            if interval is not None:
                item["range"] = interval
            key = (rid, item["version"], field)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def create(
        self,
        kind,
        data,
        *,
        tenant_id,
        project_id=None,
        actor_id="local",
        refs=None,
        reason="",
        trace_id=None,
        record_id=None,
    ):
        if (
            isinstance(data, dict)
            and any(k in data for k in ("execution_context", "origin_execution_context"))
            and not getattr(self._local, "execution", None)
        ):
            raise DomainError(
                "RESERVED_PROVENANCE", "执行来源只能由实际工作进程写入，不能手工伪造。", 403
            )
        validate_record(kind, data)
        rid = record_id or new_id(kind)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", rid):
            raise DomainError("INVALID_ID", "对象 ID 格式无效。")
        if kind == "Project":
            project_id = rid
        with self.transaction():
            scope = self._check_execution_fence()
            if scope is not None:
                if scope["tenant_id"] != tenant_id:
                    raise DomainError("TENANT_MISMATCH", "工作进程不能跨租户写入。", 403)
                data = {
                    **data,
                    "execution_context": {
                        k: v
                        for k, v in scope.items()
                        if k not in ("tenant_id", "actor", "deadline_at")
                    },
                }
            self._scope(kind, project_id, tenant_id, rid, data)
            normrefs = self._references(kind, data, refs, tenant_id, project_id, rid)
            stamp = now()
            try:
                self._local.conn.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?)",
                    (tenant_id, rid, kind, project_id, 1, stamp),
                )
            except sqlite3.IntegrityError:
                raise DomainError("ID_EXISTS", "对象 ID 已存在。", 409)
            return self._insert(
                kind,
                rid,
                1,
                data,
                normrefs,
                tenant_id,
                project_id,
                actor_id,
                reason,
                trace_id,
                stamp,
            )

    def update(
        self,
        record_id,
        data,
        *,
        tenant_id,
        expected_revision,
        actor_id="local",
        refs=None,
        reason="",
        trace_id=None,
    ):
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise DomainError("REVISION_REQUIRED", "必须提供 expected_revision 正整数。", 428)
        with self.transaction():
            self._check_execution_fence()
            old = self.get(record_id, tenant_id)
            if old["version"] != expected_revision:
                raise DomainError(
                    "REVISION_CONFLICT",
                    "对象已被修改；请读取最新版本后合并。",
                    409,
                    {
                        "expected_revision": expected_revision,
                        "actual_revision": old["version"],
                        "current": old,
                    },
                )
            if not isinstance(data, dict):
                raise DomainError("INVALID_RECORD", "记录数据必须为对象。")
            data = dict(data)
            if data.get("origin_execution_context") not in (
                None,
                old["data"].get("origin_execution_context"),
            ):
                raise DomainError("RESERVED_PROVENANCE", "不能伪造历史执行来源。", 403)
            scope = getattr(self._local, "execution", None)
            if scope:
                data["execution_context"] = {
                    k: v for k, v in scope.items() if k not in ("tenant_id", "actor", "deadline_at")
                }
            elif data.get("execution_context") not in (None, old["data"].get("execution_context")):
                raise DomainError("RESERVED_PROVENANCE", "不能伪造执行来源。", 403)
            elif old["data"].get("execution_context"):
                # A manual/new-domain revision remains historically linked but
                # cannot masquerade as the exact output of the old worker.
                data.pop("execution_context", None)
                data["origin_execution_context"] = old["data"]["execution_context"]
            validate_record(old["kind"], data)
            self._scope(old["kind"], old["project_id"], tenant_id, record_id, data)
            self._check_locks(old, data, actor_id)
            normrefs = self._references(
                old["kind"],
                data,
                refs if refs is not None else old["refs"],
                tenant_id,
                old["project_id"],
                record_id,
            )
            next_version = old["version"] + 1
            self._local.conn.execute(
                "UPDATE records SET latest_version=? WHERE tenant_id=? AND id=? AND latest_version=?",
                (next_version, tenant_id, record_id, expected_revision),
            )
            return self._insert(
                old["kind"],
                record_id,
                next_version,
                data,
                normrefs,
                tenant_id,
                old["project_id"],
                actor_id,
                reason,
                trace_id,
                now(),
            )

    def _check_locks(self, old, data, actor_id):
        # Explicit lock release is a separate command; generic record replacement
        # must not erase a manual lock or silently overwrite a protected field.
        locks = old["data"].get("locks", [])
        if isinstance(locks, dict):
            locks = [
                {"path": k, **(v if isinstance(v, dict) else {"value": v})}
                for k, v in locks.items()
            ]
        for lock in locks:
            if not isinstance(lock, dict):
                continue
            path = lock.get("path", lock.get("parameter_path"))
            if path:

                def read(obj, p):
                    for key in p.split("."):
                        if not isinstance(obj, dict):
                            return None
                        obj = obj.get(key)
                    return obj

                if read(old["data"], path) != read(data, path):
                    raise DomainError(
                        "LOCK_CONFLICT",
                        "修改触及人工锁；请先提出变更或显式解锁。",
                        409,
                        {"path": path, "owner": lock.get("owner")},
                    )
        if locks and old["data"].get("locks") != data.get("locks"):
            raise DomainError("LOCK_REMOVAL", "通用更新不能删除人工锁。", 409)

    def _insert(
        self, kind, rid, version, data, refs, tenant, project, actor, reason, trace_id, stamp
    ):
        body = {"kind": kind, "data": data, "refs": refs, "project_id": project, "version": version}
        hashed = digest(body)
        c = self._local.conn
        c.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?)",
            (
                tenant,
                rid,
                version,
                canonical(data),
                canonical(refs),
                hashed,
                stamp,
                actor,
                str(reason),
            ),
        )
        for ref in refs:
            c.execute(
                "INSERT INTO reference_index VALUES(?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    rid,
                    version,
                    ref["id"],
                    ref["version"],
                    ref.get("field", ""),
                    canonical(ref.get("fields", [])),
                    canonical(ref["range"]) if "range" in ref else None,
                ),
            )
        self.append_event(
            tenant_id=tenant,
            project_id=project,
            event_type=f"{kind.lower()}.version_committed",
            payload={
                "kind": kind,
                "id": rid,
                "version": version,
                "hash": hashed,
                "reason": str(reason),
                "refs": refs,
            },
            actor_id=actor,
            trace_id=trace_id,
            aggregate_id=rid,
            aggregate_version=version,
        )
        return self.get(rid, tenant, version)

    def append_event(
        self,
        *,
        tenant_id,
        event_type,
        payload,
        project_id=None,
        actor_id="local",
        trace_id=None,
        aggregate_id=None,
        aggregate_version=None,
    ):
        from .schemas import validate_json

        validate_json(payload)
        with self.transaction():
            event = {
                "id": new_id("Event"),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "aggregate_id": aggregate_id,
                "aggregate_version": aggregate_version,
                "type": event_type,
                "payload": payload,
                "actor_id": actor_id,
                "trace_id": trace_id or uuid.uuid4().hex,
                "created_at": now(),
            }
            cur = self._local.conn.execute(
                "INSERT INTO events(event_id,tenant_id,project_id,aggregate_id,aggregate_version,event_type,payload_json,actor_id,trace_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    event["id"],
                    tenant_id,
                    project_id,
                    aggregate_id,
                    aggregate_version,
                    event_type,
                    canonical(payload),
                    actor_id,
                    event["trace_id"],
                    event["created_at"],
                ),
            )
            event["sequence"] = cur.lastrowid
            return event

    def events(self, tenant_id, project_id=None, limit=500, after=0, ascending=False):
        where = "tenant_id=? AND seq>?"
        args = [tenant_id, after]
        if project_id:
            where += " AND project_id=?"
            args.append(project_id)
        args.append(min(max(limit, 1), 2000))
        with self.connection() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE "
                + where
                + (" ORDER BY seq ASC LIMIT ?" if ascending else " ORDER BY seq DESC LIMIT ?"),
                args,
            )
            return [
                {
                    "id": r["event_id"],
                    "sequence": r["seq"],
                    "project_id": r["project_id"],
                    "aggregate_id": r["aggregate_id"],
                    "aggregate_version": r["aggregate_version"],
                    "type": r["event_type"],
                    "payload": json.loads(r["payload_json"]),
                    "actor_id": r["actor_id"],
                    "trace_id": r["trace_id"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def idempotent(self, tenant_id, key, command_id, payload, operation):
        if not key or len(key) > 240:
            raise DomainError("IDEMPOTENCY_REQUIRED", "必须提供有效幂等键。")
        fingerprint = digest(payload)
        with self.transaction():
            row = self._local.conn.execute(
                "SELECT * FROM command_results WHERE tenant_id=? AND (idempotency_key=? OR command_id=?)",
                (tenant_id, key, command_id),
            ).fetchone()
            if row:
                if (
                    row["payload_hash"] != fingerprint
                    or row["idempotency_key"] != key
                    or row["command_id"] != command_id
                ):
                    raise DomainError(
                        "IDEMPOTENCY_CONFLICT", "幂等键或命令 ID 已用于不同请求。", 409
                    )
                result = json.loads(row["response_json"])
                result["idempotent_replay"] = True
                return result
            result = operation()
            self._local.conn.execute(
                "INSERT INTO command_results VALUES(?,?,?,?,?,?)",
                (tenant_id, key, command_id, fingerprint, canonical(result), now()),
            )
            return result

    def impact(self, tenant_id, changes):
        affected = {}
        retained = {}
        frontier = []
        visited = set()
        for change in changes:
            rid = change.get("id", change.get("record_id"))
            self.get(rid, tenant_id, change.get("version"))
            frontier.append(
                (rid, change.get("version"), change.get("fields", []), change.get("range"))
            )
        with self.connection() as c:
            while frontier:
                rid, version, fields, interval = frontier.pop(0)
                key = (rid, version)
                if key in visited:
                    continue
                visited.add(key)
                sql = """SELECT x.* FROM reference_index x JOIN records r ON r.tenant_id=x.tenant_id AND r.id=x.source_id AND r.latest_version=x.source_version WHERE x.tenant_id=? AND x.target_id=?"""
                args = [tenant_id, rid]
                if version is not None:
                    sql += " AND x.target_version=?"
                    args.append(version)
                for row in c.execute(sql, args):
                    consumed = json.loads(row["consumed_fields_json"])
                    rr = json.loads(row["range_json"]) if row["range_json"] else None
                    field_hit = (
                        not fields
                        or not consumed
                        or any(
                            a == b or a.startswith(b + ".") or b.startswith(a + ".")
                            for a in fields
                            for b in consumed
                        )
                    )
                    range_hit = (
                        interval is None
                        or rr is None
                        or (max(interval[0], rr[0]) < min(interval[1], rr[1]))
                    )
                    obj = {
                        "id": row["source_id"],
                        "version": row["source_version"],
                        "via": rid,
                        "field": row["field_name"],
                        "reason": (
                            "真实版本依赖与修改字段/区间相交"
                            if field_hit and range_hit
                            else "修改未触及已记录消费字段或区间"
                        ),
                    }
                    if field_hit and range_hit:
                        affected[obj["id"]] = obj
                        retained.pop(obj["id"], None)
                        frontier.append((obj["id"], obj["version"], [], None))
                    elif obj["id"] not in affected:
                        retained[obj["id"]] = obj
        return {
            "affected": list(affected.values()),
            "retained": list(retained.values()),
            "basis": "version_reference_index",
            "navigation_edges_used": False,
            "conservative_if_consumption_unknown": True,
        }


_SINGLETONS = {}
_LOCK = threading.Lock()


def get_store():
    root = Path(os.getenv("AI_SHIP_DATA_DIR", str(Path(__file__).resolve().parents[1] / ".data")))
    path = os.getenv("AI_SHIP_DB", str(root / "ai-ship.sqlite3"))
    with _LOCK:
        if path not in _SINGLETONS:
            _SINGLETONS[path] = Store(path)
        return _SINGLETONS[path]
