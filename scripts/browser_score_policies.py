"""Exercise the scoring-policy release controls against a real isolated server."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, type=Path)
    out = ap.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory() as tmp,
        server(Path(tmp), out) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False) as api,
    ):

        def post(path, **data):
            res = api.post(path, json=data)
            res.raise_for_status()
            return res.json()["data"]

        project = post("/projects", name="素材策略测试")
        pid = project["id"]
        ids = []
        for i in range(4):
            asset = post(
                "/records",
                kind="AssetVersion",
                project_id=pid,
                data={"name": f"样片 {i}"},
                reason="隔离测试",
            )
            preferred = not i % 2
            dims = dict.fromkeys(
                (
                    "quality",
                    "relevance",
                    "fatigue",
                    "confidence",
                    "tag_completeness",
                    "performance",
                ),
                0.2 if preferred else 0.8,
            )
            dims["quality"] = 0.9 if preferred else 0.3
            score = post(
                "/production/assets/score",
                project_id=pid,
                asset_version_id=asset["id"],
                dimensions=dims,
            )
            ids.append(score["id"])
        browser = pw.chromium.launch(
            executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium"),
            chromium_sandbox=True,
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base)
            dlg = page.locator("dialog[open]")

            def feature(code):
                page.goto(base + "/#module/learn/" + code)
                page.locator(".feature-title [data-action=feature-action]").click()
                expect(dlg).to_be_visible()

            def submit():
                dlg.locator("form[data-form] button[type=submit]").click()

            feature("X02")
            dlg.locator("[name=name]").fill("画质优先")
            dlg.locator("[name=hypothesis]").fill("测试清晰度优先策略")
            for key in ("relevance", "fatigue", "confidence", "tag_completeness", "performance"):
                dlg.locator(f"[name={key}]").fill("0")
            submit()
            expect(dlg).to_have_count(0)
            feature("X03")
            dlg.locator("[name=candidate]").select_option(index=1)
            for i in range(2):
                row = dlg.locator("[data-case]").nth(i)
                row.locator("[name=preferred]").select_option(ids[i * 2])
                row.locator("[name=comparison]").select_option(ids[i * 2 + 1])
                row.locator("[name=reason]").fill("同对中经过人工核定的清晰度优先")
            submit()
            expect(dlg.get_by_role("status")).to_contain_text("通过")
            dlg.get_by_role("button", name="关闭", exact=True).click()
            feature("X06")
            dlg.locator("[data-approve]").click()
            dlg.locator("[name=exp]").select_option(index=1)
            dlg.locator("[name=reason]").fill("仅限当前项目素材评分")
            submit()
            expect(dlg).to_have_count(0)
            feature("X06")
            dlg.locator("[data-activate]").click()
            dlg.locator("[name=approval]").select_option(index=1)
            submit()
            expect(dlg).to_have_count(0)
            live = api.get("/production/score-policies/" + pid).json()["data"]["pointer"]
            assert (
                live["version"] == 2
                and live["data"]["active"]["id"] != live["data"]["previous"]["id"]
            )
            feature("X07")
            dlg.locator("[name=reason]").fill("恢复首轮素材排序基线")
            submit()
            expect(dlg.get_by_role("status")).to_contain_text("已恢复")
            restored = api.get("/production/score-policies/" + pid).json()["data"]["pointer"]
            assert (
                restored["version"] == 3 and restored["data"]["active"] == live["data"]["previous"]
            )
            assert not errors, errors
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "features": ["X02", "X03", "X06", "X07"],
                        "pointer_versions": [1, 2, 3],
                        "page_errors": errors,
                    }
                )
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
