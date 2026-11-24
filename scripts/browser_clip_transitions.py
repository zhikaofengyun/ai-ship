"""Set and revise transitions in the real editor; verify pixels, saved overlaps and undo."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_edit_session import wait_for_browser_value
from browser_regression import server, VIEWPORTS


def run(output, engine, round_id):
    output.mkdir(exist_ok=True, parents=True)
    report = {"checks": [], "page_errors": [], "engine": engine, "passed": False}
    with (
        tempfile.TemporaryDirectory() as td,
        server(Path(td), output) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False) as client,
    ):
        prj = client.post(
            "/projects", json={"name": "双镜头转场", "width": 320, "height": 180}
        ).json()["data"]
        clips = []
        for color in ("red", "blue"):
            path = Path(td) / (color + ".mp4")
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s=320x180:r=25:d=2",
                    "-threads",
                    "1",
                    "-c:v",
                    "libx264",
                    str(path),
                ],
                check=True,
            )
            clips.append(path)
        launch = (
            {"executable_path": os.environ.get("AI_SHIP_WEBKIT")}
            if engine == "webkit"
            else {
                "executable_path": os.environ.get("AI_SHIP_CHROMIUM")
                or shutil.which("chromium-headless-shell")
                or shutil.which("chromium"),
                "chromium_sandbox": True,
            }
        )
        browser = getattr(pw, engine).launch(**launch)
        size = VIEWPORTS[str(round_id)]
        ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, locale="zh-CN")
        ctx.add_init_script("localStorage.setItem('ship-project'," + json.dumps(prj["id"]) + ")")
        page = ctx.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda er: report["page_errors"].append(str(er)))
        page.goto(base + "/#editor")
        for path in clips:
            page.locator("#editor-files").set_input_files(str(path))
            row = page.locator(".room-bin-row").filter(has_text=path.name)
            row.locator("[data-action=add-asset]").click()
        expect(page.locator(".room-clip")).to_have_count(2)
        page.locator(".room-clip").last.click()

        def change(kind, seconds):
            page.locator("[data-action=clip-transition]").click()
            d = page.locator("dialog[open]")
            d.locator("[name=joinKind]").select_option(kind)
            d.locator("[name=seconds]").fill(seconds)
            d.get_by_role("button", name="应用转场").click()

        for kind in ("dissolve", "wipe_left", "wipe_right", "dip_black", "dip_white"):
            change(kind, "0.8")
            page.wait_for_function("document.querySelectorAll('dialog[open]').length===0")
            wait_for_browser_value(
                page,
                """() => {
                const vs = Array.from(document.querySelectorAll('#preview-stage video[data-preview]'));
                return vs.length===2 && vs.every(v=>v.readyState>=2 && v.style.display==='block');
            }""",
            )
            page.locator("#preview-stage").screenshot(
                path=str(output / (kind + ".png")), animations="disabled"
            )
            # Use a standalone decoder to sample the composited browser screenshot.
            raw = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(output / (kind + ".png")),
                    "-vf",
                    "scale=80:48",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-threads",
                    "1",
                    "-",
                ]
            )
            pixel = lambda x: list(raw[(24 * 80 + x) * 3 : (24 * 80 + x + 1) * 3])
            left, middle, right = map(pixel, (25, 40, 55))
            if kind == "dissolve":
                assert min(middle[0], middle[2]) > 55 and max(middle[0], middle[2]) < 200, (
                    kind,
                    middle,
                )
            elif kind.startswith("wipe"):
                a, b = (left, right) if kind == "wipe_right" else (right, left)
                assert a[2] > a[0] + 125 and b[0] > b[2] + 125, (kind, left, right)
            elif kind == "dip_black":
                assert max(middle) < 40, middle
            else:
                assert min(middle) > 195, middle
            report.setdefault("pixels", {})[kind] = dict(left=left, middle=middle, right=right)
            report["checks"].append(
                kind + " overlap edited through inspector, composited pixels match"
            )
        page.locator("[data-action=save-timeline]").click()
        expect(page.locator("#edit-status")).to_have_text("已保存")

        def records():
            resp = client.get("/records", params={"project_id": prj["id"], "kind": "Timeline"})
            resp.raise_for_status()
            data = resp.json()["data"]
            return data if isinstance(data, list) else data.get("records", data.get("items", []))

        timeline = records()[0]
        pairs = timeline["data"]["clips"]
        assert pairs[1]["transition_in"] == {
            "from_id": pairs[0]["id"],
            "duration": 0.8,
            "kind": "dip_white",
        }
        assert abs(pairs[1]["timeline_start"] - 1.2) < 1e-6
        report["checks"].append("server saved actual source pair and overlap")
        change("cut", "0")
        page.locator("[data-action=undo]").click()
        expect(page.locator("[data-action=clip-transition]")).to_have_text("调整转场")
        change("dissolve", "3")
        expect(page.locator("dialog[open]")).to_be_visible()
        assert not report["page_errors"], report["page_errors"]
        report["checks"].append("remove / undo / reject impossible duration atomically")
        report["passed"] = True
        browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    p.add_argument("--round", default="1")
    a = p.parse_args()
    run(a.output, a.engine, a.round)
