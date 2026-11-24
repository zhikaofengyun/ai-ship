"""V03 real local TTS, rights, exact sample counts and pinned DAG hand-off."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from hashlib import sha256
import wave
from uuid import uuid4

from tests.test_production import ProductionBase


def pin(record):
    return {"id": record["id"], "version": record["version"]}


class NarrationTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.script = self.create(
            "Script", {"language": "en", "text": "The blue cup holds two apples."}
        )
        self.review = self.post(
            "/production/narration/source-reviews",
            {
                "project_id": self.pid,
                "request_id": uuid4().hex,
                "voice": "slt",
                "evidence": "Synthetic test: reviewed project-local fixture",
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            },
        )
        self.body = {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "script": pin(self.script),
            "approval": pin(self.review["approval"]),
        }
        self.route = "/production/narration/synthesize"

    def test_pcm_dedup_tempo_current_voice_rights_and_unknown_language(self):
        slow = self.post(self.route, self.body)
        self.assertEqual(self.post(self.route, self.body)["audio"]["id"], slow["audio"]["id"])
        fast = self.post(self.route, {**self.body, "tempo": 1.5, "request_id": uuid4().hex})
        self.assertLess(
            fast["audio"]["data"]["sample_count"], slow["audio"]["data"]["sample_count"]
        )
        a = slow["audio"]
        url = "/api/v1/media/" + a["id"] + "/file"
        data = self.client.get(url)
        self.assertEqual(data.status_code, 200, data.text[:100] if data.status_code != 200 else "")
        self.assertEqual(sha256(data.content).hexdigest(), a["data"]["content_hash"])
        with wave.open(BytesIO(data.content), "rb") as w:
            self.assertEqual(
                (w.getnframes(), w.getframerate()),
                (a["data"]["sample_count"], a["data"]["sample_rate"]),
            )
            self.assertGreater(len(set(w.readframes(w.getnframes()))), 20)
        self.assertEqual(a["data"]["script_version"]["id"], self.script["id"])
        self.assertEqual(slow["call"]["data"]["output_sha256"], a["data"]["content_hash"])
        chinese = self.create("Script", {"text": "两只苹果", "language": "zh-CN"})
        self.post(self.route, {**self.body, "script": pin(chinese), "request_id": uuid4().hex}, 422)
        source = self.review["source"]
        self.store.update(
            source["id"],
            {**source["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=source["version"],
            actor_id="test",
        )
        self.post(self.route, {**self.body, "request_id": uuid4().hex}, 409)
        self.assertIn(self.client.get(url).status_code, [403, 409])

    def test_corrected_price_reading_preserves_currency_numbers_and_uses_actual_tts(self):
        source = self.create("Script", {"text": "限购2件，USD 79.90。", "language": "zh-CN"})
        product = self.create(
            "Product",
            {
                "name": "杯子",
                "SKU": "cup",
                "price": 79.9,
                "currency": "USD",
                "evidence": "Product fact sheet",
            },
        )
        body = {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "script": pin(source),
            "product": pin(product),
            "source_amount": "79.90",
            "source_currency": "USD",
            "target_template": "Limit 2 mugs at {price}.",
            "review_note": "核对限购数量、美元和商品版本",
        }
        route = "/production/narration/readings"
        script = self.post(route, body)["script"]
        self.assertEqual(script["data"]["display_text"], "Limit 2 mugs at USD 79.90.")
        self.assertEqual(
            script["data"]["text"], "Limit 2 mugs at seventy-nine dollars and ninety cents."
        )
        audio = self.post(self.route, {**self.body, "script": pin(script)})["audio"]
        self.assertEqual(audio["data"]["script_version"]["id"], script["id"])
        for change, code in [
            ({"source_currency": "EUR"}, 409),
            ({"target_template": "Limit 3 mugs at {price}."}, 422),
            ({"source_amount": "9.90"}, 422),
        ]:
            self.post(route, {**body, **change, "request_id": uuid4().hex}, code)
        self.store.update(
            product["id"],
            {**product["data"], "price": 80},
            tenant_id="local",
            actor_id="test",
            expected_revision=1,
        )
        self.post(self.route, {**self.body, "script": pin(script), "request_id": uuid4().hex}, 409)
        self.assertEqual(
            self.store.get(audio["id"], "local")["data"]["script_version"]["version"], 1
        )

    def test_reading_rejects_foreign_symbols_extra_units_and_either_side_of_ranges(self):
        product = self.create(
            "Product", {"SKU": "test", "price": 79.9, "currency": "USD", "evidence": "Fixture"}
        )
        for raw, target, code in [
            ("JPY 79.90", "It costs {price}.", 409),
            ("¥79.90", "It costs {price}.", 409),
            ("USD 79.90", "It costs EUR {price}.", 409),
            ("USD 79.90", "It costs {price} pounds.", 409),
            ("USD 79.90–89.90", "From {price} to 89.90.", 422),
            ("89.90–79.90 USD", "From 89.90 to {price}.", 422),
        ]:
            with self.subTest(raw=raw, target=target):
                source = self.create("Script", {"text": raw})
                self.post(
                    "/production/narration/readings",
                    {
                        "project_id": self.pid,
                        "request_id": uuid4().hex,
                        "script": pin(source),
                        "product": pin(product),
                        "source_amount": "79.90",
                        "source_currency": "USD",
                        "target_template": target,
                        "review_note": "核对货币和区间",
                    },
                    code,
                )

    def test_dag_freezes_script_and_review_and_produces_playable_audio(self):
        nodes = [
            {
                "id": "write",
                "feature_id": "V01",
                "route_id": "V01-R3",
                "parameters": {},
                "operation_args": {"data": {"text": "Two blue cups.", "language": "en"}},
            },
            {
                "id": "speak",
                "feature_id": "V03",
                "route_id": "V03-R2",
                "parameters": {},
                "operation_args": {"tempo": 1.1},
                "input_bindings": {
                    "script": {"node_id": "write", "output": "script", "value": "ref"},
                    "approval": {
                        "record_id": self.review["approval"]["id"],
                        "version": 1,
                        "value": "ref",
                    },
                },
            },
        ]
        workflow = self.create(
            "Workflow",
            {
                "name": "本地配音",
                "DAG": {
                    "nodes": nodes,
                    "edges": [{"source": "write", "target": "speak", "type": "control"}],
                    "max_steps": 3,
                    "max_seconds": 120,
                    "max_attempts": 1,
                    "budget": 0,
                },
            },
        )
        result = self.post(
            "/execution/start",
            {"workflow_id": workflow["id"], "expected_revision": 1, "idempotency_key": uuid4().hex},
        )
        self.assertEqual(result["status"], "completed", result)
        completed = result["run"]["data"]["checkpoint"]["nodes"]
        audio = self.store.get(completed["speak"]["outputs"]["audio"]["id"], "local")
        self.assertEqual(audio["data"]["script_version"], completed["write"]["outputs"]["script"])
        self.assertGreater(audio["data"]["sample_count"], 0)
