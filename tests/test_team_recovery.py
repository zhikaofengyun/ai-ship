"""Role checkpoints, provider receipts and independent evidence in the team API."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid
from unittest.mock import patch

import httpx

from app import providers
from tests.test_production import ProductionBase


class TeamRecoveryTests(ProductionBase):
    def setUp(self):
        super().setUp()
        config = {
            "base_url": "http://127.0.0.1:19876/v1",
            "transport": "loopback_chat",
            "models": ["director", "critic"],
            "provider_identity": "test-team",
            "input_price_per_million": 0,
            "output_price_per_million": 0,
            "json_output": True,
        }
        os.environ["AI_SHIP_PROVIDERS"] = json.dumps({"local": config})
        self.profiles = [
            self.create(
                "ModelProfile",
                {
                    "model_id": model,
                    "provider": "local",
                    "endpoint_ref": "local",
                    "context_limit": 20000,
                },
            )
            for model in config["models"]
        ]
        self.brief = self.create("Brief", {"name": "First frozen brief"})
        self.body = {
            "project_id": self.pid,
            "goal": "Propose a cut from the frozen brief",
            "idempotency_key": "repeat-team-test",
            "evidence_refs": [self.brief["id"]],
            "max_cost": 1,
            "max_tokens_per_role": 128,
            "roles": [
                {
                    "role": role,
                    "independence_group": role,
                    "model_profile_id": self.profiles[idx]["id"],
                }
                for role, idx in (("planner", 0), ("creator", 0), ("judge", 1))
            ],
        }
        self.posts = []

    def transport(self, callback=None):
        def send(request):
            body = json.loads(request.content)
            self.posts.append(body)
            result = callback(body) if callback else None
            reply = (
                result
                if result is not None
                else {
                    "proposal": "Frame the selected product.",
                    "unresolved": [],
                    "counter_evidence": [],
                    "quality_checks": (
                        ["The selected brief permits this framing."] if len(self.posts) == 3 else []
                    ),
                    "evidence_refs": [self.brief["id"]],
                }
            )
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "choices": [
                        {"message": {"content": json.dumps(reply)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                },
            )

        factory = httpx.Client
        return patch(
            "app.providers.httpx.Client",
            side_effect=lambda **kw: factory(transport=httpx.MockTransport(send), **kw),
        )

    def saved(self):
        return self.store.list("local", kind="Run", project_id=self.pid)[0]

    def resume(self, run, expected=200):
        return self.post(
            f"/providers/team/{run['id']}/resume", {"expected_revision": run["version"]}, expected
        )

    def test_restart_between_provider_receipt_and_checkpoint_keeps_frozen_inputs(self):
        original = providers.chat

        def crash_after_receipt(*args, **kwargs):
            receipt = original(*args, **kwargs)
            if len(self.posts) == 2:
                raise RuntimeError("interrupted after receipt commit")
            return receipt

        with self.transport():
            with (
                patch("app.providers.chat", side_effect=crash_after_receipt),
                self.assertRaisesRegex(RuntimeError, "interrupted"),
            ):
                self.post("/providers/team", self.body)
            saved = self.saved()
            self.assertEqual(saved["data"]["checkpoint"]["next_role"], 1)
            self.store.update(
                self.brief["id"],
                {"name": "New unrelated brief"},
                tenant_id="local",
                actor_id="test",
                expected_revision=1,
            )
            result = self.resume(saved)
            self.assertEqual(len(self.posts), 3)
            self.assertTrue(
                all("First frozen brief" in p["messages"][-1]["content"] for p in self.posts)
            )
            self.assertTrue(
                all("New unrelated brief" not in p["messages"][-1]["content"] for p in self.posts)
            )
            self.assertEqual(result["status"], "proposals_ready")
            self.assertEqual(len(result["outputs"]), 3)
            self.assertEqual(
                self.post("/providers/team", self.body)["decision"]["id"], result["decision"]["id"]
            )
            self.resume(saved, 409)
        self.assertEqual(len(self.store.list("local", "Cost", self.pid)), 3)
        self.assertEqual(len(self.store.list("local", "DecisionRecord", self.pid)), 1)

    def test_concurrent_replay_observes_busy_owner_then_same_decision(self):
        entered, release = Event(), Event()

        def wait_first(body):
            if len(self.posts) == 1:
                entered.set()
                assert release.wait(8)

        with self.transport(wait_first), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.post, "/providers/team", self.body)
            self.assertTrue(entered.wait(8))
            try:
                contender = self.post("/providers/team", self.body)
                self.assertEqual(contender["status"], "running")
                self.assertEqual(len(self.posts), 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=8)["status"], "proposals_ready")
        self.assertEqual(len(self.posts), 3)

    def test_unknown_looks_up_original_receipt_without_sending_another_call(self):
        def timeout(body):
            raise httpx.ReadTimeout("unknown result")

        with self.transport(timeout):
            stopped = self.post("/providers/team", self.body)
            self.assertEqual(stopped["status"], "outcome_unknown")
            recovered = self.resume(stopped["run"])
            self.assertEqual(recovered["outputs"], stopped["outputs"])
            self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.store.list("local", "Cost", self.pid)), 1)
        self.assertEqual(self.store.list("local", "DecisionRecord", self.pid), [])

    def test_nonexistent_citation_stops_before_creator_and_judge(self):
        with self.transport(
            lambda req: {"proposal": "Cut to a new scene", "evidence_refs": ["unknown"]}
        ):
            result = self.post("/providers/team", self.body)
        self.assertEqual(result["status"], "output_invalid")
        self.assertEqual(result["run"]["data"]["error"]["code"], "TEAM_EVIDENCE_REQUIRED")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.store.list("local", "DecisionRecord", self.pid), [])

    def test_new_price_or_endpoint_during_run_stops_before_next_call(self):
        def change_config(req):
            config = json.loads(os.environ["AI_SHIP_PROVIDERS"])
            config["local"]["output_price_per_million"] = 7
            os.environ["AI_SHIP_PROVIDERS"] = json.dumps(config)

        with self.transport(change_config):
            result = self.post("/providers/team", self.body)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["run"]["data"]["error"]["code"], "PROVIDER_CONFIGURATION_CHANGED")
        self.assertEqual(len(result["outputs"]), 1)
        self.assertEqual(len(self.posts), 1)

    def test_judge_cannot_use_planners_model_with_another_group(self):
        self.body["roles"][1]["model_profile_id"] = self.profiles[1]["id"]
        self.body["roles"][2]["model_profile_id"] = self.profiles[0]["id"]
        with self.transport():
            self.post("/providers/team", self.body, 422)
        self.assertEqual(self.posts, [])

    def test_judge_that_repeats_creator_check_stops_without_decision(self):
        def echo(body):
            return {
                "proposal": "Use the selected product.",
                "evidence_refs": [self.brief["id"]],
                "unresolved": [],
                "counter_evidence": [],
                "quality_checks": ["The product is in the brief."],
            }

        with self.transport(echo):
            result = self.post("/providers/team", self.body)
        self.assertEqual(result["status"], "output_invalid")
        self.assertEqual(result["run"]["data"]["error"]["code"], "INDEPENDENT_REVIEW_REQUIRED")
        self.assertEqual(len(result["outputs"]), 3)
        self.assertEqual(self.store.list("local", "DecisionRecord", self.pid), [])

    def control(self, typ, run):
        rid = uuid.uuid4().hex
        return self.post(
            "/commands",
            {
                "command_id": rid,
                "command_type": typ,
                "policy_version": None,
                "project_id": self.pid,
                "actor_id": "local-owner",
                "actor_role": "owner",
                "target_id": run["id"],
                "expected_revision": run["version"],
                "input_version_refs": [],
                "parameters": {},
                "idempotency_key": rid,
                "trace_id": rid,
                "reason": "Operator role boundary",
            },
        )

    def test_pause_during_model_call_preserves_receipt_then_resumes_unstarted_roles(self):
        def pause_first(body):
            if len(self.posts) == 1:
                self.control("SuspendTask", self.saved())

        with self.transport(pause_first):
            paused = self.post("/providers/team", self.body)
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["run"]["data"]["checkpoint"]["next_role"], 1)
            self.assertEqual(len(self.posts), 1)
            result = self.resume(paused["run"])
        self.assertEqual(result["status"], "proposals_ready")
        self.assertEqual(len(self.posts), 3)

    def test_cancel_after_receipt_before_checkpoint_retains_result_and_stops_new_roles(self):
        original = providers.chat

        def crash_after(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("crash after receipt")

        with self.transport():
            with (
                patch("app.providers.chat", side_effect=crash_after),
                self.assertRaisesRegex(RuntimeError, "crash after receipt"),
            ):
                self.post("/providers/team", self.body)
            self.control("RequestCancel", self.saved())
            cancelled = self.resume(self.saved())
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["run"]["data"]["checkpoint"]["next_role"], 1)
            self.assertEqual(len(cancelled["outputs"]), 1)
            self.assertEqual(self.resume(cancelled["run"])["status"], "cancelled")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.store.list("local", "DecisionRecord", self.pid), [])
