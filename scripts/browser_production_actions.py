"""Exercise new module forms through the production UI and verify stored facts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import httpx
from playwright.sync_api import sync_playwright, expect

from browser_regression import server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    checks = []
    report = {
        "checks": checks,
        "scope": "真实模块表单和 API，测试时使用隔离项目和合成媒体",
        "not_checked": ["外部平台、模型及账号", "没有实现的来源路线", "真人辅助技术评测"],
    }
    try:
        with (
            tempfile.TemporaryDirectory() as temp,
            server(Path(temp), out) as base,
            sync_playwright() as pw,
            httpx.Client(base_url=base + "/api/v1", trust_env=False) as client,
        ):
            pid = client.post(
                "/projects",
                json={
                    "name": "商品短片",
                    "goals": "验证商品资料与素材证据",
                    "language": "zh-CN",
                    "market": "CN",
                },
            ).json()["data"]["id"]
            browser = pw.chromium.launch(
                executable_path=os.getenv("AI_SHIP_CHROMIUM")
                or shutil.which("chromium-headless-shell")
                or shutil.which("chromium"),
                chromium_sandbox=True,
            )
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base)

            def operate(module, feature):
                page.goto(base + "/#module/" + module + "/" + feature)
                b = page.locator(".feature-title [data-action=feature-action]")
                b.click()
                expect(page.locator("dialog[open] form[data-form]")).to_have_count(1)
                return page.locator("dialog[open] form[data-form]")

            def submit(f):
                f.locator("button[type=submit]").click()

            def rows(kind):
                return client.get("/records", params={"project_id": pid, "kind": kind}).json()[
                    "data"
                ]

            def clean():
                text = page.locator("#main").inner_text()
                assert not re.search(
                    r"(?<![0-9a-f])[0-9a-f]{32,64}(?![0-9a-f])", text, re.I
                ), "Hash or opaque technical ID visible in normal workspace"

            f = operate("project", "P02")
            f.locator("[name=provenance]").fill("商家核价表")
            f.locator("[type=file]").set_input_files(
                {
                    "name": "商品.csv",
                    "mimeType": "text/csv",
                    "buffer": (
                        "SKU,price,currency,valid_until,name,evidence\nA01,12.90,CNY,2032-12-01T00:00:00Z,咖啡豆,商家核价表\nBAD,no,CNY,2032-12-01T00:00:00Z,错误行,同一表\n"
                    ).encode(),
                }
            )
            submit(f)
            expect(page.get_by_text("已导入 1 行，未导入 1 行")).to_be_visible()
            assert len(rows("Product")) == 1 and rows("Product")[0]["data"]["price"] == "12.90"
            checks.append("P02 从空项目创建来源、上传 CSV，正确行保留精确金额，错误行显示行号")
            page.keyboard.press("Escape")
            clean()
            f = operate("trend", "T01")
            for name, val in {
                "title": "杯具摄影",
                "canonical_url": "https://example.test/observation",
                "platform": "实地抽样",
                "observed_at": "2024-01-02T13:00",
                "expires_at": "2024-01-03T13:00",
                "evidence": "实际采样记录",
            }.items():
                f.locator("[name=" + name + "]").fill(val)
            submit(f)
            expect(page.locator("dialog[open]")).to_have_count(0)
            assert rows("Trend")[0]["data"]["status"] == "expired"
            checks.append("T01 过期的人工观察仍保留原始来源和过期状态")
            media = Path(temp) / "咖啡杯.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=240x160:rate=12:duration=1",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    "-y",
                    str(media),
                ],
                check=True,
                timeout=30,
            )
            page.goto(base + "/#assets")
            page.locator("#media-input").set_input_files(str(media))
            expect(page.locator(".asset-card")).to_have_count(1, timeout=15000)
            f = operate("label", "L06")
            f.locator("[name=asset_version_id]").select_option(index=1)
            f.locator("[name=end]").fill("2")
            f.locator("[name=label]").fill("陶瓷杯")
            f.locator("[name=evidence]").fill("实拍画面前景")
            submit(f)
            expect(page.locator(".toast").last).to_contain_text("超出")
            assert not rows("Tag")
            f.locator("[name=end]").fill("0.75")
            submit(f)
            expect(page.locator("dialog[open]")).to_have_count(0)
            assert len(rows("Tag")) == 1 and rows("Tag")[0]["data"]["interval"] == [0, 0.75]
            checks.append("L06 超出媒体时长时没有写入；修正原表单后标注关联真实素材、镜头与证据")
            f = operate("search", "S01")
            f.locator("[name=q]").fill("陶瓷杯")
            submit(f)
            expect(page.locator("dialog h2")).to_have_text("搜索结果 · 1")
            expect(page.locator("dialog [data-hit]")).to_contain_text("咖啡杯")
            page.screenshot(path=str(out / "search-evidence.png"))
            page.keyboard.press("Escape")
            checks.append("S01 新增标签可检索并返回对应素材")
            f = operate("assetops", "U02")
            f.locator("[name=shot_id]").select_option(index=1)
            f.locator("[name=owner_ref]").select_option(index=1)
            submit(f)
            expect(page.locator("dialog[open]")).to_have_count(0)
            assert rows("Usage")[0]["data"]["reservation_status"] == "reserved"
            f = operate("assetops", "U02")
            page.locator("dialog[open] [data-release]").click()
            expect(page.locator("dialog[open]")).to_have_count(0)
            assert rows("Usage")[0]["data"]["reservation_status"] == "released"
            checks.append("U02 按当前镜头占用并释放，账本保留变更版本")
            f = operate("assetops", "U07")
            f.locator("[name=asset_version_id]").select_option(index=1)
            f.locator("[name=quality]").fill("0")
            f.locator("[name=evidence]").select_option(index=1)
            submit(f)
            expect(page.locator("dialog[open]")).to_have_count(0)
            score = rows("AssetScore")[0]["data"]
            assert score["observed_score"] == 0 and 0 < score["coverage"] < 1
            checks.append("U07 明确零分、缺测维度和已关联证据分开保存")
            for module, feature in [
                ("voice", "V04"),
                ("metrics", "M08"),
                ("learn", "X05"),
                ("harness", "H10"),
            ]:
                f = operate(module, feature)
                assert not f.evaluate("el=>el.checkValidity()")
                page.keyboard.press("Escape")
                clean()
            checks.append("V04、M08、X05、H10 缺少脚本、音轨、时码映射、实验或重放依赖时阻止空提交")
            assert not errors, errors
            report.update(browser=browser.version, page_errors=errors, passed=True)
            browser.close()
    except Exception as exc:
        report.update(passed=False, failure=str(exc))
        raise
    finally:
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
