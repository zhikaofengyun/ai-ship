from unittest.mock import patch
from uuid import uuid4
from app.harness_qualification import codec_trial
from tests.test_production import ProductionBase


class QualificationTests(ProductionBase):
    def flow(self, route="V01-R3", feature="V01"):
        return self.create(
            "Workflow",
            {
                "name": "Readback",
                "DAG": {
                    "nodes": [
                        {
                            "id": "script",
                            "feature_id": feature,
                            "route_id": route,
                            "parameters": {},
                            "operation_args": {"data": {"text": "示例脚本"}},
                        }
                    ],
                    "edges": [],
                    "max_steps": 1,
                    "max_attempts": 1,
                    "budget": 0,
                },
            },
        )

    def qualify(self, flow):
        return self.post(
            "/harness/qualify",
            {
                "project_id": self.pid,
                "workflow": {"id": flow["id"], "version": flow["version"]},
                "name": "fixture",
                "request_id": uuid4().hex,
            },
        )

    def test_qualified_workflow_dispatch_and_runtime_drift(self):
        flow = self.flow()
        result = self.qualify(flow)
        self.assertTrue(result["passed"], result["checks"])
        body = {
            "project_id": self.pid,
            "skill": {"id": result["skill"]["id"], "version": 1},
            "request_id": "one",
        }
        execution = self.post("/harness/dispatch", body)
        self.assertEqual(execution["status"], "completed")
        replay = self.post("/harness/dispatch", body)
        self.assertEqual(execution["run"]["id"], replay["run"]["id"])
        with patch("app.harness_qualification.runtime", return_value={"fingerprint": "changed"}):
            self.assertEqual(
                self.post("/harness/dispatch", {**body, "request_id": "two"}, 409)["error"]["code"],
                "HARNESS_RUNTIME_CHANGED",
            )

    def test_unregistered_route_keeps_failed_suite_and_blocks_forged_skill(self):
        result = self.qualify(self.flow("G03-R1", "G03"))
        self.assertIsNone(result["skill"])
        self.assertFalse(result["passed"])
        self.assertEqual(
            self.store.get(result["suite"]["id"], "local")["data"]["checks"][0]["passed"], False
        )
        skill = self.create("SkillVersion", {"name": "unqualified", "status": "validated"})
        self.post(
            "/harness/dispatch",
            {
                "project_id": self.pid,
                "skill": {"id": skill["id"], "version": 1},
                "request_id": "invalid",
            },
            409,
        )
        self.assertIn("H.264", codec_trial())
