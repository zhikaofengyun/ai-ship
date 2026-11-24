import uuid

from app.production import ref
from tests.test_production import ProductionBase


class RecoveryPackTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.task = self.create(
            "TaskContract",
            {
                "goal": "核对商品价格",
                "hard_constraints": ["保留原币种"],
                "locks": ["禁止换商品"],
                "stop_conditions": ["外部结果未知须先核对"],
                "authorization_requirements": ["人工批准"],
                "budget_caps": {"USD": "1.50"},
            },
        )
        self.run = self.create(
            "Run",
            {
                "status": "awaiting_reconciliation",
                "state": "待核对",
                "outcome_unknown": True,
                "task_contract_ref": self.task["id"],
                "checkpoint": {
                    "nodes": {
                        "script": {"status": "completed", "outputs": {}},
                        "handoff": {"status": "unknown", "attempts": 1},
                    }
                },
            },
            [ref(self.task)],
        )
        self.call = self.create("ToolCall", {"run_id": self.run["id"], "status": "unknown"})
        self.product = self.create("Product", {"SKU": "L-001", "currency": "USD", "price": "10.00"})
        self.body = self.payload(self.run)

    def payload(self, run):
        return {
            "project_id": self.pid,
            "request_id": uuid.uuid4().hex,
            "run": {"id": run["id"], "version": run["version"]},
            "reason": "人工固定未知副作用的恢复依据",
            "evidence": [{"id": self.product["id"], "version": 1}],
        }

    def pin(self, body, expected=200):
        return self.post("/execution/context-packs", body, expected)

    def test_evidence_sources_and_stops_survive_pressure_and_unknown_requires_review(self):
        result = self.pin(self.body)
        packet = result["audit"]["data"]["context_packet"]
        self.assertEqual(packet["next_read_or_review"], "reconcile_side_effect")
        self.assertIn("script", packet["completed"])
        self.assertEqual(packet["unresolved_nodes"]["handoff"]["attempts"], 1)
        self.assertEqual(packet["calls"], [ref(self.call)])
        self.assertFalse(packet["grants_execution"])
        self.assertEqual(packet["data_excerpts"][0]["fields"]["price"], "10.00")
        facts = next(
            a["facts"] for a in packet["authorities"] if a["source"]["kind"] == "TaskContract"
        )
        for field in ("stop_conditions", "locks", "authorization_requirements", "budget_caps"):
            self.assertEqual(facts[field], self.task["data"][field])
        self.assertEqual(self.pin(self.body)["audit"]["id"], result["audit"]["id"])
        self.pin({**self.body, "request_id": "new-failed"}, 409)
        self.store.update(
            self.product["id"],
            {**self.product["data"], "price": "11.00"},
            tenant_id="local",
            expected_revision=1,
        )
        live = self.client.get("/api/v1/execution/" + self.run["id"] + "/context-pack").json()[
            "data"
        ]
        self.assertEqual(live["source_changes"][0]["reason"], "VERSION_CHANGED")
        self.assertEqual(live["audit"]["data"]["context_packet"], packet)

    def test_window_refusal_and_optional_data_truncation_are_atomic(self):
        error = self.pin({**self.body, "request_id": "tiny", "byte_limit": 512}, 422)
        self.assertEqual(error["error"]["code"], "CONTEXT_WINDOW_TOO_SMALL")
        self.assertFalse(self.store.list("local", "Audit", self.pid))
        minimum = error["error"]["details"]["required_bytes"]
        pack = self.pin({**self.body, "byte_limit": minimum})
        p = pack["audit"]["data"]["context_packet"]
        self.assertEqual(p["omitted_details"], [ref(self.product)])
        self.assertEqual(p["data_excerpts"], [])
        self.assertLessEqual(pack["audit"]["data"]["payload_bytes"], minimum)
        self.assertIn("stop_conditions", p["authorities"][1]["facts"])
        newer = self.pin(self.payload(pack["run"]))["run"]
        self.assertNotIn(pack["audit"]["id"], [r["id"] for r in newer["refs"]])
        self.assertEqual(self.store.get(pack["audit"]["id"], "local")["version"], 1)

    def test_configured_product_is_required_without_reselecting_and_flags_price_drift(self):
        command = {
            "command_id": uuid.uuid4().hex,
            "command_type": "ConfigureFeature",
            "target_id": None,
            "expected_revision": None,
            "project_id": self.pid,
            "actor_id": "local-owner",
            "actor_role": "owner",
            "input_version_refs": [ref(self.product)],
            "parameters": {"feature_id": "F03", "route_id": "F03-R3", "controls": {}},
            "policy_version": None,
            "trace_id": uuid.uuid4().hex,
            "idempotency_key": uuid.uuid4().hex,
            "reason": "原始商品绑定",
        }
        run = self.post("/commands", command)["run"]
        body = {**self.payload(run), "evidence": []}
        audit = self.pin(body)["audit"]
        self.assertTrue(
            any(r["id"] == self.product["id"] and r["version"] == 1 for r in audit["refs"])
        )
        snapshot = audit["data"]["context_packet"]
        self.assertEqual(snapshot["bound_inputs"], [ref(self.product)])
        self.store.update(
            self.product["id"],
            {**self.product["data"], "price": "12.99"},
            tenant_id="local",
            expected_revision=1,
        )
        current = self.client.get("/api/v1/execution/" + run["id"] + "/context-pack").json()["data"]
        self.assertEqual(current["source_changes"][0]["pinned"], ref(self.product))
        self.assertEqual(current["source_changes"][0]["current"]["version"], 2)
        self.assertEqual(current["audit"]["data"]["context_packet"], snapshot)

    def test_late_tool_result_marks_the_original_call_without_advancing_the_run(self):
        packet = self.pin(self.body)["audit"]["data"]["context_packet"]
        self.store.update(
            self.call["id"],
            {**self.call["data"], "status": "completed"},
            tenant_id="local",
            expected_revision=1,
        )
        current = self.client.get("/api/v1/execution/" + self.run["id"] + "/context-pack").json()[
            "data"
        ]
        self.assertFalse(current["run_advanced"])
        self.assertEqual(current["source_changes"][0]["pinned"], ref(self.call))
        self.assertEqual(current["source_changes"][0]["current"]["version"], 2)
        self.assertEqual(current["audit"]["data"]["context_packet"], packet)

    def test_cancelled_unknown_effect_keeps_operator_stop_and_original_run_version(self):
        reason = "停止外发，只核对此前交接的结果"
        command = {
            "command_id": uuid.uuid4().hex,
            "command_type": "RequestCancel",
            "project_id": self.pid,
            "target_id": self.run["id"],
            "expected_revision": self.run["version"],
            "actor_id": "local-owner",
            "actor_role": "owner",
            "input_version_refs": [],
            "parameters": {},
            "policy_version": None,
            "trace_id": uuid.uuid4().hex,
            "idempotency_key": uuid.uuid4().hex,
            "reason": reason,
        }
        self.post("/commands", command)
        stopped = self.store.get(self.run["id"], "local")
        body = self.payload(stopped)
        bound = self.pin({**body, "request_id": "tiny-stop", "byte_limit": 512}, 422)["error"][
            "details"
        ]["required_bytes"]
        result = self.pin({**body, "byte_limit": bound})
        packet = result["audit"]["data"]["context_packet"]
        self.assertEqual(packet["next_read_or_review"], "reconcile_side_effect")
        self.assertEqual(packet["limits_and_stops"]["control_request"], "RequestCancel")
        self.assertEqual(packet["limits_and_stops"]["control_reason"], reason)
        self.assertEqual(packet["source_run"], ref(stopped))
        self.assertFalse(packet["grants_execution"])

    def test_cross_project_stale_and_repeated_evidence_are_refused(self):
        other = self.store.create("Project", {"name": "separate"}, tenant_id="local")
        foreign = self.store.create(
            "Product", {"SKU": "Nope"}, tenant_id="local", project_id=other["id"]
        )
        for refs, status in [
            ([{"id": foreign["id"], "version": 1}], 409),
            ([{"id": self.product["id"], "version": 2}], 409),
            (self.body["evidence"] * 2, 422),
        ]:
            self.pin({**self.body, "request_id": uuid.uuid4().hex, "evidence": refs}, status)
        self.assertFalse(self.store.list("local", "Audit", self.pid))
