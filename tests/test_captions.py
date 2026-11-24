import os
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app


class CaptionExchangeTests(TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.env = patch.dict(os.environ, {"AI_SHIP_MODE": "local"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.client.close)

    def parse(self, content, suffix="srt", **extra):
        return self.client.post(
            "/api/v1/media/captions/parse",
            json={"filename": "镜头清单." + suffix, "content": content, **extra},
        )

    def export(self, captions, fmt):
        return self.client.post(
            "/api/v1/media/captions/export", json={"captions": captions, "format": fmt}
        )

    def test_multiline_overlapping_cues_keep_actual_millisecond_and_language(self):
        result = self.parse(
            "\ufeff1\r\n00:00:00,157 --> 00:00:02,034\r\n台词一\r\n售价 ¥10。\r\n\r\n"
            "2\r\n00:00:01,500 --> 00:00:03,001\r\n‫مرحبا\r\n",
            language="zh-Hans",
        )
        self.assertEqual(result.status_code, 200, result.text)
        cues = result.json()["data"]["captions"]
        self.assertEqual(len(cues), 2)
        self.assertEqual((cues[0]["start"], cues[0]["end"]), (0.157, 2.034))
        self.assertEqual(cues[0]["language"], "zh-Hans")
        self.assertEqual(cues[0]["timing_precision"], "cue")
        self.assertEqual(cues[0]["source_filename"], "镜头清单.srt")
        for fmt in ("srt", "vtt"):
            exported = self.export(cues, fmt)
            self.assertEqual(exported.status_code, 200, exported.text)
            reparsed = self.parse(exported.json()["data"]["content"], fmt).json()["data"][
                "captions"
            ]
            self.assertEqual(
                [(c["start"], c["end"], c["text"]) for c in cues],
                [(c["start"], c["end"], c["text"]) for c in reparsed],
            )

    def test_vtt_literal_text_escapes_and_time_order(self):
        content = "WEBVTT\n\nNOTE producer note\ncomments\n\nc1\n00:00.012 --> 00:01.173\n价签 &lt;b&gt; &amp; x"
        cue = self.parse(content, "vtt").json()["data"]["captions"][0]
        self.assertEqual(cue["text"], "价签 <b> & x")
        cue2 = {**cue, "start": 3, "end": 4, "text": "late"}
        output = self.export([cue2, cue], "vtt").json()["data"]["content"]
        self.assertLess(output.index("价签"), output.index("late"))
        self.assertEqual(
            self.parse(output, "vtt").json()["data"]["captions"][0]["text"], cue["text"]
        )
        self.assertEqual(self.export([cue], "srt").status_code, 422)
        unordered = "WEBVTT\n\n00:02.000 --> 00:03.000\nx\n\n00:00.000 --> 00:01.000\ny"
        self.assertEqual(self.parse(unordered, "vtt").json()["error"]["details"], {"line": 6})

    def test_invalid_files_return_original_line_and_no_partial_result(self):
        first = "1\n00:00:00,001 --> 00:00:01,964\n保留\n\n"
        for second, line in [
            ("2\n00:00:02,000 --> 00:00:02,000\nx", 6),
            ("2\n00:00:00,000 --> 00:99:99,900\nx", 6),
            ("2\n00:00:00,000 --> 01:00:00,001\nx", 6),
            ("2\n00:00:02,000 --> 00:00:02,100\n<b>内容</b>", 7),
            ("2\n00:00:00,100 --> 00:00:00,200 align:center\nx", 6),
        ]:
            with self.subTest(second=second):
                response = self.parse(first + second)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"]["details"], {"line": line})
                self.assertNotIn("data", response.json())
        self.assertEqual(self.parse("x", "ass").status_code, 422)
        self.assertEqual(self.parse("WEBVTT\n\nSTYLE\n::cue {color:red}", "vtt").status_code, 422)

    def test_limits_and_no_silent_loss_on_export(self):
        blocks = [f"{n}\n00:00:00,000 --> 00:00:00,250\nx" for n in range(1, 402)]
        self.assertEqual(self.parse("\n\n".join(blocks[:400])).status_code, 200)
        self.assertEqual(self.parse("\n\n".join(blocks)).status_code, 422)
        self.assertEqual(
            self.export([{"start": 0, "end": 0.0001, "text": "x"}], "srt").status_code, 422
        )
        for text in ("a\n\nb", "x\x00", "   "):
            self.assertEqual(
                self.export([{"start": 0, "end": 1, "text": text}], "vtt").status_code, 422
            )

    def test_team_credentials_required_for_exchange(self):
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": "{}"}):
            self.assertEqual(self.parse("1\n00:00:00,001 --> 00:00:01,002\nx").status_code, 401)
