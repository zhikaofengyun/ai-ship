"""Two decoded sources, five joins, real audiovisual samples, and invalid overlap guards."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import math
import struct

from app import media, transitions
from app.auth import LOCAL_ACTOR
from app.errors import DomainError
from app.store import Store


class ClipTransitionTests(unittest.TestCase):
    def test_pair_constraints_and_unsupported_effects(self):
        def clip(i, start, d=1, **extra):
            return {"id": i, "timeline_start": start, "duration": d, "track": 0, **extra}

        fx = {"from_id": "a", "kind": "dissolve", "duration": 0.4}
        for bad in (
            {**fx, "duration": False},
            {**fx, "duration": float("inf")},
            {**fx, "kind": "arbitrary"},
            [fx],
        ):
            with self.assertRaises(DomainError):
                transitions.transition_spec(bad)
        for args in (
            [clip("a", 0), clip("b", 0.7, transition_in=fx)],
            [clip("a", 0), clip("b", 0.6, track=1, transition_in=fx)],
            [clip("a", 0), clip("b", 0.6, transition_in=fx), clip("c", 0.7)],
            [clip("a", 0), clip("b", 0.6, 0.4, transition_in=fx)],
        ):
            with self.assertRaises(DomainError):
                transitions.resolve_transitions(args, 25)
        tiny = {**fx, "duration": 0.001}
        with self.assertRaises(DomainError):
            transitions.resolve_transitions(
                [clip("a", 0), clip("b", 0.999, transition_in=tiny)], 25
            )

    def test_decoded_five_transitions_audio_mix_and_lineage(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"AI_SHIP_DATA_DIR": tmp}),
        ):
            store = Store(Path(tmp) / "facts.sqlite3")
            project = store.create(
                "Project",
                {"name": "真实转场取帧"},
                tenant_id="local",
                actor_id=LOCAL_ACTOR.actor_id,
            )
            kwargs = media.actor_kwargs(LOCAL_ACTOR, project["id"])
            clips = []
            for id_, color, hz, start in (("a", "red", 400, 0), ("b", "blue", 800, 0.8)):
                path = Path(tmp) / (id_ + ".mp4")
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={color}:s=160x96:r=25:d=1.6",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=f={hz}:r=48000:d=1.6",
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        "-threads",
                        "1",
                        str(path),
                    ],
                    check=True,
                )
                asset = media.import_saved_file(store, LOCAL_ACTOR, project["id"], path.name, path)[
                    "asset"
                ]
                clips.append(
                    {
                        "id": id_,
                        "track": 0,
                        "asset_id": asset["id"],
                        "source_in": 0,
                        "source_out": 1.6,
                        "timeline_start": start,
                    }
                )
            report = {}
            for kind in transitions.KINDS:
                pair = [
                    clips[0],
                    {**clips[1], "transition_in": {"kind": kind, "from_id": "a", "duration": 0.8}},
                ]
                timeline = store.create(
                    "Timeline",
                    {
                        "name": kind,
                        "clips": pair,
                        "locked": True,
                        "format": {"width": 160, "height": 96, "fps": 25},
                    },
                    **kwargs,
                )
                result = media.render_timeline(
                    store,
                    LOCAL_ACTOR,
                    {
                        "project_id": project["id"],
                        "timeline_id": timeline["id"],
                        "expected_revision": timeline["version"],
                    },
                )
                path = media.resolve_media(store, LOCAL_ACTOR, result["artifact"]["id"])[2]
                raw = subprocess.check_output(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(path),
                        "-map",
                        "0:v",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "-threads",
                        "1",
                        "-",
                    ]
                )
                frame_bytes = 96 * 160 * 3

                def pixel(frame, x):
                    start = frame_bytes * frame + (46 * 160 + x) * 3
                    return list(raw[start : start + 3])

                before, mid, after = (pixel(n, 80) for n in (10, 30, 50))
                left, right = pixel(30, 18), pixel(30, 140)
                self.assertGreater(before[0] - before[2], 180)
                self.assertGreater(after[2] - after[0], 180)
                if kind == "dissolve":
                    self.assertTrue(65 < mid[0] < 185 and 65 < mid[2] < 185, mid)
                elif kind.startswith("wipe"):
                    first, second = (left, right) if kind == "wipe_right" else (right, left)
                    self.assertGreater(first[2] - first[0], 160)
                    self.assertGreater(second[0] - second[2], 160)
                elif kind == "dip_black":
                    self.assertLess(max(mid), 45)
                else:
                    self.assertGreater(min(mid), 205)
                pcm = subprocess.check_output(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(path),
                        "-map",
                        "0:a",
                        "-ac",
                        "1",
                        "-ar",
                        "48000",
                        "-f",
                        "f32le",
                        "-",
                    ]
                )

                def tones(t):
                    start = int(t * 48000) * 4
                    samples = struct.unpack_from("<2400f", pcm, start)
                    spectrum = []
                    for hz in (400, 800):
                        fft = 0j
                        for i, sample in enumerate(samples):
                            angle = 2 * math.pi * hz * i / 48000
                            window = 0.5 - 0.5 * math.cos(2 * math.pi * i / 2399)
                            fft += sample * window * complex(math.cos(angle), -math.sin(angle))
                        spectrum.append(round(abs(fft), 3))
                    return spectrum

                early, blend, late = map(tones, (0.4, 1.18, 1.95))
                self.assertGreater(early[0], 10 * max(0.01, early[1]))
                self.assertGreater(late[1], 10 * max(0.01, late[0]))
                self.assertGreater(min(blend), 0.20 * min(early[0], late[1]))
                segment = next(
                    s
                    for s in result["time_map"]["data"]["segments"]
                    if s["output_interval"] == [800000, 1600000]
                )
                self.assertEqual({c["clip_use_id"] for c in segment["contributions"]}, {"a", "b"})
                self.assertTrue(
                    all(c["transitions"][0]["kind"] == kind for c in segment["contributions"])
                )
                self.assertEqual(result["manifest"]["data"]["transitions"][0]["to_id"], "b")
                report[kind] = {
                    "before_rgb": before,
                    "middle_rgb": mid,
                    "after_rgb": after,
                    "middle_left": left,
                    "middle_right": right,
                    "audio_400_800Hz": [early, blend, late],
                    "frames": len(raw) // frame_bytes,
                    "artifact_sha256": result["artifact"]["data"]["content_hash"],
                }
            if out := os.environ.get("AI_SHIP_MEDIA_EVIDENCE_DIR"):
                folder = Path(out)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "transition-pixel-audio.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
