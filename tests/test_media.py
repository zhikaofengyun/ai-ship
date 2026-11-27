"""Actual FFmpeg integration tests. All media are explicitly synthetic fixtures."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth import Actor
from app.store import Store, DomainError
from app import media


class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_tmp = tempfile.TemporaryDirectory(prefix="synthetic-media-fixtures-")
        cls.fixture = Path(cls.fixture_tmp.name) / "synthetic-test-pattern.mp4"
        media.run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25:duration=2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=2",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(cls.fixture),
            ],
            30,
        )
        cls.source_hash = media.hash_file(cls.fixture)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="media-test-")
        self.env = patch.dict(
            os.environ, {"AI_SHIP_DATA_DIR": self.tmp.name, "AI_SHIP_MODE": "local"}
        )
        self.env.start()
        self.store = Store(Path(self.tmp.name) / "facts.sqlite3")
        self.actor = Actor("local", "test-owner", "owner")
        self.project = self.store.create(
            "Project",
            {
                "name": "合成媒体验收项目",
                "language": "zh-CN",
                "market": "test",
                "goals": "Synthetic fixture only",
            },
            tenant_id="local",
            actor_id=self.actor.actor_id,
        )
        self.project_id = self.project["id"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def import_fixture(self, filename="synthetic-test-pattern.mp4"):
        return media.import_saved_file(
            self.store,
            self.actor,
            self.project_id,
            filename,
            self.fixture,
            "Synthetic FFmpeg testsrc2+sine generated for verification",
        )

    def timeline(self, asset_id, **override):
        data = {
            "name": "合成2秒成片",
            "locked": True,
            "clips": [
                {
                    "id": "base",
                    "asset_id": asset_id,
                    "source_in": 0,
                    "source_out": 2,
                    "timeline_start": 0,
                    "track": 0,
                }
            ],
            "captions": [
                {"text": "真实合成媒体测试 · 100%", "start": 0.1, "end": 1.8, "font_size": 14}
            ],
            "format": {"width": 320, "height": 180, "fps": 25},
            "duration": 2,
        }
        data.update(override)
        asset = self.store.get(asset_id, "local")
        return self.store.create(
            "Timeline",
            data,
            tenant_id="local",
            project_id=self.project_id,
            actor_id=self.actor.actor_id,
            refs=[media.ref(asset)],
        )

    def test_rect_mask_after_rotation_and_cover_is_applied_once_to_decoded_pixels(self):
        import subprocess

        base = Path(self.tmp.name) / "bands.mp4"
        rotated = base.with_name("portrait.mp4")
        ff = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
        subprocess.run(
            [
                *ff,
                "-f",
                "lavfi",
                "-i",
                "color=red:s=160x80:r=25:d=1,drawbox=40:0:40:80:lime:fill,drawbox=80:0:40:80:blue:fill,drawbox=120:0:40:80:white:fill",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                str(base),
            ],
            check=True,
        )
        # FFmpeg 6+ accepts display_rotation. Older releases write the same
        # display matrix with the stream metadata option during remux.
        options = subprocess.check_output(["ffmpeg", "-h", "full"], stderr=subprocess.STDOUT)
        rotation_args = (
            ["-display_rotation:v:0", "90", "-i", str(base)]
            if b"-display_rotation" in options
            else ["-i", str(base), "-metadata:s:v:0", "rotate=90"]
        )
        subprocess.run([*ff, *rotation_args, "-c", "copy", str(rotated)], check=True)
        version = media.import_saved_file(
            self.store, self.actor, self.project_id, rotated.name, rotated
        )["version"]
        mask = {"space": "post_fit_clip", "rect": [0.125, 0.25, 0.625, 0.75]}
        clip = {
            "id": "rotated",
            "asset_id": version["id"],
            "track": 0,
            "source_in": 0,
            "source_out": 1,
            "mask": mask,
            "fit": "cover",
        }

        def render(c):
            timeline = self.timeline(
                version["id"],
                clips=[c],
                format={"width": 80, "height": 80, "fps": 25},
                duration=1,
                captions=[],
            )
            return media.render_timeline(
                self.store,
                self.actor,
                {
                    "project_id": self.project_id,
                    "timeline_id": timeline["id"],
                    "expected_revision": timeline["version"],
                },
            )

        result = render(clip)
        out = media.resolve_media(self.store, self.actor, result["artifact"]["id"])[2]
        pixels = subprocess.check_output(
            [
                *ff,
                "-i",
                str(out),
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-threads",
                "1",
                "-",
            ]
        )

        def rgb(x, y):
            at = (y * 80 + x) * 3
            return list(pixels[at : at + 3])

        self.assertGreater(rgb(30, 26)[2], 225)
        self.assertGreater(rgb(30, 54)[1], 225)
        for x, y in [(5, 26), (68, 26), (30, 8), (30, 70)]:
            self.assertLess(max(rgb(x, y)), 15)
        self.assertEqual(result["manifest"]["data"]["clip_uses"][0]["mask"], mask)
        ellipse = render({**clip, "mask": {**mask, "shape": "ellipse"}})
        path = media.resolve_media(self.store, self.actor, ellipse["artifact"]["id"])[2]
        pixels = subprocess.check_output(
            [
                *ff,
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-threads",
                "1",
                "-",
            ]
        )
        self.assertGreater(rgb(30, 26)[2], 225)
        self.assertLess(max(rgb(13, 24)), 20)  # inside the rectangle but outside the ellipse
        self.assertEqual(ellipse["manifest"]["data"]["clip_uses"][0]["mask"]["shape"], "ellipse")
        for invalid in (
            {"space": "source", "rect": mask["rect"]},
            {"space": "post_fit_clip", "rect": [0.8, 0, 0.2, 1]},
        ):
            with self.assertRaisesRegex(DomainError, "遮罩|矩形"):
                render({**clip, "mask": invalid})

    def test_import_probe_original_dedup_and_relative_path(self):
        result = self.import_fixture("batch/synthetic-test-pattern.mp4")
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["asset"]["data"]["content_hash"], self.source_hash)
        self.assertEqual(result["probe"]["width"], 320)
        self.assertTrue(result["probe"]["has_audio"])
        repeated = self.import_fixture("different-name.mp4")
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(result["asset"]["id"], repeated["asset"]["id"])
        self.assertEqual(len(self.store.list("local", kind="Asset")), 1)
        self.assertEqual(len(self.store.list("local", kind="ImportEntry")), 2)
        self.assertEqual(media.hash_file(self.fixture), self.source_hash)
        # Original bytes served with MIME from probe, never caller extension.
        disguised = self.import_fixture("looks-like-html.html")
        blob = self.store.get(disguised["asset"]["data"]["blob_location_id"], "local")
        self.assertEqual(blob["data"]["mime_type"], "video/mp4")

    def test_invalid_path_invalid_bytes_and_cross_tenant(self):
        for name in (
            "../escape.mp4",
            "/etc/passwd",
            "C:\\data\\foo.mp4",
            "a/../b.mp4",
            "bad\x00.mp4",
        ):
            with self.assertRaises(DomainError):
                media.safe_filename(name)
        corrupt = Path(self.tmp.name) / "corrupt.mp4"
        corrupt.write_bytes(b"not a video")
        with self.assertRaises(DomainError) as error:
            media.import_saved_file(self.store, self.actor, self.project_id, "corrupt.mp4", corrupt)
        self.assertEqual(error.exception.code, "INVALID_MEDIA")
        self.assertEqual(
            self.store.list("local", kind="ImportEntry")[0]["data"]["status"], "failed"
        )
        self.assertEqual(len(self.store.list("local", kind="Asset")), 0)
        result = self.import_fixture()
        with self.assertRaises(DomainError):
            media.resolve_media(
                self.store, Actor("other", "intruder", "owner"), result["asset"]["id"]
            )

    def test_real_waveform_and_audio_extract(self):
        imported = self.import_fixture()
        waveform = media.waveform_data(self.store, self.actor, imported["asset"]["id"], 100)
        self.assertTrue(waveform["available"])
        self.assertTrue(90 <= len(waveform["peaks"]) <= 100)
        self.assertGreater(max(waveform["peaks"]), 0.01)
        extracted = media.extract_audio(
            self.store,
            self.actor,
            imported["asset"]["id"],
            {"project_id": self.project_id, "expected_revision": 1},
        )
        self.assertEqual(
            (
                extracted["audio"]["data"]["probe"]["sample_rate"]
                if "sample_rate" in extracted["audio"]["data"]["probe"]
                else extracted["audio"]["data"]["sample_rate"]
            ),
            48000,
        )
        self.assertFalse(extracted["audio"]["data"]["probe"]["has_video"])
        self.assertEqual(extracted["audio"]["data"]["transcription_status"], "not_run")

    def test_transform_real_bytes_trim_scale_color_loudness(self):
        imported = self.import_fixture()
        result = media.transform_asset(
            self.store,
            self.actor,
            imported["asset"]["id"],
            {
                "project_id": self.project_id,
                "expected_revision": 1,
                "recipe": {
                    "trim_start": 0.25,
                    "trim_end": 1.75,
                    "speed": 1.5,
                    "scale": 1.016,
                    "template": "atlas_preprocess",
                    "width": 160,
                    "height": 90,
                    "saturation": 0.8,
                    "brightness": 0.02,
                    "loudness_lufs": -16,
                },
            },
        )
        probe = result["version"]["data"]["probe"]
        self.assertEqual((probe["width"], probe["height"]), (160, 90))
        self.assertAlmostEqual(probe["duration"], 1, delta=0.12)
        self.assertTrue(result["qc"]["checks"]["full_decode"])
        self.assertFalse(result["qc"]["publish_ready"])
        self.assertIsNotNone(result["run"]["data"]["loudness_measurement"])
        self.assertNotEqual(result["version"]["data"]["content_hash"], self.source_hash)
        _, _, original = media.resolve_media(self.store, self.actor, imported["asset"]["id"])
        self.assertEqual(media.hash_file(original), self.source_hash)
        self.assertEqual(
            result["time_map"]["data"]["segments"][0]["contributions"][0]["source_interval"],
            [250000, 1750000],
        )

    def test_transform_rejects_unknown_effect_and_bad_parameters(self):
        imported = self.import_fixture()
        for recipe in (
            {"inpaint": True},
            {"made_up": 1},
            {"scale": 1.2, "template": "atlas_preprocess"},
            {"trim_start": 1, "trim_end": 0.5},
            {"speed": float("nan")},
            {"rotation": 45},
            {"width": 161},
        ):
            with self.assertRaises(DomainError):
                media.transform_asset(
                    self.store,
                    self.actor,
                    imported["asset"]["id"],
                    {"project_id": self.project_id, "expected_revision": 1, "recipe": recipe},
                )
        self.assertEqual(len(self.store.list("local", kind="TransformRun")), 0)

    def test_actual_multitrack_render_manifest_package_and_qc_boundary(self):
        imported = self.import_fixture()
        asset_id = imported["asset"]["id"]
        timeline = self.timeline(
            asset_id,
            clips=[
                {
                    "id": "base",
                    "asset_id": asset_id,
                    "source_in": 0,
                    "source_out": 2,
                    "timeline_start": 0,
                    "track": 0,
                },
                {
                    "id": "overlay",
                    "asset_id": asset_id,
                    "source_in": 0,
                    "source_out": 1,
                    "timeline_start": 0.5,
                    "track": 1,
                    "width": 80,
                    "height": 44,
                    "x": 200,
                    "y": 20,
                    "opacity": 0.6,
                    "volume": 0,
                },
            ],
        )
        result = media.render_timeline(
            self.store,
            self.actor,
            {
                "project_id": self.project_id,
                "timeline_id": timeline["id"],
                "expected_revision": 1,
                "profile": {"width": 320, "height": 180, "fps": {"num": 30000, "den": 1001}},
            },
        )
        self.assertEqual(
            result["manifest"]["data"]["actual_profile"]["fps_rational"],
            {"num": 30000, "den": 1001},
        )
        self.assertEqual(result["artifact"]["data"]["probe"]["fps"], "30000/1001")
        self.assertEqual(result["job"]["data"]["status"], "succeeded")
        self.assertEqual(result["artifact"]["data"]["probe"]["width"], 320)
        self.assertAlmostEqual(result["artifact"]["data"]["probe"]["duration"], 2, delta=0.1)
        self.assertTrue(
            any(len(s["contributions"]) > 2 for s in result["time_map"]["data"]["segments"])
        )
        self.assertFalse(result["artifact"]["data"]["publish_ready"])
        self.assertEqual(len(self.store.list("local", kind="Usage")), 2)
        package, path = media.create_package(self.store, self.actor, result["artifact"]["id"])
        with zipfile.ZipFile(path) as archive:
            self.assertIn("video.mp4", archive.namelist())
            self.assertIn("time-map.json", archive.namelist())
            self.assertEqual(
                hashlib.sha256(archive.read("video.mp4")).hexdigest(),
                result["artifact"]["data"]["content_hash"],
            )
            exported = json.loads(archive.read("timeline.json"))
            self.assertEqual(exported["hash"], timeline["hash"])
        self.assertIn("原始素材字节", package["data"]["omissions"])
        evidence_dir = os.environ.get("AI_SHIP_MEDIA_EVIDENCE_DIR")
        if evidence_dir:
            target = Path(evidence_dir).resolve()
            target.mkdir(parents=True, exist_ok=True)
            _, _, artifact_path = media.resolve_media(
                self.store, self.actor, result["artifact"]["id"]
            )
            shutil.copyfile(self.fixture, target / "synthetic-source.mp4")
            shutil.copyfile(artifact_path, target / "synthetic-render.mp4")
            (target / "render-evidence.json").write_text(
                json.dumps(
                    {
                        "fixture_disclaimer": "Synthetic FFmpeg test pattern plus sine; no real product/rights/posted metrics claimed",
                        "source_sha256": self.source_hash,
                        "timeline": timeline,
                        "result": result,
                        "tool_versions": media.tool_versions(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            (target / "synthetic-frame.png").write_bytes(
                media.frame_png(self.store, self.actor, result["artifact"]["id"], 0.8, 640)
            )

    def test_render_rejects_unlocked_stale_version_and_unsupported_keyframes(self):
        imported = self.import_fixture()
        timeline = self.timeline(imported["asset"]["id"], locked=False)
        body = {
            "project_id": self.project_id,
            "timeline_id": timeline["id"],
            "expected_revision": 1,
        }
        with self.assertRaises(DomainError) as error:
            media.render_timeline(self.store, self.actor, body)
        self.assertEqual(error.exception.code, "TIMELINE_NOT_LOCKED")
        with self.assertRaises(DomainError) as error:
            media.render_timeline(self.store, self.actor, {**body, "expected_revision": 9})
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        with self.assertRaises(DomainError):
            unsupported = self.timeline(imported["asset"]["id"], keyframes={"opacity": [0, 1]})
            media.render_timeline(
                self.store, self.actor, {**body, "timeline_id": unsupported["id"]}
            )
        self.assertEqual(len(self.store.list("local", kind="RenderJob")), 0)

    def test_derivative_chain_keeps_original_identity_and_revocation_blocks_all(self):
        imported = self.import_fixture()
        source_id = imported["asset"]["id"]
        first = media.transform_asset(
            self.store,
            self.actor,
            source_id,
            {
                "project_id": self.project_id,
                "expected_revision": 1,
                "recipe": {"width": 160, "height": 90},
            },
        )
        second = media.transform_asset(
            self.store,
            self.actor,
            first["version"]["id"],
            {
                "project_id": self.project_id,
                "expected_revision": 1,
                "recipe": {"width": 80, "height": 44},
            },
        )
        self.assertEqual(second["version"]["data"]["asset_id"], source_id)
        self.assertEqual(
            second["version"]["data"]["parent_asset_version_ids"], [first["version"]["id"]]
        )
        extracted = media.extract_audio(
            self.store, self.actor, first["version"]["id"], {"project_id": self.project_id}
        )
        self.assertEqual(extracted["audio"]["data"]["asset_id"], source_id)
        source = self.store.get(source_id, "local")
        self.store.update(
            source_id,
            {**source["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=1,
        )
        for identifier in [
            source_id,
            first["version"]["id"],
            second["version"]["id"],
            extracted["audio"]["id"],
        ]:
            with self.assertRaises(DomainError) as error:
                media.resolve_media(self.store, self.actor, identifier)
            self.assertEqual(error.exception.code, "MEDIA_REVOKED")

    def test_source_expiry_is_timezone_aware_and_inspection_never_grants_rights(self):
        imported = self.import_fixture()
        asset = imported["asset"]
        source = self.store.get(asset["data"]["source_id"], "local")
        self.assertEqual(source["data"]["rights_status"], "unverified")
        self.assertFalse(source["data"]["source_note_is_evidence"])
        naive = {**source["data"], "expires_at": "2999-01-01T00:00:00"}
        with self.assertRaises(DomainError) as invalid:
            self.store.update(
                source["id"], naive, tenant_id="local", expected_revision=source["version"]
            )
        self.assertEqual(invalid.exception.code, "SCHEMA_VALIDATION")

        # Old ledgers could predate input validation. Read a historical invalid value
        # without weakening today's write contract or changing the immutable fixture.
        real_get = self.store.get

        def historical(record_id, *args, **kwargs):
            item = real_get(record_id, *args, **kwargs)
            return {**item, "data": naive} if record_id == source["id"] else item

        with patch.object(self.store, "get", side_effect=historical):
            with self.assertRaises(DomainError) as error:
                media.resolve_media(self.store, self.actor, asset["id"], purpose="process")
            self.assertEqual(error.exception.code, "MEDIA_RIGHTS_INVALID")
            media.resolve_media(self.store, self.actor, asset["id"], purpose="inspect")
        cases = [
            ({"expires_at": "2000-01-01T10:00:00+08:00"}, "MEDIA_RIGHTS_EXPIRED"),
            (
                {"expires_at": None, "validity": {"valid_until": "2000-01-01T00:00:00Z"}},
                "MEDIA_RIGHTS_EXPIRED",
            ),
            (
                {"expires_at": None, "validity": None, "valid_from": "2999-01-01T00:00:00Z"},
                "MEDIA_RIGHTS_NOT_ACTIVE",
            ),
            (
                {
                    "expires_at": None,
                    "validity": None,
                    "valid_from": None,
                    "processing_scope": {"allowed": False},
                },
                "MEDIA_PROCESSING_DENIED",
            ),
        ]
        for extra, expected in cases:
            source = self.store.update(
                source["id"],
                {**source["data"], **extra},
                tenant_id="local",
                expected_revision=source["version"],
            )
            with self.assertRaises(DomainError) as error:
                media.extract_audio(
                    self.store, self.actor, asset["id"], {"project_id": self.project_id}
                )
            self.assertEqual(error.exception.code, expected)
            # A technical audit preview is permitted, without changing rights status.
            self.assertEqual(
                media.resolve_media(self.store, self.actor, asset["id"], purpose="inspect")[0][
                    "id"
                ],
                asset["id"],
            )
        self.assertEqual(len(self.store.list("local", kind="Audio")), 0)
        source = self.store.update(
            source["id"],
            {
                **source["data"],
                "expires_at": "2999-01-01T01:00:00+08:00",
                "valid_from": None,
                "processing_scope": None,
            },
            tenant_id="local",
            expected_revision=source["version"],
        )
        media.resolve_media(self.store, self.actor, asset["id"])
        source = self.store.update(
            source["id"],
            {**source["data"], "expires_at": None, "revoked_at": "2000-01-01T00:00:00Z"},
            tenant_id="local",
            expected_revision=source["version"],
        )
        with self.assertRaises(DomainError) as error:
            media.resolve_media(self.store, self.actor, asset["id"], purpose="inspect")
        self.assertEqual(error.exception.code, "MEDIA_REVOKED")

    def test_artifact_traces_current_grant_through_frozen_deep_inputs(self):
        imported = self.import_fixture()
        asset = imported["asset"]
        derivative = media.transform_asset(
            self.store,
            self.actor,
            asset["id"],
            {
                "project_id": self.project_id,
                "expected_revision": 1,
                "recipe": {"width": 160, "height": 90},
            },
        )
        audio = media.extract_audio(
            self.store, self.actor, derivative["version"]["id"], {"project_id": self.project_id}
        )["audio"]
        timeline = self.timeline(
            asset["id"],
            captions=[],
            audio_tracks=[
                {
                    "id": "extra-audio",
                    "asset_id": audio["id"],
                    "source_in": 0,
                    "source_out": 2,
                    "timeline_start": 0,
                    "volume": 0.25,
                }
            ],
        )
        result = media.render_timeline(
            self.store,
            self.actor,
            {"project_id": self.project_id, "timeline_id": timeline["id"], "expected_revision": 1},
        )
        artifact_id = result["artifact"]["id"]
        source = self.store.get(asset["data"]["source_id"], "local")
        grant = self.store.create(
            "Grant",
            {
                "grant_kind": "internal_acl",
                "scopes": ["private_processing"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
            tenant_id="local",
            project_id=self.project_id,
        )
        # Attach the current grant after the output was frozen. Frozen old refs
        # cannot be used to bypass a later restriction.
        source = self.store.update(
            source["id"],
            {**source["data"], "grant_id": grant["id"]},
            refs=[media.ref(grant)],
            tenant_id="local",
            expected_revision=source["version"],
        )
        media.resolve_media(self.store, self.actor, artifact_id)
        grant = self.store.update(
            grant["id"],
            {**grant["data"], "expires_at": "2000-01-01T00:00:00Z"},
            tenant_id="local",
            expected_revision=grant["version"],
        )
        for identifier in [asset["id"], derivative["version"]["id"], audio["id"], artifact_id]:
            with self.assertRaises(DomainError) as error:
                media.resolve_media(self.store, self.actor, identifier)
            self.assertEqual(error.exception.code, "MEDIA_RIGHTS_EXPIRED")
        with self.assertRaises(DomainError):
            media.create_package(self.store, self.actor, artifact_id)
        grant = self.store.update(
            grant["id"],
            {**grant["data"], "expires_at": None, "revoked_at": "2000-01-01T00:00:00Z"},
            tenant_id="local",
            expected_revision=grant["version"],
        )
        with self.assertRaises(DomainError) as error:
            media.resolve_media(self.store, self.actor, artifact_id, purpose="inspect")
        self.assertEqual(error.exception.code, "MEDIA_REVOKED")
        self.assertEqual(len(self.store.list("local", kind="DeliveryPackage")), 0)

    def test_rights_graph_is_bounded_and_keeps_pinned_creative_dependencies(self):
        imported = self.import_fixture()
        asset = imported["asset"]
        timeline = self.timeline(asset["id"], captions=[])
        result = media.render_timeline(
            self.store,
            self.actor,
            {"project_id": self.project_id, "timeline_id": timeline["id"], "expected_revision": 1},
        )
        unrelated = self.store.create(
            "Source",
            {"name": "Later unrelated draft", "status": "revoked"},
            tenant_id="local",
            project_id=self.project_id,
        )
        self.store.update(
            timeline["id"],
            {**timeline["data"], "name": "Later draft"},
            refs=[media.ref(unrelated)],
            tenant_id="local",
            expected_revision=timeline["version"],
        )
        # Existing output consumes Timeline v1, not the later unrelated draft.
        media.resolve_media(self.store, self.actor, result["artifact"]["id"])
        with patch.object(media, "MAX_LINEAGE_RECORDS", 2):
            with self.assertRaises(DomainError) as error:
                media.resolve_media(self.store, self.actor, result["artifact"]["id"])
        self.assertEqual(error.exception.code, "MEDIA_RIGHTS_GRAPH_LIMIT")

    def test_audio_commit_rechecks_expiry_after_real_decode(self):
        imported = self.import_fixture()
        asset = imported["asset"]
        source = self.store.get(asset["data"]["source_id"], "local")
        original_qc = media.technical_qc

        def expire_after_qc(path, expected, duration):
            result = original_qc(path, expected, duration)
            self.store.update(
                source["id"],
                {**source["data"], "expires_at": "2000-01-01T00:00:00Z"},
                tenant_id="local",
                expected_revision=source["version"],
            )
            return result

        with patch.object(media, "technical_qc", side_effect=expire_after_qc):
            with self.assertRaises(DomainError) as error:
                media.extract_audio(
                    self.store, self.actor, asset["id"], {"project_id": self.project_id}
                )
        self.assertEqual(error.exception.code, "MEDIA_RIGHTS_EXPIRED")
        self.assertEqual(len(self.store.list("local", kind="Audio")), 0)

    def test_injected_qc_failure_does_not_create_deliverable(self):
        imported = self.import_fixture()
        timeline = self.timeline(imported["asset"]["id"], captions=[])
        with patch.object(
            media,
            "technical_qc",
            side_effect=DomainError("MEDIA_QC_FAILED", "injected QC rejection"),
        ):
            with self.assertRaises(DomainError):
                media.render_timeline(
                    self.store,
                    self.actor,
                    {
                        "project_id": self.project_id,
                        "timeline_id": timeline["id"],
                        "expected_revision": 1,
                    },
                )
        self.assertEqual(self.store.list("local", kind="RenderJob")[0]["data"]["status"], "failed")
        self.assertEqual(len(self.store.list("local", kind="Artifact")), 0)
        self.assertEqual(len(self.store.list("local", kind="RenderManifest")), 0)

    def test_revocation_during_render_rechecked_before_fact_commit(self):
        imported = self.import_fixture()
        asset = imported["asset"]
        timeline = self.timeline(asset["id"], captions=[])
        original_qc = media.technical_qc

        def revoke_after_real_qc(path, expected, duration):
            result = original_qc(path, expected, duration)
            self.store.update(
                asset["id"],
                {**asset["data"], "status": "revoked"},
                tenant_id="local",
                expected_revision=asset["version"],
            )
            return result

        with patch.object(media, "technical_qc", side_effect=revoke_after_real_qc):
            with self.assertRaises(DomainError) as error:
                media.render_timeline(
                    self.store,
                    self.actor,
                    {
                        "project_id": self.project_id,
                        "timeline_id": timeline["id"],
                        "expected_revision": 1,
                    },
                )
        self.assertEqual(error.exception.code, "MEDIA_REVOKED")
        self.assertEqual(len(self.store.list("local", kind="Artifact")), 0)
        self.assertEqual(self.store.list("local", kind="RenderJob")[0]["data"]["status"], "failed")

    def test_chunk_upload_resume_duplicate_chunk_and_full_hash(self):
        payload = self.fixture.read_bytes()
        session = media.start_upload(
            self.store,
            self.actor,
            {
                "project_id": self.project_id,
                "filename": "folder/synthetic.mp4",
                "size_bytes": len(payload),
                "content_hash": self.source_hash,
            },
        )
        entry_id = session["entry"]["id"]
        split = len(payload) // 2
        first = payload[:split]
        response = media.append_upload_chunk(
            self.store, self.actor, entry_id, 0, first, hashlib.sha256(first).hexdigest()
        )
        self.assertEqual(response["offset"], split)
        repeated = media.append_upload_chunk(self.store, self.actor, entry_id, 0, first)
        self.assertTrue(repeated["duplicate_chunk"])
        with self.assertRaises(DomainError):
            media.finish_upload(self.store, self.actor, entry_id)
        with self.assertRaises(DomainError):
            media.append_upload_chunk(self.store, self.actor, entry_id, split + 1, b"oops")
        with self.assertRaises(DomainError):
            media.append_upload_chunk(
                self.store, self.actor, entry_id, split, payload[split:], "0" * 64
            )
        # Simulate process death after bytes append but before offset transaction commits.
        uncommitted = media.upload_path(self.store.get(entry_id, "local"), self.actor)
        with uncommitted.open("ab") as handle:
            handle.write(b"uncommitted-tail-must-be-discarded")
        # Reopening the database models a server restart; persisted byte offset survives.
        reopened = Store(Path(self.tmp.name) / "facts.sqlite3")
        media.append_upload_chunk(reopened, self.actor, entry_id, split, payload[split:])
        result = media.finish_upload(reopened, self.actor, entry_id)
        self.assertEqual(result["asset"]["data"]["content_hash"], self.source_hash)
        self.assertEqual(result["import_entry"]["id"], entry_id)
        self.assertEqual(len(result["import_entry"]["data"]["chunk_manifest"]), 2)
        second_completion = media.finish_upload(reopened, self.actor, entry_id)
        self.assertEqual(second_completion["asset"]["id"], result["asset"]["id"])
        self.assertEqual(len(reopened.list("local", kind="Asset")), 1)

    def test_real_scene_candidates_frame_and_onsets_are_not_semantic_claims(self):
        imported = self.import_fixture()
        asset_id = imported["asset"]["id"]
        frame = media.frame_png(self.store, self.actor, asset_id, 0.8, 160)
        self.assertTrue(frame.startswith(b"\x89PNG"))
        shots = media.detect_shots(self.store, self.actor, asset_id, 0.3)
        self.assertGreaterEqual(len(shots["shots"]), 1)
        self.assertEqual(shots["shots"][0]["data"]["start"], 0)
        self.assertAlmostEqual(shots["shots"][-1]["data"]["end"], 2, delta=0.1)
        analysis = media.analyze_audio_onsets(self.store, self.actor, asset_id)["analysis"]
        self.assertIsNone(analysis["data"]["bpm"])
        self.assertIsNone(analysis["data"]["beat_grid"])
        self.assertEqual(analysis["data"]["review_status"], "unreviewed")

    def test_keyframe_render_changes_actual_frame_pixels(self):
        imported = self.import_fixture()
        timeline = self.timeline(
            imported["asset"]["id"],
            captions=[],
            clips=[
                {
                    "id": "animated",
                    "asset_id": imported["asset"]["id"],
                    "source_in": 0,
                    "source_out": 2,
                    "timeline_start": 0,
                    "keyframes": {
                        "opacity": [{"time": 0, "value": 0}, {"time": 1, "value": 1}],
                        "x": [{"time": 0, "value": 0}, {"time": 1, "value": 30}],
                    },
                }
            ],
        )
        result = media.render_timeline(
            self.store,
            self.actor,
            {"project_id": self.project_id, "timeline_id": timeline["id"], "expected_revision": 1},
        )
        _, _, path = media.resolve_media(self.store, self.actor, result["artifact"]["id"])

        def frame_average(at):
            pixels, _ = media.run_process(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(at),
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                30,
                True,
            )
            return sum(pixels) / len(pixels)

        self.assertLess(frame_average(0), 5)
        self.assertGreater(frame_average(1.5), 20)
        self.assertEqual(
            result["manifest"]["data"]["clip_uses"][0]["keyframes"]["opacity"][1]["value"], 1
        )
        with self.assertRaises(DomainError):
            media.normalize_keyframes({"rotation": [{"time": 0, "value": 3}]}, 2)
        with self.assertRaises(DomainError):
            media.normalize_keyframes({"x": [{"time": 0, "value": 0}, {"time": 0, "value": 10}]}, 2)

    def test_http_range_bytes_and_filename_security(self):
        imported = self.import_fixture()
        app = FastAPI()
        app.include_router(media.router, prefix="/api/v1")

        @app.exception_handler(DomainError)
        async def handler(request, exc):
            return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status)

        with patch.object(media, "get_store", return_value=self.store):
            with TestClient(app) as client:
                response = client.get(
                    f"/api/v1/media/{imported['asset']['id']}/file", headers={"Range": "bytes=0-31"}
                )
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, self.fixture.read_bytes()[:32])
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertTrue(response.headers["content-range"].startswith("bytes 0-31/"))
                response = client.get(
                    f"/api/v1/media/{imported['asset']['id']}/file",
                    headers={"Range": "bytes=999999999-"},
                )
                self.assertEqual(response.status_code, 416)


if __name__ == "__main__":
    unittest.main()
