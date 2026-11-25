from pathlib import Path
import subprocess
from uuid import uuid4

from app import batches
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class BatchMarketTests(ProductionBase):
    base = "/production/batches"

    def setUp(self):
        super().setUp()

        def set_fields(r, **changes):
            return self.store.update(
                r["id"], {**r["data"], **changes}, tenant_id="local", expected_revision=r["version"]
            )

        self.project = set_fields(self.project, market="US", language="en-US")
        self.account = set_fields(self.account, region="US")
        self.src = self.create("Source", {"provenance": "授权商品与市场规则表"})
        self.product = self.create(
            "Product",
            {
                "SKU": "SKU7",
                "price": "5.00",
                "quantity": 2,
                "currency": "USD",
                "market": "US",
                "evidence": "核对表单第 3 行",
                "evidence_source_id": self.src["id"],
                "valid_until": "2099-01-01T00:00:00Z",
            },
            refs=[self.pin(self.src)],
        )
        self.original = self.create(
            "Script", {"text": "SKU7 两件装，USD 5.00，共 2 件", "language": "zh-CN"}
        )
        self.target = self.create(
            "Script", {"text": "SKU7 pack of 2, USD 5.00", "language": "en-US"}
        )
        self.spec = {
            "project_id": self.pid,
            "name": "美国商品页",
            "market": "US",
            "language": "en-US",
            "account": self.pin(self.account),
            "source": self.pin(self.src),
            "original_script": self.pin(self.original),
            "localized_script": self.pin(self.target),
            "product": self.pin(self.product),
            "audience_hypothesis": "购物者可能关心套装数量",
            "prohibited_phrases": ["always free"],
            "valid_until": "2099-01-01T00:00:00Z",
        }

    def pin(self, r):
        return {"id": r["id"], "version": r["version"]}

    def make(self, expected=200, **changes):
        return self.post(
            self.base + "/market-briefs",
            {**self.spec, "request_id": uuid4().hex, **changes},
            expected,
        )

    def test_market_scope_sku_number_and_prohibited_expression(self):
        self.make(409, market="CA")
        self.make(409, prohibited_phrases=["pack"])
        bad = self.create("Script", {"text": "SKU7 pack of 3, USD 5.00", "language": "en-US"})
        self.make(409, localized_script=self.pin(bad))
        badsku = self.create("Script", {"text": "XYZ7 pack of 2, USD 5.00", "language": "en-US"})
        self.make(409, localized_script=self.pin(badsku))
        wrong_price = self.create(
            "Script", {"text": "SKU7 pack of 2, USD 9.00", "language": "en-US"}
        )
        error = self.make(
            409, original_script=self.pin(wrong_price), localized_script=self.pin(wrong_price)
        )
        self.assertEqual(error["error"]["code"], "MARKET_PRICE_DRIFT")
        self.make(409, valid_until="2020-01-01T00:00:00Z")
        ok = self.make()
        self.assertEqual(ok["brief"]["data"]["audience_basis"], "operator_hypothesis")
        self.assertEqual(ok["approval"]["data"]["subject_hash"], ok["brief"]["hash"])

    def test_caption_binding_gate_and_live_evidence_drift_before_worker(self):
        brief = self.make()["brief"]
        path = Path(self.temp.name) / "market.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=128x96:d=1:r=25",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                str(path),
            ],
            check=True,
        )
        asset = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["asset"]

        def template(txt):
            return self.create(
                "Timeline",
                {
                    "name": "当地字幕",
                    "clips": [{"asset_id": asset["id"], "source_in": 0, "source_out": 0.4}],
                    "captions": [{"text": txt, "language": "en-US", "start": 0.01, "end": 0.35}],
                },
            )

        recipe = {
            "name": "美国批次",
            "project_id": self.pid,
            "market_brief": self.pin(brief),
            "count": 1,
            "output": "timeline",
            "slots": [{"choices": [{"asset_id": asset["id"], "version": 1, "source_out": 0.4}]}],
        }

        def submit(t, expected=200):
            return self.post(
                self.base,
                {**recipe, "idempotency_key": uuid4().hex, "template": self.pin(t)},
                expected,
            )

        submit(template("wrong text"), 409)
        t = template(self.target["data"]["text"])
        bid = submit(t)["id"]
        batches.work_once(self.store)
        result = self.client.get("/api/v1" + self.base + "/" + bid).json()["data"]["items"][0]
        self.assertEqual(result["status"], "succeeded", result)
        ev = self.store.get(result["timeline_id"], "local")["data"]["localization_evidence"]
        self.assertEqual(ev["brief"]["id"], brief["id"])
        self.assertEqual(ev["product_sku"], "SKU7")
        bid = submit(t)["id"]
        self.store.update(
            self.product["id"],
            {**self.product["data"], "price": "6.00"},
            tenant_id="local",
            expected_revision=1,
        )
        batches.work_once(self.store)
        result = self.client.get("/api/v1" + self.base + "/" + bid).json()["data"]["items"][0]
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["error"]["code"], "COMMERCIAL_FACT_CHANGED")
        self.assertIsNone(result["timeline_id"])
