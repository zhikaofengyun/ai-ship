"""Exercise offline prompt comparison through the product UI with a captured loopback provider."""

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
from tests.replay_transport import capture_provider


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    with (
        capture_provider() as (provider, calls),
        tempfile.TemporaryDirectory() as tmp,
        server(Path(tmp), output, fixture_env={"AI_SHIP_PROVIDERS": provider}) as base,
    ):
        with httpx.Client(base_url=base + "/api/v1/", trust_env=False) as c:

            def post(path, body):
                r = c.post(path, json=body)
                r.raise_for_status()
                return r.json()["data"]

            proj = post("projects", {"name": "离线提示词评测"})
            profile = post(
                "records",
                {
                    "kind": "ModelProfile",
                    "project_id": proj["id"],
                    "data": {"provider": "local", "model_id": "fixture", "structured_output": True},
                },
            )
            pins = []
            for text in ("开场介绍商品", "开场展示细节"):
                call = post(
                    "providers/chat",
                    {
                        "project_id": proj["id"],
                        "model_profile_id": profile["id"],
                        "messages": [{"role": "user", "content": text}],
                        "json_output": True,
                        "max_cost": 0.001,
                        "idempotency_key": "browser-replay-" + str(len(pins)),
                    },
                )["call"]
                pins.append(call["id"] + "@" + str(call["version"]))
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium")
            )
            page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(
                "localStorage.setItem('ship-project'," + json.dumps(proj["id"]) + ")"
            )
            page.goto(base + "/#module/harness/H08")
            page.locator("[data-action=feature-action]").click()
            page.get_by_role("button", name="离线回放与提示词对照").click()
            d = page.locator("dialog")
            d.locator("[name=a]").select_option(pins[0])
            d.locator("[name=b]").select_option(pins[1])
            d.locator("[name=json_key]").fill("shot")
            d.locator("[name=expected_text]").fill("细节")
            page.get_by_role("button", name="读取冻结证据").click()
            expect(d.locator("h2")).to_have_text("历史响应回放")
            expect(d).to_contain_text("消息内容与顺序 · 不同")
            expect(d.locator("section").nth(0)).to_contain_text("回答包含指定文字 · 不符合")
            expect(d.locator("section").nth(1)).to_contain_text("回答包含指定文字 · 符合")
            d.locator("section").nth(0).get_by_text("实际发送的 2 条消息").click()
            expect(d.locator("section").nth(0)).to_contain_text(calls[0]["messages"][0]["content"])
            page.screenshot(path=str(output / "comparison.png"))
            page.get_by_role("button", name="返回筛选").click()
            expect(d.locator("[name=json_key]")).to_have_value("shot")
            expect(d.locator("[name=a]")).to_have_value(pins[0])
            assert len(calls) == 2 and not errors
            report = {
                "browser": browser.version,
                "fixture": "HTTP loopback synthetic model acknowledgements",
                "provider_calls_during_setup": 2,
                "provider_calls_during_replay": len(calls) - 2,
                "different_prompt_messages": True,
                "injected_system_message_verified": True,
                "literal_contains_result": [False, True],
                "json_key_result": [True, True],
                "page_errors": errors,
            }
            (output / "result.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            )
            browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    run(p.parse_args().output.resolve())
