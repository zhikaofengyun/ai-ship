"""Keep a rendered batch item bound to its current account-specific publication chain."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import json
import os
from unittest.mock import patch

from app import batches
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class BatchPublicationMigrationTests(ProductionBase):
    def setUp(self):
        super().setUp()
        path = Path(__file__).resolve().parents[1] / "evidence/ai-production/ocr/SAMPLE.mp4"
        asset = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["asset"]
        job = self.post(
            "/production/batches",
            {
                "name": "Migration fixture",
                "project_id": self.pid,
                "idempotency_key": "migration-fixture",
                "output": "render",
                "count": 1,
                "width": 96,
                "height": 64,
                "fps": 25,
                "slots": [
                    {
                        "choices": [
                            {
                                "asset_id": asset["id"],
                                "version": 1,
                                "source_in": 0,
                                "source_out": 0.4,
                            }
                        ]
                    }
                ],
            },
        )
        self.batch = job["id"]
        self.assertTrue(batches.work_once(self.store))
        self.project_path = f"/production/batches/{self.batch}/publications"
        self.a, self.b, self.c = [
            self.create("Account", {"provider": "tiktok", "name": x}) for x in ("A", "B", "C")
        ]

    def prepare(self, account, channel="manual", **overrides):
        return self.post(
            self.project_path + "/prepare",
            {"account_id": account["id"], "channel": channel, "title": "Original {n}", **overrides},
        )["items"][0]

    def listing(self, account, channel="manual"):
        response = self.client.get(
            "/api/v1" + self.project_path, params={"account_id": account["id"], "channel": channel}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["items"][0]

    def change(self, pub_id, **fields):
        pub = self.store.get(pub_id, "local")
        return self.client.post(
            "/api/v1/production/publication-lifecycle/" + pub_id + "/change",
            json={
                "request_id": uuid4().hex,
                "reason": "Operator revision",
                "expected_revision": pub["version"],
                **fields,
            },
        )

    def move(self, pub_id, **fields):
        response = self.change(pub_id, **fields)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["successor"]

    def test_multihop_account_move_prepare_replay_and_distinct_channels(self):
        first = self.prepare(self.a)
        title = self.move(first["publication_id"], title="Revised")
        self.assertEqual(self.prepare(self.a)["publication_id"], title["id"])
        scheduled = self.move(title["id"], local_datetime="2030-04-02T10:00:00", timezone="UTC")
        b = self.move(scheduled["id"], account={"id": self.b["id"], "version": 1})
        self.assertEqual(self.listing(self.b)["publication_id"], b["id"])
        c = self.move(b["id"], account={"id": self.c["id"], "version": 1})
        self.assertEqual((c["data"]["batch_id"], c["data"]["batch_ordinal"]), (self.batch, 1))
        for account in (self.a, self.b):
            for row in (self.listing(account), self.prepare(account)):
                self.assertEqual(row["code"], "BATCH_PUBLICATION_MOVED")
                self.assertFalse(row["can_send"])
                self.assertNotIn("publication_id", row)
        for row in (self.listing(self.c), self.prepare(self.c)):
            self.assertEqual(row["publication_id"], c["id"])
            self.assertEqual(row["status"], "existing")
        back = self.move(c["id"], account={"id": self.a["id"], "version": 1})
        self.assertEqual(self.listing(self.a)["publication_id"], back["id"])
        self.assertEqual(self.listing(self.c)["code"], "BATCH_PUBLICATION_MOVED")
        independent = self.prepare(self.a, "tiktok_inbox")
        self.assertEqual(independent["status"], "created")
        self.assertNotEqual(independent["publication_id"], back["id"])

    def test_concurrent_prepare_and_move_cannot_bind_two_independent_intents(self):
        source = self.prepare(self.a)["publication_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            change = pool.submit(self.change, source, account={"id": self.b["id"], "version": 1})
            target = pool.submit(self.prepare, self.b)
            response = change.result()
            result = target.result()
        if response.status_code == 200:
            self.assertEqual(response.json()["data"]["successor"]["id"], result["publication_id"])
            self.assertEqual(self.listing(self.a)["code"], "BATCH_PUBLICATION_MOVED")
        else:
            self.assertEqual(
                (response.status_code, response.json()["error"]["code"]),
                (409, "BATCH_PUBLICATION_ACCOUNT_OCCUPIED"),
            )
            self.assertEqual(self.listing(self.a)["publication_id"], source)
        self.assertEqual(self.listing(self.b)["publication_id"], result["publication_id"])
        self.assertEqual(len(self.store.list("local", "Publication", self.pid)), 2)

    def test_legacy_successor_membership_and_project_tenant_guards(self):
        first = self.prepare(self.a)
        old = self.move(first["publication_id"], account={"id": self.b["id"], "version": 1})
        legacy_data = {
            k: v for k, v in old["data"].items() if k not in ("batch_id", "batch_ordinal")
        }
        self.store.update(
            old["id"],
            legacy_data,
            tenant_id="local",
            actor_id="fixture",
            expected_revision=old["version"],
        )
        self.assertEqual(self.listing(self.b)["publication_id"], old["id"])
        new = self.move(old["id"], title="Keep old batch attribution")
        self.assertEqual(new["data"]["batch_id"], self.batch)
        self.assertEqual(self.prepare(self.b)["publication_id"], new["id"])
        outside = self.create("Project", {"name": "Other project"})
        account = self.store.create(
            "Account", {"provider": "tiktok"}, tenant_id="local", project_id=outside["id"]
        )
        failed = self.change(new["id"], account={"id": account["id"], "version": 1})
        self.assertEqual(
            (failed.status_code, failed.json()["error"]["code"]), (409, "PROJECT_MISMATCH")
        )
        params = {"account_id": self.b["id"], "channel": "manual"}
        keys = {
            "viewer-test": {"tenant_id": "local", "actor_id": "readonly", "role": "viewer"},
            "outside-test": {"tenant_id": "elsewhere", "actor_id": "writer", "role": "owner"},
        }
        with patch.dict(os.environ, {"AI_SHIP_MODE": "team", "AI_SHIP_API_KEYS": json.dumps(keys)}):
            route = "/api/v1" + self.project_path
            allowed = {"Authorization": "Bearer viewer-test"}
            visible = self.client.get(route, params=params, headers=allowed)
            self.assertEqual(visible.json()["data"]["items"][0]["publication_id"], new["id"])
            self.assertEqual(
                self.client.post(
                    route + "/prepare", json={**params, "title": "Test"}, headers=allowed
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.get(
                    route, params=params, headers={"Authorization": "Bearer outside-test"}
                ).status_code,
                404,
            )
