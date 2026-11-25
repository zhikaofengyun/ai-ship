from hashlib import sha1
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from app import source_media
from tests.test_production import ProductionBase


class SourceMediaTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.path = Path(self.temp.name) / "sample.webm"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=navy:size=120x80:duration=0.3",
                "-c:v",
                "libvpx",
                "-threads",
                "1",
                str(self.path),
            ],
            check=True,
        )
        self.row = {
            "page_id": 7,
            "title": "File:Sample.webm",
            "sha1": sha1(self.path.read_bytes()).hexdigest(),
            "size_bytes": self.path.stat().st_size,
            "mime": "video/webm",
            "description_url": "https://commons.wikimedia.org/wiki/File:Sample.webm",
            "download_url": "https://upload.wikimedia.org/test.webm",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "author": "Artist",
            "credit": "Studio",
        }
        self.body = {
            "project_id": self.pid,
            "page_id": 7,
            "expected_author": "Artist",
            "expected_license": "CC BY 4.0",
            "expected_sha1": self.row["sha1"],
        }

    def test_verified_bytes_source_and_idempotent_retry(self):
        with (
            patch.object(source_media, "lookup", return_value=[self.row]),
            patch.object(
                source_media,
                "download",
                side_effect=lambda row, dest: shutil.copyfile(self.path, dest),
            ) as downloaded,
        ):
            self.post(
                "/production/source-media/import", {**self.body, "expected_author": "changed"}, 409
            )
            saved = self.post("/production/source-media/import", self.body)
            replay = self.post("/production/source-media/import", self.body)
        self.assertEqual(downloaded.call_count, 1)
        self.assertEqual(replay["asset"]["id"], saved["asset"]["id"])
        self.assertEqual(saved["source"]["data"]["rights_status"], "unverified")
        self.assertEqual(saved["source"]["data"]["license_snapshot"]["credit"], "Studio")
        self.assertEqual(saved["source"]["data"]["provenance"]["sha1"], self.row["sha1"])

    def test_disabling_connector_before_download_commit(self):
        def disable(row, dest):
            shutil.copyfile(self.path, dest)
            self.post(
                "/production/source-media/settings",
                {"project_id": self.pid, "expected_revision": 0, "enabled": False},
            )

        with (
            patch.object(source_media, "lookup", return_value=[self.row]),
            patch.object(source_media, "download", side_effect=disable),
        ):
            result = self.post("/production/source-media/import", self.body, 409)
        self.assertEqual(result["error"]["code"], "SOURCE_MEDIA_DISABLED")
        self.assertFalse(self.store.list("local", "Asset", self.pid))
        self.post(
            "/production/source-media/settings",
            {"project_id": self.pid, "expected_revision": 0, "enabled": True},
            409,
        )
        self.assertFalse(
            source_media.approved_url(
                "https://upload.wikimedia.org.example/file", "upload.wikimedia.org"
            )
        )
