"""Destructive recovery hazards tested only inside disposable directories."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.media import persist_blob
from app.store import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("restore_script", ROOT / "scripts/restore.py")
restore_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore_script)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.env = patch.dict(
            os.environ,
            {"AI_SHIP_DATA_DIR": str(self.data), "AI_SHIP_DB": str(self.data / "ai-ship.sqlite3")},
        )
        self.env.start()
        self.store = Store(str(self.data / "ai-ship.sqlite3"))
        self.project = self.store.create(
            "Project", {"name": "Synthetic backup fixture"}, tenant_id="test"
        )
        original = self.root / "synthetic.bin"
        original.write_bytes(b"Synthetic immutable backup bytes")
        self.blob_data = persist_blob(original, "test")
        self.store.create(
            "BlobLocation", self.blob_data, tenant_id="test", project_id=self.project["id"]
        )
        self.archive = self.root / "backup.zip"
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts/backup.py"), "--output", str(self.archive)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_backup_restore_checks_database_and_real_object_bytes(self):
        result = restore_script.restore(self.archive, self.root / "restored")
        self.assertTrue(result["byte_hashes_verified"])
        restored = Store(str(self.root / "restored/ai-ship.sqlite3"))
        self.assertEqual(restored.get(self.project["id"], "test")["hash"], self.project["hash"])
        self.assertEqual(
            (self.root / "restored" / self.blob_data["object_key"]).read_bytes(),
            b"Synthetic immutable backup bytes",
        )
        with self.assertRaises(ValueError):
            restore_script.restore(self.archive, self.root / "restored")

    def test_corrupt_and_path_traversal_archives_do_not_commit(self):
        corrupted = self.root / "corrupt.zip"
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(corrupted, "w") as target:
            for name in source.namelist():
                target.writestr(
                    name, b"corrupt" if name.startswith("objects/") else source.read(name)
                )
        with self.assertRaises(ValueError):
            restore_script.restore(corrupted, self.root / "bad-restored")
        self.assertFalse((self.root / "bad-restored").exists())
        malicious = self.root / "malicious.zip"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../outside", "bad")
        with self.assertRaises(ValueError):
            restore_script.restore(malicious, self.root / "malicious-restored")
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
