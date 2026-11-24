"""Cross-module monetary admission; injected transport never contacts a provider."""

import json
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx

from app.auth import LOCAL_ACTOR
from app.commands import dispatch
from app.errors import DomainError
from app.providers import ChatInput, chat
from app.store import get_store


class BudgetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AI_SHIP_MODE": "local",
                "AI_SHIP_DATA_DIR": self.temp.name,
                "AI_SHIP_DB": self.temp.name + "/ledger.sqlite3",
                "BUDGET_TEST_KEY": "synthetic-injected-secret",
                "AI_SHIP_PROVIDERS": json.dumps(
                    {
                        "budget_test": {
                            "base_url": "https://synthetic.invalid/v1",
                            "api_key_env": "BUDGET_TEST_KEY",
                            "models": ["synthetic-model"],
                            "input_price_per_million": 1,
                            "output_price_per_million": 2,
                            "currency": "USD",
                        }
                    }
                ),
            },
        )
        self.env.start()
        self.store = get_store()
        self.actor = LOCAL_ACTOR
        self.project = self.store.create(
            "Project", {"name": "Synthetic budget review", "budget": 10}, tenant_id="local"
        )
        self.pid = self.project["id"]
        self.profile = self.store.create(
            "ModelProfile",
            {
                "provider": "budget_test",
                "endpoint_ref": "budget_test",
                "model_id": "synthetic-model",
                "credential_ref": "env:BUDGET_TEST_KEY",
                "context_limit": 100000,
            },
            tenant_id="local",
            project_id=self.pid,
        )
        self.calls = 0

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def command(self, kind, parameters=None, target=None, revision=None):
        return {
            "command_id": uuid.uuid4().hex,
            "command_type": kind,
            "project_id": self.pid,
            "actor_id": self.actor.actor_id,
            "actor_role": self.actor.role,
            "target_id": target,
            "expected_revision": revision,
            "input_version_refs": [],
            "parameters": parameters or {},
            "policy_version": None,
            "idempotency_key": uuid.uuid4().hex,
            "trace_id": "synthetic-budget-check",
            "reason": "Isolated cross-module regression",
        }

    def reserve(self, amount, limit=10, currency="USD"):
        return dispatch(
            self.store,
            self.actor,
            self.command("ReserveBudget", {"amount": amount, "limit": limit, "currency": currency}),
        )

    def release(self, cost):
        return dispatch(
            self.store,
            self.actor,
            self.command("ReleaseBudget", target=cost["id"], revision=cost["version"]),
        )

    def model_call(self, failure=None, key="synthetic-model-call"):
        parent = self

        class InjectedTransport:
            def __init__(self, **kwargs):
                parent.assertFalse(kwargs["follow_redirects"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, **kwargs):
                parent.calls += 1
                if failure:
                    raise failure
                return httpx.Response(
                    200,
                    json={
                        "id": "synthetic-receipt",
                        "model": kwargs["json"]["model"],
                        "choices": [
                            {"message": {"content": "Synthetic proposal"}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                    },
                )

        body = ChatInput(
            project_id=self.pid,
            model_profile_id=self.profile["id"],
            messages=[{"role": "user", "content": "Synthetic budget test"}],
            max_tokens=128,
            max_cost=1,
            idempotency_key=key,
        )
        return chat(body, self.actor, InjectedTransport)

    def test_manual_reservation_blocks_model_before_network_then_releases_exact_hold(self):
        cost = self.reserve(10)["record"]
        self.assertEqual(cost["data"]["reserved"], 10)
        with self.assertRaises(DomainError) as blocked:
            self.model_call()
        self.assertEqual(blocked.exception.code, "PROJECT_COST_BUDGET")
        self.assertEqual(self.calls, 0)
        released = self.release(cost)["record"]
        self.assertEqual(released["data"]["reserved"], 0)
        self.assertEqual(released["data"]["estimated"], 10)
        self.assertEqual(self.store.get(cost["id"], "local", 1)["data"]["reserved"], 10)
        self.assertEqual(self.model_call()["status"], "succeeded")
        self.assertEqual(self.calls, 1)

    def test_measured_model_cost_cannot_release_and_reduces_manual_capacity(self):
        self.assertEqual(self.model_call()["status"], "succeeded")
        cost = self.store.list("local", "Cost", self.pid)[0]
        self.assertIsNone(cost["data"]["actual"])
        self.assertGreater(cost["data"]["calculated_charge"], 0)
        with self.assertRaises(DomainError) as blocked:
            self.release(cost)
        self.assertEqual(blocked.exception.code, "ACTUAL_COST")
        self.assertEqual(self.store.get(cost["id"], "local")["data"]["status"], "usage_recorded")
        with self.assertRaises(DomainError) as capacity:
            self.reserve(10)
        self.assertEqual(capacity.exception.code, "BUDGET_EXCEEDED")

    def test_unknown_model_outcome_retains_reservation_and_cannot_be_released(self):
        result = self.model_call(httpx.ReadTimeout("Synthetic timeout after send"))
        self.assertEqual(result["status"], "outcome_unknown")
        cost = self.store.list("local", "Cost", self.pid)[0]
        with self.assertRaises(DomainError) as blocked:
            self.release(cost)
        self.assertEqual(blocked.exception.code, "COST_RECONCILIATION_REQUIRED")
        with self.assertRaises(DomainError):
            self.reserve(10)
        with self.assertRaises(DomainError) as repeated:
            self.model_call(key="different-synthetic-key")
        self.assertEqual(repeated.exception.code, "RECONCILE_FIRST")
        self.assertEqual(self.calls, 1)

    def test_project_limit_cannot_be_expanded_or_reused_in_another_currency(self):
        with self.assertRaises(DomainError) as ceiling:
            self.reserve(11, limit=100)
        self.assertEqual(ceiling.exception.code, "BUDGET_EXCEEDED")
        with self.assertRaises(DomainError) as currency:
            self.reserve(1, currency="CNY")
        self.assertEqual(currency.exception.code, "BUDGET_CURRENCY_MISMATCH")
        self.assertEqual(self.store.list("local", "Cost", self.pid), [])
        self.store.update(
            self.pid,
            {**self.project["data"], "budget": {"amount": 10, "currency": "CNY"}},
            tenant_id="local",
            expected_revision=1,
        )
        self.assertEqual(self.reserve(9, limit=100, currency="CNY")["remaining"], 1)
        with self.assertRaises(DomainError) as provider_currency:
            self.model_call()
        self.assertEqual(provider_currency.exception.code, "BUDGET_CURRENCY_MISMATCH")
        self.assertEqual(self.calls, 0)

    def test_actual_calculated_and_estimated_are_not_added_twice_or_erased_by_old_release(self):
        cost = self.store.create(
            "Cost",
            {
                "estimated": 9,
                "reserved": 9,
                "actual": 2,
                "calculated_charge": 3,
                "status": "released",
                "currency": "USD",
            },
            tenant_id="local",
            project_id=self.pid,
        )
        self.assertEqual(self.reserve(8)["remaining"], 0)
        with self.assertRaises(DomainError):
            self.release(cost)
        with self.assertRaises(DomainError) as blocked:
            self.reserve(0.01)
        self.assertEqual(blocked.exception.code, "BUDGET_EXCEEDED")

    def test_decimal_boundary_and_idempotent_reservation_do_not_double_spend(self):
        self.store.update(
            self.pid,
            {**self.project["data"], "budget": 0.3},
            tenant_id="local",
            expected_revision=1,
        )
        command = self.command("ReserveBudget", {"amount": 0.1, "limit": 0.3, "currency": "USD"})
        first = dispatch(self.store, self.actor, command)
        repeated = dispatch(self.store, self.actor, command)
        self.assertEqual(first["record"]["id"], repeated["record"]["id"])
        self.assertEqual(self.reserve(0.2, limit=0.3)["remaining"], 0)
        self.assertEqual(len(self.store.list("local", "Cost", self.pid)), 2)

    def test_unpriced_local_telemetry_is_explicit_but_unknown_monetary_liability_blocks(self):
        telemetry = self.store.create(
            "Cost",
            {
                "category": "local_render",
                "pricing_status": "unconfigured",
                "actual": None,
                "currency": "USD",
                "unit": "wall_seconds",
                "quantity": 3,
            },
            tenant_id="local",
            project_id=self.pid,
        )
        generation = self.store.create(
            "Cost",
            {
                "category": "local_generation",
                "pricing_status": "unconfigured",
                "actual": None,
                "currency": "USD",
                "unit": "generated_video_seconds",
                "quantity": 2,
            },
            tenant_id="local",
            project_id=self.pid,
        )
        response = self.reserve(1)
        self.assertEqual(response["budget_scope"], "priced_monetary_costs")
        self.assertEqual(set(response["unpriced_cost_refs"]), {telemetry["id"], generation["id"]})
        unknown = self.store.create(
            "Cost",
            {
                "status": "outcome_unknown",
                "actual": None,
                "currency": "USD",
                "provider": "synthetic",
            },
            tenant_id="local",
            project_id=self.pid,
        )
        with self.assertRaises(DomainError) as blocked:
            self.reserve(1)
        self.assertEqual(blocked.exception.code, "COST_RECONCILIATION_REQUIRED")
        self.assertEqual(blocked.exception.details["cost_id"], unknown["id"])

    def test_concurrent_reservations_admit_only_one_under_shared_cap(self):
        gate = threading.Barrier(2)

        def reserve_once(_):
            gate.wait()
            try:
                return self.reserve(6)["status"]
            except DomainError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve_once, range(2)))
        self.assertCountEqual(results, ["completed", "BUDGET_EXCEEDED"])
        self.assertEqual(len(self.store.list("local", "Cost", self.pid)), 1)


if __name__ == "__main__":
    unittest.main()
