"""Read each mutation back after using T06/N02 from an empty local workspace."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"passed": False, "checks": [], "screenshots": {}, "page_errors": []}
    try:
        with (
            tempfile.TemporaryDirectory(prefix="selection-ui-") as tmp,
            server(Path(tmp), output) as base,
            sync_playwright() as driver,
        ):
            browser = driver.chromium.launch(
                executable_path=os.environ.get("AI_SHIP_CHROMIUM")
                or shutil.which("chromium-headless-shell")
                or shutil.which("chromium"),
                headless=True,
                chromium_sandbox=True,
            )
            evidence["browser_version"] = browser.version
            page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
            page.set_default_timeout(10000)
            page.on("pageerror", lambda e: evidence["page_errors"].append(str(e)))
            client = httpx.Client(base_url=base + "/api/v1", trust_env=False)
            dialog = page.locator("dialog[open]")

            def snap(name):
                image = page.screenshot(path=str(output / (name + ".png")))
                evidence["screenshots"][name] = hashlib.sha256(image).hexdigest()

            def feature(module, fid, label):
                page.goto(f"{base}/#module/{module}/{fid}")
                page.get_by_role("button", name=label, exact=True).click()

            def live(kind):
                response = client.get("/records", params={"kind": kind, "project_id": pid})
                response.raise_for_status()
                return response.json()["data"]

            try:
                page.goto(base)
                page.locator('[data-action="new-project"]').first.click()
                dialog.locator("[name=name]").fill("实拍分镜")
                dialog.locator("[name=goals]").fill("手工比较脚本与分镜选片")
                dialog.locator("[name=market]").fill("本地验收")
                dialog.locator("form[data-form] button[type=submit]").click()
                expect(dialog).to_have_count(0)
                pid = client.get("/projects").json()["data"][0]["id"]
                evidence["checks"].append("在空库通过正常界面创建项目")
                feature("trend", "T06", "选择并批准简报")

                def candidate(name, idea):
                    dialog.locator("[name=name]").fill(name)
                    dialog.locator("[name=intent]").fill(idea)
                    dialog.get_by_role("button", name="保存候选").click()
                    expect(dialog.get_by_role("heading")).to_have_text("选择创意简报")

                candidate("平视开箱", "打开杯盒并展示杯身")
                dialog.get_by_role("button", name="新建候选").click()
                candidate("空镜开场", "先展示窗边静物")
                dialog.locator("[name=chosen]").select_option(label="平视开箱 · 第 1 版")
                dialog.locator("[data-alternatives] summary").click()
                dialog.locator("[data-compare]").check()
                dialog.locator("[data-reason]").fill("预留给短版")
                dialog.locator("[name=reason]").fill("开箱镜头直接展示产品")
                snap("01-creative-review")
                dialog.get_by_role("button", name="批准这个版本").click()
                expect(dialog).to_have_count(0)
                approval = live("Approval")[0]
                briefs = live("Brief")
                assert approval["data"]["subject_version"] == 2 and len(briefs) == 2
                assert [b["version"] for b in briefs if b["data"]["name"] == "空镜开场"] == [1]
                assert live("DecisionRecord")[0]["data"]["options"][1]["reason"] == "预留给短版"
                evidence["checks"].append("新建两个候选并选择一项，核对绑定版本、备选及理由")
                feature("plan", "N02", "挑选分镜素材")
                dialog.locator("[name=plan_name]").fill("桌面拍摄")
                dialog.locator("[name=slot]").fill("杯身近景")
                dialog.locator("[name=constraints]").fill("单杯近景")
                dialog.locator("[name=evidence]").fill("已检查原片和镜头区间")
                fixture = Path(tmp) / "橙色样片.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=orange:s=320x180:r=25:d=2",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "1",
                        "-y",
                        str(fixture),
                    ],
                    check=True,
                    timeout=20,
                )
                dialog.locator("[data-source-file]").set_input_files(fixture)
                expect(dialog.locator("[data-upload-status]")).to_have_text("已导入")
                dialog.locator("[name=start]").fill("0.24")
                dialog.locator("[name=end]").fill("1.56")
                dialog.locator("[name=lock]").check()
                snap("02-shot-selection")
                dialog.get_by_role("button", name="保存并加入剪辑").click()
                expect(page).to_have_url(base + "/#editor")
                expect(dialog).to_have_count(0)
                expect(page.locator("[data-clip]").first).to_be_visible()
                page.locator("[data-action=save-timeline]").click()
                expect(page.locator("#edit-status")).to_contain_text("已保存")
                timeline = live("Timeline")[0]
                shot = live("Shot")[0]
                clip = timeline["data"]["clips"][0]
                assert clip["source_in"] == 0.24 and clip["source_out"] == 1.56
                assert clip["asset_version_id"] == shot["data"]["asset_version_id"]
                assert live("Plan")[0]["data"]["slot_shot_refs"][0]["id"] == shot["id"]
                assert clip.get("shot_id", clip.get("shot_ref", {}).get("id")) == shot["id"]
                evidence["checks"].append(
                    "分镜原地上传、预览取段、锁定、一键转剪辑保存，核对原片版本、分镜与精确区间"
                )
                snap("03-shot-in-editor")
                feature("plan", "N02", "挑选分镜素材")
                plan = live("Plan")[0]
                dialog.locator("[name=plan]").select_option(plan["id"])
                dialog.locator("[name=slot]").fill("杯身近景")
                dialog.locator("[name=slot]").press("Tab")
                dialog.locator("[name=decision]").select_option("gap")
                dialog.locator("[name=evidence]").fill("拟以补拍替换")
                dialog.get_by_role("button", name="保存分镜", exact=True).click()
                expect(page.locator("#toasts")).to_contain_text("人工锁定")
                expect(dialog).to_be_visible()
                assert live("Plan")[0]["version"] == plan["version"]
                dialog.locator("[name=unlock]").check()
                dialog.get_by_role("button", name="保存分镜", exact=True).click()
                expect(dialog).to_have_count(0)
                latest = live("Plan")[0]
                assert latest["version"] == plan["version"] + 1
                assert (
                    latest["data"]["slots"][0]["status"] == "gap"
                    and not latest["data"]["slot_shot_refs"]
                )
                assert live("Timeline")[0]["data"]["clips"][0] == clip
                evidence["checks"].append(
                    "拒绝覆盖锁定分镜；明确解锁后登记缺口，原剪辑固定版本保留"
                )
                feature("music", "B02", "试听与打点")
                sample = Path(tmp) / "打点音轨.wav"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=240:sample_rate=44100:duration=4",
                        "-y",
                        str(sample),
                    ],
                    check=True,
                    timeout=20,
                )
                dialog.locator("[data-rhythm-upload]").set_input_files(sample)
                expect(dialog.locator("[data-rhythm-console]")).to_be_visible()
                player = dialog.locator("audio")
                player.evaluate("(a) => a.play()")
                expect(dialog.locator("[name=rhythm-time]")).not_to_have_value("0.000")
                dialog.locator("[data-mark=beat]").click()
                player.evaluate("(a) => a.pause()")
                dialog.locator("[name=rhythm-time]").fill("0.500")
                dialog.locator("[data-mark=downbeat]").click()
                dialog.locator("[name=rhythm-time]").fill("1.250")
                dialog.locator("[name=rhythm-label]").fill("主段")
                dialog.locator("[data-mark=section]").click()
                expect(dialog.locator("[data-remove]")).to_have_count(3)
                dialog.locator("[data-remove]").last.click()
                expect(dialog.locator("[data-remove]")).to_have_count(2)
                dialog.locator("[data-undo]").click()
                expect(dialog.locator("[data-remove]")).to_have_count(3)
                snap("04-music-timing")
                dialog.get_by_role("button", name="保存标记", exact=True).click()
                expect(dialog).to_have_count(0)
                m = live("Music")[0]
                marks = m["data"]["markers"]
                assert (
                    m["data"]["beats"][0]["time"] > 0 and m["data"]["downbeats"][0]["time"] == 0.5
                )
                assert m["data"]["sections"][0]["time"] == 1.25
                assert m["data"]["analysis_clock"]["sample_rate_hz"] == 48000
                evidence["checks"].append(
                    "原地导入音频并实播打点；精确输入强拍与段落，撤销移除，保存后核对采样时钟和独立的标记组"
                )
                feature("music", "B02", "试听与打点")
                dialog.locator("[name=rhythm-version]").select_option(m["id"])
                expect(dialog.locator("[data-remove]")).to_have_count(3)
                dialog.get_by_role("button", name="在剪辑室使用").click()
                expect(page).to_have_url(base + "/#editor")
                page.get_by_role("button", name="加入拍点音轨").click()
                expect(page.locator("[data-beat-time]")).to_have_count(3)
                points = page.locator("[data-beat-time]").evaluate_all(
                    "(xs)=>xs.map(x=>Number(x.dataset.beatTime))"
                )
                assert points == [x["time"] for x in marks], (points, marks)
                page.locator("[data-beat-time][data-kind=section]").click()
                assert abs(float(page.locator("#playhead").input_value()) - 1.25) < 0.002
                snap("05-music-in-editor")
                evidence["checks"].append(
                    "重新打开已保存的音乐标记，转剪辑时显式加入固定音轨，验证三种尺标和点击定位"
                )
                assert not evidence["page_errors"], evidence["page_errors"]
                evidence["passed"] = True
            except Exception:
                snap("failure")
                (output / "failure-state.txt").write_text(page.locator("body").inner_text())
                raise
            finally:
                client.close()
                browser.close()
    finally:
        (output / "report.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
        )
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
