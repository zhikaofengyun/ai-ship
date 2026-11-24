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
import tempfile
import zipfile


def restore(archive_path: Path, destination: Path, max_bytes: int = 100_000_000_000) -> dict:
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
                for row in database.execute(
                    "SELECT v.data_json FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version WHERE r.kind='BlobLocation'"
                ):
                    blob = json.loads(row[0])
                    if blob.get("availability") != "available":
                        continue
                    key = blob.get("object_key", "")
                    candidate = (temporary / key).resolve()
                    if (
                        not key.startswith("objects/")
                        or not candidate.is_relative_to(temporary)
                        or not candidate.is_file()
                    ):
                        raise ValueError("可用媒体对象缺失，恢复不能报告成功。")
                    with candidate.open("rb") as handle:
                        actual = hashlib.file_digest(handle, "sha256").hexdigest()
                    if actual != (blob.get("checksum") or blob.get("content_hash")):
                        raise ValueError("媒体对象与数据库字节 hash 不一致。")
        # A competing restore may have created destination; never replace it.
        if destination.exists():
            raise ValueError("恢复过程中目标目录已出现，请选择新目录。")
        os.rename(temporary, destination)
        return {
            "restored": str(destination),
            "files": len(items),
            "database_integrity": "ok",
            "byte_hashes_verified": True,
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--max-bytes", type=int, default=100_000_000_000)
    args = parser.parse_args()
    try:
        print(
            json.dumps(restore(args.archive, args.destination, args.max_bytes), ensure_ascii=False)
        )
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, sqlite3.DatabaseError) as error:
        parser.exit(1, f"恢复未完成：{error}\n")


if __name__ == "__main__":
    main()
