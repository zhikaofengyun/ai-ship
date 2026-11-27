"""Review real indexed video and photo relatives in the S04 workspace."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import ROOT, server


def run(engine, other_photo, out):
    with tempfile.TemporaryDirectory() as temp:
        scratch = Path(temp)
        video = ROOT / "evidence/batch-production/controlled-photo/horizontal.mp4"
        photo = scratch / "coffee-table-frame.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-threads",
                "1",
                str(photo),
            ],
            check=True,
        )
        with (
            server(scratch, scratch, fixture_env={"AI_SHIP_MATERIAL_WORKER": "0"}) as base,
            httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=60) as client,
        ):
            r = client.post(
                "/records", json={"kind": "Project", "data": {"name": "真实视觉素材族复核"}}
            )
            r.raise_for_status()
            pid = r.json()["data"]["id"]
            ids = []
            for f in (photo, video, other_photo):
                r = client.post(
                    "/media/import",
                    params={"project_id": pid, "filename": f.name},
                    content=f.read_bytes(),
                )
                r.raise_for_status()
                ids.append(r.json()["data"]["version"]["id"])
            build = client.post("/production/asset-index/frames/build", json={"project_id": pid})
            build.raise_for_status()
            with sync_playwright() as pw:
                exe = os.getenv("AI_SHIP_" + engine.upper()) or (
                    shutil.which("chromium") if engine == "chromium" else None
                )
                browser = getattr(pw, engine).launch(executable_path=exe)
                page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="zh-CN")
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.add_init_script("localStorage.setItem('ship-project'," + json.dumps(pid) + ")")
                page.goto(base + "/#module/search/S04")
                page.get_by_role("button", name="管理素材族", exact=True).click()
                dlg = page.locator("dialog[open]")
                dlg.locator("[name=mode]").select_option("clip")
                dlg.locator("[name=members]").select_option(ids)
                with page.expect_response(lambda r: "/asset-families/suggest" in r.url) as outcome:
                    dlg.get_by_role("button", name="生成待复核候选族").click()
                audit = outcome.value.json()["data"]["audit"]["data"]
                expect(dlg.get_by_role("heading", name="视觉语义候选族")).to_be_visible()
                expect(dlg).to_contain_text(other_photo.name)
                expect(dlg.locator("img")).to_have_count(1)
                expect(dlg.locator("video")).to_have_count(1)
                assert dlg.locator("img").evaluate("(el)=>el.decode().then(()=>el.naturalWidth>0)")
                dlg.locator("details summary").click()
                expect(dlg).to_contain_text("双向覆盖分")
                dlg.get_by_role("button", name="复核这个族").click()
                dlg.locator("[name=name]").fill("咖啡实拍")
                dlg.locator("[name=reason]").fill("视频原帧与照片可核对同一张桌上的杯盘")
                with page.expect_response(lambda r: "/asset-families/group" in r.url) as accepted:
                    dlg.get_by_role("button", name="确认分组").click()
                record = accepted.value.json()["data"]["cluster"]
                expect(dlg.get_by_role("button", name="已确认")).to_be_disabled()
                dlg.get_by_role("button", name="关闭").click()
                page.get_by_role("button", name="管理素材族", exact=True).click()
                dlg.locator("[name=mode]").select_option("retire")
                dlg.locator("[name=group]").select_option(record["id"])
                dlg.locator("[name=name]").fill("撤销")
                dlg.locator("[name=reason]").fill("检验撤销路径")
                with page.expect_response(lambda r: "/asset-families/group" in r.url) as retired:
                    dlg.get_by_role("button", name="保存分组").click()
                retired = retired.value.json()["data"]["cluster"]
                expect(dlg).to_have_count(0)
                assert (
                    not errors
                    and retired["id"] == record["id"]
                    and retired["version"] == 2
                    and retired["data"]["status"] == "retired"
                )
                assert (
                    len(audit["groups"]) == 1
                    and set(audit["groups"][0]["members"]) == set(ids[:2])
                    and audit["unmatched"] == ids[2:]
                )
                out.write_text(
                    json.dumps(
                        {
                            "engine": engine,
                            "version": browser.version,
                            "threshold": audit["threshold"],
                            "space": audit["space"],
                            "minimum_similarity": audit["groups"][0]["minimum_score"],
                            "matched_inputs": [photo.name, video.name],
                            "unmatched_input": other_photo.name,
                            "confirmed_revision": record["version"],
                            "retired_revision": retired["version"],
                            "page_errors": errors,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    parser.add_argument("--other-photo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.engine, args.other_photo, args.output)
