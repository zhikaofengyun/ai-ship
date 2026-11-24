"""Drive M04's consent-bounded excerpts and verify classifications drop out on withdrawal."""

import argparse
import os
import json
import shutil
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    out = ap.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory() as tmp,
        server(Path(tmp), out) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False) as client,
    ):
        resp = client.post("/projects", json={"name": "评论测试"})
        resp.raise_for_status()
        pid = resp.json()["data"]["id"]
        r = client.post(
            "/records",
            json={
                "project_id": pid,
                "kind": "Account",
                "data": {"name": "商家", "provider": "manual"},
                "reason": "test",
            },
        )
        r.raise_for_status()
        browser = pw.chromium.launch(
            executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium"),
            chromium_sandbox=True,
        )
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 855})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/#module/metrics/M04")
            dlg = page.locator("dialog[open]")

            def open_action(name):
                page.locator(".feature-title [data-action=feature-action]").click()
                dlg.locator(f"[data-{name}]").click()

            def submit():
                dlg.locator("form[data-form] button[type=submit]").click()
                expect(dlg).to_have_count(0)

            open_action("observe")
            dlg.locator("[name=account_id]").select_option(index=1)
            for key, val in {
                "platform_post_id": "desk-post",
                "platform_comment_id": "desk-comment",
                "provenance": "商家回复工作台",
                "retention_basis": "允许在本地保留节选",
                "retain_until": "2099-11-25T00:00:00Z",
                "excerpt": "<b>怎么清洁杯盖？</b>",
            }.items():
                dlg.locator("[name=" + key + "]").fill(val)
            submit()
            open_action("classify")
            dlg.locator("[name=comment]").select_option(index=1)
            expect(dlg.locator("[data-preview]")).to_have_text("<b>怎么清洁杯盖？</b>")
            expect(dlg.locator("[data-preview] b")).to_have_count(0)
            dlg.locator("[name=topic]").select_option("usage")
            dlg.locator("[name=sentiment]").select_option("uncertain")
            submit()
            open_action("summary")
            expect(dlg).to_contain_text("可用且已分类 1 条")
            expect(dlg).to_contain_text("未确定 1")
            dlg.get_by_role("button", name="关闭", exact=True).click()
            open_action("observe")
            dlg.locator("[name=existing]").select_option(index=1)
            dlg.locator("[name=provenance]").fill("人工复核已删除")
            dlg.locator("[name=retention_basis]").fill("停止保留")
            dlg.locator("[name=visibility]").select_option("deleted")
            dlg.locator("[name=unavailable_reason]").fill("作者已删除")
            submit()
            open_action("summary")
            expect(dlg).to_contain_text("可用且已分类 0 条")
            expect(dlg).to_contain_text("不可用 1 条")
            assert not errors, errors
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "features": ["M04"],
                        "checks": [
                            "record",
                            "read-escaped-body",
                            "classify",
                            "summary",
                            "withdraw",
                            "exclude-old-labels",
                        ],
                        "page_errors": errors,
                    }
                )
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
