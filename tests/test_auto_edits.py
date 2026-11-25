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

    def test_bitemporal_project_preferences_feed_model_and_enforce_withdrawal(self):
        def submit(key, mode, expected, value, **fields):
            b = {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "key": key,
                "mode": mode,
                "action": "set" if value else "revoke",
                "expected_version": expected,
                "reason": "明确的品牌剪辑选择",
                "valid_from": "2025-01-01T00:00:00Z",
                **fields,
            }
            if value:
                b["value"] = value
            response = self.client.post("/api/v1/execution/edit-preferences", json=b)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["data"]

        def recall(mode="model", **times):
            res = self.client.get(
                "/api/v1/execution/edit-preferences",
                params={"project_id": self.pid, "mode": mode, **times},
            )
            self.assertEqual(res.status_code, 200, res.text)
            return res.json()["data"]

        first = submit("pace", "all", 0, "短镜头")
        second = submit("pace", "all", 1, "舒缓", valid_from="2026-01-01T00:00:00Z")
        self.assertEqual(
            recall(
                valid_at="2026-03-01T00:00:00Z", known_at=first["memory"]["data"]["observed_time"]
            )["preferences"][0]["value"],
            "短镜头",
        )
        self.assertEqual(
            recall(valid_at="2025-03-01T00:00:00Z")["preferences"][0]["value"], "短镜头"
        )
        self.assertEqual(recall(valid_at="2026-03-01T00:00:00Z")["preferences"][0]["value"], "舒缓")
        submit("pace", "team", 0, "先慢后快")
        self.assertEqual(recall("team")["preferences"][0]["mode"], "team")
        self.assertEqual(recall("model")["preferences"][0]["mode"], "all")
        proposal = {
            "title": "Export",
            "approach": "measured",
            "missing": [],
            "shots": [
                {
                    "asset_id": self.assets[0]["id"],
                    "source_in": 0,
                    "source_out": 1,
                    "reason": "retained",
                }
            ],
        }
        seen, team = self.endpoint([proposal, proposal])
        body = {
            k: v for k, v in team.items() if k not in {"judge_profile_id", "reviewer_profile_id"}
        }
        body.update(mode="model", use_project_preferences=True)
        rr = self.post("", body)
        self.assertEqual(
            rr["session"]["data"]["stage"], "review", rr["session"]["data"].get("issue")
        )
        self.post("", {**body, "idempotency_key": uuid4().hex})
        self.assertEqual(recall()["preferences"][0]["current_run_uses"], 2)
        payload = json.loads(seen[0][1]["messages"][-1]["content"])
        self.assertEqual(
            payload["explicit_edit_preferences"][0]["profile"]["id"], second["profile"]["id"]
        )
        submit("pace", "all", 2, None)
        current = recall()
        self.assertEqual(current["preferences"], [])
        self.assertIn({"key": "pace", "mode": "all", "latest_version": 3}, current["current_heads"])
        self.assertEqual(
            recall(known_at=second["memory"]["data"]["observed_time"])["preferences"][0]["value"],
            "舒缓",
        )
        refusal = self.post(
            "/" + rr["session"]["id"] + "/approve",
            {"expected_revision": rr["session"]["version"]},
            409,
        )
        self.assertEqual(refusal["code"], "PREFERENCE_CHANGED")
        for rid in (first["memory"]["id"], second["profile"]["id"]):
            self.assertEqual(
                self.client.patch(
                    "/api/v1/records/" + rid,
                    json={
                        "expected_revision": 1,
                        "data": {"name": "generic change"},
                        "reason": "test",
                    },
                ).status_code,
                403,
            )

    def test_repair_invalid_source_interval_then_review_without_duplicating_paid_calls(self):
        sid = self.assets[0]["id"]

        def plan(end):
            return {
                "title": "cut",
                "approach": "raw source",
                "missing": [],
                "shots": [
                    {"asset_id": sid, "source_in": 0, "source_out": end, "reason": "opening"}
                ],
            }

        yes = {
            "evidence_id": "D1",
            "observed_value": "1.6",
            "decision": "accept",
            "reason": "Interval falls inside 1.6 seconds",
        }
        seen, body = self.endpoint(
            [plan(99), plan(1), yes, {**yes, "reason": "1.6 seconds available"}]
        )
        body["max_review_rounds"] = 3
        state = self.post("", body)
        run = state["session"]["data"]
        self.assertEqual(run["stage"], "review", run["issue"])
        self.assertEqual([c["round"] for c in run["call_refs"]], [1, 2, 2, 2])
        first = run["review_rounds"][0]
        self.assertNotIn("plan_ref", first)
        constraint = json.loads(seen[1][1]["messages"][-1]["content"])["revision_feedback"][
            "constraint"
        ]
        self.assertEqual(
            constraint,
            {
                "asset_id": sid,
                "shot_index": 1,
                "source_in": 0,
                "source_out": 99,
                "source_duration": 1.6,
                "minimum_seconds": 0.04,
            },
        )
        self.assertEqual(first["invalid_output"]["validation_code"], "SHOT_OUT_OF_RANGE")
        self.assertEqual(self.post("", body)["plan"]["id"], state["plan"]["id"])
        self.assertEqual(len(seen), 4)

    def test_repeated_invalid_director_answer_stops_with_budget_remaining(self):
        invalid = {"title": "Empty choice", "shots": [], "missing": [], "approach": "invalid"}
        seen, body = self.endpoint([invalid, invalid])
        body["max_review_rounds"] = 4
        result = self.post("", body)
        run = result["session"]["data"]
        self.assertIsNone(result["plan"])
        self.assertEqual(run["stage"], "blocked")
        self.assertEqual(run["loop_stop"], "repeated_invalid_answer")
        self.assertEqual(
            run["review_rounds"][0]["invalid_output"]["answer_hash"],
            run["review_rounds"][1]["invalid_output"]["answer_hash"],
        )
        self.assertEqual(len(seen), 2)

    def test_on_screen_quote_cannot_be_backed_by_duration_or_another_sources_ocr(self):
        from app.auto_edits import Verdict, copied_review, evidence_problem

        facts = {
            "D1": {"kind": "duration_seconds", "observed_value": "1.6"},
            "W1": {"kind": "frame_ocr", "observed_value": "SAMPLE"},
        }

        def problem(proof, reason):
            v = Verdict(
                evidence_id=proof,
                observed_value=facts[proof]["observed_value"],
                decision="accept",
                reason=reason,
            )
            return evidence_problem(v, facts, [])

        self.assertIsNotNone(
            problem(
                "D1",
                "The observed value '1.6' indicates that the video contains a frame with the text 'FRESH LIME'.",
            )
        )
        self.assertIsNotNone(problem("W1", "SAMPLE.mp4 shows “FRESH LIME”."))
        self.assertIsNotNone(problem("D1", "原片长1.6秒，画面显示“限时促销”。"))
        self.assertIsNone(
            problem("W1", "Sampled source reads ‘SAMPLE’. Check the opening manually.")
        )
        self.assertIsNone(
            problem(
                "D1",
                "1.6 seconds of footage are available. The editor's brief requests 'FRESH LIME'.",
            )
        )
        prior = "The source has a measured duration of 1.6 seconds. The observed clip is usable but the editor should confirm that the opening matches the requested wording."
        self.assertTrue(copied_review(prior.replace("clip", "shot"), [prior]))
        self.assertFalse(copied_review("The 1.6 seconds are available.", [prior]))

    def test_role_receives_its_own_failed_citation_and_chinese_units_validate(self):
        plan = {
            "title": "原片",
            "approach": "逐镜选段",
            "missing": [],
            "shots": [
                {"asset_id": self.assets[0]["id"], "source_in": 0, "source_out": 1, "reason": "cut"}
            ],
        }
        wrong = {
            "evidence_id": "D1",
            "observed_value": "SAMPLE",
            "decision": "accept",
            "reason": "wrong",
        }
        corrected = {
            "evidence_id": "D1",
            "observed_value": "1.6",
            "decision": "accept",
            "reason": "原片时长1.6秒，片段可用。",
        }
        judge = {**corrected, "reason": "已测量1.6秒，本次选段在片内。"}
        seen, body = self.endpoint([plan, wrong, judge, plan, corrected, judge])
        result = self.post("", {**body, "max_review_rounds": 2})
        self.assertEqual(result["session"]["data"]["stage"], "review")
        repaired = json.loads(seen[4][1]["messages"][-1]["content"])
        self.assertEqual(repaired["citation_feedback"]["measured_fact"]["observed_value"], "1.6")
        self.assertEqual(repaired["citation_feedback"]["submitted_value"], "SAMPLE")
        self.assertNotIn("citation_feedback", json.loads(seen[5][1]["messages"][-1]["content"]))
        self.assertEqual(repaired["reviews"], [])

    def test_repeated_observed_objection_and_cut_stop_even_if_title_changes(self):
        plan = {
            "title": "First",
            "approach": "Measured",
            "missing": [],
            "shots": [
                {"asset_id": self.assets[0]["id"], "source_in": 0, "source_out": 1, "reason": "cut"}
            ],
        }
        objection = {
            "evidence_id": "D1",
            "observed_value": "1.6",
            "decision": "revise",
            "reason": "Review the 1.6 second source.",
        }
        judge = {**objection, "reason": "The 1.6 second source requires a different in point."}
        seen, body = self.endpoint(
            [plan, objection, judge, {**plan, "title": "Changed title"}, objection, judge]
        )
        body["max_review_rounds"] = 4
        result = self.post("", body)
        run = result["session"]["data"]
        self.assertEqual(
            (run["stage"], run["loop_stop"], len(seen)), ("blocked", "repeated_rejected_cut", 6)
        )
        self.assertEqual(len(self.store.list("local", kind="Plan", project_id=self.pid)), 2)
        self.assertIsNone(result["timeline"])

    def test_director_turn_budget_preserves_hard_fields_and_matches_provider_measure(self):
        from app.edit_context import edit_messages, fit_turn, turn_bound
        from app.providers import input_upper_bound, wire_messages as provider_messages

        from app.providers import context_capacity

        self.assertEqual(
            context_capacity({"context_limit": 99000}, {"context_limit": "4096"}), 4096
        )
        self.assertEqual(context_capacity({"context_limit": 99000}, {}), 99000)
        self.assertEqual(context_capacity({}, {}), 32768)
        for bad in (0, -1, True, 4.3, "invalid"):
            with self.assertRaises(DomainError):
                context_capacity({"context_limit": bad}, {"context_limit": 2048})
        brief = {
            "goal": "保留开场原文",
            "opening_text": "FRESH LIME",
            "target_seconds": 2,
            "creative_evidence": [
                {
                    "source": {"id": "frozen", "version": 1},
                    "data_excerpt": "假设",
                    "causal_claim": False,
                }
            ],
            "sources": [
                {
                    "asset_id": "asset_A",
                    "duration": 1.6,
                    "ocr": {
                        "status": "read",
                        "words": [
                            {"text": "FRESH", "confidence": 0.95, "box_px": [100, 100, 200, 200]}
                        ],
                    },
                    "sampled_frames": [{"at": n, "contrast": 23} for n in (0, 0.8, 1.5)],
                }
            ],
        }
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        initial = turn_bound(edit_messages("prompt", brief), schema)
        msgs, proof = fit_turn("prompt", brief, schema, initial + 16 - 1, 16)
        self.assertLess(proof["sent_input_upper_bound"], initial)
        self.assertEqual(
            proof["sent_input_upper_bound"],
            input_upper_bound(provider_messages(msgs, True), schema),
        )
        self.assertEqual(proof["omissions"][0]["kind"], "ocr_geometry")
        sent = json.loads(msgs[1]["content"])
        self.assertIn("box_px", brief["sources"][0]["ocr"]["words"][0])
        self.assertEqual(sent["creative_evidence"], brief["creative_evidence"])
        self.assertEqual(sent["opening_text"], brief["opening_text"])
        self.assertEqual(sent["sources"][0]["ocr"]["words"][0]["text"], "FRESH")
        self.assertEqual(fit_turn("prompt", brief, schema, initial + 15, 16), (msgs, proof))
        with self.assertRaises(DomainError) as refused:
            fit_turn("prompt", brief, schema, 100, 16)
        self.assertEqual(refused.exception.code, "CONTEXT_BUDGET")
        self.assertEqual(refused.exception.details["omissions"][-1]["sources"][0]["times"], [0.8])

    def test_durable_second_director_turn_consumes_failure_and_keeps_original(self):
        from app import auto_edits

        source = self.create("Source", {"name": "Frozen trend provenance"})
        trend = self.create(
            "Trend",
            {"title": "Product reveal", "observations": {"format": "closeup"}},
            [{"id": source["id"], "version": 1}],
        )
        sid = self.assets[0]["id"]

        def plan(offset):
            return {
                "title": "Cut",
                "approach": "verified cut",
                "missing": [],
                "shots": [
                    {
                        "asset_id": sid,
                        "source_in": offset,
                        "source_out": offset + 1,
                        "reason": "chosen in source bounds",
                    }
                ],
            }

        def review(decision, reason):
            return {
                "evidence_id": "D1",
                "observed_value": "1.6",
                "decision": decision,
                "reason": reason,
            }

        seen, body = self.endpoint(
            [
                plan(0),
                review("revise", "Review the 1.6 second source."),
                review("revise", "Check the final interval in this 1.6 second source."),
                plan(0.2),
                review("accept", "New interval stays within 1.6 seconds."),
                review("accept", "The 1.6 second source covers the requested replacement."),
            ]
        )
        body.update(max_review_rounds=2, feedback_evidence=[{"id": trend["id"], "version": 1}])
        build = auto_edits.build

        def interrupt(*args):
            if args[2]["data"].get("review_rounds"):
                raise RuntimeError("simulated process interruption between rounds")
            return build(*args)

        with (
            patch.object(auto_edits, "build", interrupt),
            self.assertRaisesRegex(RuntimeError, "interruption"),
        ):
            self.post("", body)
        self.assertEqual(len(seen), 3)
        result = self.post("", body)
        self.assertEqual(
            result["session"]["data"]["stage"], "review", result["session"]["data"].get("issue")
        )
        history = result["plan"]["data"]["review_rounds"]
        self.assertEqual([r["decision"] for r in history], ["revise", "review"])
        original = self.store.get(history[0]["plan_ref"]["id"], "local")
        self.assertEqual(original["data"]["shots"][0]["source_in"], 0)
        self.assertEqual(result["plan"]["data"]["shots"][0]["source_in"], 0.2)
        self.assertEqual(len(seen), 6)
        payload = json.loads(seen[3][1]["messages"][-1]["content"])
        self.assertEqual(payload["revision_feedback"]["prior_plan"]["id"], original["id"])
        self.assertEqual(payload["creative_evidence"][0]["content_hash"], trend["hash"])
        self.assertFalse(payload["creative_evidence"][0]["causal_claim"])
        all_calls = result["session"]["data"]["call_refs"]
        self.assertEqual([r["round"] for r in all_calls], [1, 1, 1, 2, 2, 2])
        self.assertTrue(all(r["prompt_hash"] and r["context_hash"] for r in all_calls))
        costs = self.store.list("local", kind="Cost", project_id=self.pid)
        self.assertAlmostEqual(
            sum(c["data"]["limits"]["max_cost"] for c in costs), body.get("max_cost", 0.1)
        )
        self.assertEqual(self.post("", body)["plan"]["hash"], result["plan"]["hash"])
        self.assertEqual(len(seen), 6)

    def test_retention_maps_actual_render_to_frozen_edit_feedback(self):
        from app.edit_feedback import feedback_context
        from app.selection import ChosenVersion
        from tests.test_selection import pin

        data = self.post("", self.body)
        rid = data["session"]["id"]
        draft = self.post(f"/{rid}/approve", {"expected_revision": data["session"]["version"]})
        rendered = self.post(
            f"/{rid}/render",
            {
                "expected_revision": draft["session"]["version"],
                "timeline_revision": draft["timeline"]["version"],
            },
        )
        artifact = rendered["artifact"]
        path = Path(self.temp.name) / "post-download.mp4"
        path.write_bytes(
            self.client.get("/api/v1/media/artifacts/" + artifact["id"] + "/file").content
        )
        downloaded = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)[
            "version"
        ]

        def action(route, payload, status=200):
            r = self.client.post("/api/v1/production/" + route, json=payload)
            self.assertEqual(r.status_code, status, r.text)
            return r.json().get("data") or r.json()["error"]

        pub = action(
            "publications",
            {
                "project_id": self.pid,
                "artifact_id": artifact["id"],
                "account_id": self.account["id"],
                "title": "fixture",
            },
        )
        post = action(
            "publications/" + pub["id"] + "/receipt",
            {
                "expected_revision": 1,
                "platform_post_id": "synthetic",
                "post_url": "https://example.test/synthetic",
                "observed_at": "2026-01-01T00:00:00Z",
                "evidence": "fixture ledger",
            },
        )["post"]
        inputs = {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "post": pin(post),
            "source": "synthetic frozen first-play analytics file",
            "denominator": 1000,
            "traffic": "organic",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-02T00:00:00Z",
            "observed_at": "2026-01-03T00:00:00Z",
            "points": [
                {"at_seconds": 0, "retained": 960},
                {"at_seconds": 1.1, "retained": 720},
                {"at_seconds": 2, "retained": 480},
            ],
        }
        self.assertEqual(
            action("post-reviews/retention-mapping", inputs, 409)["code"], "POST_MAPPING_REQUIRED"
        )
        mapped = action(
            "assets/post-mapping",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "post": pin(post),
                "observed_file": pin(downloaded),
                "published_at": "2026-01-01T00:00:00Z",
                "evidence": "synthetic round-trip recovered exact rendered bytes",
            },
        )
        inputs["post"] = pin(mapped["post"])
        insight = action("post-reviews/retention-mapping", inputs)["insight"]
        self.assertEqual(insight["data"]["intervals"][0]["change_percentage_points"], -24.0)
        self.assertTrue(insight["data"]["intervals"][0]["contributors"])
        self.assertFalse(insight["data"]["causal_claim"])
        self.assertEqual(
            action("post-reviews/retention-mapping", inputs)["insight"]["id"], insight["id"]
        )

        def placements(**options):
            filters = dict(
                project_id=self.pid, account_id=self.account["id"], start_seconds=0, end_seconds=2
            )
            result = self.client.get(
                "/api/v1/production/post-reviews/placement-performance",
                params={**filters, **options},
            )
            self.assertEqual(result.status_code, 200, result.text)
            return result.json()["data"]

        # A corrected observation supersedes the same post/window and preserves
        # the old evidence. Paid traffic and incomplete intervals stay separate.
        corrected = {
            **inputs,
            "request_id": uuid4().hex,
            "observed_at": "2026-01-04T00:00:00Z",
            "points": [*inputs["points"][:-1], {"at_seconds": 2, "retained": 384}],
        }
        fixed = action("post-reviews/retention-mapping", corrected)["insight"]
        positions = placements()
        self.assertEqual(positions["matching_posts"], 1)
        self.assertGreater(positions["total"], 0)
        for row in positions["items"]:
            self.assertAlmostEqual(row["mean_post_retention"], 0.4)
            self.assertEqual(row["posts"], 1)
            self.assertEqual(row["example_evidence"][0]["insight"], fixed["id"])
        self.assertEqual(placements(traffic="paid")["total"], 0)
        self.assertEqual(placements(cohort_hours=48)["matching_posts"], 0)
        self.assertEqual(placements(end_seconds=0.5)["missing_boundary_points"], 1)
        sample = positions["items"][0]
        cohort = dict(
            account_id=self.account["id"],
            start_seconds=0,
            end_seconds=2,
            traffic="organic",
            cohort_after_hours=0,
            cohort_hours=24,
        )
        body = dict(
            project_id=self.pid,
            request_id=uuid4().hex,
            cohort=cohort,
            source={"id": sample["source_id"], "version": sample["source_version"]},
            role=sample["role"],
            evidence_hash=sample["evidence_hash"],
            hypothesis="Possible framing effect",
            counterevidence="Co-occurring footage",
            next_edit="Compare a different opening",
            stop_rule="Stop if lower on held-out posts",
        )
        result = action("post-reviews/placement-hypotheses", body)["insight"]
        self.assertEqual(
            action("post-reviews/placement-hypotheses", body)["insight"]["id"], result["id"]
        )
        self.assertTrue(any(r["id"] == fixed["id"] for r in result["refs"]))
        excerpt = feedback_context(LOCAL_ACTOR, self.pid, [ChosenVersion(**pin(result))])[0]
        self.assertIn("Co-occurring footage", excerpt["data_excerpt"])
        self.assertFalse(excerpt["causal_claim"])
        stale = {**body, "request_id": uuid4().hex, "evidence_hash": "0" * 64}
        self.assertEqual(
            action("post-reviews/placement-hypotheses", stale, 409)["code"],
            "PLACEMENT_EVIDENCE_CHANGED",
        )
        new_curve = {**corrected, "request_id": uuid4().hex, "observed_at": "2026-01-05T00:00:00Z"}
        action("post-reviews/retention-mapping", new_curve)
        from app.errors import DomainError

        with self.assertRaises(DomainError) as caught:
            feedback_context(LOCAL_ACTOR, self.pid, [ChosenVersion(**pin(result))])
        self.assertEqual(caught.exception.code, "PLACEMENT_EVIDENCE_CHANGED")
        feedback = feedback_context(LOCAL_ACTOR, self.pid, [ChosenVersion(**pin(insight))])
        self.assertIn("largest_observed_drop", feedback[0]["data_excerpt"])
        invalid = {
            **inputs,
            "request_id": uuid4().hex,
            "points": [{"at_seconds": 0, "retained": 2}, {"at_seconds": 1, "retained": 3}],
        }
        self.assertEqual(
            action("post-reviews/retention-mapping", invalid, 422)["code"], "RETENTION_COHORT"
        )
        self.assertEqual(
            action(
                "post-reviews/retention-mapping",
                {
                    **invalid,
                    "points": [{"at_seconds": 0, "retained": 2}, {"at_seconds": 20, "retained": 1}],
                },
                422,
            )["code"],
            "RETENTION_TIME_RANGE",
        )

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
