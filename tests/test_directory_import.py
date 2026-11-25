from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from app import directory_import
from tests.test_production import ProductionBase


class DirectoryIntakeTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.temp.name) / "card"
        self.root.mkdir()
        self.conf = {
            "tenant_id": "local",
            "project_id": self.pid,
            "alias": "camera",
            "path": str(self.root),
            "stable_seconds": 1,
        }
        self.config = patch.dict(os.environ, {"AI_SHIP_CAPTURE_ROOTS": json.dumps([self.conf])})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.body = {"root": "camera", "project_id": self.pid}
        self.base = "/production/directory-imports/"
        self.good = self.root / "take.mp4"
        self.media(self.good, "teal")

    def media(self, path, color):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color={color}:s=64x64:r=10:d=0.3",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                str(path),
            ],
            check=True,
        )

    def call(self, op, expected=200, **kwargs):
        return self.post(self.base + op, {**self.body, **kwargs}, expected)

    def test_pending_during_write_same_name_history_and_duplicate_bytes_after_restart(self):
        other = self.root / "duplicate.mp4"
        shutil.copyfile(self.good, other)
        corrupt = self.root / "broken.mp4"
        corrupt.write_bytes(b"invalid")
        raw = self.good.read_bytes()
        self.good.write_bytes(raw[:100])
        self.assertEqual(self.call("observe")["counts"], {"waiting": 3})
        self.assertEqual(self.call("advance")["counts"], {"waiting": 3})
        time.sleep(1.05)
        self.good.write_bytes(raw)
        first = self.call("advance")
        self.assertEqual(first["counts"], {"waiting": 1, "failed": 1, "imported": 1})
        time.sleep(1.05)
        # Simulate a restarted server: a separate interpreter reopens SQLite and the registered root.
        cmd = "from app.directory_import import Advance, advance;from app.auth import LOCAL_ACTOR; import json,sys;print(json.dumps(advance(Advance(**json.loads(sys.argv[1])),None,LOCAL_ACTOR)['data']))"
        child = subprocess.run(
            [sys.executable, "-c", cmd, json.dumps(self.body)],
            text=True,
            capture_output=True,
            check=True,
        )
        second = json.loads(child.stdout)
        self.assertEqual(second["counts"], {"deduplicated": 1, "failed": 1, "imported": 1})
        items = {i["path"]: i for i in second["items"]}
        original = items["take.mp4"]
        self.assertEqual(original["asset_id"], items["duplicate.mp4"]["asset_id"])
        self.assertNotEqual(original["source_id"], items["duplicate.mp4"]["source_id"])
        self.assertEqual(original["content_hash"], sha256(raw).hexdigest())
        self.media(self.good, "red")
        self.assertEqual(self.call("observe")["counts"]["waiting"], 1)
        time.sleep(1.05)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.call("advance"), range(2)))
        newest = next(x for x in self.call("items")["items"] if x["path"] == "take.mp4")
        self.assertNotEqual(newest["asset_id"], original["asset_id"])
        src = self.store.get(original["source_id"], "local")
        self.assertEqual(src["data"]["directory_origin"]["relative_path"], "take.mp4")
        self.assertEqual(src["data"]["content_hash"], sha256(raw).hexdigest())
        self.assertEqual(len(self.store.list("local", "Asset", self.pid)), 2)
        self.assertEqual(len(self.store.list("local", "Source", self.pid)), 3)

    def test_manifest_digest_link_swap_scope_and_per_item_failure(self):
        sibling = Path(self.temp.name) / "private"
        sibling.mkdir()
        shutil.copy(self.good, sibling / "outside.mp4")
        child = self.root / "child"
        child.mkdir()
        shutil.copy(self.good, child / "picture.mp4")
        (self.root / "filelink.mp4").symlink_to(sibling / "outside.mp4")
        (self.root / "dirlink").symlink_to(sibling, target_is_directory=True)
        entries = [
            {"path": "take.mp4", "sha256": "0" * 64},
            {"path": "child/picture.mp4"},
            {"path": "filelink.mp4"},
            {"path": "dirlink/outside.mp4"},
        ]
        self.assertEqual(
            self.call("observe", manifest=entries)["counts"], {"waiting": 2, "blocked": 2}
        )
        child.rename(self.root / "old")
        child.symlink_to(sibling, target_is_directory=True)
        self.call("observe", 422, manifest=[{"path": "../private/outside.mp4"}])
        self.call("observe", 403, root="another")
        time.sleep(1.05)
        out = self.call("advance", manifest=entries)
        self.assertEqual(out["counts"], {"blocked": 3, "failed": 1})
        self.assertEqual(
            next(i for i in out["items"] if i["path"] == "take.mp4")["code"],
            "CAPTURE_HASH_MISMATCH",
        )
        self.assertFalse(self.store.list("local", "Asset", self.pid))
        self.call("observe", manifest=[{"path": "take.mp4"}])
        time.sleep(1.1)
        real_read = os.read
        changed = False

        def mutate(fd, n):
            nonlocal changed
            data = real_read(fd, n)
            if data and not changed:
                changed = True
                with self.good.open("ab") as stream:
                    stream.write(b"writer")
            return data

        with patch.object(directory_import.os, "read", side_effect=mutate):
            out = self.call("advance", manifest=[{"path": "take.mp4"}])
        self.assertTrue(changed)
        self.assertEqual(out["counts"], {"waiting": 1})
        self.assertFalse(self.store.list("local", "Asset", self.pid))

    def test_large_manifest_pages_and_snapshot_isolation(self):
        manifest = [{"path": f"day/{i:04d}.mp4"} for i in range(3201)]
        summary = self.call("observe", manifest=manifest)
        self.assertEqual(summary["counts"], {"blocked": 3201})
        self.assertEqual(len(summary["items"]), 50)
        snapshot = summary["snapshot"]
        page = self.post(
            self.base + "items?after=" + summary["next_after"] + "&limit=500",
            {**self.body, "snapshot": snapshot},
        )
        self.assertEqual(len(page["items"]), 500)
        self.assertEqual(page["items"][0]["path"], "day/0050.mp4")
        self.call("advance", 403, snapshot="0" * 64)
        self.assertFalse(self.store.list("local", "Asset", self.pid))

    def test_root_substitution_during_probe_never_commits_original(self):
        self.call("observe")
        time.sleep(1.05)
        real_probe = directory_import.probe_media
        alternate = Path(self.temp.name) / "elsewhere"
        alternate.mkdir()

        def swap(path):
            data = real_probe(path)
            self.root.rename(self.root.with_name("original_card"))
            self.root.symlink_to(alternate, target_is_directory=True)
            return data

        with patch.object(directory_import, "probe_media", side_effect=swap):
            out = self.call("advance")
        self.assertEqual(out["counts"], {"failed": 1})
        self.assertFalse(self.store.list("local", "Asset", self.pid))
        self.assertFalse(self.store.list("local", "Source", self.pid))
