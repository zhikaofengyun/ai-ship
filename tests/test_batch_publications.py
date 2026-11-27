"""Real batch encodes, scope-aware approvals and recorded TikTok protocol exchanges."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import json
import os
import subprocess
from unittest.mock import patch

import httpx
from app import batches, tiktok_inbox
from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


class BatchPublicationTests(ProductionBase):
    def setUp(self):
        super().setUp()
        source = Path(self.temp.name) / "phone.mov"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=96x64:r=25:d=1",
                "-threads",
                "1",
                str(source),
            ],
            check=True,
        )
        asset = import_saved_file(self.store, LOCAL_ACTOR, self.pid, source.name, source)["asset"]
        recipe = {
            "name": "双作品",
            "project_id": self.pid,
            "idempotency_key": "two-movies",
            "output": "render",
            "count": 2,
            "width": 96,
            "height": 64,
            "fps": 25,
            "slots": [
                {
                    "choices": [
                        {
                            "asset_id": asset["id"],
                            "version": 1,
                            "source_in": s,
                            "source_out": s + 0.36,
                        }
                        for s in (0, 0.4)
                    ]
                }
            ],
        }
        self.batch = self.post("/production/batches", recipe)["id"]
        self.path = f"/production/batches/{self.batch}/publications/prepare"
        self.tiktok = self.create("Account", {"provider": "tiktok", "name": "隔离测试账号"})
        self.body = {
            "account_id": self.tiktok["id"],
            "title": "实拍 · {n}",
            "channel": "tiktok_inbox",
        }

    def approve(self, art, kind, decision="approved", account=None):
        return self.post(
            "/production/approvals",
            {
                "subject_id": art,
                "subject_version": 1,
                "kind": kind,
                "decision": decision,
                "account_id": account,
                "evidence": "隔离回归审片",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    def test_resume_pages_scope_large_approval_history_and_conflicts(self):
        self.assertTrue(batches.work_once(self.store))
        body = {**self.body, "channel": "manual"}
        page = self.post(self.path, body)
        first, second = page["items"]
        self.assertEqual((first["status"], second["status"]), ("created", "skipped"))
        self.assertFalse(first["can_handoff"])
        self.assertTrue(batches.work_once(self.store))
        with ThreadPoolExecutor(max_workers=2) as pool:
            pages = list(pool.map(lambda _: self.post(self.path, body), range(2)))
        self.assertEqual(sorted(p["items"][1]["status"] for p in pages), ["created", "existing"])
        for p in pages:
            self.assertEqual(first["publication_id"], p["items"][0]["publication_id"])
        self.assertEqual(self.post(self.path, {**body, "limit": 1})["next_after"], 1)
        self.assertEqual(self.post(self.path, {**body, "after": 1})["items"][0]["ordinal"], 2)
        self.assertEqual(
            self.post(self.path, {**body, "title": "不同文案"})["items"][0]["code"],
            "IDEMPOTENCY_CONFLICT",
        )
        art = first["artifact_id"]
        self.approve(art, "content_check", account=self.account["id"])
        self.approve(art, "rights_check")
        route = "/api/v1/production/publications/" + first["publication_id"] + "/preflight"
        checks = lambda: self.client.get(route).json()["data"]
        self.assertFalse(checks()["can_handoff"])
        self.approve(art, "content_check")
        with self.store.transaction():
            for i in range(501):
                self.create("Approval", {"kind": "review_finding", "evidence": str(i)})
        self.assertTrue(checks()["can_handoff"])
        self.approve(art, "content_check", "rejected")
        self.assertFalse(checks()["can_handoff"])
        self.assertEqual(len(self.store.list("local", "Publication", self.pid)), 2)

    def test_inbox_scopes_signed_bytes_status_and_no_duplicate_send(self):
        self.assertTrue(batches.work_once(self.store))
        row = self.post(self.path, self.body)["items"][0]
        art = row["artifact_id"]
        pid = row["publication_id"]
        self.approve(art, "content_check")
        self.approve(art, "rights_check")
        path = f"/production/tiktok/inbox/{pid}"
        self.post(path + "/send", {"expected_revision": 1}, 409)
        original = self.client.get("/api/v1/media/artifacts/" + art + "/file?download=true").content
        calls = []
        phase = "SEND_TO_USER_INBOX"

        def exchange(req):
            calls.append((req.method, req.url.path))
            if req.url.host == "open-upload.tiktokapis.com":
                # An operator polling while the chunk is in flight must not invalidate its lease.
                nonlocal phase
                phase = "PROCESSING_UPLOAD"
                active = self.store.get(pid, "local")
                polled = self.post(path + "/status", {"expected_revision": active["version"]})
                self.assertEqual(active["version"], polled["version"])
                phase = "SEND_TO_USER_INBOX"
                self.assertNotIn("authorization", req.headers)
                self.assertEqual(req.content, original)
                self.assertEqual(
                    req.headers["Content-Range"], f"bytes 0-{len(original)-1}/{len(original)}"
                )
                return httpx.Response(201)
            self.assertEqual(req.headers["authorization"], "Bearer isolated-token")
            if req.url.path.endswith("/info/"):
                data = {"user": {"open_id": "isolated-id"}}
            elif req.url.path.endswith("/init/"):
                self.assertEqual(
                    json.loads(req.content)["source_info"]["video_size"], len(original)
                )
                data = {
                    "publish_id": "fixture-001",
                    "upload_url": "https://open-upload.tiktokapis.com/video/?upload_token=secret-example",
                }
            else:
                data = {
                    "status": phase,
                    "publicaly_available_post_id": [102030] if phase == "PUBLISH_COMPLETE" else [],
                }
            return httpx.Response(
                200, json={"data": data, "error": {"code": "ok", "log_id": "unit-test"}}
            )

        client = httpx.Client

        def make(**kwargs):
            return client(transport=httpx.MockTransport(exchange), **kwargs)

        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {
                        "AI_SHIP_TIKTOK_UPLOAD_ACCOUNTS": json.dumps(
                            [
                                {
                                    "tenant_id": "local",
                                    "project_id": self.pid,
                                    "account_id": self.tiktok["id"],
                                    "open_id": "isolated-id",
                                    "token_env": "AI_SHIP_TIKTOK_UPLOAD_TOKEN_TEST",
                                }
                            ]
                        ),
                        "AI_SHIP_TIKTOK_UPLOAD_TOKEN_TEST": "isolated-token",
                    },
                )
            )
            stack.enter_context(patch("app.tiktok_inbox.httpx.Client", side_effect=make))
            sent = self.post(path + "/send", {"expected_revision": 1})
            self.assertEqual(sent["data"]["inbox_stage"], "uploaded", sent)
            self.post(path + "/send", {"expected_revision": sent["version"]}, 409)
            inbox = self.post(path + "/status", {"expected_revision": sent["version"]})
            self.assertEqual(inbox["data"]["outcome"], "accepted")
            self.assertEqual(inbox["data"]["post_refs"], [])
            manual = self.post(
                f"/production/publications/{pid}/receipt",
                {
                    "expected_revision": inbox["version"],
                    "platform_post_id": "102030",
                    "post_url": "https://example.test/102030",
                    "observed_at": "2026-01-01T00:00:00Z",
                    "evidence": "人工登记的隔离测试帖子",
                },
            )
            self.assertFalse(manual["post"]["data"]["independently_verified"])
            phase = "PUBLISH_COMPLETE"
            posted = self.post(
                path + "/status", {"expected_revision": manual["publication"]["version"]}
            )
            again = self.post(path + "/status", {"expected_revision": posted["version"]})
            self.assertEqual(again["data"]["post_refs"], posted["data"]["post_refs"])
            post = self.store.get(again["data"]["post_refs"][0], "local")
            self.assertEqual(post["data"]["platform_post_id"], "102030")
            self.assertEqual(post["id"], manual["post"]["id"])
            self.assertTrue(post["data"]["independently_verified"])
            self.assertEqual(sum(path.endswith("/init/") for _, path in calls), 1)
            self.assertNotIn("secret-example", json.dumps(again))
        self.assertEqual(tiktok_inbox.chunk_plan(70_000_001)["total_chunk_count"], 4)

    def test_lost_initial_receipt_prevents_replay_and_binds_authorization_before_init(self):
        self.assertTrue(batches.work_once(self.store))
        item = self.post(self.path, self.body)["items"][0]
        self.approve(item["artifact_id"], "content_check")
        self.approve(item["artifact_id"], "rights_check")
        path = "/production/tiktok/inbox/" + item["publication_id"]
        calls = []

        def exchange(req):
            calls.append(req.url.path)
            if req.url.path.endswith("/info/"):
                return httpx.Response(
                    200, json={"data": {"user": {"open_id": "person"}}, "error": {"code": "ok"}}
                )
            raise httpx.ReadTimeout("lost isolated reply")

        def client(_token):
            return httpx.Client(
                base_url="https://open.tiktokapis.com/v2/", transport=httpx.MockTransport(exchange)
            )

        with (
            patch.object(tiktok_inbox, "tiktok_client", client),
            patch.object(tiktok_inbox, "upload_binding", return_value=("person", "one")),
        ):
            with patch.object(
                tiktok_inbox,
                "submission_context",
                side_effect=[("person", "one"), ("person", "two")],
            ):
                self.post(path + "/send", {"expected_revision": 1}, 409)
            self.assertFalse(any(r.endswith("/init/") for r in calls))
            with patch.object(tiktok_inbox, "submission_context", return_value=("person", "one")):
                unknown = self.post(path + "/send", {"expected_revision": 1})
                self.assertEqual(unknown["data"]["outcome"], "outcome_unknown")
                self.post(path + "/send", {"expected_revision": unknown["version"]}, 409)
                self.post(path + "/status", {"expected_revision": unknown["version"]}, 409)
        self.assertEqual(sum(r.endswith("/init/") for r in calls), 1)
