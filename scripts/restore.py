"""Verify a backup completely, then restore into a new directory atomically.

Never merge with an existing workspace. Stop the application before selecting
the restored directory as AI_SHIP_DATA_DIR; sessions and API keys are not backed up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
from contextlib import closing, nullcontext
import tempfile
import zipfile


def validate_blob_locations(database, root, *, recovery_budget=None):
    """Check cold and online objects. Only a restore may reconstruct an absent online replica."""
    cold = {}
    online = []
    rows = database.execute("""SELECT r.tenant_id,r.id,v.data_json,v.refs_json
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id
            AND v.version=r.latest_version WHERE r.kind='BlobLocation'""")
    for tenant, blob_id, encoded, refs_json in rows:
        blob = json.loads(encoded)
        is_cold = (
            blob.get("storage_tier") == "local_cold"
            and blob.get("availability") == "restore_required"
        )
        if not is_cold and blob.get("availability") != "available":
            continue
        key = blob.get("object_key")
        if (
            not isinstance(key, str)
            or not key.startswith("objects/")
            or ".." in PurePosixPath(key).parts
        ):
            raise ValueError("媒体对象位置无效。")
        path = (root / key).resolve()
        if not path.is_relative_to(root / "objects"):
            raise ValueError("媒体对象位置超出备份范围。")
        checksum = blob.get("checksum") or blob.get("content_hash")
        if not is_cold:
            online.append((tenant, blob_id, key, checksum, path))
            continue
        prefix = hashlib.sha256(tenant.encode()).hexdigest()[:24]
        if (
            not isinstance(checksum, str)
            or key != f"objects/{prefix}/cold/{checksum[:2]}/{checksum}"
            or not path.is_file()
        ):
            raise ValueError("本地冷藏副本缺失或位置无效。")
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != checksum:
                raise ValueError("本地冷藏副本与数据库摘要不一致。")
        for ref in json.loads(refs_json):
            if ref.get("kind") == "BlobLocation":
                cold[(tenant, ref["id"], checksum)] = path

    for tenant, blob_id, key, checksum, path in online:
        if not path.is_file():
            source = cold.get((tenant, blob_id, checksum))
            prefix = hashlib.sha256(tenant.encode()).hexdigest()[:24]
            if source is None or key != f"objects/{prefix}/{checksum[:2]}/{checksum}":
                raise ValueError("可用媒体对象缺失，且没有经核实的同租户冷藏副本。")
            if recovery_budget is None:
                continue
            size = source.stat().st_size
            if size > recovery_budget:
                raise ValueError("恢复在线副本后的总大小超过明确上限。")
            recovery_budget -= size
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != checksum:
                raise ValueError("媒体对象与数据库登记的摘要不一致。")


def isolate_changed_media(archive, authority):
    """Compare actual rights-bearing records, including grants attached below a source."""
    rights = {
        "status",
        "rights_status",
        "revoked_at",
        "deleted_at",
        "expires_at",
        "valid_until",
        "valid_from",
        "validity",
        "processing_scope",
        "storage_tier",
        "blob_location_id",
        "source_id",
        "grant_id",
        "grant_ids",
        "rights_grant_ref",
        "rights_grant_ids",
        "provenance_sources",
        "parent_asset_version_ids",
    }
    held = 0
    rows = archive.execute(
        """SELECT r.id,r.kind,r.tenant_id,v.content_hash,v.data_json,v.refs_json FROM records r
        JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
        WHERE r.kind IN ('Grant','Asset','AssetVersion','Audio','Artifact','BlobLocation')"""
    )
    for rid, kind, tenant, old_hash, old_json, old_refs in rows:
        current = (
            authority.execute(
                """SELECT v.content_hash,v.data_json,v.refs_json FROM records r
            JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE r.tenant_id=? AND r.id=? AND r.kind=?""",
                (tenant, rid, kind),
            ).fetchone()
            if authority is not None
            else None
        )
        if current and current[0] == old_hash:
            continue
        if current and kind != "Grant":
            old, live = json.loads(old_json), json.loads(current[1])
            if all(old.get(k) == live.get(k) for k in rights) and json.loads(
                old_refs
            ) == json.loads(current[2]):
                continue
        archive.execute(
            """INSERT OR REPLACE INTO media_restore_restrictions
                  VALUES(?,?,?,?,?,?)""",
            (
                tenant,
                rid,
                kind,
                old_hash,
                current[0] if current else None,
                "rights_changed" if current else "current_object_unverified",
            ),
        )
        held += 1
    return held


def apply_current_governance(database_path: Path, authority_path: Path | None):
    # The runner is also invoked as "python scripts/restore.py" outside an installed package.
    if str(Path(__file__).resolve().parents[1]) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.store import Store

    restored = Store(database_path)
    authority = (
        closing(sqlite3.connect(authority_path.resolve().as_uri() + "?mode=ro", uri=True))
        if authority_path
        else nullcontext(None)
    )
    result = {
        "sources_checked": 0,
        "sources_isolated": 0,
        "leases_fenced": 0,
        "authority_available": bool(authority_path),
    }
    with authority as current, restored.transaction(), restored.connection() as db:
        authority_has_admission = False
        if current is not None:
            current.row_factory = sqlite3.Row
            current.execute("BEGIN")
            authority_has_admission = bool(
                current.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_admission_log'"
                ).fetchone()
            )
        # A restored worker must reacquire a fresh token even when the backup captured a live lease.
        for lease in db.execute(
            "SELECT tenant_id,resource_id,fencing_token FROM leases"
        ).fetchall():
            newest = (
                current.execute(
                    "SELECT fencing_token FROM leases WHERE tenant_id=? AND resource_id=?",
                    lease[:2],
                ).fetchone()
                if current is not None
                else None
            )
            token = max(lease[2], newest[0] if newest else 0) + 1
            db.execute(
                "UPDATE leases SET fencing_token=?,expires_at='1970-01-01T00:00:00+00:00' WHERE tenant_id=? AND resource_id=?",
                (token, *lease[:2]),
            )
            result["leases_fenced"] += 1
        if current is not None:
            # A lease issued only after the backup also fences a future resource of the same name.
            for tenant, rid, token in current.execute(
                "SELECT tenant_id,resource_id,fencing_token FROM leases"
            ):
                if db.execute(
                    """INSERT OR IGNORE INTO leases VALUES(?,?,?,?,'1970-01-01T00:00:00+00:00')""",
                    (tenant, rid, "backup-restore", token + 1),
                ).rowcount:
                    result["leases_fenced"] += 1
        result["media_or_grants_held"] = isolate_changed_media(db, current)
        sources = db.execute("""SELECT r.id,r.project_id,r.tenant_id,v.content_hash,r.latest_version
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE r.kind='Source'""")
        for sid, project, tenant, source_hash, version in sources:
            result["sources_checked"] += 1
            local = db.execute(
                """SELECT revision,decision_id FROM media_admission_log
                WHERE tenant=? AND project=? AND source=? ORDER BY revision DESC LIMIT 1""",
                (tenant, project or "", sid),
            ).fetchone()
            latest = (
                current.execute(
                    """SELECT v.content_hash,v.data_json FROM records r JOIN revisions v
                ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
                WHERE r.tenant_id=? AND r.project_id IS ? AND r.id=? AND r.kind='Source'""",
                    (tenant, project, sid),
                ).fetchone()
                if current is not None
                else None
            )
            latest_decision = (
                current.execute(
                    """SELECT a.decision_id,v.content_hash,v.data_json FROM media_admission_log a
                JOIN revisions v ON v.tenant_id=a.tenant AND v.record_id=a.decision_id AND v.version=1
                WHERE a.tenant=? AND a.project=? AND a.source=? ORDER BY a.revision DESC LIMIT 1""",
                    (tenant, project or "", sid),
                ).fetchone()
                if authority_has_admission
                else None
            )
            if (
                latest
                and latest["content_hash"] == source_hash
                and (latest_decision[0] if latest_decision else None)
                == (local[1] if local else None)
            ):
                continue
            info = json.loads(latest_decision["data_json"]) if latest_decision else {}
            action = "reconciled" if info.get("action") == "reconciled" else "quarantine"
            revision = (local[0] if local else 0) + 1
            reason = "恢复时重核当前来源与墓碑，缺失或不一致的授权隔离待对账。"
            refs = [{"id": sid, "version": version}]
            if local:
                refs.append({"id": local[1], "version": 1})
            item = restored.create(
                "Approval",
                {
                    "feature_id": "D06",
                    "route_id": "D06-R3",
                    "kind": "rights_check",
                    "status": "revoked",
                    "action": action,
                    "allowed_purposes": [],
                    "subject_kind": "Source",
                    "subject_id": sid,
                    "subject_version": version,
                    "subject_hash": source_hash,
                    "admission_revision": revision,
                    "principal": "backup-restore",
                    "reason": reason,
                    "retention_restore_hold": True,
                    "current_source_hash": latest["content_hash"] if latest else None,
                    "current_decision_id": latest_decision[0] if latest_decision else None,
                    "current_decision_hash": latest_decision[1] if latest_decision else None,
                },
                tenant_id=tenant,
                project_id=project,
                actor_id="backup-restore",
                refs=refs,
                reason=reason,
            )
            db.execute(
                "INSERT INTO media_admission_log VALUES(?,?,?,?,?)",
                (tenant, project or "", sid, revision, item["id"]),
            )
            result["sources_isolated"] += 1
    return result


def restore(
    archive_path: Path,
    destination: Path,
    max_bytes: int = 100_000_000_000,
    *,
    current_db: Path | None = None,
) -> dict:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("目标已存在；恢复必须使用全新目录，禁止覆盖工作区。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".ai-ship-restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if len(names) != len(set(names)) or len(members) > 100000:
                raise ValueError("备份包含重复或过多条目。")
            if sum(m.file_size for m in members) > max_bytes:
                raise ValueError("备份展开大小超过明确上限。")
            for member in members:
                path = PurePosixPath(member.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.filename
                    or member.is_dir()
                ):
                    raise ValueError("备份包含不安全路径或目录条目。")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("备份不能包含符号链接。")
            manifest_info = archive.getinfo("backup-manifest.json")
            if manifest_info.file_size > 20_000_000:
                raise ValueError("备份清单过大。")
            manifest = json.loads(archive.read(manifest_info))
            if manifest.get("format") != "ai-ship-backup-v1":
                raise ValueError("未知备份格式。")
            items = manifest.get("files", [])
            expected_names = [item["path"] for item in items]
            if len(expected_names) != len(set(expected_names)) or set(names) != set(
                expected_names
            ) | {"backup-manifest.json"}:
                raise ValueError("实际文件与备份清单不一致。")
            if "ai-ship.sqlite3" not in expected_names:
                raise ValueError("备份缺少数据库。")
            for item in items:
                relative = item["path"]
                if relative != "ai-ship.sqlite3" and not relative.startswith("objects/"):
                    raise ValueError("备份只允许数据库和不可变媒体对象。")
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open(relative) as source, target.open("xb") as output:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(block)
                        if size > item["size"]:
                            raise ValueError("条目大小与清单不一致。")
                        digest.update(block)
                        output.write(block)
                if size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError("备份字节 hash 校验失败。")
            with sqlite3.connect(str(temporary / "ai-ship.sqlite3")) as database:
                if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("恢复数据库完整性校验失败。")
                if database.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("恢复数据库外键校验失败。")
                validate_blob_locations(
                    database,
                    temporary,
                    recovery_budget=max_bytes - sum(m.file_size for m in members),
                )
        if current_db is not None and not current_db.is_file():
            raise ValueError("指定的现行授权数据库不存在。")
        if current_db is None:
            configured = os.getenv("AI_SHIP_DB")
            if not configured and os.getenv("AI_SHIP_DATA_DIR"):
                configured = str(Path(os.environ["AI_SHIP_DATA_DIR"]) / "ai-ship.sqlite3")
            if configured and Path(configured).is_file():
                current_db = Path(configured)
        governance = apply_current_governance(temporary / "ai-ship.sqlite3", current_db)
        # A competing restore may have created destination; never replace it.
        if destination.exists():
            raise ValueError("恢复过程中目标目录已出现，请选择新目录。")
        os.rename(temporary, destination)
        return {
            "restored": str(destination),
            "files": len(items),
            "database_integrity": "ok",
            "byte_hashes_verified": True,
            "governance": governance,
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--max-bytes", type=int, default=100_000_000_000)
    parser.add_argument(
        "--current-db", type=Path, help="可信现行数据库，用于在恢复时重放来源隔离和失效租约。"
    )
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                restore(args.archive, args.destination, args.max_bytes, current_db=args.current_db),
                ensure_ascii=False,
            )
        )
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, sqlite3.DatabaseError) as error:
        parser.exit(1, f"恢复未完成：{error}\n")


if __name__ == "__main__":
    main()
