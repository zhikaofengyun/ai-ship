import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from app.providers import provider_configuration
from app.errors import DomainError
from tests.test_production import ProductionBase


class AutoEditTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.assets = []
        self.base = "/api/v1/production/auto-edits"
        for word, color in (("FRESH LIME", "white"), ("SAMPLE", "yellow")):
            path = Path(self.temp.name) / (word + ".mp4")
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=" + color + ":s=640x240:r=25:d=1.6",
                    "-vf",
                    "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text="
                    + word
                    + ":fontcolor=black:fontsize=76:x=(w-tw)/2:y=(h-th)/2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    "1",
                    str(path),
                ],
                check=True,
            )
            self.assets.append(
                import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["asset"]
            )
        self.body = {
            "project_id": self.pid,
            "asset_ids": [a["id"] for a in reversed(self.assets)],
            "goal": "FRESH LIME",
            "mode": "local_vision",
            "target_seconds": 2,
            "width": 320,
            "height": 180,
            "fps": 25,
            "idempotency_key": uuid4().hex,
        }

    def post(self, suffix, body, status=200):
        r = self.client.post(self.base + suffix, json=body)
        self.assertEqual(r.status_code, status, r.text)
        return r.json().get("data") or r.json()["error"]

    def test_real_analysis_approval_render_decode_replay_and_listing(self):
        d = self.post("", self.body)
        self.assertEqual(d["session"]["data"]["stage"], "review", d["session"]["data"].get("issue"))
        rid = d["session"]["id"]
        self.assertIsNone(d["timeline"])
        self.assertEqual(d["plan"]["data"]["duration_seconds"], 2)
        evidence = d["session"]["data"]["evidence"]
        self.assertEqual(len(evidence[0]["samples"]), 3)
        self.assertNotEqual(
            evidence[0]["samples"][0]["cluster"], evidence[1]["samples"][0]["cluster"]
        )
        if (
            shutil.which("tesseract")
            and Path("/usr/share/tesseract-ocr/5/tessdata/eng.traineddata").exists()
        ):
            self.assertIn("FRESH", [w["text"] for w in evidence[1]["ocr"]["words"]])
            self.assertEqual(d["plan"]["data"]["shots"][0]["asset_id"], self.assets[0]["id"])
        self.assertEqual(self.post("", self.body)["plan"]["id"], d["plan"]["id"])
        self.post("", {**self.body, "goal": "different"}, 409)
        self.assertEqual(
            self.client.get(self.base, params={"project_id": self.pid}).json()["data"]["sessions"][
                0
            ]["id"],
            rid,
        )
        req = {"expected_revision": d["session"]["version"]}
        self.post("/" + rid + "/approve", {"expected_revision": 1}, 409)
        approved = self.post("/" + rid + "/approve", req)
        self.assertEqual(
            self.post("/" + rid + "/approve", req)["timeline"]["id"], approved["timeline"]["id"]
        )
        self.assertFalse(approved["timeline"]["data"]["locked"])
        render = {
            "expected_revision": approved["session"]["version"],
            "timeline_revision": approved["timeline"]["version"],
        }
        self.post("/" + rid + "/render", {**render, "timeline_revision": 23}, 409)
        result = self.post("/" + rid + "/render", render)
        self.assertEqual(
            result["session"]["data"]["stage"], "rendered", result["session"]["data"].get("issue")
        )
        artifact = result["artifact"]
        self.assertFalse(artifact["data"]["publish_ready"])
        movie = self.client.get("/api/v1/media/artifacts/" + artifact["id"] + "/file?download=true")
        self.assertEqual(movie.status_code, 200)
        check = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "null", "-"],
            input=movie.content,
            capture_output=True,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(self.post("/" + rid + "/render", render)["artifact"]["id"], artifact["id"])
        self.assertEqual(len(self.store.list("local", kind="Artifact")), 1)
        self.assertEqual(
            self.client.get(self.base + "/" + rid).json()["data"]["qc"]["id"], result["qc"]["id"]
        )

    def test_source_revision_after_proposal_blocks_approval_and_other_project_is_rejected(self):
        d = self.post("", self.body)
        version = self.assets[0]
        self.store.update(
            version["id"],
            {**version["data"], "name": "corrected"},
            expected_revision=version["version"],
            actor_id="test",
            tenant_id="local",
        )
        e = self.post(
            "/" + d["session"]["id"] + "/approve",
            {"expected_revision": d["session"]["version"]},
            409,
        )
        self.assertEqual(e["code"], "REVISION_CONFLICT")
        self.assertEqual(self.store.list("local", kind="Timeline"), [])
        foreign = self.create("Project", {"name": "another project"})
        blocked = self.post("", {**self.body, "project_id": foreign["id"]})
        self.assertIsNone(blocked["plan"])
        self.assertEqual(blocked["session"]["data"]["issue"]["code"], "PROJECT_SCOPE_MISMATCH")

    def test_invalid_unknown_shortfall_and_unconfigured_model(self):
        self.post("", {**self.body, "asset_ids": [self.assets[0]["id"]] * 2}, 422)
        self.post("", {**self.body, "width": 127}, 422)
        d = self.post("", {**self.body, "target_seconds": 10})
        self.assertTrue(d["plan"]["data"]["missing"])
        self.assertAlmostEqual(d["plan"]["data"]["duration_seconds"], 3.2, places=2)
        profile = self.create(
            "ModelProfile", {"name": "Missing", "provider": "missing", "model_id": "unknown"}
        )
        blocked = self.post(
            "",
            {
                **self.body,
                "mode": "model",
                "model_profile_id": profile["id"],
                "idempotency_key": uuid4().hex,
            },
        )
        self.assertEqual(blocked["session"]["data"]["issue"]["code"], "PROVIDER_NOT_CONFIGURED")
        self.assertEqual(self.store.list("local", kind="ToolCall"), [])

    def endpoint(self, answers):
        # A protocol fixture on a real loopback socket. It does not perform language-model inference.
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, body, self.headers.get("Authorization")))
                value = answers[len(seen) - 1]
                reply = {
                    "id": "wire-" + str(len(seen)),
                    "model": body["model"],
                    "choices": [
                        {"message": {"content": json.dumps(value)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 21, "completion_tokens": 16},
                }
                output = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(output))
                self.end_headers()
                self.wfile.write(output)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        cfg = {
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "transport": "loopback_chat",
            "currency": "USD",
            "models": ["director", "critic", "judge"],
            "json_output": True,
            "input_price_per_million": 0,
            "output_price_per_million": 0,
        }
        os.environ["AI_SHIP_PROVIDERS"] = json.dumps({"local": cfg})
        profiles = [
            self.create(
                "ModelProfile", {"provider": "local", "model_id": name, "structured_output": True}
            )
            for name in cfg["models"]
        ]
        b = {
            **self.body,
            "mode": "team",
            "model_profile_id": profiles[0]["id"],
            "reviewer_profile_id": profiles[1]["id"],
            "judge_profile_id": profiles[2]["id"],
        }
        return seen, b

    def test_real_http_contract_three_roles_cost_replay_and_independent_judge(self):
        proposal = {
            "title": "供应商提案",
            "approach": "选择已授权片段",
            "missing": [],
            "shots": [
                {
                    "asset_id": self.assets[0]["id"],
                    "source_in": 0.1,
                    "source_out": 1.1,
                    "reason": "特写候选",
                }
            ],
        }
        seen, b = self.endpoint(
            [
                proposal,
                {
                    "evidence_id": "D1",
                    "observed_value": "1.6",
                    "decision": "revise",
                    "reason": "原片 1.6 秒，需核对。",
                },
                {
                    "evidence_id": "D2",
                    "observed_value": "1.6",
                    "decision": "accept",
                    "reason": "已核原片时长 1.6 秒，保留短片提醒。",
                },
            ]
        )
        same = self.post("", {**b, "judge_profile_id": b["model_profile_id"]})
        self.assertEqual(same["session"]["data"]["issue"]["code"], "INDEPENDENT_JUDGE_REQUIRED")
        self.assertFalse(seen)
        b["idempotency_key"] = uuid4().hex
        d = self.post("", b)
        self.assertEqual(d["session"]["data"]["stage"], "review", d["session"]["data"].get("issue"))
        self.assertEqual([x[1]["model"] for x in seen], ["director", "critic", "judge"])
        self.assertTrue(all(x[0] == "/v1/chat/completions" and x[2] is None for x in seen))
        self.assertEqual(len(d["session"]["data"]["call_refs"]), 3)
        self.assertEqual(len(d["plan"]["data"]["reviews"]), 2)
        self.post("", b)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(self.store.list("local", kind="Cost")), 3)

    def test_evidence_mismatch_blocks_even_when_real_protocol_roles_vote_accept(self):
        proposal = {
            "title": "混剪",
            "approach": "选择授权片段",
            "shots": [
                {
                    "asset_id": self.assets[0]["id"],
                    "source_in": 0.1,
                    "source_out": 1.1,
                    "reason": "选择真实原片",
                }
            ],
            "missing": [],
        }
        seen, b = self.endpoint(
            [
                proposal,
                {
                    "evidence_id": "D1",
                    "observed_value": "1.6",
                    "decision": "accept",
                    "reason": "已核对 1.6 秒原片。",
                },
                {
                    "evidence_id": "D2",
                    "observed_value": "99",
                    "decision": "accept",
                    "reason": "两段都是 99 秒。",
                },
            ]
        )
        d = self.post("", b)
        self.assertEqual(len(seen), 3)
        self.assertEqual(d["session"]["data"]["stage"], "blocked")
        self.assertEqual(d["session"]["data"]["issue"]["code"], "TEAM_EVIDENCE_REQUIRED")
        self.assertEqual(d["plan"]["data"]["reviews"][1]["decision"], "accept")
        self.assertIsNone(d["plan"]["data"]["reviews"][1]["verified_observation"])
        self.assertIsNone(d["timeline"])
        self.post(
            "/" + d["session"]["id"] + "/approve",
            {"expected_revision": d["session"]["version"]},
            409,
        )
        changed = self.post(
            "/" + d["session"]["id"] + "/replace-shot",
            {
                "expected_revision": d["session"]["version"],
                "shot_index": 0,
                "asset_id": self.assets[1]["id"],
                "source_in": 0,
                "source_out": 1.1,
                "reason": "人工接管，换正选镜头。",
            },
        )
        self.assertEqual(changed["session"]["data"]["stage"], "review")
        self.assertEqual(
            changed["plan"]["data"]["base_review_issue"]["code"], "TEAM_EVIDENCE_REQUIRED"
        )

    def test_out_of_bounds_and_invented_model_asset_never_become_a_timeline(self):
        bad = {
            "title": "无效",
            "approach": "镜头",
            "missing": [],
            "shots": [{"asset_id": "outside", "source_in": 0, "source_out": 1, "reason": "test"}],
        }
        seen, b = self.endpoint(
            [
                bad,
                {
                    **bad,
                    "shots": [
                        {**bad["shots"][0], "asset_id": self.assets[0]["id"], "source_out": 15}
                    ],
                },
            ]
        )
        b = {k: v for k, v in b.items() if k not in {"reviewer_profile_id", "judge_profile_id"}}
        b["mode"] = "model"
        for i, code in enumerate(("UNKNOWN_SOURCE", "SHOT_OUT_OF_RANGE")):
            result = self.post("", {**b, "idempotency_key": uuid4().hex})
            self.assertEqual(result["session"]["data"]["issue"]["code"], code)
            self.assertIsNone(result["timeline"])
            self.assertEqual(len(seen), i + 1)
        self.assertEqual(self.store.list("local", kind="Plan"), [])

    def test_overlapping_requests_reserve_once_and_cannot_forge_ocr_or_double_apply(self):
        from app.visual_candidates import inspect

        entered, release = threading.Event(), threading.Event()

        def delayed(*a):
            entered.set()
            self.assertTrue(release.wait(10))
            return inspect(*a)

        with (
            patch("app.auto_edits.inspect", side_effect=delayed),
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool,
        ):
            pending = pool.submit(self.post, "", self.body)
            self.assertTrue(entered.wait(10))
            try:
                self.assertEqual(self.post("", self.body)["session"]["data"]["stage"], "planning")
            finally:
                release.set()
            done = pending.result(timeout=20)
        self.assertEqual(done["session"]["data"]["stage"], "review")
        self.assertEqual(len(self.store.list("local", kind="Plan")), 1)
        self.post("", {**self.body, "evidence": {"injected": "no"}}, 422)
        self.assertEqual(self.store.list("local", kind="Timeline"), [])

    def test_rights_withdrawn_during_provider_inference_cannot_become_a_proposal(self):
        from app import providers

        raw = {
            "title": "Proposal",
            "approach": "Cut",
            "shots": [
                {
                    "asset_id": self.assets[0]["id"],
                    "source_in": 0,
                    "source_out": 1,
                    "reason": "user-selected source",
                }
            ],
            "missing": [],
        }
        seen, b = self.endpoint([raw])
        b = {k: v for k, v in b.items() if k not in {"reviewer_profile_id", "judge_profile_id"}}
        b["mode"] = "model"
        real_chat = providers.chat

        def revoke_after(*args, **kwargs):
            reply = real_chat(*args, **kwargs)
            asset = self.assets[0]
            self.store.update(
                asset["id"],
                {**asset["data"], "rights_status": "revoked"},
                tenant_id="local",
                actor_id="test",
                expected_revision=asset["version"],
            )
            return reply

        with patch("app.auto_edits.providers.chat", side_effect=revoke_after):
            result = self.post("", b)
        self.assertEqual(len(seen), 1)
        self.assertEqual(result["session"]["data"]["stage"], "blocked")
        self.assertEqual("MEDIA_REVOKED", result["session"]["data"]["issue"]["code"])
        self.assertIsNone(result["plan"])
        self.assertEqual(len(self.store.list("local", kind="ToolCall")), 1)
        self.assertEqual(self.store.list("local", kind="Timeline"), [])

    def test_single_shot_fork_retains_unchanged_intervals_and_replays(self):
        original = self.post("", self.body)
        run = original["session"]
        first, second = original["plan"]["data"]["shots"]
        body = {
            "expected_revision": run["version"],
            "asset_id": first["asset_id"],
            "shot_index": 0,
            "source_in": 0.2,
            "source_out": 0.8,
            "reason": "保留第二段，收紧第一段",
        }
        url = "/" + run["id"] + "/replace-shot"
        self.assertEqual(self.post(url, {**body, "shot_index": 5}, 422)["code"], "NO_SUCH_SHOT")
        fork = self.post(url, body)
        self.assertEqual(fork["session"]["data"]["stage"], "review")
        self.assertNotEqual(fork["session"]["id"], run["id"])
        self.assertIsNone(fork["timeline"])
        self.assertEqual(fork["plan"]["data"]["shots"][1], {**second, "timeline_start": 0.6})
        self.assertEqual(self.post(url, body)["plan"]["id"], fork["plan"]["id"])
        parent = self.client.get(self.base + "/" + run["id"]).json()["data"]
        self.assertEqual(parent["plan"], original["plan"])

    def test_bounded_menu_freezes_actual_range_options_and_json_schema(self):
        seen, b = self.endpoint([{"choice": "C", "reason": "Reverse the source sequence."}])
        cfg = json.loads(os.environ["AI_SHIP_PROVIDERS"])
        cfg["local"].update(candidate_menu=True, structured_schema=True)
        os.environ["AI_SHIP_PROVIDERS"] = json.dumps(cfg)
        b = {k: v for k, v in b.items() if k not in {"reviewer_profile_id", "judge_profile_id"}}
        b["mode"] = "model"
        result = self.post("", b)
        self.assertEqual(
            result["session"]["data"]["stage"], "review", result["session"]["data"]["issue"]
        )
        self.assertEqual(result["plan"]["data"]["approach"], "Reverse the source sequence.")
        request = seen[0][1]
        self.assertEqual(request["response_format"]["type"], "json_schema")
        menu = json.loads(request["messages"][-1]["content"])["menu"]
        chosen = next(item["cuts"] for item in menu if item["choice"] == "C")
        self.assertEqual(
            [
                (x["asset_id"], x["source_in"], x["source_out"])
                for x in result["plan"]["data"]["shots"]
            ],
            [(x["asset_id"], x["in"], x["out"]) for x in chosen],
        )

    def test_opening_text_conflict_freezes_original_and_rechecks_pinned_human_replacement(self):
        raw = {
            "title": "导演选案",
            "approach": "FRESH LIME appears first",
            "shots": [
                {
                    "asset_id": self.assets[1]["id"],
                    "source_in": 0.0,
                    "source_out": 1.0,
                    "reason": "FRESH LIME appears first",
                }
            ],
        }
        seen, b = self.endpoint([raw])
        b = {k: v for k, v in b.items() if k not in {"reviewer_profile_id", "judge_profile_id"}}
        d = self.post("", {**b, "mode": "model", "opening_text": "FRESH LIME"})
        run = d["session"]
        check = d["plan"]["data"]["opening_check"]
        self.assertEqual(run["data"]["issue"]["code"], "OPENING_TEXT_CONFLICT")
        self.assertEqual(check["status"], "conflict")
        self.assertEqual([w["text"] for w in check["observation"]["words"]], ["SAMPLE"])
        self.assertEqual(check["observation"]["at"], 0.0)
        self.assertEqual(len(check["observation"]["frame_sha256"]), 64)
        self.assertEqual(d["plan"]["data"]["approach"], raw["approach"])
        self.post("/" + run["id"] + "/approve", {"expected_revision": run["version"]}, 409)
        candidate = check["alternatives"][0]
        request = {
            "expected_revision": run["version"],
            "shot_index": 0,
            "reason": "采用实帧文字匹配的候选",
            **{k: candidate[k] for k in ("asset_id", "source_in", "source_out")},
            "expected_asset_version": candidate["asset_version"],
        }
        url = "/" + run["id"] + "/replace-shot"
        self.post(url, {**request, "expected_asset_version": 99}, 409)
        wrong = self.post(url, {**request, "asset_id": self.assets[1]["id"]})
        self.assertEqual(wrong["session"]["data"]["stage"], "blocked")
        fork = self.post(url, request)
        self.assertEqual(fork["plan"]["data"]["opening_check"]["status"], "matched")
        self.assertEqual(fork["session"]["data"]["base_session_ref"]["id"], run["id"])
        self.assertEqual(self.post(url, request)["plan"]["id"], fork["plan"]["id"])
        self.assertEqual(
            self.client.get(self.base + "/" + run["id"]).json()["data"]["session"], run
        )
        self.assertEqual(len(seen), 1)
        child = fork["session"]
        path = "/" + child["id"]
        draft = self.post(path + "/approve", {"expected_revision": child["version"]})
        movie = self.post(
            path + "/render",
            {
                "expected_revision": draft["session"]["version"],
                "timeline_revision": draft["timeline"]["version"],
            },
        )
        self.assertEqual(
            movie["session"]["data"]["stage"], "rendered", movie["session"]["data"].get("issue")
        )
        qc = movie["artifact"]["data"]["qc"]["opening_check"]
        self.assertEqual(qc["timeline_version"], movie["timeline"]["version"])
        self.assertEqual(qc["encoded_first_frame"]["at"], 0)
        self.assertTrue(
            qc["preflight"]["frame_sha256"] and qc["encoded_first_frame"]["frame_sha256"]
        )

    def test_edited_timeline_renders_current_opening_including_origin_and_actual_composition(self):
        plan = self.post("", {**self.body, "goal": "剪辑\n首镜文字：FRESH LIME"})
        self.assertEqual(plan["plan"]["data"]["opening_check"]["status"], "matched")
        run = plan["session"]
        draft = self.post("/" + run["id"] + "/approve", {"expected_revision": run["version"]})
        timeline = draft["timeline"]
        original = timeline["data"]
        first, second = original["clips"]
        base = {**original, "locked": True}
        base.pop(
            "opening_text"
        )  # Latest editor data cannot clear the accepted first revision's requirement.
        for clips, captions, expected in (
            ([{**first, "asset_id": second["asset_id"]}, second], [], "原片首帧"),
            ([first, second, {**first, "id": "over", "track": 1}], [], "叠层"),
            ([first, second], [{"text": "FRESH LIME", "start": 0, "end": 0.3}], "字幕"),
            ([first, second], [{"text": "FR3SH L1ME", "start": 0, "end": 0.3}], "字幕"),
            ([{**first, "x": 1000}, second], [], "输出画面"),
        ):
            timeline = self.store.update(
                timeline["id"],
                {**base, "clips": clips, "captions": captions},
                tenant_id="local",
                expected_revision=timeline["version"],
                actor_id="editor",
            )
            resp = self.client.post(
                "/api/v1/media/render",
                json={
                    "project_id": self.pid,
                    "timeline_id": timeline["id"],
                    "expected_revision": timeline["version"],
                },
            )
            self.assertEqual(resp.status_code, 409, resp.text)
            e = resp.json()["error"]
            self.assertEqual(e["code"], "OPENING_TEXT_UNVERIFIED")
            self.assertIn(expected, e["message"])
            self.assertEqual(self.store.list("local", kind="Artifact"), [])

        independent = self.store.update(
            timeline["id"],
            {
                **base,
                "clips": [first, second],
                "captions": [{"text": "Bonus edition", "font_size": 10, "start": 0, "end": 0.3}],
            },
            tenant_id="local",
            expected_revision=timeline["version"],
            actor_id="editor",
        )
        result = self.client.post(
            "/api/v1/media/render",
            json={
                "project_id": self.pid,
                "timeline_id": timeline["id"],
                "expected_revision": independent["version"],
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        qc = result.json()["data"]["qc"]["opening_check"]
        self.assertEqual(qc["timeline_version"], independent["version"])
        self.assertEqual(qc["encoded_first_frame"]["at"], 0)
        self.assertEqual(qc["status"], "matched")

    def test_literal_opening_observation_requires_order_confidence_and_an_explicit_constraint(self):
        from app.opening_check import matched_reading, opening_requirement

        self.assertIsNone(opening_requirement("优先考虑 FRESH LIME，开头不限定。", None))
        self.assertEqual(
            opening_requirement("Edit freely.\nOpening text: FRESH LIME", None), "FRESH LIME"
        )
        for bad in ("Opening text:", "Opening text: X\n首镜文字：Y", "Opening text: " + "a" * 121):
            with self.assertRaises(ValueError):
                opening_requirement(bad, None)
        with self.assertRaises(ValueError):
            opening_requirement("首镜文字：A", "B")

        def reading(words):
            return {
                "status": "read",
                "words": [{"text": t, "line": [1, 1, l], "confidence": c} for t, l, c in words],
            }

        for pairs in (
            [("REFRESH", 1, 0.95), ("LIME", 1, 0.99)],
            [("LIME", 1, 0.92), ("FRESH", 1, 0.92)],
            [("FRESH", 1, 0.99), ("LIME", 2, 0.96)],
            [("FRESH", 1, 0.99), ("SECRET", 1, 0.12), ("LIME", 1, 0.98)],
            [("FRESH", 1, 0.99), ("LIME", 1, 0.60)],
        ):
            self.assertFalse(matched_reading(reading(pairs), "FRESH LIME"))
        self.assertTrue(
            matched_reading(reading([("FRESH", 1, 0.99), ("LIME", 1, 0.96)]), "ＦＲＥＳＨ lime")
        )
        self.assertTrue(matched_reading(reading([("青", 1, 0.99), ("柠", 1, 0.97)]), "青柠"))
        unknown = self.post("", {**self.body, "opening_text": "A word missing from both cards"})
        self.assertEqual(unknown["session"]["data"]["issue"]["code"], "OPENING_TEXT_UNVERIFIED")
        self.assertEqual(unknown["plan"]["data"]["opening_check"]["alternatives"], [])
        free = self.post("", {**self.body, "idempotency_key": uuid4().hex})
        self.assertNotIn("opening_check", free["plan"]["data"])
        self.assertEqual(free["session"]["data"]["stage"], "review")

    def test_opening_checks_exact_trim_and_returns_later_verified_candidate_in_same_asset(self):
        from app.opening_check import frame_observation

        path = Path(self.temp.name) / "FRESH LIME on opening.mp4"
        src = [Path(self.temp.name) / name for name in ("SAMPLE.mp4", "FRESH LIME.mp4")]
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(src[0]),
                "-i",
                str(src[1]),
                "-filter_complex",
                "[0:v][1:v]concat=n=2:v=1:a=0[out]",
                "-map",
                "[out]",
                "-threads",
                "1",
                str(path),
            ],
            check=True,
        )
        source = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["asset"]
        answer = {
            "title": "选案",
            "approach": "以青柠字样开场",
            "shots": [
                {
                    "asset_id": source["id"],
                    "source_in": 0.021,
                    "source_out": 1.021,
                    "reason": "用户要求",
                }
            ],
        }
        _, b = self.endpoint([answer])
        b = {k: v for k, v in b.items() if k not in {"reviewer_profile_id", "judge_profile_id"}}
        out = self.post(
            "", {**b, "mode": "model", "asset_ids": [source["id"]], "opening_text": "FRESH LIME"}
        )
        check = out["plan"]["data"]["opening_check"]
        self.assertEqual(check["status"], "conflict")
        self.assertAlmostEqual(check["observation"]["at"], 0.04)
        self.assertEqual([w["text"] for w in check["observation"]["words"]], ["SAMPLE"])
        later = check["alternatives"][0]
        self.assertEqual(later["asset_id"], source["id"])
        self.assertGreaterEqual(later["at"], 1.6)
        self.assertEqual(frame_observation(path, 0.021, 0.039)["status"], "unavailable")

    def test_loopback_transport_never_admits_bare_remote_or_untrusted_credentials(self):
        env = {"transport": "loopback_chat", "models": ["x"], "base_url": "http://0.0.0.0:11434/v1"}
        p = self.create("ModelProfile", {"provider": "local", "model_id": "x"})
        for url in [
            "http://0.0.0.0/v1",
            "http://localhost.example/v1",
            "http://user:secret@127.0.0.1/v1",
            "http://127.0.0.1/v1?k=v",
        ]:
            os.environ["AI_SHIP_PROVIDERS"] = json.dumps({"local": {**env, "base_url": url}})
            with self.assertRaises(DomainError):
                provider_configuration(p)
