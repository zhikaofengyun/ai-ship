"""Exercise creative matching, director proposals and a scoped structural check from the UI."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with TemporaryDirectory() as tmp, server(Path(tmp), output) as base, sync_playwright() as pw:
        browser = pw.webkit.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda e: errors.append(str(e)))
        dialog = page.locator("#dialog[open]")
        page.goto(base)
        page.locator('[data-action="new-project"]').first.click()
        dialog.locator("[name=name]").fill("创意工作流回归")
        dialog.locator("[name=goals]").fill("通勤杯子使用演示")
        dialog.locator("form[data-form] button[type=submit]").click()
        expect(dialog).to_have_count(0)

        def open_feature(fid, label):
            page.goto(base + "/#module/trend/" + fid)
            page.get_by_role("button", name=label, exact=True).click()

        open_feature("T01", "记录热点来源")
        for key, value in {
            "title": "通勤观察",
            "canonical_url": "https://example.test/news",
            "platform": "手工观察",
            "observed_at": "2025-02-02T12:00",
            "expires_at": "2099-02-02T12:00",
            "evidence": "现场记录",
        }.items():
            dialog.locator(f"[name={key}]").fill(value)
        dialog.get_by_role("button", name="保存观察").click()
        expect(dialog).to_have_count(0)
        open_feature("T02", "核验热点出处")
        for key, value in {
            "original0": "https://example.test/news",
            "publisher0": "同一编辑组",
            "published0": "2025-02-01T12:00",
            "observed0": "2025-02-02T12:00",
            "note0": "单一出处",
        }.items():
            dialog.locator(f"[name={key}]").fill(value)
        dialog.get_by_role("button", name="核对并保存版本").click()
        expect(dialog).to_contain_text("独立来源不足")
        dialog.get_by_role("button", name="关闭").click()

        open_feature("T03", "匹配主题与受众")
        for key, value in {
            "topic": "通勤杯子",
            "audience": "通勤用户",
            "rationale": "工作日带水",
        }.items():
            dialog.locator("[name=" + key + "]").fill(value)
        dialog.get_by_role("button", name="匹配并保存候选").click()
        expect(dialog.get_by_role("heading")).to_contain_text("匹配依据")
        expect(dialog).to_contain_text("通勤")
        dialog.get_by_role("button", name="关闭").click()

        open_feature("T04", "编排导演路线")
        for i, words in enumerate(
            [
                [
                    "开箱",
                    "拆包",
                    "问题\n演示",
                    "杯身\n杯盖",
                    "环境音",
                    "拆开看看",
                    "认识杯子",
                    "实物在手",
                ],
                [
                    "讲解",
                    "测量",
                    "刻度\n容量",
                    "标尺\n杯盖",
                    "口播",
                    "量一下容量",
                    "比较场景",
                    "标尺在手",
                ],
            ]
        ):
            for k, v in zip(
                (
                    "name",
                    "hook",
                    "narrative",
                    "shot_families",
                    "sound",
                    "script",
                    "hypothesis",
                    "feasibility",
                ),
                words,
            ):
                dialog.locator(f"[name={k}{i}]").fill(v)
        dialog.get_by_role("button", name="保存候选与脚本").click()
        expect(dialog).to_have_count(0)
        open_feature("T05", "检查创意重复")
        dialog.locator("[name=brief]").select_option(label="讲解 · v1")
        dialog.get_by_role("checkbox", name="开箱", exact=True).check()
        dialog.locator("[name=threshold]").fill("0.5")
        dialog.get_by_role("button", name="检查结构与用词").click()
        expect(dialog.get_by_role("heading").first).to_have_text("预检结果")
        expect(dialog).to_contain_text("杯盖")
        with httpx.Client(base_url=base + "/api/v1", trust_env=False) as client:
            pid = client.get("/projects").json()["data"][0]["id"]
            items = client.get("/records", params={"project_id": pid, "kind": "Insight"}).json()[
                "data"
            ]
            assert (
                len(items) == 1
                and items[0]["data"]["evidence"][0]["scores"]["shot_families"] == 0.5
            )
        page.screenshot(path=str(output / "creative-preflight.png"))
        dialog.get_by_role("button", name="关闭").click()
        page.goto(base + "/#module/acquire/A06")
        page.get_by_role("button", name="核对来源变化", exact=True).click()
        dialog.locator("[name=change0]").select_option("origin_deleted")
        dialog.locator("[name=when0]").fill("2025-03-01T15:00")
        dialog.locator("[name=evidence0]").fill("人工核对：原出处已移除")
        dialog.locator("[name=confirmed0]").check()
        dialog.get_by_role("button", name="处理清单").click()
        expect(dialog).to_contain_text("已更新")
        with httpx.Client(base_url=base + "/api/v1", trust_env=False) as client:
            sources = client.get("/records", params={"kind": "Source", "project_id": pid}).json()[
                "data"
            ]
            assert sources[0]["data"]["origin_availability"] == "removed_at_origin"
            assert not sources[0]["data"].get("revoked_at")
        browser.close()
        assert not errors, errors
        result = {
            "browser": "webkit",
            "passed": True,
            "features": ["A06", "T01", "T02", "T03", "T04", "T05"],
            "errors": errors,
        }
        (output / "report.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
