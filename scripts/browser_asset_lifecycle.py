"""Exercise local-purpose review, cooldown, director selection and cold recovery from the UI."""

import argparse
import json
from pathlib import Path
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server


def run(output, engine):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp, server(Path(tmp), output) as base, sync_playwright() as pw:
        browser = (
            pw.webkit.launch(executable_path=os.environ.get("AI_SHIP_WEBKIT"))
            if engine == "webkit"
            else pw.chromium.launch(
                executable_path=os.environ.get("AI_SHIP_CHROMIUM")
                or shutil.which("chromium-headless-shell")
                or shutil.which("chromium")
            )
        )
        errors = []
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        dialog = page.locator("#dialog[open]")
        page.goto(base)
        page.locator('[data-action="new-project"]').first.click()
        dialog.locator("[name=name]").fill("素材生命周期浏览器验证")
        dialog.locator("[name=goals]").fill("验证红色片段")
        dialog.locator("form[data-form] button[type=submit]").click()
        expect(dialog).to_have_count(0)
        with httpx.Client(base_url=base + "/api/v1", trust_env=False) as client:
            projects = client.get("/projects").json()["data"]
            pid = (projects if isinstance(projects, list) else projects["items"])[0]["id"]
            source = Path(tmp) / "red.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=160x90:r=12",
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    str(source),
                ],
                check=True,
            )
            upload = client.post(
                "/media/import",
                params={"project_id": pid, "filename": source.name},
                content=source.read_bytes(),
            )
            upload.raise_for_status()
            v = upload.json()["data"]["version"]
            score = client.post(
                "/production/assets/score",
                json={
                    "project_id": pid,
                    "asset_version_id": v["id"],
                    "dimensions": {"quality": 0.91},
                    "weights": {"quality": 1},
                },
            )
            score.raise_for_status()

        def feature(code, label):
            page.goto(base + "/#module/assetops/" + code)
            page.get_by_role("button", name=label, exact=True).click()

        def close():
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(dialog).to_have_count(0)

        page.reload()
        feature("U04", "核对使用压力")
        dialog.locator("[name=reason]").fill("一周内用量")
        dialog.get_by_role("button", name="计算并保存规则").click()
        expect(dialog).to_contain_text("映射已核帖子 0")
        close()
        feature("U05", "编排探索配额")
        dialog.get_by_role("button", name="核对来源的本地制作用途").click()
        dialog.locator("[name=evidence]").fill("测试片由本机生成，仅供本次离线剪辑")
        dialog.get_by_role("button", name="记录本地制作用途").click()
        expect(dialog).to_have_count(0)
        page.goto(base + "/#production")
        page.get_by_role("button", name="探索配额", exact=True).click()
        dialog.locator("[name=assets]").select_option(v["id"])
        dialog.locator("[name=slots]").fill("1")
        dialog.locator("[name=share]").fill("1")
        dialog.get_by_role("button", name="计算选材方案").click()
        expect(dialog).to_contain_text("已选 1 段")
        dialog.get_by_role("button", name="用于本次制作").click()
        expect(page.locator("[data-source]:checked")).to_have_count(1)
        feature("U06", "归档与复苏")
        dialog.locator("[name=tier]").select_option("local_cold")
        dialog.locator("[name=reason]").fill("存入冷层")
        dialog.get_by_role("button", name="变更层级").click()
        expect(dialog).to_contain_text("恢复时需校验")
        close()
        page.goto(base + "/#production")
        expect(page.locator("[data-source]")).to_have_count(0)
        feature("U06", "归档与复苏")
        dialog.locator("[name=tier]").select_option("online")
        dialog.locator("[name=reason]").fill("恢复并重验")
        dialog.get_by_role("button", name="变更层级").click()
        expect(dialog).to_contain_text("恢复并重验")
        close()
        page.goto(base + "/#production")
        expect(page.locator("[data-source]")).to_have_count(1)
        second = Path(tmp) / "red-second.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(source),
                "-c",
                "copy",
                "-metadata",
                "comment=alternative encode",
                str(second),
            ],
            check=True,
        )
        with httpx.Client(base_url=base + "/api/v1", trust_env=False) as client:
            incoming = client.post(
                "/media/import",
                params={"project_id": pid, "filename": second.name},
                content=second.read_bytes(),
            )
            incoming.raise_for_status()
        page.reload()
        page.goto(base + "/#module/search/S03")
        page.get_by_role("button", name="对照素材区间", exact=True).click()
        dialog.get_by_role("button", name="对照原片", exact=True).click()
        expect(dialog.get_by_role("heading")).to_contain_text("疑似近似区间", timeout=20000)
        expect(dialog.locator("video")).to_have_count(2)
        dialog.get_by_role("button", name="人工复核近似关系", exact=True).click()
        dialog.locator("[name=reason]").fill("两版片段按三个采样点核对")
        dialog.get_by_role("button", name="确认近似关系", exact=True).click()
        expect(dialog).to_have_count(0)
        page.goto(base + "/#module/search/S04")
        page.get_by_role("button", name="管理素材族", exact=True).click()
        dialog.locator("[name=mode]").select_option("retire")
        dialog.locator("[name=name]").fill("撤销近似组")
        dialog.locator("[name=reason]").fill("复核使用范围后撤销")
        dialog.get_by_role("button", name="保存分组", exact=True).click()
        expect(dialog).to_have_count(0)
        assert not errors, errors
        browser.close()
        print(
            json.dumps(
                {
                    "engine": engine,
                    "checks": [
                        "cooldown",
                        "scope_evidence",
                        "exploration",
                        "director_handoff",
                        "cold",
                        "restore",
                        "duplicate_review",
                        "retire_group",
                    ],
                    "page_errors": errors,
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="webkit")
    args = parser.parse_args()
    run(args.output, args.engine)
