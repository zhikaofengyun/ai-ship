"""Check user-operated workspace dividers, viewer and exact timeline handoff."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import VIEWPORTS, server
from browser_edit_session import PREVIEW_PIXELS, wait_for_browser_value


def run(output, engine, round_id):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "engine": engine,
        "round": str(round_id),
        "checks": [],
        "measurements": {},
        "screenshots": [],
        "page_errors": [],
        "passed": False,
    }
    checks = report["checks"]
    with tempfile.TemporaryDirectory(prefix="ship-layout-") as td:
        folder = Path(td)
        media = folder / "画面测试.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=480x270:rate=24",
                "-t",
                "4",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                str(media),
            ],
            check=True,
        )
        with (
            server(folder, output) as base,
            sync_playwright() as pw,
            httpx.Client(base_url=base + "/api/v1", trust_env=False) as api,
        ):
            project = api.post(
                "/projects", json={"name": "布局校对", "width": 480, "height": 270}
            ).json()["data"]
            browser = (
                pw.webkit.launch(executable_path=os.environ.get("AI_SHIP_WEBKIT"))
                if engine == "webkit"
                else pw.chromium.launch(
                    executable_path=os.environ.get("AI_SHIP_CHROMIUM")
                    or shutil.which("chromium-headless-shell")
                    or shutil.which("chromium"),
                    chromium_sandbox=True,
                )
            )
            dw, dh, mw, mh = VIEWPORTS.get(str(round_id), VIEWPORTS["1"])
            context = browser.new_context(viewport={"width": dw, "height": dh}, locale="zh-CN")
            context.add_init_script(
                "localStorage.setItem('ship-project'," + json.dumps(project["id"]) + ")"
            )
            page = context.new_page()
            page.set_default_timeout(9000)
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            loc = page.locator

            def box(selector):
                rect = loc(selector).bounding_box()
                assert rect, selector
                return rect

            def dimension(selector, key="width"):
                return round(box(selector)[key])

            def wait(expression):
                return wait_for_browser_value(page, expression)

            def shot(name):
                path = output / (name + ".png")
                page.screenshot(path=str(path), scale="css")
                report["screenshots"].append(
                    {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                )

            def drag(selector, dx, dy):
                r = box(selector)
                x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + dx, y + dy, steps=6)
                page.mouse.up()

            def near(a, b):
                assert abs(a - b) < 3, (a, b)

            try:
                page.goto(base + "/#editor")
                expect(loc(".room-title h1")).to_have_text("布局校对")
                nav = "[data-divider=sidebar]"
                start = dimension(".sidebar")
                drag(nav, 40, 0)
                near(dimension(".sidebar"), start + 40)
                loc(nav).press("ArrowRight")
                near(dimension(".sidebar"), start + 48)
                loc(nav).press("Home")
                near(dimension(".sidebar"), 144)
                loc(nav).press("End")
                assert dimension(".sidebar") <= 480
                loc(nav).press("Enter")
                near(dimension(".sidebar"), start)
                drag(nav, 34, 0)
                nav_width = dimension(".sidebar")
                checks.append("navigation drag, keyboard steps, min/max and restore")
                with page.expect_file_chooser() as chooser:
                    loc("[data-action=import-assets]").first.click()
                chooser.value.set_files(str(media))
                expect(loc(".room-bin-row")).to_have_count(1, timeout=25000)
                loc("[data-action=audition]").click()
                wait("document.querySelector('#preview-stage video')?.readyState>=2")
                loc("#viewer-screen").click()
                wait("document.querySelector('#preview-stage video').currentTime>0.08")
                loc("[data-source-play]").click()
                wait("document.querySelector('#preview-stage video').paused")
                loc("[data-source-scrub]").fill("1.25")
                wait(
                    "Math.abs(document.querySelector('#preview-stage video').currentTime-1.25)<.05"
                )
                checks.append("click source viewer to play, pause and seek actual source")
                loc("[data-action=add-asset]").last.click()
                wait(PREVIEW_PIXELS)
                loc("[data-action=save-timeline]").click()
                expect(loc("#edit-status")).to_have_text("已保存")
                records = api.get(
                    "/records", params={"project_id": project["id"], "kind": "Timeline"}
                ).json()["data"]
                timeline_id = records[0]["id"]
                frozen = records[0]["version"]
                first = "[data-divider=pane-0]"
                second = "[data-divider=pane-1]"
                old_bin = dimension(".room-bin")
                drag(first, 25, 0)
                near(dimension(".room-bin"), old_bin + 25)
                old_inspector = dimension(".room-inspector")
                loc(second).press("ArrowLeft")
                near(dimension(".room-inspector"), old_inspector + 8)
                old_height = dimension(".room-upper", "height")
                drag("[data-divider=timeline-height]", 0, -35)
                near(dimension(".room-upper", "height"), old_height - 35)
                report["measurements"].update(
                    sidebar_px=nav_width,
                    bin_px=dimension(".room-bin"),
                    inspector_px=dimension(".room-inspector"),
                    upper_px=dimension(".room-upper", "height"),
                )
                checks.append("resize both adjacent pane splitters and timeline height")
                loc(".room-layout-menu summary").click()
                loc("[data-layout-order]").select_option("inspector monitor bin")
                loc("[data-layout-timeline]").select_option("above")
                loc(".room-layout-menu summary").click()
                assert (
                    box(".room-inspector")["x"] < box(".room-monitor")["x"] < box(".room-bin")["x"]
                )
                assert box(".room-timeline")["y"] < box(".room-upper")["y"]
                page.reload()
                expect(loc(".room-title h1")).to_have_text("布局校对")
                wait("document.querySelector('.room-body')?.dataset.timelinePosition==='above'")
                near(dimension(".sidebar"), nav_width)
                near(dimension(".room-bin"), old_bin + 25)
                near(dimension(".room-upper", "height"), old_height - 35)
                assert box(".room-bin")["x"] > box(".room-monitor")["x"]
                checks.append("panel order, track position and pixel sizes survive reload")
                loc(".room-bin [data-dock-handle]").drag_to(loc(".room-monitor [data-dock-handle]"))
                wait(
                    "document.querySelector('[data-layout-order]').value==='inspector bin monitor'"
                )
                assert box(".room-bin")["x"] < box(".room-monitor")["x"]
                checks.append("drag a panel heading directly onto another panel to swap")

                screen = loc("#viewer-screen")
                stage = "#preview-stage"
                zoom = loc("[data-view-mode]")
                zoom.select_option("1")
                near(dimension(stage), 480)
                zoom.select_option("fit")
                window = box("#viewer-screen")
                near(dimension(stage), min(window["width"], window["height"] * 480 / 270))
                zoom.select_option("fill")
                near(dimension(stage), max(window["width"], window["height"] * 480 / 270))
                zoom.select_option("4")
                before = box(stage)
                drag("#viewer-screen", 38, 19)
                after = box(stage)
                near(after["x"] - before["x"], 38)
                near(after["y"] - before["y"], 19)
                loc("[data-view-minus]").click()
                assert dimension(stage) < round(before["width"])
                loc("[data-view-plus]").click()
                near(dimension(stage), round(before["width"]))
                screen.press("0")
                expect(zoom).to_have_value("fit")
                checks.append("actual native-pixel, fit, fill, zoom and pan geometry")
                screen.click()
                wait("Number(document.querySelector('#playhead').value)>.1")
                screen.click()
                playhead = float(loc("#playhead").input_value())
                page.wait_for_timeout(100)
                assert float(loc("#playhead").input_value()) == playhead
                loc("[data-view-full]").click()
                wait(
                    "document.fullscreenElement===document.querySelector('.room-monitor') || document.webkitFullscreenElement===document.querySelector('.room-monitor') || document.querySelector('.room-monitor').classList.contains('room-view-expanded')"
                )
                near(dimension(".room-monitor"), page.evaluate("innerWidth"))
                wait(PREVIEW_PIXELS)
                shot("fullscreen")
                loc("[data-view-full]").click()
                wait(
                    "!document.fullscreenElement && !document.webkitFullscreenElement && !document.querySelector('.room-monitor').classList.contains('room-view-expanded')"
                )
                # Exercise the embedded/denied native-fullscreen path, then restore the browser method.
                loc(".room-monitor").evaluate(
                    "m=>{m.requestFullscreen=()=>Promise.reject(new Error('test permission denied'));}"
                )
                loc("[data-view-full]").click()
                wait(
                    "document.querySelector('.room-monitor').classList.contains('room-view-expanded')"
                )
                near(dimension(".room-monitor"), page.evaluate("innerWidth"))
                loc("[data-view-full]").press("Escape")
                wait(
                    "!document.querySelector('.room-monitor').classList.contains('room-view-expanded')"
                )
                loc(".room-monitor").evaluate("m=>delete m.requestFullscreen")
                assert api.get("/records/" + timeline_id).json()["data"]["version"] == frozen
                checks.append("timeline playback, fullscreen and exit preserve edit version")
                loc(".room-layout-menu summary").click()
                loc("[data-layout-reset]").click()
                near(dimension(".room-bin"), old_bin)
                before_w = dimension(".room-monitor")
                loc("[data-action=bin-toggle]").click()
                expect(loc(".room-bin")).to_be_hidden()
                assert dimension(".room-monitor") > before_w
                loc("[data-action=bin-toggle]").click()
                shot("arranged-workspace")
                page.set_viewport_size({"width": mw, "height": mh})
                expect(loc(first)).to_be_hidden()
                expect(loc(".room-layout-menu")).to_be_hidden()
                mobile_height = dimension(".room-monitor", "height")
                drag("[data-divider=mobile-preview-height]", 0, 24)
                near(dimension(".room-monitor", "height"), mobile_height + 24)
                loc("[data-divider=mobile-preview-height]").press("ArrowUp")
                near(dimension(".room-monitor", "height"), mobile_height + 16)

                loc("[data-global=menu]").click()
                expect(loc(nav)).to_be_visible()
                drawer = dimension(".sidebar")
                drag(nav, -26, 0)
                near(dimension(".sidebar"), drawer - 26)
                loc(".sidebar-scrim").click(position={"x": mw - 6, "y": 80})
                expect(loc(nav)).to_be_hidden()
                screen.click()
                wait("Number(document.querySelector('#playhead').value)>" + str(playhead + 0.04))
                screen.click()
                loc("[data-action=room-panel][data-panel=properties]").click()
                expect(loc(".room-inspector")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                shot("phone-viewer")
                checks.append("narrow drawer width isolated from desktop, panel and preview usable")
                page.set_viewport_size({"width": dw, "height": dh})
                near(dimension(".sidebar"), nav_width)

                def copy_timeline(name):
                    response = api.post(
                        "/records",
                        json={
                            "kind": "Timeline",
                            "project_id": project["id"],
                            "data": dict(records[0]["data"], name=name),
                            "refs": records[0]["refs"],
                        },
                    )
                    response.raise_for_status()
                    return response.json()["data"]

                chosen = copy_timeline("制作台选定的剪辑")
                newer = copy_timeline("另一个更新的剪辑")
                loc("#timeline-name").fill("手动未保存草稿")
                loc("#timeline-name").press("Tab")
                page.goto(base + "/#editor/project-timeline/" + chosen["id"])
                expect(loc(".room-title h1")).to_have_text("制作台选定的剪辑")
                expect(loc(".room-clip")).to_have_count(1)
                loc("#timeline-name").fill("制作台选定剪辑的未保存修改")
                loc("#timeline-name").press("Tab")
                page.goto(base + "/#editor")
                expect(loc(".room-title h1")).to_have_text("手动未保存草稿")
                page.goto(base + "/#editor/project-timeline/" + chosen["id"])
                expect(loc(".room-title h1")).to_have_text("制作台选定剪辑的未保存修改")
                loc("[data-action=timeline-list]").click()
                loc('[data-timeline="' + newer["id"] + '"]').click()
                expect(loc(".room-title h1")).to_have_text("另一个更新的剪辑")
                page.goto(base + "/#editor/project-timeline/" + chosen["id"])
                page.reload()
                expect(loc(".room-title h1")).to_have_text("制作台选定剪辑的未保存修改")
                assert (
                    api.get("/records/" + chosen["id"]).json()["data"]["data"]["name"]
                    == "制作台选定的剪辑"
                )
                page.goto(base + "/#production")
                production = "[data-divider=production]"
                expect(loc(production)).to_be_visible()
                prior = dimension(".director-primary")
                drag(production, -30, 0)
                near(dimension(".director-primary"), prior - 30)
                page.reload()
                expect(loc(production)).to_be_visible()
                near(dimension(".director-primary"), prior - 30)
                loc(production).press("ArrowRight")
                near(dimension(".director-primary"), prior - 22)
                loc(production).press("Enter")
                near(dimension(".director-primary"), prior)
                page.set_viewport_size({"width": mw, "height": mh})
                expect(loc(production)).to_be_hidden()
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                checks.append(
                    "production goal and review monitor resize, persist, reset and stack on mobile"
                )
                assert not report["page_errors"], report["page_errors"]
                checks.append(
                    "open chosen revision stream despite newer timeline, isolate both dirty drafts across navigation and reload"
                )
                report["passed"] = True
            finally:
                if not report["passed"]:
                    page.screenshot(path=str(output / "failure.png"))
                report["browser_version"] = browser.version
                (output / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--round", default="development")
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    args = parser.parse_args()
    run(args.output.resolve(), args.engine, args.round)
