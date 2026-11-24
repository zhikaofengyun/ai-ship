from pathlib import Path
from uuid import uuid4
import subprocess

from app.auth import LOCAL_ACTOR
from app.media import import_saved_file, extract_audio
from tests.test_production import ProductionBase


class ManualRhythmTests(ProductionBase):
    def setUp(self):
        super().setUp()
        file = Path(self.temp.name) / "markers.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:sample_rate=44100:duration=1.5",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(file),
            ],
            check=True,
            timeout=20,
        )
        asset = import_saved_file(
            self.store, LOCAL_ACTOR, self.pid, file.name, file, "生成的音频测试"
        )
        self.a = extract_audio(
            self.store, LOCAL_ACTOR, asset["version"]["id"], {"project_id": self.pid}
        )["audio"]

    def body(self, **extra):
        return {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "audio": {"id": self.a["id"], "version": self.a["version"]},
            "name": "测试节拍",
            "use_markers": True,
            "markers": [
                {"type": "beat", "time": 0.125},
                {"type": "downbeat", "time": 0.125},
                {"type": "section", "time": 0.6, "label": "主段"},
            ],
            **extra,
        }

    def send(self, body, status=200):
        result = self.client.post("/api/v1/production/music/markers", json=body)
        self.assertEqual(result.status_code, status, result.text)
        return result.json()["data"] if status == 200 else result.json()["error"]

    def test_manual_audio_marks_independent_clock_disable_and_versions(self):
        req = self.body()
        a = self.send(req)["music"]
        data = a["data"]
        self.assertEqual(self.send(req)["music"]["id"], a["id"])
        self.assertEqual(data["beats"][0]["sample"], 6000)
        self.assertEqual(data["analysis_clock"]["sample_rate_hz"], 48000)
        self.assertEqual(data["sections"][0]["label"], "主段")
        self.assertEqual(data["analysis_outputs"]["onsets"]["status"], "not_run")
        self.assertEqual(data["analysis_outputs"]["downbeats"]["provider"], "human")
        self.assertIsNone(data["bpm"])
        edited = self.send(
            self.body(music={"id": a["id"], "version": 1}, use_markers=False, markers=[])
        )["music"]
        self.assertEqual(edited["version"], 2)
        self.assertEqual(edited["data"]["analysis_outputs"]["beats"]["status"], "unmarked")
        self.assertFalse(edited["data"]["use_markers"])
        self.assertEqual(
            self.store.get(a["id"], "local", version=1)["data"]["markers"], data["markers"]
        )
        self.assertEqual(
            self.send(self.body(music={"id": a["id"], "version": 1}), 409)["code"],
            "STALE_SELECTION",
        )

    def test_invalid_project_time_revision_duplicates_and_no_inferred_hits(self):
        self.assertEqual(self.send(self.body(markers=[]), 422)["code"], "NO_RHYTHM_MARKERS")
        self.assertEqual(
            self.send(self.body(markers=[{"time": 1.5, "type": "beat"}]), 422)["code"],
            "MARKER_OUT_OF_RANGE",
        )
        self.assertEqual(
            self.send(
                self.body(markers=[{"time": 0.4, "type": "beat"}, {"time": 0.4, "type": "beat"}]),
                422,
            )["code"],
            "DUPLICATE_MARKER",
        )
        other = self.store.create("Project", {"name": "其他"}, tenant_id="local", actor_id="test")
        self.assertEqual(
            self.send(self.body(project_id=other["id"]), 403)["code"], "PROJECT_SCOPE_MISMATCH"
        )
        self.assertEqual(
            self.send(self.body(audio={"id": self.a["id"], "version": 20}), 409)["code"],
            "AUDIO_VERSION_MISMATCH",
        )
        self.assertEqual(len(self.store.list("local", kind="Music")), 0)
