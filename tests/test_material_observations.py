"""Camera formats, persistent frame/OCR evidence, current rights and batch reuse."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch

from app.auth import LOCAL_ACTOR
from app import material_worker, visual_candidates
from app.errors import DomainError
from app.media import import_saved_file
from app.store import Store
from tests.test_production import ProductionBase


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], check=True)


class MaterialObservationsTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.temp.name) / "card"
        self.folder.mkdir()
        self.asset_index = "/production/asset-index/"

    def fixture(self):
        path = self.folder / "001.mp4"
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "color=white:s=640x240:r=12:d=1",
            "-vf",
            "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text=ORCHID:fontcolor=black:fontsize=76:x=(w-tw)/2:y=(h-th)/2",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            path,
        )
        return path

    def call(self, path, **extra):
        return self.post(self.asset_index + path, {"project_id": self.pid, **extra})

    def test_phone_rotation_receipt_image_fallback_and_director_cache_survives_restart(self):
        original = self.fixture()
        movie = self.folder / "002.MOV"
        ffmpeg("-display_rotation:v:0", "90", "-i", original, "-c", "copy", movie)
        upright = import_saved_file(self.store, LOCAL_ACTOR, self.pid, movie.name, movie)
        self.assertEqual(upright["asset"]["data"]["media_type"], "video/quicktime")
        probe = upright["probe"]
        self.assertEqual((probe["display_width"], probe["display_height"]), (240, 640))
        still = self.folder / "003.png"
        ffmpeg("-i", original, "-frames:v", "1", "-threads", "1", still)
        picture = import_saved_file(self.store, LOCAL_ACTOR, self.pid, still.name, still)
        with patch.dict(os.environ, {"AI_SHIP_CLIP_MODEL_DIR": str(self.folder / "missing")}):
            first = self.call("analyze", limit=1)
            second = self.call("analyze", limit=1, after=first["next_after"])
            self.assertIsNone(second["next_after"])
            self.assertEqual((first["processed"], second["processed"]), (1, 1))
            self.assertFalse(first["errors"] + second["errors"])
            self.assertEqual(first["clip_status"], "CLIP_UNAVAILABLE")
            second_pass = self.call("analyze")
            self.assertEqual((second_pass["processed"], second_pass["reused"]), (0, 2))
        projected = self.call("query", text="竖屏")
        self.assertEqual([x["version_id"] for x in projected["items"]], [upright["version"]["id"]])
        if (
            shutil.which("tesseract")
            and Path("/usr/share/tesseract-ocr/5/tessdata/eng.traineddata").exists()
        ):
            hits = self.call("query", text="orchid", media_type="image")["items"]
            self.assertEqual(
                len(hits), 1, {"first": first, "second": second, "replay": second_pass}
            )
            hit = hits[0]
            self.assertEqual(hit["version_id"], picture["version"]["id"])
            word = next(
                t for t in hit["tags"] if t["method"] == "tesseract_lstm" and t["label"] == "ORCHID"
            )
            self.assertEqual(len(word["sample_sha256"]), 64)
            self.assertEqual(word["source_seconds"], 0)
        sid = self.call("build", expected_revision=0, request_key="observations")["snapshot"]
        self.call("switch", snapshot=sid, expected_revision=0)
        self.assertEqual(self.call("query", text="竖屏")["items"][0]["source"], "index")
        restarted = Store(self.store.path)
        with (
            patch.object(visual_candidates, "get_store", return_value=restarted),
            patch.object(
                visual_candidates,
                "sample",
                side_effect=AssertionError("cached frames were decoded twice"),
            ),
        ):
            director = visual_candidates.inspect(LOCAL_ACTOR, self.pid, [upright["asset"]["id"]])
            self.assertTrue(director[0]["samples"])
            self.assertTrue(all("cluster" in frame for frame in director[0]["samples"]))
        src = self.store.get(upright["asset"]["data"]["source_id"], "local")
        self.store.update(
            src["id"],
            {**src["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=src["version"],
        )
        self.assertEqual(self.call("query", text="竖屏")["count"], 0)
        self.assertEqual(self.call("analyze")["reused"], 1)

    def test_ocr_contention_retries_without_decoding_measured_frames_again(self):
        path = self.fixture()
        version = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["version"]
        process = visual_candidates.run_process

        def busy_ocr(args, **kwargs):
            if args[:2] == ["tesseract", "--list-langs"]:
                raise DomainError("MEDIA_BUSY", "资源占用", 429)
            return process(args, **kwargs)

        if not shutil.which("tesseract"):
            self.skipTest("requires an installed OCR engine")
        with (
            patch.object(visual_candidates, "run_process", side_effect=busy_ocr),
            patch.dict(os.environ, {"AI_SHIP_CLIP_MODEL_DIR": ""}),
        ):
            self.assertTrue(material_worker.work_once(self.store))
        with self.store.connection() as db:
            state = db.execute(
                "SELECT state,code FROM material_analysis_jobs WHERE version_id=?", (version["id"],)
            ).fetchone()
            self.assertEqual(tuple(state), ("queued", "MEDIA_BUSY"))
            db.execute("UPDATE material_analysis_jobs SET due=0")
        self.assertEqual(self.call("query", text="横屏")["count"], 1)
        with (
            patch.object(
                visual_candidates, "sample", side_effect=AssertionError("duplicate decode")
            ),
            patch.dict(os.environ, {"AI_SHIP_CLIP_MODEL_DIR": ""}),
        ):
            self.assertTrue(material_worker.work_once(self.store))
        rows = self.call("query", text="orchid")["items"]
        self.assertEqual([r["version_id"] for r in rows], [version["id"]])

    def test_empty_cloud_download_does_not_starve_ready_file_in_single_item_pages(self):
        movie = self.fixture()
        (self.folder / "000.MOV").touch()
        config = [
            {
                "tenant_id": "local",
                "project_id": self.pid,
                "alias": "sync",
                "stable_seconds": 1,
                "path": str(self.folder),
            }
        ]
        request = {
            "root": "sync",
            "project_id": self.pid,
            "manifest": [{"path": "000.MOV"}, {"path": movie.name}],
        }
        route = "/production/directory-imports/"
        with patch.dict(os.environ, {"AI_SHIP_CAPTURE_ROOTS": json.dumps(config)}):
            self.post(route + "observe", request)
            time.sleep(1.05)
            self.assertEqual(
                self.post(route + "advance", {**request, "limit": 1})["counts"], {"waiting": 2}
            )
            result = self.post(route + "advance", {**request, "limit": 1})
        self.assertEqual(result["counts"], {"waiting": 1, "imported": 1})

    def test_import_job_survives_restart_and_rechecks_authorization(self):
        path = self.fixture()
        imported = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)
        import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)
        claimed = material_worker.claim(self.store)
        self.assertEqual(claimed["version_id"], imported["version"]["id"])
        self.assertIsNone(material_worker.claim(self.store))
        with self.store.connection() as db:
            db.execute("UPDATE material_analysis_jobs SET due=0")
        with patch.dict(os.environ, {"AI_SHIP_CLIP_MODEL_DIR": ""}):
            self.assertTrue(material_worker.work_once(Store(self.store.path)))
        self.assertFalse(material_worker.work_once(self.store))
        self.assertEqual(self.call("query", text="横屏")["count"], 1)
        with self.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT state,attempt FROM material_analysis_jobs").fetchone()[:],
                ("completed", 2),
            )
        other = self.folder / "002.png"
        ffmpeg("-i", path, "-frames:v", "1", "-threads", "1", other)
        image = import_saved_file(self.store, LOCAL_ACTOR, self.pid, other.name, other)
        src = self.store.get(image["asset"]["data"]["source_id"], "local")
        self.store.update(
            src["id"],
            {**src["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=src["version"],
        )
        self.assertTrue(material_worker.work_once(self.store))
        with self.store.connection() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state,code FROM material_analysis_jobs WHERE version_id=?",
                    (image["version"]["id"],),
                ).fetchone()[:],
                ("failed", "MEDIA_REVOKED"),
            )
            self.assertEqual(db.execute("SELECT count(*) FROM media_observations").fetchone()[0], 1)

    def test_manual_tag_drives_director_across_asset_and_version_entry_points(self):
        first = self.fixture()
        other = self.folder / "004.mov"
        ffmpeg("-f", "lavfi", "-i", "color=blue:s=96x96:r=12:d=4", "-threads", "1", other)
        before = import_saved_file(self.store, LOCAL_ACTOR, self.pid, first.name, first)
        after = import_saved_file(self.store, LOCAL_ACTOR, self.pid, other.name, other)
        self.post(
            "/production/tags",
            {
                "project_id": self.pid,
                "asset_version_id": after["version"]["id"],
                "start": 2.7,
                "end": 3.2,
                "category": "action",
                "label": "开盒",
                "evidence": "操作员核实片段",
            },
        )
        evidence = visual_candidates.inspect(
            LOCAL_ACTOR, self.pid, [before["version"]["id"], after["asset"]["id"]]
        )
        plan = visual_candidates.local_proposal("优先开盒", evidence, 1)
        first_cut = plan["shots"][0]
        self.assertEqual(first_cut["asset_id"], after["asset"]["id"])
        self.assertLessEqual(first_cut["source_in"], 2.95)
        self.assertGreater(first_cut["source_out"], 2.95)
        self.assertEqual(evidence[1]["labels"][0]["method"], "manual")

    @staticmethod
    def projected_frame(tag):
        return {key: tag[key] for key in ("source_seconds", "sample_rgb_sha256", "space")}

    @unittest.skipUnless(
        os.getenv("AI_SHIP_TEST_CLIP_MODEL_DIR") or os.getenv("AI_SHIP_CLIP_MODEL_DIR"),
        "requires configured local CLIP weights",
    )
    def test_real_clip_on_still_and_video_with_manual_override(self):
        # Load the real weights for this case without enabling inference in unrelated fixtures.
        fixture_model = os.getenv("AI_SHIP_TEST_CLIP_MODEL_DIR")
        if fixture_model:
            from app import frame_vectors, visual_index

            self.addCleanup(frame_vectors._load.cache_clear)
            self.addCleanup(visual_index.label_vectors.cache_clear)
            self.enterContext(patch.dict(os.environ, {"AI_SHIP_CLIP_MODEL_DIR": fixture_model}))
        path = self.fixture()
        image = self.folder / "001.png"
        ffmpeg("-i", path, "-frames:v", "1", "-threads", "1", image)
        for data in (path, image):
            import_saved_file(self.store, LOCAL_ACTOR, self.pid, data.name, data)
        measured = self.call("analyze")
        self.assertFalse(measured["errors"])
        self.assertEqual(measured["clip_status"], "available")
        self.assertEqual(measured["clip_frames"], 4)
        self.assertEqual(self.call("analyze")["clip_frames"], 0)
        query = self.call(
            "query", channel="visual", text="a word printed in black ink", visual_threshold=-1
        )
        self.assertEqual(query["count"], 2)
        self.assertTrue(all("frame_match" in hit for hit in query["items"]))
        video = next(i for i in query["items"] if i["media_type"] == "video")
        candidates = [t for t in video["tags"] if t.get("review_state") == "suggested"]
        # Scores below the abstention threshold legitimately produce no candidate.
        if candidates:
            candidate = candidates[0]
            self.post(
                "/production/tags",
                {
                    "project_id": self.pid,
                    "asset_version_id": video["version_id"],
                    "start": 0,
                    "end": 1,
                    "category": candidate["category"],
                    "label": "人工复核",
                    "evidence": "实帧核对",
                    "reviewed_frame": {
                        "version": video["revision"],
                        **self.projected_frame(candidate),
                    },
                },
            )
            updated = self.call("query", text="人工复核")["items"][0]
            self.assertFalse(
                any(
                    t.get("review_state") == "suggested" and t["category"] == candidate["category"]
                    for t in updated["tags"]
                )
            )
