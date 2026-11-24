"""Y04-R2 public API regressions against SQLite and actual encoded, downloaded media."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import math
import struct
import subprocess
import threading
from unittest.mock import patch

from app import media
from app.auth import LOCAL_ACTOR
from app.errors import DomainError
from tests.test_production import ProductionBase


def make_timeline(store, project_id, directory):
    path = Path(directory) / "recovery-synthetic.mp4"
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
            "testsrc2=size=96x64:rate=25:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=400:duration=1",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        30,
    )
    version = media.import_saved_file(store, LOCAL_ACTOR, project_id, path.name, path)["version"]
    timeline = store.create(
        "Timeline",
        {
            "name": "恢复测试",
            "locked": True,
            "duration": 1,
            "clips": [
                {
                    "id": "head",
                    "asset_id": version["id"],
                    "source_in": 0.5,
                    "source_out": 1,
                    "timeline_start": 0,
                },
                {
                    "id": "tail",
                    "asset_id": version["id"],
                    "source_in": 0,
                    "source_out": 0.5,
                    "timeline_start": 0.5,
                },
            ],
            "captions": [{"start": 0.1, "end": 0.85, "text": "cross-cut caption", "font_size": 10}],
            "format": {"width": 96, "height": 64, "fps": {"num": 24000, "den": 1001}},
        },
        project_id=project_id,
        tenant_id="local",
        actor_id=LOCAL_ACTOR.actor_id,
        refs=[media.ref(version)],
    )
    return timeline, version


class RenderRecoveryTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.timeline, self.version = make_timeline(self.store, self.pid, self.temp.name)
        self.args = {
            "project_id": self.pid,
            "timeline_id": self.timeline["id"],
            "expected_revision": 1,
        }

    def items(self, kind):
        return self.store.list("local", kind=kind, project_id=self.pid)

    def failed(self):
        # Stop after a real encode, before the completed artifact is accepted.
        with patch(
            "app.media.technical_qc",
            side_effect=DomainError("OUTPUT_CHECK_INTERRUPTED", "测试中断", 503),
        ):
            self.post("/media/render", self.args, 503)
        self.assertFalse(self.items("Artifact"))
        return self.items("RenderJob")[0]

    def request(self, job, expected=200, **extra):
        return self.post(
            "/media/render-jobs/" + job["id"] + "/recover",
            {
                "project_id": self.pid,
                "expected_revision": job["version"],
                "request_id": "same-operator-request",
                "reason": "上次输出校验被中断，重新生成",
                **extra,
            },
            expected,
        )

    def test_retry_uses_frozen_sequence_exact_fps_full_decode_and_no_duplicate_outputs(self):
        parent = self.failed()
        self.assertEqual(parent["data"]["error_code"], "OUTPUT_CHECK_INTERRUPTED")
        snap = parent["data"]["checkpoint"]
        self.assertEqual(len(snap["media"]), 2)
        self.assertEqual(snap["profile"]["fps"], {"num": 24000, "den": 1001})
        self.assertEqual(snap["media"][0]["checksum"], self.version["data"]["content_hash"])
        self.assertTrue(snap["font"]["sha256"])
        result = self.request(parent)
        child, manifest = result["job"], result["manifest"]["data"]
        self.assertEqual(child["data"]["previous_attempt"], media.ref(parent))
        self.assertEqual(child["data"]["checkpoint"], snap)
        self.assertEqual(child["data"]["reused_segments"], 0)
        self.assertEqual(child["data"]["restarted_at_seconds"], 0)
        self.assertEqual(manifest["actual_profile"]["fps_rational"], snap["profile"]["fps"])
        self.assertEqual([c["id"] for c in manifest["clip_uses"]], ["head", "tail"])
        self.assertTrue(manifest["qc"]["checks"]["full_decode"])
        self.assertTrue(manifest["qc"]["checks"]["exact_frame_rate"])
        body = self.client.get(result["preview_url"])
        self.assertEqual(body.status_code, 200)
        self.assertEqual(sha256(body.content).hexdigest(), manifest["output_hash"])
        replay = self.request(parent)
        self.assertEqual(replay, {**result, "idempotent_replay": True})
        self.request(parent, 409, request_id="different")
        for kind, count in (
            ("RenderJob", 2),
            ("Usage", 2),
            ("Artifact", 1),
            ("RenderManifest", 1),
            ("Cost", 1),
        ):
            self.assertEqual(len(self.items(kind)), count)

    def test_current_source_bytes_and_rights_and_timeline_revision_block_recovery(self):
        parent = self.failed()
        _, _, path = media.resolve_media(self.store, LOCAL_ACTOR, self.version["id"])
        initial = path.read_bytes()
        path.write_bytes(initial[:-32])
        self.request(parent, 409)
        path.write_bytes(initial)
        source = self.items("Source")[0]
        revoked = self.store.update(
            source["id"],
            {**source["data"], "status": "revoked"},
            expected_revision=source["version"],
            tenant_id="local",
            actor_id="test",
            reason="source grant withdrawn",
        )
        self.assertEqual(self.request(parent, 403)["error"]["code"], "MEDIA_REVOKED")
        self.store.update(
            source["id"],
            source["data"],
            expected_revision=revoked["version"],
            tenant_id="local",
            actor_id="test",
            reason="source restored",
        )
        self.store.update(
            self.timeline["id"],
            {**self.timeline["data"], "name": "new frozen version"},
            expected_revision=1,
            tenant_id="local",
            actor_id="test",
            reason="new save",
        )
        self.request(parent, 409)
        self.assertEqual(len(self.items("RenderJob")), 1)
        self.assertEqual(len(self.items("Artifact")), 0)

    def test_running_predecessor_is_fenced_after_confirmed_handoff(self):
        reached, resume = threading.Event(), threading.Event()
        first = True
        real_qc = media.technical_qc

        def delay_first(*args):
            nonlocal first
            if first:
                first = False
                reached.set()
                if not resume.wait(30):
                    raise RuntimeError("Handoff did not release the first worker")
            return real_qc(*args)

        with (
            patch("app.media.technical_qc", side_effect=delay_first),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            old = pool.submit(media.render_timeline, self.store, LOCAL_ACTOR, self.args)
            try:
                self.assertTrue(reached.wait(15))
                parent = self.items("RenderJob")[0]
                self.request(parent, 409)
                self.request(parent, 422, interrupt_running="yes")
                result = self.request(parent, interrupt_running=True)
            finally:
                resume.set()
            with self.assertRaises(DomainError) as exc:
                old.result(timeout=15)
            self.assertEqual(exc.exception.code, "RENDER_FENCED")
        parent = self.store.get(parent["id"], "local")
        self.assertEqual(parent["data"]["status"], "superseded")
        self.assertEqual(parent["data"]["continuation"]["job_id"], result["job"]["id"])
        self.assertEqual(len(self.items("Artifact")), 1)

    def test_mid_encode_input_update_has_no_publishable_child(self):
        parent = self.failed()
        real_qc = media.technical_qc

        def revise_source(*args):
            qc = real_qc(*args)
            self.store.update(
                self.version["id"],
                {**self.version["data"], "display_label": "new metadata"},
                expected_revision=self.version["version"],
                tenant_id="local",
                actor_id="test",
                reason="new source revision",
            )
            return qc

        with patch("app.media.technical_qc", side_effect=revise_source):
            self.request(parent, 409)
        self.assertEqual(len(self.items("RenderJob")), 2)
        self.assertEqual(self.items("RenderJob")[0]["data"]["error_code"], "RENDER_INPUT_CHANGED")
        self.assertEqual(len(self.items("Artifact")), 0)

    def test_direct_rerun_of_completed_version_compares_both_decoded_streams_and_preserves_original(
        self,
    ):
        second_file = Path(self.temp.name) / "blue-tone-800.mp4"
        media.run_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=blue:s=96x64:r=25:d=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=800:duration=1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-c:a",
                "aac",
                "-shortest",
                str(second_file),
            ],
            30,
        )
        second = media.import_saved_file(
            self.store, LOCAL_ACTOR, self.pid, second_file.name, second_file
        )["version"]
        transition = {"kind": "dissolve", "from_id": "head", "duration": 0.4}
        timeline = self.create(
            "Timeline",
            {
                **self.timeline["data"],
                "clips": [
                    {
                        "id": "head",
                        "track": 0,
                        "asset_id": self.version["id"],
                        "source_in": 0,
                        "source_out": 0.7,
                        "timeline_start": 0,
                    },
                    {
                        "id": "tail",
                        "track": 0,
                        "asset_id": second["id"],
                        "source_in": 0,
                        "source_out": 0.7,
                        "timeline_start": 0.3,
                        "transition_in": transition,
                    },
                ],
            },
            refs=[media.ref(self.version), media.ref(second)],
        )
        original = self.post("/media/render", {**self.args, "timeline_id": timeline["id"]})
        self.assertEqual(
            original["manifest"]["data"]["transitions"][0],
            {**transition, "to_id": "tail", "start": 0.3, "end": 0.7, "track": 0},
        )
        result = self.request(original["job"])
        old, new = original["artifact"], result["artifact"]
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(self.store.get(old["id"], "local"), old)
        # The overlap really contains both input tones; a matching silent output would fail.
        rendered = media.resolve_media(self.store, LOCAL_ACTOR, new["id"])[2]
        pcm = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "0.47",
                "-i",
                str(rendered),
                "-t",
                "0.05",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "-",
            ]
        )
        samples = [n[0] for n in struct.iter_unpack("<h", pcm)]
        for hz in [400, 800]:
            phase = 2 * math.pi * hz / 8000
            energy = abs(
                sum(
                    n * complex(math.cos(phase * i), math.sin(phase * i))
                    for i, n in enumerate(samples)
                )
            )
            self.assertGreater(energy / len(samples), 35)

        comparison = new["data"]["qc"]["baseline_comparison"]
        self.assertEqual(comparison["artifact"], media.ref(old))
        for name in ("decoded_pcm_sha256", "decoded_video_sha256"):
            self.assertRegex(comparison[name], r"^[a-f0-9]{64}$")
        self.assertFalse(new["data"]["publish_ready"])
        from app.render_recovery import decoded_streams

        def simulate_mismatch(path):
            signature = decoded_streams(path)
            if path.name == "artifact.mp4":
                signature["v"] = "0" * 64
            return signature

        with patch("app.render_recovery.decoded_streams", side_effect=simulate_mismatch):
            rejected = self.request(result["job"], 409)
        self.assertEqual(rejected["error"]["code"], "RENDER_CONTENT_MISMATCH")
        self.assertEqual(len(self.items("Artifact")), 2)

    def test_legacy_jobs_require_reconstructible_input_refs_and_exact_rate(self):
        parent = self.failed()
        legacy = dict(parent["data"])
        legacy.pop("checkpoint")

        def old_with(**changes):
            return self.create("RenderJob", {**legacy, **changes}, refs=parent["refs"])

        invalid = old_with()
        self.assertEqual(self.request(invalid, 409)["error"]["code"], "RENDER_FPS_UNVERIFIABLE")
        integer_profile = {**legacy["profile"], "fps": 25.0}
        no_inputs = self.create(
            "RenderJob", {**legacy, "profile": integer_profile}, refs=[media.ref(self.timeline)]
        )
        self.assertEqual(
            self.request(no_inputs, 409)["error"]["code"], "RENDER_LEGACY_INPUTS_CHANGED"
        )
        compatible = old_with(profile=integer_profile)
        done = self.request(compatible)
        self.assertFalse(done["job"]["data"]["prior_environment_recorded"])
        self.assertTrue(done["qc"]["checks"]["full_decode"])
