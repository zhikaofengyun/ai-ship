"""Check personal module layouts and the exact search-result handoff to the editor."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server


def run(out, engine):
    out.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with (
        tempfile.TemporaryDirectory() as td,
        server(Path(td), out) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False) as api,
    ):

        def project(name):
            v = api.post("/records", json={"kind": "Project", "data": {"name": name}})
            v.raise_for_status()
            return v.json()["data"]["id"]

        pid, other = project("目录布局项目"), project("独立项目")
        movie = Path(td) / "orange.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=orange:s=240x160:r=12:d=1",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(movie),
            ],
            check=True,
        )
        upload = api.post(
            "/media/import",
            params={"project_id": pid, "filename": "茶杯实拍.mp4"},
            content=movie.read_bytes(),
        )
        upload.raise_for_status()
        version = upload.json()["data"]["version"]
        refs_before = api.get("/records", params={"project_id": pid}).json()["data"]
        executable = os.getenv("AI_SHIP_" + engine.upper()) or (
            shutil.which("chromium") if engine == "chromium" else None
        )
        browser = getattr(pw, engine).launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.evaluate('(id)=>localStorage.setItem("ship-project",id)', pid)
        page.reload()
        page.goto(base + "/#module/search/S02")
        layout = page.locator(".feature-layout")
        menu = page.locator("#module-feature-list")
        divider = layout.get_by_role("separator")
        tools = page.locator(".module-layout-tools").first
        expect(layout).to_have_attribute("data-side", "left")
        box = divider.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + min(32, box["height"] / 2)
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 24, y, steps=4)
        page.mouse.up()
        expect(divider).to_have_attribute("aria-valuenow", "248")
        divider.press("Enter")
        expect(divider).to_have_attribute("aria-valuenow", "224")
        divider.focus()
        divider.press("Shift+ArrowRight")
        expect(divider).to_have_attribute("aria-valuenow", "256")
        tools.locator("[data-pane-move]").click()
        expect(layout).to_have_attribute("data-side", "right")
        assert menu.bounding_box()["x"] > page.locator(".feature-detail").bounding_box()["x"]
        tools.locator("[data-pane-toggle]").click()
        expect(menu).to_be_hidden()
        page.reload()
        expect(menu).to_be_hidden()
        tools.locator("[data-pane-toggle]").click()
        expect(divider).to_have_attribute("aria-valuenow", "256")
        expect(layout).to_have_attribute("data-side", "right")
        page.goto(base + "/#module/voice/V01")
        expect(layout).to_have_attribute("data-side", "right")
        expect(divider).to_have_attribute("aria-valuenow", "256")
        checks.append(
            "Pointer drag and keyboard resize, reset, side and collapse survive reload and feature/module changes"
        )
        page.evaluate('(id)=>localStorage.setItem("ship-project",id)', other)
        page.reload()
        expect(layout).to_have_attribute("data-side", "left")
        expect(divider).to_have_attribute("aria-valuenow", "224")
        page.evaluate('(id)=>localStorage.setItem("ship-project",id)', pid)
        page.reload()
        expect(layout).to_have_attribute("data-side", "right")
        checks.append("Projects have separate layout preferences")
        page.set_viewport_size({"width": 390, "height": 844})
        expect(layout).to_have_attribute("data-split", "false")
        expect(divider).to_be_hidden()
        expect(menu).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
        page.screenshot(path=str(out / "module-mobile.png"))
        page.set_viewport_size({"width": 1440, "height": 960})
        expect(divider).to_have_attribute("aria-valuenow", "256")
        tools.locator("[data-pane-reset]").click()
        expect(divider).to_have_attribute("aria-valuenow", "224")
        expect(layout).to_have_attribute("data-side", "left")
        checks.append("Narrow and wide layouts, no page overflow, explicit reset")
        page.goto(base + "/#module/search/S02")
        page.locator("[data-action=feature-action]").click()
        dlg = page.locator("dialog[open]")
        pane = dlg.locator(".indexed-preview")
        sdiv = dlg.get_by_role("separator")
        dlg.locator("[name=channel]").select_option("source")
        dlg.get_by_role("button", name="查找", exact=True).click()
        dlg.get_by_role("button", name="预览 茶杯实拍.mp4").click()
        expect(pane.locator("h3")).to_have_text("茶杯实拍.mp4")
        expect(pane.locator("video")).to_have_attribute(
            "src", "/api/v1/media/" + version["id"] + "/file"
        )
        sdiv.focus()
        sdiv.press("Shift+ArrowLeft")
        expect(sdiv).to_have_attribute("aria-valuenow", "332")
        dlg.locator("[data-pane-move]").click()
        expect(dlg.locator(".indexed-layout")).to_have_attribute("data-side", "left")
        pane.locator("video").evaluate(
            "async v=>{v.muted=true;await v.play();v.pause();v.currentTime=0.5}"
        )
        page.wait_for_function(
            "()=>{const v=document.querySelector('#indexed-preview video');return v&&!v.seeking&&v.readyState>=2}"
        )
        page.screenshot(path=str(out / "search-desktop.png"))
        page.keyboard.press("Escape")
        page.locator("[data-action=feature-action]").click()
        expect(sdiv).to_have_attribute("aria-valuenow", "332")
        expect(dlg.locator(".indexed-layout")).to_have_attribute("data-side", "left")
        page.set_viewport_size({"width": 390, "height": 844})
        expect(sdiv).to_be_hidden()
        dlg.locator("[name=channel]").select_option("source")
        dlg.get_by_role("button", name="查找", exact=True).click()
        dlg.get_by_role("button", name="预览 茶杯实拍.mp4").click()
        expect(pane.locator("video")).to_be_visible()
        assert dlg.evaluate("e=>e.scrollWidth <= e.clientWidth+1")
        checks.append("Search split and source preview persist independently and stack on a phone")
        dlg.get_by_role("button", name="在剪辑台预览").click()
        expect(page.locator("#preview-stage video")).to_have_attribute(
            "src", "/api/v1/media/" + version["id"] + "/file"
        )
        refs_after = api.get("/records", params={"project_id": pid}).json()["data"]
        assert [(r["id"], r["version"]) for r in refs_before] == [
            (r["id"], r["version"]) for r in refs_after
        ]
        checks.append(
            "Editor displays the pinned search version without writing a Timeline, Workflow or other business revision"
        )
        assert not errors, errors
        report = {
            "passed": True,
            "engine": engine,
            "browser": browser.version,
            "checks": checks,
            "page_errors": errors,
        }
        browser.close()
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    args = parser.parse_args()
    run(args.output.resolve(), args.engine)
