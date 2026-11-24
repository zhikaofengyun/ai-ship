"""Public API checks against durable SQLite snapshots, independent requests and current grants."""

from concurrent.futures import ThreadPoolExecutor

from tests.test_production import ProductionBase


class AssetIndexTests(ProductionBase):
    base = "/api/v1/production/asset-index"

    def setUp(self):
        super().setUp()
        self.source = self.create("Source", {"name": "拍摄原件", "rights_status": "approved"})
        self.asset = self.create(
            "Asset",
            {"name": "茶杯", "media_type": "video/mp4", "source_id": self.source["id"]},
            [self.pin(self.source)],
        )
        self.version = self.create(
            "AssetVersion",
            {"asset_id": self.asset["id"], "filename": "BLUE CUP.mov", "probe": {"duration": 4.0}},
            [self.pin(self.asset), self.pin(self.source)],
        )

    @staticmethod
    def pin(item):
        return {"id": item["id"], "version": item["version"], "kind": item["kind"]}

    def call(self, path, code=200, **values):
        r = self.client.post(self.base + "/" + path, json={"project_id": self.pid, **values})
        self.assertEqual(r.status_code, code, r.text)
        return r.json()["data" if code == 200 else "error"]

    def shadow(self, key="one", revision=0):
        return self.call("build", mode="shadow", request_key=key, expected_revision=revision)[
            "snapshot"
        ]

    def activate(self, sid, revision=0):
        return self.call("switch", snapshot=sid, expected_revision=revision)

    def search(self, text="", **kwargs):
        return self.call("query", text=text, **kwargs)

    def change(self, item, data):
        return self.store.update(
            item["id"],
            {**item["data"], **data},
            tenant_id="local",
            expected_revision=item["version"],
            actor_id="reviewer",
            refs=item["refs"],
        )

    def test_shadow_switch_fallback_incremental_then_rollback(self):
        self.assertEqual(self.search("BLUE")["items"][0]["source"], "source")
        s1 = self.shadow()
        self.assertIsNone(self.search("BLUE")["head"]["generation"])
        self.activate(s1)
        row = self.search("blue CUP")["items"][0]
        self.assertEqual(
            (row["name"], row["source"], row["matched"]), ("BLUE CUP.mov", "index", ["blue", "cup"])
        )
        self.assertLess(row["rank"], 0)
        tag = self.client.post(
            "/api/v1/production/tags",
            json={
                "project_id": self.pid,
                "asset_version_id": self.version["id"],
                "category": "subject",
                "label": "蓝色陶瓷",
                "start": 0.0,
                "end": 1.0,
                "confidence": 1,
                "evidence": "眼见原片前一秒",
            },
        )
        self.assertEqual(tag.status_code, 200, tag.text)
        self.assertEqual(self.search("蓝色")["items"][0]["source"], "source")
        s2 = self.call("build", mode="incremental", request_key="two", expected_revision=1)
        self.assertEqual(s2["reused"], 0)
        self.activate(s2["snapshot"], 1)
        self.assertEqual(self.search("蓝色陶瓷")["items"][0]["source"], "index")
        s3 = self.call("build", mode="incremental", request_key="three", expected_revision=2)
        self.assertEqual(s3["reused"], 1)
        self.call("switch", snapshot=s1, action="rollback", expected_revision=2)
        self.assertEqual(self.search("蓝色陶瓷")["items"][0]["source"], "source")
        self.change(self.source, {"status": "revoked"})
        for channel in ("indexed", "source", "semantic"):
            result = self.search("蓝色", channel=channel)
            self.assertEqual(result["count"], 0)
        self.assertEqual(self.search("蓝色", channel="semantic")["vector_available"], False)

    def test_switch_race_stale_source_and_atomic_failure_keep_previous(self):
        s1 = self.shadow()
        self.activate(s1)
        a = self.shadow("a", 1)
        b = self.shadow("b", 1)

        def race(s):
            return self.client.post(
                self.base + "/switch",
                json={"project_id": self.pid, "snapshot": s, "expected_revision": 1},
            ).status_code

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(race, [a, b])), [200, 409])
        stage = self.shadow("stale", 2)
        self.change(self.asset, {"name": "新修订"})
        self.assertEqual(
            self.call("switch", 409, snapshot=stage, expected_revision=2)["code"],
            "INDEX_SOURCE_CHANGED",
        )
        self.assertEqual(self.search()["head"]["revision"], 2)
        self.assertEqual(
            self.call("switch", 409, snapshot=stage, expected_revision=2, action="rollback")[
                "code"
            ],
            "INDEX_NOT_PREVIOUS",
        )
        self.assertEqual(self.search("blue", channel="source")["items"][0]["source"], "source")
        self.assertEqual(self.search("blue", media_type="audio")["count"], 0)

    def test_replay_restart_short_terms_tenant_scope_and_request_conflict(self):
        s1 = self.shadow()
        self.assertEqual(self.shadow(), s1)
        self.assertEqual(
            self.call("build", 409, request_key="one", mode="incremental", expected_revision=0)[
                "code"
            ],
            "IDEMPOTENCY_CONFLICT",
        )
        self.activate(s1)
        self.assertEqual(self.search("b")["count"], 1)
        other = self.create("Project", {"name": "其他工程"})
        r = self.client.post(
            self.base + "/switch",
            json={"project_id": other["id"], "snapshot": s1, "expected_revision": 0},
        )
        self.assertEqual(r.status_code, 404)
        from app.store import Store

        restarted = Store(self.store.path)
        with restarted.connection() as c:
            self.assertEqual(
                c.execute(
                    "SELECT generation FROM media_search_heads WHERE tenant=? AND project=?",
                    ("local", self.pid),
                ).fetchone()[0],
                s1,
            )
        history = self.client.get(self.base + "/history", params={"project_id": self.pid}).json()[
            "data"
        ]
        self.assertEqual(history["snapshots"][0]["previously_active"], 1)
        self.assertTrue(self.search("cup", channel="semantic")["fallback"])

    def test_result_pagination_rechecks_view_and_current_rights(self):
        other = self.create(
            "AssetVersion",
            {"asset_id": self.asset["id"], "filename": "second.mov", "probe": {"duration": 1.0}},
            [self.pin(self.asset), self.pin(self.source)],
        )
        page = self.search(limit=1)
        self.assertEqual((page["count"], page["next_offset"]), (2, 1))
        second = self.search(limit=1, offset=1, expected_view=page["view"])
        self.assertNotEqual(page["items"][0]["version_id"], second["items"][0]["version_id"])
        self.assertIsNone(second["next_offset"])
        self.change(other, {"filename": "third.mov"})
        result = self.call("query", 409, limit=1, offset=1, expected_view=page["view"])
        self.assertEqual(result["code"], "RESULT_SET_CHANGED")
        self.change(self.source, {"status": "revoked"})
        self.assertEqual(self.search(limit=1)["count"], 0)

    def test_current_grant_is_checked_in_every_search_route(self):
        grant = self.create("Grant", {"status": "active", "expires_at": "2099-01-01T00:00:00Z"})
        source = self.store.update(
            self.source["id"],
            self.source["data"],
            expected_revision=1,
            actor_id="test",
            tenant_id="local",
            refs=[self.pin(grant)],
        )
        self.assertEqual(source["version"], 2)
        self.activate(self.shadow())
        self.assertEqual(self.search()["count"], 1)
        self.change(grant, {"status": "revoked"})
        self.assertEqual(self.search()["count"], 0)
        legacy = self.client.get(
            "/api/v1/production/search", params={"project_id": self.pid, "q": "BLUE"}
        )
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertFalse(legacy.json()["data"]["items"])
