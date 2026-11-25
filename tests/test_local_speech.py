"""Default, fully offline guards for the separately exercised native ASR adapter."""

import io
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import wave

from app.speech_worker import model_digest
from tests.test_production import ProductionBase


class LocalSpeechTests(ProductionBase):
    def setUp(self):
        super().setUp()
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00" * 32000)
        uploaded = self.client.post(
            "/api/v1/media/import",
            params={"project_id": self.pid, "filename": "one-second.wav"},
            content=stream.getvalue(),
        )
        self.assertTrue(uploaded.is_success, uploaded.text)
        self.source = uploaded.json()["data"]["version"]
        self.pin = {"id": self.source["id"], "version": self.source["version"]}

    def test_disabled_inference_never_creates_candidates_and_enforces_source_version(self):
        with patch.dict(os.environ, {"AI_SHIP_ASR_MODELS": "{}"}):
            self.assertEqual(
                self.client.get("/api/v1/production/speech/models").json()["data"]["models"], []
            )
            body = {
                "source": self.pin,
                "project_id": self.pid,
                "request_id": "disabled",
                "model_id": "en",
            }
            error = self.post("/production/speech/recognize", body, 503)
            self.assertIn("ASR_UNAVAILABLE", str(error))
            error = self.post(
                "/production/speech/recognize", {**body, "source": {**self.pin, "version": 2}}, 409
            )
            self.assertIn("ASR_STALE_SOURCE", str(error))
            self.assertFalse(
                [
                    r
                    for r in self.store.list("local", "Audit", self.pid)
                    if r["data"].get("protocol") == "vosk-source-captions-v1"
                ]
            )

    def test_review_permissions_and_source_bound_indices(self):
        item = self.create(
            "Audit",
            {
                "protocol": "vosk-source-captions-v1",
                "status": "awaiting_review",
                "clock": "source_presentation_seconds",
                "source": self.pin,
                "cues": [{"start": 0.12, "end": 0.67, "text": "fixture"}],
                "model_sha256": "0" * 64,
            },
        )
        body = {
            "project_id": self.pid,
            "request_id": "review",
            "candidate": {"id": item["id"], "version": 1},
            "reason": "fixture reviewer",
            "cues": [{"index": 0, "text": "corrected"}],
        }
        with patch.dict(
            os.environ,
            {
                "AI_SHIP_MODE": "team",
                "AI_SHIP_API_KEYS": '{"edit":{"tenant_id":"local","actor_id":"e","role":"editor"}}',
            },
        ):
            blocked = self.client.post(
                "/api/v1/production/speech/review",
                json=body,
                headers={"Authorization": "Bearer edit"},
            )
            self.assertEqual(blocked.status_code, 403)
        self.post("/production/speech/review", {**body, "cues": body["cues"] * 2}, 422)
        self.post(
            "/production/speech/review", {**body, "cues": [{"index": 4, "text": "outside"}]}, 422
        )
        self.post("/production/speech/review", {**body, "cues": [{"index": 0, "text": "   "}]}, 422)
        result = self.post("/production/speech/review", body)["approval"]
        self.assertEqual(
            result["data"]["captions"], [{"start": 0.12, "end": 0.67, "text": "corrected"}]
        )
        self.assertEqual(
            self.post("/production/speech/review", body)["approval"]["id"], result["id"]
        )
        self.assertIn(
            "IDEMPOTENCY",
            str(self.post("/production/speech/review", {**body, "reason": "different"}, 409)),
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/production/speech/reviews/" + item["id"] + "/captions",
                params={"project_id": self.pid},
            ).status_code,
            422,
        )
        parsed = self.client.get(
            "/api/v1/production/speech/reviews/" + result["id"] + "/captions",
            params={"project_id": self.pid},
        ).json()["data"]
        self.assertIn("00:00:00,120 --> 00:00:00,670", parsed["content"])

    def test_weights_change_and_symbolic_links_cannot_match_original_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "weights"
            f.write_bytes(b"a")
            original = model_digest(root)
            f.write_bytes(b"b")
            self.assertNotEqual(original, model_digest(root))
            (root / "link").symlink_to(f)
            with self.assertRaises(ValueError):
                model_digest(root)
