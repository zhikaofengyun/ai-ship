"""Exercise the manual handoff window from J05 and from publication preflight."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server, ROOT

sys.path.insert(0, str(ROOT))
from app.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory() as temp,
        server(Path(temp), out) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False) as api,
    ):

        def post(path, **body):
            r = api.post(path, json=body)
            r.raise_for_status()
            return r.json()["data"]

        pid = post("/projects", name="发布席位验收")["id"]
        account = post(
            "/records",
            project_id=pid,
            kind="Account",
            data={"name": "合成测试账号", "provider": "manual"},
            reason="测试",
        )
        # Standard synthetic technical fixture. This test makes no platform call.
        art = Store(Path(temp) / "browser.sqlite3").create(
            "Artifact",
            {"name": "合成审核样片", "checksum": "fixture", "status": "technical_passed"},
            tenant_id="local",
            project_id=pid,
        )
        for kind in ("content_check", "rights_check"):
            post(
                "/production/approvals",
                subject_id=art["id"],
                subject_version=1,
                kind=kind,
                decision="approved",
                evidence="合成测试审核",
                expires_at="2099-01-01T00:00:00Z",
            )
        pub = post(
            "/production/publications",
            project_id=pid,
            account_id=account["id"],
            artifact_id=art["id"],
            title="测试交付",
        )
        launch = (
            {"executable_path": os.getenv("AI_SHIP_WEBKIT")}
            if args.engine == "webkit"
            else {
                "executable_path": os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium"),
                "chromium_sandbox": True,
            }
        )
        browser = getattr(pw, args.engine).launch(**launch)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/#module/account/J05")
            page.locator(".feature-title [data-action=feature-action]").click()
            dlg = page.locator("dialog[open]")
            dlg.locator("[data-slot=settings]").click()
            dlg.locator("[name=daily]").fill("3")
            dlg.locator("[name=concurrent]").fill("1")
            dlg.locator("[name=reason]").fill("内部测试的产能限制")
            dlg.locator("form[data-form] button[type=submit]").click()
            expect(dlg).to_contain_text("0 / 3")
            for step, msg in (("reserve", "已预留"), ("handoff", "已交接，等待核对")):
                dlg.locator("[data-slot=" + step + "]").click()
                dlg.locator("[name=reason]").fill("核对当前意图后执行 " + step)
                dlg.locator("form[data-form] button[type=submit]").click()
                expect(dlg.locator('[role="status"]')).to_contain_text(msg)
            dlg.get_by_role("button", name="关闭", exact=True).click()
            page.goto(base + "/#delivery")
            page.locator("[data-action=preflight]").first.click()
            dlg.locator("[data-account-quota]").click()
            expect(dlg.locator('[role="status"]')).to_contain_text("已交接，等待核对")
            dlg.locator("[data-slot=release]").click()
            dlg.locator("[name=reason]").fill("已向接手人核实，未向平台提交")
            dlg.locator("form[data-form] button[type=submit]").click()
            expect(dlg.locator('[role="status"]')).to_contain_text("已释放")
            state = api.get(
                "/production/account-quota/" + pid + "/" + account["id"],
                params={"publication_id": pub["id"]},
            ).json()["data"]
            assert (
                state["effective_state"] == "released"
                and state["receipt_count_24h"] == 0
                and not errors
            )
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "engine": args.engine,
                        "checks": [
                            "J05-settings",
                            "reserve",
                            "handoff",
                            "publication-preflight",
                            "confirmed-unsent-release",
                        ],
                        "page_errors": errors,
                    }
                )
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
