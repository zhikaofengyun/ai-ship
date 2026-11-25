"""Operate Atlas source sections and a real standalone feature from the normal UI."""

import argparse
import json
import os
import io
import math
import struct
import wave
import re
import shutil
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(exist_ok=True, parents=True)
    checks, errors = [], []
    with (
        tempfile.TemporaryDirectory(prefix="ship-workspace-actions-") as tmp,
        server(Path(tmp), output) as base,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch(
            executable_path=os.getenv("AI_SHIP_CHROMIUM")
            or shutil.which("chromium-headless-shell")
            or shutil.which("chromium"),
            chromium_sandbox=True,
        )
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="domcontentloaded")
        expect(page.locator("#main h1")).to_have_text("项目")
        expect(page.locator(".nav-cluster")).to_have_count(8)
        expect(page.locator("[data-module-link]")).to_have_count(23)
        page.locator("[data-action=new-project]").click()
        form = page.locator("#dialog form[data-form]")
        form.locator("[name=name]").fill("分区操作回归")
        form.locator("button[type=submit]").click()
        expect(page).to_have_url(re.compile("#production$"))
        expect(page.locator("#production-goal")).to_be_visible()
        expect(page.locator(".context-label")).to_have_text("分区操作回归")
        checks.append("empty_workspace_create_opens_ai_production")
        page.goto(base + "/#module/project/P03", wait_until="domcontentloaded")
        page.locator("[data-action=feature-action]").click()
        expect(form.locator("[name=name]")).to_have_value("分区操作回归")
        form.locator("[name=language]").fill("zh-Hant")
        form.locator("button[type=submit]").click()
        expect(page.locator("#dialog[open]")).to_have_count(0)
        client = httpx.Client(base_url=base + "/api/v1", trust_env=False)
        project = client.get("/projects").json()["data"][0]
        assert project["data"]["language"] == "zh-Hant" and project["version"] == 2
        checks.append("project_specific_action_edits_existing_version")

        page.goto(base + "/#module/trend/T04", wait_until="domcontentloaded")
        expect(page.locator(".feature-detail h2")).to_have_text("多路线创意")
        expect(page.locator("[data-action=run-feature]")).to_be_hidden()
        expect(page.locator("#feature-state")).to_contain_text("专项操作")
        expect(page.locator("[data-action=feature-action]")).to_be_visible()
        checks.append("creative_feature_exposes_its_dedicated_action")
        page.goto(base + "/#module/acquire/A03", wait_until="domcontentloaded")
        expect(page.locator(".feature-detail h2")).to_have_text("采集适配器管理")
        expect(page.locator("#feature-state")).to_contain_text("专项操作")
        expect(page.locator("[data-action=feature-action]")).to_have_text("管理素材来源")
        checks.append("acquisition_feature_exposes_its_project_source_settings")
        page.goto(base + "/#module/acquire/A04", wait_until="domcontentloaded")
        expect(page.locator(".feature-detail h2")).to_have_text("来源与权利核验")
        expect(page.locator("[data-action=run-feature]")).to_be_hidden()
        expect(page.locator("#feature-state")).to_contain_text("仅支持配置")
        checks.append("unsupported_route_truthful_configuration_status")

        page.goto(base + "/#module/voice/V01", wait_until="domcontentloaded")
        page.select_option("#feature-route", "V01-R3")
        prompt = page.locator("#feature-execution-fields textarea[data-execution-field]")
        expect(prompt).to_be_visible()
        prompt.fill("玻璃杯放在木桌上。")
        page.locator("[data-action=run-feature]").click()
        expect(page.locator("#feature-result h3")).to_have_text("已完成", timeout=15000)
        scripts = client.get(
            "/records", params={"project_id": project["id"], "kind": "Script"}
        ).json()["data"]
        assert len(scripts) == 1 and scripts[0]["data"]["text"] == "玻璃杯放在木桌上。"
        expect(page.locator("#feature-records [data-action=feature-record]")).to_have_count(1)
        page.reload(wait_until="domcontentloaded")
        expect(prompt).to_have_value("玻璃杯放在木桌上。")
        expect(page.locator("#feature-route")).to_have_value("V01-R3")
        page.locator("#feature-records [data-action=feature-record]").click()
        expect(page.locator("#dialog [data-record-key=text]")).to_have_value("玻璃杯放在木桌上。")
        page.locator("#dialog [data-record-key=text]").fill("玻璃杯静置在木桌上。")
        form.locator("[name=reason]").fill("文字修订")
        form.locator("button[type=submit]").click()
        expect(page.locator("#dialog[open]")).to_have_count(0)
        after = client.get("/records/" + scripts[0]["id"]).json()["data"]
        assert after["version"] == 2 and after["data"]["text"] == "玻璃杯静置在木桌上。"
        checks.extend(
            [
                "standalone_manual_script_route_executes",
                "execution_parameters_persist",
                "versioned_record_edits_without_json",
            ]
        )

        page.goto(base + "/#module/label/L07", wait_until="domcontentloaded")
        page.select_option("#feature-route", "L07-R1")
        page.locator("[data-action=run-feature]").click()
        expect(page.locator("#feature-result h3")).to_have_text("待补齐条件", timeout=15000)
        sounds = client.get(
            "/records", params={"kind": "Audio", "project_id": project["id"]}
        ).json()["data"]
        assert not sounds
        checks.append("missing_audio_binding_creates_no_fabricated_output")
        page.goto(base + "/#module/ops/O06", wait_until="domcontentloaded")
        page.locator("[data-action=feature-action]").click()
        blocked_run = page.locator("#dialog [data-inspect-run]").filter(has_text="原声提取")
        blocked_run.click()
        expect(page.locator("#dialog h3")).to_have_text("执行步骤")
        expect(page.locator("#dialog")).to_contain_text("待补齐条件")
        expect(page.locator("#dialog [data-action=resume]")).to_be_visible()
        page.locator("#dialog [data-action=context-pack]").click()
        expect(page.locator("#dialog h2")).to_have_text("固定任务证据")
        form.locator("[name=reason]").fill("恢复前固定运行依据")
        form.locator("button[type=submit]").click()
        expect(page.locator("#dialog h2")).to_have_text("任务恢复证据")
        expect(page.locator("#dialog")).to_contain_text("核对尚未完成的条件")
        expect(page.locator("#dialog")).to_contain_text("恢复前固定运行依据")
        page.screenshot(path=str(output / "context-pack.png"))
        page.locator("#dialog [data-pack-source]").first.click()
        expect(page.locator("#dialog [data-back]")).to_be_visible()
        page.locator("#dialog [data-back]").click()
        expect(page.locator("#dialog h2")).to_have_text("任务恢复证据")
        page.keyboard.press("Escape")
        page.goto(base + "/#module/harness/H04", wait_until="domcontentloaded")
        page.locator("[data-action=feature-action]").click()
        audits = client.get(
            "/records", params={"kind": "Audit", "project_id": project["id"]}
        ).json()["data"]
        pinned = next(a for a in audits if a["data"].get("action") == "pin_context")
        form.locator("[name=run]").select_option(
            pinned["data"]["context_packet"]["source_run"]["id"]
        )
        form.locator("[data-read-pack]").click()
        expect(page.locator("#dialog h2")).to_have_text("任务恢复证据")
        page.keyboard.press("Escape")
        checks.append("recover_blocked_workflow_pin_and_read_audited_context_from_feature")

        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(
                b"".join(
                    struct.pack(
                        "<h",
                        (
                            int(math.sin(i * 2 * math.pi * 320 / 8000) * 19000)
                            if i % 2000 > 1000
                            else 0
                        ),
                    )
                    for i in range(12000)
                )
            )
        res = client.post(
            "/media/import",
            params={"project_id": project["id"], "filename": "脉冲.wav"},
            content=wav.getvalue(),
        )
        res.raise_for_status()
        page.goto(base + "/#module/music/B01", wait_until="domcontentloaded")
        page.locator("[data-action=feature-action]").click()
        form.locator("button[type=submit]").click()
        expect(page.locator("#dialog h2")).to_have_text("声音起音候选", timeout=20000)
        marker = page.locator("#dialog [data-moment]").first
        expect(marker).to_be_visible()
        page.wait_for_function("document.querySelector('#dialog audio')?.readyState>=1")
        marker.click()
        assert (
            abs(
                page.locator("#dialog audio").evaluate("el=>el.currentTime")
                - float(marker.get_attribute("data-moment"))
            )
            < 0.02
        )
        music = client.get(
            "/records", params={"kind": "Music", "project_id": project["id"]}
        ).json()["data"]
        assert music[0]["data"]["onset_candidates"] and music[0]["data"]["beat_grid"] is None
        page.keyboard.press("Escape")
        checks.append("coarse_audio_onsets_persist_and_seek_real_audio")

        page.goto(base + "/#workflow", wait_until="domcontentloaded")
        expect(page.locator("#workflow-name")).to_have_value("未命名工作流")
        checks.append("single_feature_run_does_not_replace_user_workflow")
        page.keyboard.press("Control+k")
        page.locator("#global-search").fill("商品事实与主张")
        page.locator("#global-results [data-feature=P02]").click()
        expect(page).to_have_url(re.compile("#module/project/P02$"))
        expect(page.locator(".feature-detail h2")).to_have_text("商品事实与主张")
        assert not re.search(
            r"\b(?:[0-9a-f]{32,}|[0-9a-f]{8}-[0-9a-f-]{27,})\b",
            page.locator("#app").inner_text(),
            re.I,
        )
        page.screenshot(path=str(output / "workspace-project-facts.png"))
        checks.append("palette_deep_link_without_technical_identifiers")
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("[data-global=menu]").click()
        group = page.locator(".nav-cluster[data-cluster=voice]")
        if group.get_attribute("open") is None:
            group.locator("summary").click()
        page.locator("[data-module-link=music]").click()
        expect(page.locator("#main h1")).to_have_text("BGM与声音")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "workspace-mobile-module.png"))
        checks.append("mobile_source_section_opens_and_fits")
        assert not errors, errors
        checks.append("no_unhandled_page_errors")
        result = {
            "passed": True,
            "browser": browser.version,
            "checks": checks,
            "page_errors": errors,
        }
        (output / "workspace-actions.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(result, ensure_ascii=False))
        client.close()
        browser.close()


if __name__ == "__main__":
    main()
