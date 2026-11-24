"""Consistent SQLite snapshot plus immutable objects; temporary uploads excluded."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(
        description="Back up AI Ship without copying a live WAL file incorrectly"
    )
    parser.add_argument("--data-dir", default=os.getenv("AI_SHIP_DATA_DIR", ".data"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.data_dir).resolve()
    db = Path(os.getenv("AI_SHIP_DB", str(root / "ai-ship.sqlite3"))).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("输出已存在，避免覆盖已有备份。")
    if not db.is_file():
        parser.error("数据库不存在。")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": "ai-ship-backup-v1",
        "files": [],
    }
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "ai-ship.sqlite3"
        with sqlite3.connect(str(db)) as source, sqlite3.connect(str(snapshot)) as target:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("SQLite integrity check failed")
        files = [(snapshot, "ai-ship.sqlite3")]
        objects = root / "objects"
        if objects.exists():
            files.extend(
                (p, str(p.relative_to(root)))
                for p in sorted(objects.rglob("*"))
                if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp")
            )
        with zipfile.ZipFile(
            output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1
        ) as archive:
            for path, arc in files:
                checksum = hashlib.sha256()
                with path.open("rb") as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        checksum.update(chunk)
                archive.write(path, arc)
                manifest["files"].append(
                    {"path": arc, "size": path.stat().st_size, "sha256": checksum.hexdigest()}
                )
            archive.writestr(
                "backup-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
    print(json.dumps({"output": str(output), "files": len(manifest["files"]), "integrity": "ok"}))


if __name__ == "__main__":
    main()
