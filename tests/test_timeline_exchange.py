import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import opentimelineio as otio
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class TimelineExchangeTests(ProductionBase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp_media.name) / "shot.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=navy:size=160x90:rate=24:duration=1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-y",
                str(cls.path),
            ],
            check=True,
            timeout=30,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp_media.cleanup()

    def setUp(self):
        super().setUp()
        media = import_saved_file(
            self.store, LOCAL_ACTOR, self.pid, "shot.mp4", self.path, "本地合成回归样片"
        )
        self.v = media["version"]
        self.asset = media["asset"]
        self.base = "/projects/" + self.pid + "/timeline-exchange/"
        self.clip = {
            "id": "a",
            "asset_id": self.asset["id"],
            "asset_version_id": self.v["id"],
            "track": 0,
            "source_in": 0.125,
            "source_out": 0.875,
            "timeline_start": 0.5,
            "volume": 0.7,
            "fit": "contain",
        }
        self.t = self.create(
            "Timeline",
            {
                "name": "剪辑交换",
                "format": {"width": 320, "height": 180, "fps": 24},
                "clips": [self.clip],
                "captions": [{"text": "雨后", "start": 0.6, "end": 1}],
                "audio_tracks": [],
            },
            refs=[{"id": self.v["id"], "version": 1}],
        )

    def exported(self):
        return self.post(
            self.base + "export", {"timeline_id": self.t["id"], "expected_revision": 1}
        )

    def test_official_serializer_cuts_gap_captions_pinned_refs_and_idempotent_import(self):
        result = self.exported()
        self.assertTrue(result["notes"])
        content = result["content"]
        seq = otio.adapters.read_from_string(content, adapter_name="otio_json")
        self.assertIsInstance(seq.tracks[0][0], otio.schema.Gap)
        self.assertEqual(seq.tracks[0][1].source_range.start_time.to_seconds(), 0.125)
        self.assertEqual(self.post(self.base + "inspect", {"content": content})["unresolved"], [])
        body = {"content": content, "request_id": "repeat"}
        imported = self.post(self.base + "import", body)
        again = self.post(self.base + "import", body)
        self.assertEqual(imported["id"], again["id"])
        self.assertNotEqual(imported["id"], self.t["id"])
        self.assertEqual(imported["data"]["captions"], self.t["data"]["captions"])
        clip = imported["data"]["clips"][0]
        for k in ("asset_version_id", "source_in", "source_out", "timeline_start", "volume"):
            self.assertEqual(clip[k], self.clip[k])
        self.assertIn(self.t["id"], [r["id"] for r in imported["refs"]])

    def test_external_address_requires_user_binding_and_unresolved_never_creates(self):
        seq = otio.core.deserialize_json_from_string(self.exported()["content"])
        seq.tracks[0][1].media_reference.target_url = "file:///offline/unknown.mov"
        content = otio.core.serialize_json_to_string(seq)
        report = self.post(self.base + "inspect", {"content": content})
        slot = report["unresolved"][0]["slot"]
        body = {"content": content, "request_id": "map"}
        self.post(self.base + "import", body, 409)
        self.assertEqual(len(self.store.list("local", "Timeline", self.pid)), 1)
        imported = self.post(self.base + "import", {**body, "bindings": {slot: self.v["id"]}})
        self.assertEqual(imported["data"]["clips"][0]["asset_id"], self.asset["id"])

    def test_cross_project_media_stays_offline_and_never_attaches_original(self):
        content = self.exported()["content"]
        other = self.create("Project", {"name": "另一项目"})
        base = "/projects/" + other["id"] + "/timeline-exchange/"
        self.assertEqual(len(self.post(base + "inspect", {"content": content})["unresolved"]), 1)
        self.post(
            base + "import",
            {"content": content, "request_id": "cross", "bindings": {"0.1": self.v["id"]}},
            409,
        )

    def test_unsupported_transitions_and_invalid_document_leave_draft_untouched(self):
        seq = otio.core.deserialize_json_from_string(self.exported()["content"])
        seq.tracks[0].append(otio.schema.Transition())
        self.post(self.base + "inspect", {"content": otio.core.serialize_json_to_string(seq)}, 422)
        for text in ("{}", '{"OTIO_SCHEMA":"Timeline.1","tracks":false}', "garbage"):
            self.post(self.base + "inspect", {"content": text}, 422)
        self.assertEqual(self.store.get(self.t["id"], "local")["version"], 1)

    def test_legacy_unversioned_asset_and_fractional_fps_roundtrip(self):
        clip = dict(self.clip)
        del clip["asset_version_id"]
        data = {
            **self.t["data"],
            "clips": [clip],
            "format": {"width": 320, "height": 180, "fps": {"num": 24000, "den": 1001}},
        }
        data["captions"] = [
            {
                **data["captions"][0],
                "timing_precision": "cue",
                "source_filename": "夜景.srt",
                "source_format": "srt",
                "language": "zh-CN",
            }
        ]
        source = self.create("Timeline", data)
        content = self.post(
            self.base + "export", {"timeline_id": source["id"], "expected_revision": 1}
        )["content"]
        draft = self.post(self.base + "import", {"content": content, "request_id": "legacy"})
        self.assertEqual(draft["data"]["format"]["fps"], data["format"]["fps"])
        self.assertEqual(draft["data"]["captions"], data["captions"])
        self.assertEqual(draft["data"]["clips"][0]["asset_version_id"], self.v["id"])

    def test_foreign_metadata_disclosed_and_needs_explicit_consent(self):
        seq = otio.core.deserialize_json_from_string(self.exported()["content"])
        seq.metadata["foreign"] = {"x": 3}
        content = otio.core.serialize_json_to_string(seq)
        self.assertTrue(self.post(self.base + "inspect", {"content": content})["notes"])
        body = {"content": content, "request_id": "review"}
        self.post(self.base + "import", body, 409)
        self.assertTrue(
            self.post(self.base + "import", {**body, "acknowledge_metadata": True})["data"][
                "exchange_receipt"
            ]["notes"]
        )

    def test_export_refuses_silent_retime_loss_and_overlaps(self):
        for edits in ([{**self.clip, "speed": 2}], [self.clip, {**self.clip, "id": "b"}]):
            t = self.create("Timeline", {**self.t["data"], "clips": edits})
            self.post(self.base + "export", {"timeline_id": t["id"], "expected_revision": 1}, 422)

    def with_transition(self, kind="dissolve"):
        first = dict(self.clip, source_in=0.125, source_out=0.875, timeline_start=0.5)
        second = dict(
            self.clip,
            id="b",
            source_in=0.05,
            source_out=0.80,
            timeline_start=1.0,
            transition_in={"from_id": "a", "kind": kind, "duration": 0.25},
        )
        timeline = self.create("Timeline", {**self.t["data"], "clips": [second, first]})
        exported = self.post(
            self.base + "export", {"timeline_id": timeline["id"], "expected_revision": 1}
        )
        return exported, otio.core.deserialize_json_from_string(exported["content"])

    def test_all_five_renderable_transitions_restore_full_ranges_and_source_link(self):
        for kind in ("dissolve", "wipe_left", "wipe_right", "dip_black", "dip_white"):
            with self.subTest(kind=kind):
                result, sequence = self.with_transition(kind)
                track = sequence.tracks[0]
                self.assertEqual(
                    [type(x) for x in track],
                    [otio.schema.Gap, otio.schema.Clip, otio.schema.Transition, otio.schema.Clip],
                )
                trans = track[2]
                self.assertEqual(
                    trans.transition_type,
                    (
                        otio.schema.TransitionTypes.SMPTE_Dissolve
                        if kind == "dissolve"
                        else otio.schema.TransitionTypes.Custom
                    ),
                )
                self.assertAlmostEqual(trans.in_offset.to_seconds(), 0.125)
                self.assertAlmostEqual(trans.out_offset.to_seconds(), 0.125)
                self.assertAlmostEqual(
                    track[1].source_range.end_time_exclusive().to_seconds(), 0.75
                )
                self.assertAlmostEqual(track[3].source_range.start_time.to_seconds(), 0.175)
                self.assertAlmostEqual(track.range_of_child(trans).start_time.to_seconds(), 1.0)
                self.assertAlmostEqual(track.duration().to_seconds(), 1.75)
                if kind != "dissolve":
                    self.assertEqual(trans.metadata["ai_ship"]["kind"], kind)
                    self.assertTrue(any("Custom_Transition" in note for note in result["notes"]))
                imported = self.post(
                    self.base + "import", {"content": result["content"], "request_id": kind}
                )["data"]["clips"]
                a, b = imported
                self.assertAlmostEqual(a["source_in"], 0.125)
                self.assertAlmostEqual(a["source_out"], 0.875)
                self.assertAlmostEqual(b["source_in"], 0.05)
                self.assertAlmostEqual(b["source_out"], 0.80)
                self.assertAlmostEqual(a["timeline_start"], 0.50)
                self.assertAlmostEqual(b["timeline_start"], 1)
                self.assertEqual(
                    b["transition_in"], {"kind": kind, "duration": 0.25, "from_id": a["id"]}
                )
                self.assertNotEqual(a["id"], "a")

    def test_asymmetric_external_dissolve_preserves_nominal_cut_on_reexport(self):
        _, seq = self.with_transition()
        track = seq.tracks[0]
        # Move only the nominal cut. The shared .25-second crossfade and its
        # frames remain identical when the external edit is re-imported.
        rt = otio.opentime.RationalTime
        track[2].in_offset = rt(2, 24)
        track[2].out_offset = rt(4, 24)
        content = otio.core.serialize_json_to_string(seq)
        draft = self.post(self.base + "import", {"content": content, "request_id": "asymmetric"})
        again = self.post(
            self.base + "export", {"timeline_id": draft["id"], "expected_revision": 1}
        )
        tr = otio.core.deserialize_json_from_string(again["content"]).tracks[0]
        for idx in (1, 3):
            self.assertAlmostEqual(
                tr[idx].source_range.start_time.to_seconds(),
                track[idx].source_range.start_time.to_seconds(),
            )
            self.assertAlmostEqual(
                tr[idx].source_range.duration.to_seconds(),
                track[idx].source_range.duration.to_seconds(),
            )
        self.assertAlmostEqual(tr[2].in_offset.value, 2)
        self.assertAlmostEqual(tr[2].out_offset.value, 4)

    def test_unknown_custom_disabled_or_insufficient_transition_handles_are_atomic(self):
        result, seq = self.with_transition()
        baseline = len(self.store.list("local", "Timeline", self.pid))

        def reject(mutate):
            sequence = otio.core.deserialize_json_from_string(result["content"])
            mutate(sequence.tracks[0])
            body = {
                "content": otio.core.serialize_json_to_string(sequence),
                "request_id": "rejected",
            }
            self.post(self.base + "inspect", {"content": body["content"]}, 422)
            self.post(self.base + "import", body, 422)
            self.assertEqual(len(self.store.list("local", "Timeline", self.pid)), baseline)

        reject(lambda tr: setattr(tr[2], "transition_type", "unregistered-effect"))
        reject(lambda tr: setattr(tr[2], "transition_type", otio.schema.TransitionTypes.Custom))
        native = json.loads(result["content"])
        native["tracks"]["children"][0]["children"][2]["enabled"] = False
        self.post(self.base + "inspect", {"content": json.dumps(native)}, 422)
        reject(lambda tr: setattr(tr[2], "in_offset", otio.opentime.RationalTime(18, 24)))
        reject(lambda tr: setattr(tr[2], "out_offset", otio.opentime.RationalTime(18, 24)))
        reject(lambda tr: setattr(tr, "kind", "Audio"))
        reject(
            lambda tr: tr.insert(
                2,
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(1, 24)
                    )
                ),
            )
        )

    def test_hand_edited_otio_is_a_dag_input(self):
        node = {
            "id": "handcut",
            "feature_id": "E01",
            "route_id": "E01-R3",
            "parameters": {},
            "operation_args": {"content": self.exported()["content"]},
        }
        flow = self.create(
            "Workflow",
            {
                "name": "参考剪辑",
                "DAG": {
                    "nodes": [node],
                    "edges": [],
                    "max_steps": 1,
                    "max_attempts": 1,
                    "budget": 0,
                },
            },
        )
        result = self.post(
            "/execution/start",
            {
                "workflow_id": flow["id"],
                "expected_revision": 1,
                "idempotency_key": "reference-cut-import",
            },
        )
        self.assertEqual(result["status"], "completed", result["run"]["data"])
        imported = self.store.get(result["outputs"][0]["id"], "local")
        self.assertEqual(imported["data"]["clips"][0]["source_in"], self.clip["source_in"])
        self.assertEqual(imported["data"]["captions"], self.t["data"]["captions"])
