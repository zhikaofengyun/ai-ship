"""Exercise the native Login Kit UI on local TLS. All TikTok responses are loopback fixtures."""

import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit, urlencode

from cryptography.fernet import Fernet
import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.store import get_store


def run(output):
    output.mkdir(exist_ok=True, parents=True)
    errors = []
    operations = []
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        temp = Path(tmp)
        cert = temp / "cert.pem"
        priv = temp / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
                "-keyout",
                str(priv),
                "-out",
                str(cert),
            ],
            capture_output=True,
            check=True,
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"https://127.0.0.1:{port}"
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "AI_SHIP_MODE": "local",
                    "AI_SHIP_DATA_DIR": tmp,
                    "AI_SHIP_DB": str(temp / "test.sqlite"),
                },
            )
        )
        store = get_store()
        pid = store.create("Project", {"name": "本地授权演练"}, tenant_id="local")["id"]
        aid = store.create(
            "Account",
            {"name": "测试 TikTok 商号", "provider": "tiktok"},
            tenant_id="local",
            project_id=pid,
        )["id"]

        class Platform(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                op = fields.get("grant_type", ["revoke"])[0]
                operations.append(op)
                n = len(operations)
                data = (
                    {}
                    if op == "revoke"
                    else {
                        "access_token": f"fixture-at-{n}",
                        "refresh_token": f"fixture-rt-{n}",
                        "open_id": "expected",
                        "token_type": "Bearer",
                        "scope": "user.info.basic,video.list",
                        "expires_in": 3600,
                        "refresh_expires_in": 86400,
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

        mock = ThreadingHTTPServer(("127.0.0.1", 0), Platform)
        Thread(target=mock.serve_forever, daemon=True).start()
        stack.callback(mock.server_close)
        stack.callback(mock.shutdown)
        callback = "/api/v1/production/tiktok/login/callback"
        config = {
            **os.environ,
            "AI_SHIP_TIKTOK_LOGIN_ACCOUNTS": json.dumps(
                [
                    {
                        "tenant_id": "local",
                        "project_id": pid,
                        "account_id": aid,
                        "open_id": "expected",
                    }
                ]
            ),
            "AI_SHIP_TIKTOK_CLIENT_KEY": "fixture-key",
            "AI_SHIP_TIKTOK_CLIENT_SECRET": "private-fixture-client",
            "AI_SHIP_TIKTOK_REDIRECT_URI": origin + callback,
            "AI_SHIP_TIKTOK_FERNET_KEY": Fernet.generate_key().decode(),
        }
        launch = "import sys,uvicorn;import app.tiktok_login as l;l.PLATFORM=sys.argv[1];uvicorn.run('app.main:app',host='127.0.0.1',port=int(sys.argv[2]),ssl_keyfile=sys.argv[3],ssl_certfile=sys.argv[4])"
        log = stack.enter_context((output / "server.log").open("w"))
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                launch,
                f"http://127.0.0.1:{mock.server_port}/",
                str(port),
                str(priv),
                str(cert),
            ],
            env=config,
            stdout=log,
            stderr=log,
            cwd=ROOT,
        )

        def stop():
            process.terminate()
            process.wait(10)

        stack.callback(stop)
        with httpx.Client(verify=False, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(origin + "/api/v1/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Local TLS server did not start")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium")
            )
            page = browser.new_page(
                ignore_https_errors=True, locale="zh-CN", viewport={"width": 1280, "height": 900}
            )
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script("localStorage.setItem('ship-project'," + json.dumps(pid) + ")")
            states = []

            def authorize(route):
                query = parse_qs(urlsplit(route.request.url).query)
                assert query["redirect_uri"] == [origin + callback]
                assert set(query["scope"][0].split(",")) == {"user.info.basic", "video.list"}
                states.append(query["state"][0])
                route.fulfill(
                    status=302,
                    headers={
                        "Location": origin
                        + callback
                        + "?"
                        + urlencode({"code": "fixture-callback-code", "state": states[-1]})
                    },
                )

            page.route("https://www.tiktok.com/v2/auth/authorize/**", authorize)
            page.goto(origin + "/#module/account/J01")

            def account():
                page.get_by_role("button", name="管理账号", exact=True).click()
                page.get_by_role("button", name="只读连接", exact=True).click()

            account()
            with page.expect_response(lambda r: r.url.startswith(origin + callback)):
                page.get_by_role("button", name="前往 TikTok 授权").click()
            page.wait_for_url("**/#module/account/J01")
            account()
            expect(page.locator("[data-login-panel]")).to_contain_text("只读凭据已保存")
            page.get_by_role("button", name="刷新只读授权").click()
            expect(page.get_by_role("button", name="解除 TikTok 授权")).to_be_enabled()
            page.screenshot(path=output / "read-authorization.png")
            page.once("dialog", lambda d: d.accept())
            page.get_by_role("button", name="解除 TikTok 授权").click()
            expect(page.locator("[data-login-panel]")).to_contain_text("本地已解绑")
            assert not errors, errors
            browser.close()
        assert operations == ["authorization_code", "refresh_token", "revoke"], operations
        log.flush()
        logs = (output / "server.log").read_text()
        assert all(
            x not in logs
            for x in [
                "fixture-callback-code",
                "fixture-at-",
                "fixture-rt-",
                "private-fixture-client",
                *states,
            ]
        )
        result = {
            "checks": [
                "normal account UI",
                "real local HTTPS cookie through browser redirect",
                "state-bound callback",
                "encrypted grant -> refresh -> revoke",
                "callback query and token material absent from Uvicorn access log",
            ],
            "network": "localhost token protocol fixture; authorization navigation intercepted before external network",
            "page_errors": errors,
            "operations": operations,
        }
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
