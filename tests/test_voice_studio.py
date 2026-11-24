"""Project-scoped voice/visual bindings with real decoded speech and picture fixtures."""

from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.media import extract_audio, import_saved_file
from tests.test_production import ProductionBase


def pinned(item):
    return {"id": item["id"], "version": item["version"]}


class VoiceStudioTests(ProductionBase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.clip = Path(cls.directory.name) / "two-blue-squares.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=white:s=160x120:r=25:d=2,drawbox=x=12:y=34:w=45:h=45:c=blue:t=fill,drawbox=x=86:y=34:w=45:h=45:c=blue:t=fill",
                "-f",
                "lavfi",
                "-i",
                "flite=text='two blue squares':voice=slt,apad=pad_dur=2",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(cls.clip),
            ],
            check=True,
            timeout=30,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        super().setUp()
        self.media = import_saved_file(
            self.store, LOCAL_ACTOR, self.pid, self.clip.name, self.clip, "语音实测素材"
        )["version"]
        self.audio = extract_audio(
            self.store, LOCAL_ACTOR, self.media["id"], {"project_id": self.pid}
        )["audio"]
        self.product = self.create(
            "Product",
            {
                "name": "蓝色方块",
                "SKU": "BLUE-SQUARE",
                "price": 8,
                "currency": "CNY",
                "evidence": "合成商品卡",
            },
        )
        self.fact = {
            "subject": "蓝色方块",
            "quantity": 2,
            "unit": "个",
            "product": pinned(self.product),
        }
        self.cue = {
            "key": "square",
            "words": "two blue squares",
            "meaning": self.fact,
            "start": 0,
            "end": 1.2,
            "heard": True,
            "price_caption": True,
        }

    def send(self, route, body, status=200):
        r = self.client.post(
            "/api/v1/production/voice-studio/" + route,
            json={"project_id": self.pid, "request_id": uuid4().hex, **body},
        )
        self.assertEqual(r.status_code, status, r.text)
        return r.json()["data" if status == 200 else "error"]

    def map(self, **kw):
        return self.send(
            "maps", {"name": "双蓝方块", "audio": pinned(self.audio), "cues": [self.cue], **kw}
        )["map"]

    def observation(self, **kw):
        return self.send(
            "observations",
            {
                "media": pinned(self.media),
                "meaning": self.fact,
                "start": 0,
                "end": 1.8,
                "seen_at": 0.64,
                "visual_note": "原片两枚蓝色方块并列",
                "request_id": uuid4().hex,
                **kw,
            },
        )["observation"]

    def plan(self, m, obs, **kw):
        return self.send(
            "plans", {"voice_map": pinned(m), "observations": list(map(pinned, obs)), **kw}
        )["plan"]

    def test_actual_audio_clock_counterexample_clip_audio_caption_render_and_idempotency(self):
        m = self.map()
        self.assertEqual(m["data"]["clock"]["sha256"], self.audio["data"]["content_hash"])
        self.assertEqual(
            m["data"]["anchors"][0]["sample_range"],
            [0, round(m["data"]["clock"]["sample_rate"] * 1.2)],
        )
        wrong = self.observation(meaning={**self.fact, "quantity": 1})
        exact = self.observation()
        p = self.plan(m, [wrong, exact], locked={"square": pinned(exact)})
        s = p["data"]["slots"][0]
        self.assertEqual(s["candidates"][0]["differences"], ["quantity"])
        self.assertEqual(s["selected"]["id"], exact["id"])
        req = {"plan": pinned(p), "request_id": uuid4().hex}
        adopted = self.send("adopt", req)
        t = adopted["timeline"]
        binding = adopted["bindings"][0]["data"]
        self.assertEqual(self.send("adopt", req)["timeline"]["id"], t["id"])
        self.assertEqual(binding["clip_use_ids"], [t["data"]["clips"][0]["id"]])
        self.assertEqual(t["data"]["audio_tracks"][0]["asset_id"], self.audio["id"])
        self.assertEqual(t["data"]["captions"][0]["text"], "CNY 8")
        t = self.store.update(
            t["id"],
            {**t["data"], "locked": True},
            tenant_id="local",
            expected_revision=t["version"],
            actor_id="test",
        )
        rendered = self.post(
            "/media/render",
            {
                "project_id": self.pid,
                "timeline_id": t["id"],
                "expected_revision": t["version"],
                "profile": {"width": 160, "height": 120, "fps": 25},
            },
        )
        self.assertEqual(rendered["artifact"]["kind"], "Artifact")
        file = self.client.get("/api/v1/media/artifacts/" + rendered["artifact"]["id"] + "/file")
        self.assertEqual(file.status_code, 200)
        out = Path(self.temp.name) / "export.mp4"
        out.write_bytes(file.content)
        pcm = subprocess.check_output(
            ["ffmpeg", "-v", "error", "-i", str(out), "-vn", "-f", "s16le", "-"]
        )
        self.assertGreater(len(set(pcm)), 20)
        rgb = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "0.5",
                "-i",
                str(out),
                "-vf",
                "scale=160:120",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ]
        )
        for x in (25, 100):
            pixel = rgb[(50 * 160 + x) * 3 : (50 * 160 + x) * 3 + 3]
            self.assertGreater(pixel[2], pixel[0] + 100)

    def test_rhythm_preserves_speech_and_lock_and_uses_a_real_marked_audio_clock(self):
        cues = [
            {**self.cue, "start": 0.1, "end": 0.4},
            {**self.cue, "key": "second", "start": 0.8, "end": 1.1},
        ]
        vm = self.map(cues=cues)
        obs = self.observation()
        plan = self.plan(vm, [obs])
        rate = vm["data"]["clock"]["sample_rate"]
        music = self.post(
            "/production/music/markers",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "audio": pinned(self.audio),
                "name": "拍点实测",
                "use_markers": True,
                "markers": [
                    {"type": "beat", "time": 0.61},
                    {"type": "downbeat", "time": 0.58},
                    {"type": "section", "time": 0.65},
                ],
            },
        )["music"]
        changed = self.send(
            "rhythm",
            {
                "plan": pinned(plan),
                "mode": "beat",
                "music": pinned(music),
                "marker_types": ["downbeat"],
                "tolerance_ms": 30,
            },
        )["plan"]
        edge = changed["data"]["rhythm"]["boundaries"][0]
        self.assertEqual(edge["cut_sample"], round(rate * 0.58))
        self.assertEqual([m["type"] for m in edge["eligible_markers"]], ["downbeat"])
        adopted = self.send("adopt", {"plan": pinned(changed)})
        left, right = adopted["timeline"]["data"]["clips"]
        self.assertAlmostEqual(
            left["timeline_start"] + left["source_out"] - left["source_in"], 0.58
        )
        self.assertAlmostEqual(right["timeline_start"], 0.58)
        self.assertEqual(
            adopted["bindings"][0]["data"]["audio_sample_range"],
            [round(0.1 * rate), round(0.4 * rate)],
        )
        manual = self.send(
            "rhythm",
            {
                "plan": pinned(changed),
                "mode": "manual",
                "manual_cuts": {"square": 0.75},
                "tolerance_ms": 20,
            },
        )["plan"]
        self.assertTrue(manual["data"]["rhythm"]["boundaries"][0]["kept_original"])
        locked = self.plan(vm, [obs], previous=pinned(manual), locked={"square": pinned(obs)})
        limited = self.send(
            "rhythm", {"plan": pinned(locked), "mode": "semantic", "tolerance_ms": 5}
        )["plan"]
        self.assertTrue(limited["data"]["rhythm"]["boundaries"][0]["kept_original"])
        self.send("rhythm", {"plan": pinned(plan), "mode": "semantic"}, 409)
        forged = self.create("Music", music["data"])
        self.send("rhythm", {"plan": pinned(limited), "mode": "beat", "music": pinned(forged)}, 409)

    def test_untimed_estimated_gap_repair_and_immutable_old_script(self):
        draft = self.map(
            audio=None, cues=[{**self.cue, "start": None, "end": None, "heard": False}]
        )
        good = self.observation()
        p = self.plan(draft, [good])
        self.assertTrue(p["data"]["slots"][0]["semantic_ok"])
        self.assertFalse(p["data"]["slots"][0]["time_ok"])
        self.assertEqual(self.send("adopt", {"plan": pinned(p)}, 409)["code"], "VOICE_INCOMPLETE")
        m = self.map()
        short = self.observation(end=0.8)
        p = self.plan(m, [short])
        self.assertEqual(p["data"]["slots"][0]["candidates"][0]["differences"], ["duration"])
        task = self.send(
            "repair",
            {
                "plan": pinned(p),
                "anchor": "square",
                "action": "generation",
                "instruction": "生成两枚方块",
            },
        )
        self.assertEqual(task["task"]["data"]["status"], "draft")
        self.assertIn("two blue squares", task["generation_request"]["prompt"])
        rewrite = self.send(
            "repair",
            {
                "plan": pinned(p),
                "anchor": "square",
                "action": "rewrite",
                "instruction": "blue square",
            },
        )["script"]
        self.assertEqual(rewrite["data"]["text"], "blue square")
        self.assertTrue(rewrite["data"]["requires_new_audio"])
        self.assertEqual(
            self.store.get(m["data"]["script_version"]["id"], "local")["data"]["text"],
            "two blue squares",
        )

    def test_clock_bounds_untrusted_notes_conflict_and_project_scope(self):
        base = {"name": "review", "audio": pinned(self.audio), "cues": [{**self.cue, "end": 3}]}
        self.assertEqual(self.send("maps", base, 422)["code"], "VOICE_RANGE_INVALID")
        self.assertEqual(
            self.send("maps", {**base, "audio": None}, 422)["code"], "VOICE_AUDIO_REQUIRED"
        )
        self.assertEqual(
            self.send("maps", {**base, "cues": [self.cue, self.cue]}, 422)["code"],
            "VOICE_DUPLICATE_ANCHOR",
        )
        other = self.create("Project", {"name": "other"})
        self.assertEqual(
            self.send("maps", {**base, "project_id": other["id"]}, 403)["code"],
            "PROJECT_SCOPE_MISMATCH",
        )
        invalid_claim = {**self.cue, "words": "特价 USD 8"}
        self.assertEqual(
            self.send("maps", {**base, "cues": [invalid_claim]}, 422)["code"],
            "VOICE_PRICE_CONFLICT",
        )
        fake = self.create("Tag", {"name": "未看片"})
        m = self.map()
        self.assertEqual(
            self.send("plans", {"voice_map": pinned(m), "observations": [pinned(fake)]}, 409)[
                "code"
            ],
            "VOICE_REVIEW_REQUIRED",
        )

    def test_human_lock_local_replanning_stale_product_and_revoked_source(self):
        m = self.map()
        tag = self.observation()
        second = self.observation()
        p = self.plan(m, [tag, second], locked={"square": pinned(second)})
        revised = self.plan(m, [tag, second], previous=pinned(p))
        self.assertEqual(revised["data"]["slots"][0]["selected"]["id"], second["id"])
        self.assertEqual(
            self.send(
                "plans",
                {
                    "previous": pinned(revised),
                    "voice_map": pinned(m),
                    "observations": [pinned(tag)],
                },
                409,
            )["code"],
            "VOICE_LOCK_EVIDENCE",
        )
        free = self.plan(m, [tag], previous=pinned(revised), unlock=["square"])
        self.assertFalse(free["data"]["slots"][0]["locked"])
        self.assertEqual(self.send("adopt", {"plan": pinned(p)}, 409)["code"], "STALE_SELECTION")
        self.store.update(
            self.product["id"],
            {**self.product["data"], "price": 9},
            tenant_id="local",
            expected_revision=1,
            actor_id="test",
        )
        self.assertEqual(self.send("adopt", {"plan": pinned(free)}, 409)["code"], "STALE_SELECTION")
        self.store.update(
            self.media["id"],
            {**self.media["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=1,
            actor_id="test",
        )
        response = self.client.get("/api/v1/media/" + self.media["id"] + "/frame")
        self.assertEqual(response.status_code, 403)

    def test_equal_duration_audio_changes_require_new_attestation_and_changed_clip_blocks_render(
        self,
    ):
        m = self.map()
        tag = self.observation()
        p = self.plan(m, [tag])
        t = self.send("adopt", {"plan": pinned(p)})["timeline"]
        clip = t["data"]["clips"][0]
        edited = self.store.update(
            t["id"],
            {**t["data"], "locked": True, "clips": [{**clip, "timeline_start": 0.2}]},
            tenant_id="local",
            expected_revision=1,
            actor_id="test",
        )
        denied = self.post(
            "/media/render",
            {
                "project_id": self.pid,
                "timeline_id": edited["id"],
                "expected_revision": edited["version"],
            },
            409,
        )
        self.assertEqual(denied["error"]["code"], "VOICE_TIMELINE_REVIEW")
        raw = Path(self.temp.name) / "alternative.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(self.clip),
                "-vn",
                "-af",
                "volume=0.6",
                "-ar",
                "48000",
                "-t",
                "2",
                "-y",
                str(raw),
            ],
            check=True,
            timeout=20,
        )
        fresh = import_saved_file(self.store, LOCAL_ACTOR, self.pid, raw.name, raw, "替换口播")
        new_audio = extract_audio(
            self.store, LOCAL_ACTOR, fresh["version"]["id"], {"project_id": self.pid}
        )["audio"]
        self.assertEqual(
            self.send(
                "maps",
                {
                    "name": "保留旧听审",
                    "audio": pinned(new_audio),
                    "previous": pinned(m),
                    "cues": [self.cue],
                },
                409,
            )["code"],
            "VOICE_REHEAR_REQUIRED",
        )
        revised = self.map(
            audio=pinned(new_audio),
            previous=pinned(m),
            script=pinned(m["data"]["script_version"]),
            cues=[{**self.cue, "start": 0.1, "end": 1.3, "heard": False}],
        )
        self.assertTrue(revised["data"]["audio_changed"])
        self.assertNotEqual(revised["data"]["clock"]["sha256"], m["data"]["clock"]["sha256"])
        self.assertEqual(revised["data"]["changed_anchors"], ["square"])
        self.assertEqual(self.send("adopt", {"plan": pinned(p)}, 409)["code"], "STALE_SELECTION")
        self.assertEqual(
            self.store.get(m["id"], "local", version=1)["data"]["anchors"][0]["timing_status"],
            "human_verified",
        )
