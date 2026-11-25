"""Exercise TikTok's form protocol through a loopback-only token server and real callback API."""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Event
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from app import tiktok_login as login, tiktok_metrics
from app.auth import LOCAL_ACTOR
from app.main import app
from tests.test_production import ProductionBase


class LoginKitTests(ProductionBase):
    def setUp(self):
        super().setUp()
        self.browser = TestClient(app, base_url="https://testserver", follow_redirects=False)
        self.addCleanup(self.browser.close)
        self.a = self.create("Account", {"name": "Demo", "provider": "tiktok"})
        self.key = ("local", self.pid, self.a["id"])
        self.url = "/api/v1/production/tiktok/login/accounts/" + self.a["id"]
        self.redirect = "https://testserver" + login.CALLBACK
        self.conf = patch.dict(
            os.environ,
            {
                "AI_SHIP_TIKTOK_LOGIN_ACCOUNTS": json.dumps(
                    [
                        dict(
                            zip(("tenant_id", "project_id", "account_id"), self.key),
                            open_id="sample-open-id",
                        )
                    ]
                ),
                "AI_SHIP_TIKTOK_CLIENT_KEY": "sample-client",
                "AI_SHIP_TIKTOK_CLIENT_SECRET": "sample-client-secret",
                "AI_SHIP_TIKTOK_REDIRECT_URI": self.redirect,
                "AI_SHIP_TIKTOK_FERNET_KEY": Fernet.generate_key().decode(),
            },
        )
        self.conf.start()
        self.addCleanup(self.conf.stop)
        self.requests = []
        self.behavior = "ok"
        self.version = 1
        self.received = Event()
        self.resume = Event()
        fixture = self

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                data = {
                    k: v[0]
                    for k, v in parse_qs(
                        self.rfile.read(int(self.headers["Content-Length"])).decode()
                    ).items()
                }
                fixture.requests.append((self.path, data, self.headers["content-type"]))
                if fixture.behavior == "wait":
                    fixture.received.set()
                    fixture.resume.wait(5)
                version = fixture.version
                fixture.version += 1
                status = 200
                result = {}
                if fixture.behavior == "unconfirmed":
                    status = 503
                    result = {"error": "temporarily_unavailable"}
                elif self.path == "/token/":
                    result = {
                        "access_token": f"demo-access-secret-{version}",
                        "refresh_token": f"demo-refresh-secret-{version}",
                        "open_id": (
                            "other" if fixture.behavior == "wrong_user" else "sample-open-id"
                        ),
                        "token_type": "bearer",
                        "scope": "user.info.basic,video.list"
                        + (",video.publish" if fixture.behavior == "extra_scope" else ""),
                        "expires_in": 120,
                        "refresh_expires_in": 600,
                    }
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        origin = patch.object(login, "PLATFORM", f"http://127.0.0.1:{srv.server_port}/")
        origin.start()
        self.addCleanup(origin.stop)

    def status(self):
        return self.browser.get(self.url, params={"project_id": self.pid}).json()["data"]

    def action(self, action, expected=200, revision=None):
        rev = self.status()["revision"] if revision is None else revision
        res = self.browser.post(
            self.url + "/" + action, json={"project_id": self.pid, "expected_revision": rev}
        )
        self.assertEqual(res.status_code, expected, res.text)
        return res.json()

    def start(self):
        data = self.action("start")["data"]
        parsed = urlsplit(data["url"])
        self.assertEqual(parsed.netloc, "www.tiktok.com")
        fields = parse_qs(parsed.query)
        self.assertEqual(set(fields["scope"][0].split(",")), {"user.info.basic", "video.list"})
        self.assertEqual(fields["redirect_uri"], [self.redirect])
        return fields["state"][0]

    def callback(self, state, **kw):
        return self.browser.get(login.CALLBACK, params={"state": state, **kw})

    def test_form_exchange_rotation_revocation_and_encrypted_storage(self):
        state = self.start()
        bogus = TestClient(app, base_url="https://testserver", follow_redirects=False)
        self.assertEqual(
            bogus.get(login.CALLBACK, params={"state": state, "code": "foreign"}).status_code, 403
        )
        result = self.callback(state, code="one-time-test-grant")
        self.assertEqual(result.status_code, 303, result.text)
        self.assertEqual(result.headers["referrer-policy"], "no-referrer")
        self.assertEqual(self.callback(state, code="replay").status_code, 403)
        self.assertEqual(len(self.requests), 1)
        path, form, content = self.requests[0]
        self.assertEqual(
            (path, form["grant_type"], form["code"], form["redirect_uri"], content),
            (
                "/token/",
                "authorization_code",
                "one-time-test-grant",
                self.redirect,
                "application/x-www-form-urlencoded",
            ),
        )
        self.assertEqual(
            tiktok_metrics.binding(LOCAL_ACTOR, *self.key[1:]),
            ("sample-open-id", "demo-access-secret-1"),
        )
        self.action("refresh")
        self.action("refresh")
        self.assertEqual(
            [r[1].get("refresh_token") for r in self.requests[1:]],
            ["demo-refresh-secret-1", "demo-refresh-secret-2"],
        )
        self.action("refresh", 409, revision=1)
        with self.store.connection() as db:
            saved = "\n".join(db.iterdump())
        for value in [
            "demo-access-secret",
            "demo-refresh-secret",
            "sample-client-secret",
            "one-time-test-grant",
            state,
        ]:
            self.assertNotIn(value, saved)
        self.assertNotIn("token", json.dumps(self.status()))
        self.behavior = "unconfirmed"
        self.action("revoke", 502)
        self.assertEqual(self.status()["state"], "revoke_unconfirmed")
        with self.assertRaisesRegex(Exception, "不能用于读取"):
            tiktok_metrics.binding(LOCAL_ACTOR, *self.key[1:])
        self.behavior = "ok"
        self.action("revoke")
        self.assertEqual(self.requests[-1][0], "/revoke/")
        self.assertEqual(self.requests[-1][1]["token"], "demo-access-secret-3")
        self.assertIsNone(login.row_for(self.key)["secret"])
        self.assertEqual(self.status()["state"], "disconnected")

    def test_expiry_origin_roles_subject_scope_refusal_and_no_external_replay(self):
        plain = self.client.post(
            self.url + "/start", json={"project_id": self.pid, "expected_revision": 0}
        )
        self.assertEqual(plain.status_code, 403)
        stale = self.start()
        with self.store.connection() as db:
            db.execute("UPDATE tiktok_login_states SET expires=0")
        self.assertEqual(self.callback(stale, code="expired").status_code, 403)
        self.assertEqual(len(self.requests), 0)
        first = self.start()
        self.callback(first, error="access_denied")
        self.assertEqual(self.status()["state"], "denied")
        for v in ("extra_scope", "wrong_user"):
            self.behavior = v
            state = self.start()
            self.callback(state, code="fixture")
            self.assertEqual(self.status()["state"], "authorization_failed")
            self.assertIsNone(login.row_for(self.key)["secret"])
        with patch.dict(
            os.environ,
            {
                "AI_SHIP_MODE": "team",
                "AI_SHIP_API_KEYS": json.dumps(
                    {"demo-viewer": {"tenant_id": "local", "actor_id": "viewer", "role": "viewer"}}
                ),
            },
        ):
            res = self.browser.post(
                self.url + "/start",
                json={"project_id": self.pid, "expected_revision": self.status_local_rev()},
                headers={"Authorization": "Bearer demo-viewer"},
            )
            self.assertEqual(res.status_code, 403)
        log = logging.LogRecord(
            "uvicorn.access",
            20,
            "",
            0,
            "%s - %s %s %s %s",
            ("ip", "GET", login.CALLBACK + "?code=private&state=secret", "1.1", 303),
            None,
        )
        self.assertTrue(login.CallbackLog().filter(log))
        self.assertNotIn("private", log.getMessage())

    def status_local_rev(self):
        return login.row_for(self.key)["revision"]

    def test_refresh_claim_fences_concurrent_rotation(self):
        self.callback(self.start(), code="fixture")
        rev = self.status()["revision"]
        self.behavior = "wait"
        result = []
        worker = Thread(
            target=lambda: result.append(
                self.browser.post(
                    self.url + "/refresh", json={"project_id": self.pid, "expected_revision": rev}
                )
            )
        )
        worker.start()
        self.assertTrue(self.received.wait(5))
        try:
            self.action("refresh", 409, revision=rev)
        finally:
            self.resume.set()
            worker.join(5)
        self.assertEqual(result[0].status_code, 200)
        self.assertEqual(len(self.requests), 2)

    def test_assignment_drift_auto_refresh_and_expired_grant_can_reauthorize(self):
        state = self.start()
        changed = [
            {
                "tenant_id": "local",
                "project_id": self.pid,
                "account_id": self.a["id"],
                "open_id": "different-identity",
            }
        ]
        with patch.dict(os.environ, {"AI_SHIP_TIKTOK_LOGIN_ACCOUNTS": json.dumps(changed)}):
            self.assertEqual(self.callback(state, code="wrong-binding").status_code, 403)
        self.assertFalse(self.requests)
        self.callback(state, code="ok")
        with self.store.connection() as db:
            db.execute("UPDATE tiktok_login_vault SET access_until=1")
        identity, token = tiktok_metrics.binding(LOCAL_ACTOR, *self.key[1:])
        self.assertEqual((identity, token), ("sample-open-id", "demo-access-secret-2"))
        with self.store.connection() as db:
            db.execute("UPDATE tiktok_login_vault SET access_until=1,refresh_until=2")
        self.assertEqual(self.status()["state"], "expired")
        self.callback(self.start(), code="new-consent")
        self.assertEqual(self.status()["state"], "active")
        self.assertEqual(len(self.requests), 3)
        self.assertIn("exchanging", [h["state"] for h in self.status()["history"]])
