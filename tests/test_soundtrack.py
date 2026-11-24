"""Decode independent frequencies and inspect actual archive and render rights."""

from array import array
from math import cos, hypot, pi, sin
from pathlib import Path
import subprocess
from uuid import uuid4
import wave

from app.auth import LOCAL_ACTOR
from app.media import extract_audio, import_saved_file, render_timeline, resolve_media
from tests.test_production import ProductionBase


def pin(row):
    return {"id": row["id"], "version": row["version"]}


class SoundtrackTests(ProductionBase):
    def tone(self, name, frequency, seconds=2):
        path = Path(self.temp.name) / (name + ".wav")
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 12000, 0, "NONE", "not compressed"))
            out.writeframes(
                array(
                    "h",
                    (
                        int(13000 * sin(2 * pi * frequency * i / 12000))
                        for i in range(round(seconds * 12000))
                    ),
                ).tobytes()
            )
        v = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path, name)["version"]
        return extract_audio(self.store, LOCAL_ACTOR, v["id"], {"project_id": self.pid})["audio"]

    def req(self, route, data, status=200, project=None, key=None):
        response = self.client.post(
            "/api/v1/production/soundtrack/" + route,
            json={"project_id": project or self.pid, "request_id": key or uuid4().hex, **data},
        )
        self.assertEqual(response.status_code, status, response.text)
        return response.json()["data" if status == 200 else "error"]

    def rights(self, a, **extra):
        return {
            "mode": "local_stock",
            "audio": pin(a),
            "use": "commercial",
            "market": "GB",
            "allowed_uses": ["commercial"],
            "allowed_markets": ["GB"],
            "perpetual": True,
            "name": "已授权原创音乐",
            "evidence": "测试原创人登记，限英国本项目本地混入。",
            **extra,
        }

    def mix(self, a, m, **extra):
        return {
            "name": "成片声音",
            "tracks": [
                {"role": "music", "audio": pin(a), "music": pin(m), "source_in": 0, "source_out": 2}
            ],
            "use": "commercial",
            "market": "GB",
            **extra,
        }

    def video_timeline(self):
        vpath = Path(self.temp.name) / "visual.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=blue:s=96x96:r=25:d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(vpath),
            ],
            check=True,
        )
        v = import_saved_file(self.store, LOCAL_ACTOR, self.pid, vpath.name, vpath, "蓝色画面")[
            "version"
        ]
        return self.create(
            "Timeline",
            {
                "name": "验证声音",
                "format": {"width": 96, "height": 96, "fps": 25},
                "status": "draft",
                "locked": False,
                "clips": [
                    {
                        "id": "visual",
                        "asset_id": v["id"],
                        "source_in": 0,
                        "source_out": 2,
                        "volume": 1,
                    }
                ],
                "audio_tracks": [],
            },
            [pin(v)],
        )

    def level(self, audio, start, hz):
        _, _, path = resolve_media(self.store, LOCAL_ACTOR, audio["id"], project_id=self.pid)
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                str(start),
                "-i",
                str(path),
                "-t",
                "0.15",
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "12000",
                "-f",
                "s16le",
                "-",
            ]
        )
        samples = array("h")
        samples.frombytes(raw)
        return hypot(
            sum(v * cos(i * 2 * pi * hz / 12000) for i, v in enumerate(samples)),
            sum(v * sin(i * 2 * pi * hz / 12000) for i, v in enumerate(samples)),
        ) / len(samples)

    def test_licensed_stock_and_scope_expiry_no_music_and_platform_handoff(self):
        a = self.tone("stock", 220)
        self.req("rights", self.rights(a, allowed_markets=["US"]), 403)
        self.req("rights", self.rights(a, allowed_uses=["private"]), 403)
        self.req("rights", self.rights(a, expires_at="2020-01-01T00:00:00Z", perpetual=False), 403)
        self.req("rights", self.rights(a, perpetual=False), 422)
        body = self.rights(a)
        approved = self.req("rights", body, key="rights-request")
        again = self.req("rights", body, key="rights-request")
        self.assertEqual(approved["music"]["id"], again["music"]["id"])
        self.assertEqual(approved["approval"]["data"]["kind"], "rights_check")
        self.assertEqual(approved["grant"]["data"]["bound_checksum"], a["data"]["content_hash"])
        base = {"use": "private", "market": "GB", "name": "选择声音", "evidence": "核对项目要求"}
        no = self.req("rights", {**base, "mode": "no_music"})
        self.assertIsNone(no["grant"])
        plat = self.req(
            "rights",
            {
                **base,
                "mode": "platform_sound",
                "sound_id": "licensed-sound-id",
                "account": pin(self.account),
            },
        )
        self.assertIsNone(plat["approval"])
        self.assertFalse(plat["music"]["data"]["platform_effect_verified"])
        self.req("mixes", self.mix(a, plat["music"]), 403)
        self.req("rights", {**base, "mode": "platform_sound", "sound_id": "s"}, 422)
        project = self.create(
            "Project", {"name": "必须有音乐", "content_mode": "music", "market": "GB"}
        )
        self.req("rights", {**base, "mode": "no_music"}, 422, project=project["id"])

    def test_mix_scopes_stale_pins_invalid_ranges_and_currency_of_rights(self):
        a = self.tone("original", 220)
        b = self.tone("other", 660)
        checked = self.req("rights", self.rights(a))
        m = checked["music"]
        grant = checked["grant"]
        self.req("mixes", self.mix(b, m), 403)
        self.req("mixes", self.mix(a, m, market="US"), 403)
        self.req("mixes", self.mix(a, m, use="editorial"), 403)
        self.req("mixes", self.mix(a, m, ducking=True), 422)
        bad = self.mix(a, m)
        bad["tracks"][0]["source_out"] = 3
        self.req("mixes", bad, 422)
        stale = self.mix(a, m)
        stale["tracks"][0]["audio"]["version"] = 99
        self.req("mixes", stale, 409)
        self.store.update(
            grant["id"],
            {**grant["data"], "licensed_markets": ["US"]},
            tenant_id="local",
            actor_id="reviewer",
            expected_revision=1,
        )
        self.req("mixes", self.mix(a, m), 409)
        self.assertFalse(self.store.list("local", kind="Mix", project_id=self.pid))

    def test_ducking_changes_only_music_under_dialogue_and_periods_are_preserved(self):
        music = self.tone("score", 240)
        voice = self.tone("voice", 720, 0.7)
        m = self.req("rights", self.rights(music))["music"]
        body = self.mix(music, m)
        body["tracks"].append(
            {"role": "voice", "audio": pin(voice), "source_out": 0.7, "start": 0.6}
        )
        level = self.req("mixes", body)["audio"]
        result = self.req("mixes", {**body, "ducking": True})
        actual = result["audio"]
        self.assertLess(self.level(actual, 0.9, 240), self.level(level, 0.9, 240) * 0.45)
        self.assertGreater(self.level(actual, 0.1, 240), self.level(level, 0.1, 240) * 0.95)
        self.assertGreater(self.level(actual, 0.9, 720), self.level(level, 0.9, 720) * 0.92)
        self.assertGreater(self.level(actual, 1.8, 240), self.level(level, 1.8, 240) * 0.7)
        d = result["mix"]["data"]
        self.assertEqual(d["bus_order"], ["music", "voice"])
        self.assertIsNotNone(d["measurement"]["integrated_lufs"])
        self.assertLess(d["measurement"]["true_peak_dbtp"], 0)
        self.assertAlmostEqual(d["measurement"]["duration"], 2, places=3)

    def test_stock_arrange_adopt_without_duplication_deliver_measure_and_revoke(self):
        a = self.tone("score", 380)
        checked = self.req("rights", self.rights(a))
        m = checked["music"]
        body = self.mix(a, m)
        body["tracks"][0].update(
            source_in=0.2, source_out=1.2, start=0.5, fade_in=0.1, fade_out=0.1
        )
        mixed = self.req("mixes", body, key="mix-request")
        again = self.req("mixes", body, key="mix-request")
        self.assertEqual(again["mix"]["id"], mixed["mix"]["id"])
        self.assertEqual(mixed["music"]["data"]["analysis_status"], "not_run")
        self.assertNotIn("provider", mixed["music"]["data"])
        report = mixed["mix"]["data"]["measurement"]
        self.assertAlmostEqual(report["silences"][0]["end"], 0.5, delta=0.03)
        timeline = self.video_timeline()
        request = {"mix": pin(mixed["mix"]), "timeline": pin(timeline), "original_audio": "mute"}
        adopted = self.req("adopt", request)["timeline"]
        self.assertEqual(adopted["data"]["clips"][0]["volume"], 0)
        self.assertEqual(
            self.store.get(timeline["id"], "local", version=1)["data"]["clips"][0]["volume"], 1
        )
        self.req("adopt", request, 409)
        repeat = self.req("adopt", {**request, "timeline": pin(adopted)})["timeline"]
        self.assertEqual(len(repeat["data"]["audio_tracks"]), 1)
        locked = self.store.update(
            repeat["id"],
            {**repeat["data"], "locked": True, "status": "locked"},
            tenant_id="local",
            expected_revision=repeat["version"],
            actor_id="test",
        )
        self.req("adopt", {**request, "timeline": pin(locked)}, 409)
        art = render_timeline(
            self.store,
            LOCAL_ACTOR,
            {
                "project_id": self.pid,
                "timeline_id": locked["id"],
                "expected_revision": locked["version"],
            },
        )["artifact"]
        output = self.req("inspect-output", {"artifact": pin(art)})["measurement"]
        self.assertEqual(output["stream_clock"]["status"], "measured_stream_boundaries")
        self.assertLess(abs(output["stream_clock"]["end_delta_seconds"]), 0.06)
        self.assertIsNotNone(output["true_peak_dbtp"])
        self.assertGreater(self.level(art, 0.8, 380), 1000)
        g = checked["grant"]
        self.store.update(
            g["id"],
            {**g["data"], "status": "revoked"},
            tenant_id="local",
            actor_id="test",
            expected_revision=g["version"],
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/media/artifacts/" + art["id"] + "/file?download=true"
            ).status_code,
            403,
        )
        self.req("mixes", body, 409)

    def test_remove_score_preserves_voice_and_source_versions(self):
        music = self.tone("note", 220)
        voice = self.tone("spoken", 760, 0.8)
        permit = self.req("rights", self.rights(music))["music"]
        req = self.mix(music, permit)
        req["tracks"].append(
            {"role": "voice", "audio": pin(voice), "source_out": 0.8, "start": 0.4}
        )
        original = self.req("mixes", req)
        tl = self.video_timeline()
        adopted = self.req(
            "adopt", {"timeline": pin(tl), "mix": pin(original["mix"]), "original_audio": "keep"}
        )["timeline"]
        none = self.req(
            "rights",
            {
                "mode": "no_music",
                "name": "仅口播",
                "market": "GB",
                "use": "commercial",
                "evidence": "保留对白，去除配乐",
            },
        )["music"]
        removed = self.req("remove-music", {"timeline": pin(adopted), "music": pin(none)})
        self.assertTrue(removed["removed_managed_music"])
        new = removed["timeline"]["data"]
        audio_id = new["audio_tracks"][0]["asset_id"]
        voice_only = self.store.get(audio_id, "local")
        self.assertGreater(self.level(voice_only, 0.6, 760), 1500)
        self.assertLess(self.level(voice_only, 0.6, 220), 30)
        self.assertGreater(self.level(original["audio"], 0.6, 220), 1000)
        self.assertEqual(
            self.store.get(tl["id"], "local", adopted["version"])["data"]["soundtrack_mix"]["id"],
            original["mix"]["id"],
        )

    def test_meter_cannot_approve_different_file_or_fabricated_mix(self):
        a = self.tone("score", 400)
        cue = self.req("rights", self.rights(a))["music"]
        result = self.req("mixes", self.mix(a, cue))
        self.assertEqual(result["music"]["data"]["arranged_source"]["kind"], "Source")
        version = self.store.get(result["music"]["data"]["arranged_version"]["id"], "local")
        self.assertEqual(version["data"]["content_hash"], result["audio"]["data"]["content_hash"])
        timeline = self.video_timeline()
        fake = self.create("Mix", result["mix"]["data"], result["mix"]["refs"])
        self.assertEqual(
            self.req(
                "adopt",
                {"mix": pin(fake), "timeline": pin(timeline), "original_audio": "keep"},
                409,
            )["code"],
            "SOUNDTRACK_RECEIPT_MISSING",
        )
        locked = self.store.update(
            timeline["id"],
            {**timeline["data"], "locked": True, "status": "locked"},
            tenant_id="local",
            expected_revision=1,
            actor_id="test",
        )
        art = render_timeline(
            self.store,
            LOCAL_ACTOR,
            {
                "project_id": self.pid,
                "timeline_id": locked["id"],
                "expected_revision": locked["version"],
            },
        )["artifact"]
        qc = self.req("inspect-output", {"artifact": pin(art)})["audit"]
        self.assertIsNone(qc["data"]["audio_measurement"]["integrated_lufs"])
        self.assertEqual(qc["data"]["audio_measurement"]["silence_count"], 1)
        req = {
            "artifact": pin(art),
            "meter": pin(qc),
            "decision": "rework",
            "findings": [
                {
                    "start": 0,
                    "end": 0.4,
                    "category": "silence",
                    "observation": "开头静音，需制作确认",
                }
            ],
        }
        assessment = self.req("listen-review", req)["review"]
        self.assertEqual(assessment["data"]["kind"], "review_finding")
        self.assertEqual(assessment["data"]["subject_hash"], art["hash"])
        req["findings"][0]["end"] = 9
        self.req("listen-review", req, 422)
        req["meter"] = pin(result["audit"])
        self.req("listen-review", req, 409)
