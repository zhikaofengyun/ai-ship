"""Exercise a continuous editing session using real local media and the public UI."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import VIEWPORTS, server

CAPTIONS = "1\n00:00:00,200 --> 00:00:00,800\n第一镜\n\n2\n00:00:01,000 --> 00:00:01,600\n第二镜\n"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_for_browser_value(page, expression, timeout=12):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        result = page.evaluate(expression)
        if result:
            return result
        page.wait_for_timeout(80)
    raise AssertionError("Browser condition timed out: " + expression)


# Values are checked only on the generated color chart in this test.
# A normal production video may contain intentionally black frames.
RGB_EXTENT_JS = """media => {
  const canvas=document.createElement('canvas');canvas.width=24;canvas.height=16;
  const ctx=canvas.getContext('2d');ctx.drawImage(media,0,0,24,16);
  const data=ctx.getImageData(0,0,24,16).data;
  const rgb=[0,1,2].map(channel=>{
    const values=[]; for(let i=channel;i<data.length;i+=4) values.push(data[i]);
    return [Math.min(...values),Math.max(...values)];
  });
  return rgb.every(([lo,hi])=>hi-lo>110) ? rgb : false;
}"""
PREVIEW_PIXELS = (
    """() => {
  const media=[...document.querySelectorAll('#preview-stage video')].find(v=>v.style.display==='block');
  if(!media || media.readyState<2 || !media.videoWidth) return false;
  return ("""
    + RGB_EXTENT_JS
    + """)(media);
}"""
)
FILMSTRIP_PIXELS = (
    r"""async () => {
  const strips=[...document.querySelectorAll('[data-role=video] .room-strip')];
  if(!strips.length) return false;
  const rgb=await Promise.all(strips.map(async el=>{
    const css=el.style.backgroundImage;
    const source=css.match(/data:image\/jpeg;base64,[A-Za-z0-9+/=]+/)?.[0];
    if (!source) return false;
    const img=new Image(); img.src=source; await img.decode();
    return ("""
    + RGB_EXTENT_JS
    + """)(img);
  }));
  return rgb.every(Boolean)?rgb:false;
}"""
)


def mobile_touch_edit(browser, profile, descriptor, project, base, api, output, report):
    options = {k: v for k, v in descriptor.items() if k != "default_browser_type"}
    context = browser.new_context(**options, locale="zh-CN")
    context.add_init_script(
        "localStorage.setItem('ship-project',"
        + json.dumps(project["id"])
        + ");"
        + "window.shipTrustedTouches=0;window.addEventListener('touchstart',e=>{if(e.isTrusted)window.shipTrustedTouches++},{passive:true});"
    )
    try:
        page = context.new_page()
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
        page.goto(base + "/#editor")
        page.locator("[data-action=room-panel][data-panel=timeline]").tap()
        count = page.locator(".timeline-caption").count()
        page.locator("[data-action=caption]").tap()
        dialog = page.locator("dialog[open]")
        dialog.locator("[name=text]").fill("触控校对")
        dialog.locator("[name=start]").fill("0.2")
        dialog.locator("[name=end]").fill("0.85")
        dialog.locator("form[data-form] button[type=submit]").tap()
        expect(page.locator(".timeline-caption")).to_have_count(count + 1)
        page.locator("[data-action=save-timeline]").tap()
        expect(page.locator("#edit-status")).to_have_text("已保存")
        timelines = api.get(
            "/records", params={"kind": "Timeline", "project_id": project["id"]}
        ).json()["data"]
        check(
            any(any(c["text"] == "触控校对" for c in row["data"]["captions"]) for row in timelines),
            "touch edits were not committed",
        )
        pixels = wait_for_browser_value(page, PREVIEW_PIXELS)
        filmstrips = wait_for_browser_value(page, FILMSTRIP_PIXELS)
        for button in page.locator(".toast button").all():
            if button.is_visible():
                button.tap()
        png = output / "phone-touch.png"
        page.screenshot(path=str(png), scale="css")
        report["screenshots"].append(
            {"file": png.name, "sha256": hashlib.sha256(png.read_bytes()).hexdigest()}
        )
        report["touch_context"] = {
            "profile": profile,
            "user_agent": page.evaluate("navigator.userAgent"),
            "trusted_touchstart_events": page.evaluate("window.shipTrustedTouches"),
            "viewport": page.viewport_size,
            "physical_hardware": False,
            "preview_rgb": pixels,
            "filmstrip_rgb": filmstrips,
        }
        check(
            report["touch_context"]["trusted_touchstart_events"] > 0,
            "No touch events delivered by browser",
        )
    finally:
        context.close()


def run(output, round_id, engine):
    output.mkdir(parents=True, exist_ok=True)
    checks, errors, routes = [], [], []
    report = {
        "round": round_id,
        "checks": checks,
        "screenshots": [],
        "page_errors": errors,
        "fixture": "synthetic timed video and sine track, public project creation API",
    }
    with tempfile.TemporaryDirectory(prefix="ai-ship-editor-") as temp:
        folder = Path(temp)
        video = folder / "运动色卡.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=480x270:rate=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "3",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(video),
            ],
            check=True,
        )
        with (
            server(folder, output) as base,
            sync_playwright() as pw,
            httpx.Client(base_url=base + "/api/v1", trust_env=False) as api,
        ):
            project = api.post(
                "/projects", json={"name": "镜头编排", "width": 480, "height": 270}
            ).json()["data"]
            if engine == "webkit":
                browser = pw.webkit.launch(
                    executable_path=os.environ.get("AI_SHIP_WEBKIT"), headless=True
                )
            else:
                browser = pw.chromium.launch(
                    executable_path=os.environ.get("AI_SHIP_CHROMIUM")
                    or shutil.which("chromium-headless-shell")
                    or shutil.which("chromium"),
                    chromium_sandbox=True,
                )
            report["browser_engine"] = engine
            dw, dh, mw, mh = VIEWPORTS.get(str(round_id), VIEWPORTS["1"])
            report["viewport_variant"] = {"desktop": [dw, dh], "mobile": [mw, mh]}
            context = browser.new_context(viewport={"width": dw, "height": dh}, locale="zh-CN")
            context.add_init_script(
                "localStorage.setItem('ship-project'," + json.dumps(project["id"]) + ")"
            )
            page = context.new_page()
            page.set_default_timeout(8000)
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.on(
                "framenavigated", lambda f: routes.append(f.url) if f == page.main_frame else None
            )

            def action(name):
                return page.locator('[data-action="' + name + '"]')

            def wait_js(expression):
                return wait_for_browser_value(page, expression)

            def shot(name):
                for item in page.locator(".toast button").all():
                    if item.is_visible():
                        item.click()
                path = output / (name + ".png")
                page.screenshot(path=str(path), animations="disabled")
                report["screenshots"].append(
                    {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                )

            dialog = page.locator("dialog[open]")

            def close():
                dialog.get_by_role("button", name="关闭", exact=True).click()

            def pick(action_name, file):
                with page.expect_file_chooser() as chooser:
                    action(action_name).first.click()
                chooser.value.set_files(file)

            try:
                page.goto(base + "/#editor")
                expect(page.locator(".room-title h1")).to_have_text("镜头编排")
                pick("import-assets", str(video))
                expect(page.locator(".room-bin-row")).to_have_count(1, timeout=25000)
                action("audition").click()
                wait_js("document.querySelector('#preview-stage video')?.readyState>=2")
                action("add-asset").last.click()
                expect(page.locator(".room-clip")).to_have_count(1)
                wait_js("document.querySelector('#preview-stage video')?.readyState>=2")
                checks.append(
                    "editor import / real source preview / add to timeline without navigation"
                )
                action("frame-next").click()
                check(float(page.locator("#playhead").input_value()) > 0, "frame step did not move")
                page.locator("[data-property=source_in]").fill("0.25")
                page.locator("[data-property=source_in]").press("Tab")
                page.locator("#playhead").focus()
                page.locator("#playhead").press("Home")
                page.locator("#playhead").evaluate(
                    "e=>{e.value='1.0';e.dispatchEvent(new Event('input'))}"
                )
                action("split").click()
                expect(page.locator(".room-clip")).to_have_count(2)
                action("undo").click()
                expect(page.locator(".room-clip")).to_have_count(1)
                action("redo").click()
                expect(page.locator(".room-clip")).to_have_count(2)
                checks.append("frame step / source trim / split / undo / redo")
                page.locator(".room-clip").first.click()
                action("move-next").click()
                action("move-prev").click()
                action("replace-source").click()
                dialog.locator("form[data-form] button[type=submit]").click()
                expect(dialog).to_have_count(0)
                action("clip-mask").click()
                dialog.locator("[name=shape]").select_option("ellipse")
                dialog.locator("[name=edge0]").fill("20")
                dialog.get_by_role("button", name="应用遮罩").click()
                expect(dialog).to_have_count(0)
                action("clip-mask").click()
                expect(dialog.locator("[name=edge0]")).to_have_value("20")
                expect(dialog.locator("[name=shape]")).to_have_value("ellipse")
                dialog.locator("[data-clear-mask]").click()
                expect(dialog).to_have_count(0)
                checks.append("Apply, reopen and clear a clip-relative post-fit visual mask")
                action("save-timeline").click()
                expect(page.locator("#edit-status")).to_have_text("已保存")
                checks.append("reorder neighbors / replace source / save")
                action("timeline-list").click()
                dialog.locator("[data-action=export-otio]").click()
                with page.expect_download() as otio_download:
                    dialog.locator("[data-download-otio]").click()
                otio = folder / "编辑工程.otio"
                otio_download.value.save_as(otio)
                check(
                    json.loads(otio.read_text())["OTIO_SCHEMA"].startswith("Timeline."), "OTIO file"
                )
                action("timeline-list").click()
                dialog.locator("[data-action=import-otio]").click()
                dialog.locator("[name=file]").set_input_files(str(otio))
                dialog.locator("form[data-form] button[type=submit]").click()
                expect(dialog.locator("h2")).to_have_text("核对导入工程")
                if dialog.locator("[name=ack]").count():
                    dialog.locator("[name=ack]").check()
                dialog.locator("form[data-form] button[type=submit]").click()
                expect(dialog).to_have_count(0)
                expect(page.locator(".room-clip")).to_have_count(2)
                timelines = api.get(
                    "/records", params={"project_id": project["id"], "kind": "Timeline"}
                ).json()["data"]
                check(len(timelines) == 2, "OTIO import must add an independent saved timeline")
                checks.append(
                    "download OTIO file / inspect file / open distinct populated timeline"
                )
                capfile = folder / "镜头.srt"
                capfile.write_text(CAPTIONS)
                pick("import-captions", str(capfile))
                expect(page.locator(".timeline-caption")).to_have_count(2)
                page.reload()
                expect(page.locator(".timeline-caption")).to_have_count(2)
                bad = folder / "错误.srt"
                bad.write_text("1\nnot a time\n字幕\n")
                pick("import-captions", str(bad))
                expect(page.locator(".toast.error")).to_be_visible()
                expect(page.locator(".timeline-caption")).to_have_count(2)
                action("timeline-list").click()
                with page.expect_download() as sub_download:
                    dialog.locator("[data-action=export-vtt]").click()
                sub = folder / "导出.vtt"
                sub_download.value.save_as(sub)
                check("WEBVTT" in sub.read_text() and "第一镜" in sub.read_text(), "caption export")
                close()
                checks.append(
                    "SRT import / reload unsaved draft / reject invalid SRT atomically / download WebVTT"
                )
                page.locator(".room-clip").first.click()
                action("extract-track").click()
                expect(page.locator("[data-role=audio] [data-clip]")).to_have_count(
                    1, timeout=20000
                )
                check(
                    float(page.locator("#clip-volume").input_value()) == 0,
                    "source audio should be muted",
                )
                action("grade-clip").click()
                dialog.locator("[name=saturation]").fill("0.7")
                dialog.locator("form[data-form] button[type=submit]").click()
                expect(dialog).to_have_count(0, timeout=30000)
                action("save-timeline").click()
                expect(page.locator("#edit-status")).to_have_text("已保存")
                checks.append(
                    "detach aligned audio / mute original / apply real derived color grade"
                )
                action("timeline-list").click()
                dialog.locator("[data-action=compare-versions]").click()
                expect(dialog.locator("h2")).to_have_text("比较剪辑版本")
                expect(dialog.locator("#edit-differences")).to_contain_text("字幕")
                close()
                checks.append("compare saved editing revisions by clip, audio and subtitle changes")
                wait_js(
                    "Array.from(document.querySelectorAll('[data-role=video] .room-strip')).some(e=>e.style.backgroundImage.includes('image/jpeg'))"
                )
                wait_js(
                    "document.querySelector('[data-role=audio] .room-strip')?.style.backgroundImage.includes('image/png')"
                )
                page.locator("#clip-inspector").evaluate("e=>e.scrollTo(0,0)")
                report["desktop_preview_rgb"] = wait_js(PREVIEW_PIXELS)
                report["desktop_filmstrip_rgb"] = wait_js(FILMSTRIP_PIXELS)
                shot("desktop-edit")
                page.set_viewport_size({"width": mw, "height": mh})
                page.locator("[data-action=room-panel][data-panel=properties]").click()
                expect(page.locator("#clip-volume")).to_be_visible()
                page.locator("[data-action=room-panel][data-panel=timeline]").click()
                expect(page.locator(".room-timeline")).to_be_visible()
                check(
                    page.evaluate("document.documentElement.scrollWidth<=innerWidth"),
                    "mobile document overflow",
                )
                report["mobile_preview_rgb"] = wait_js(PREVIEW_PIXELS)
                report["mobile_filmstrip_rgb"] = wait_js(FILMSTRIP_PIXELS)
                shot("mobile-edit")
                checks.append(
                    "real source filmstrip / desktop viewport / mobile inspector and timeline"
                )
                page.set_viewport_size({"width": dw, "height": dh})
                action("render-timeline").click()
                dialog.get_by_role("button", name="锁定并渲染", exact=True).click()
                expect(dialog.locator("h2")).to_have_text("成片已生成", timeout=60000)
                with page.expect_download() as file_download:
                    dialog.get_by_role("link", name="下载成片").click()
                mp4 = output / "edited.mp4"
                file_download.value.save_as(mp4)
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-xerror", "-i", str(mp4), "-f", "null", "-"],
                    check=True,
                )
                report["render_bytes"] = mp4.stat().st_size
                report["render_sha256"] = hashlib.sha256(mp4.read_bytes()).hexdigest()
                wait_js("document.querySelector('dialog .room-export-preview')?.readyState>=2")
                shot("export-complete")
                close()
                action("last-export").click()
                expect(dialog.locator("h2")).to_have_text("成片已生成")
                close()
                checks.append(
                    "lock / FFmpeg render / download and decode entire MP4 / reopen delivery in place"
                )
                action("lock-timeline").click()
                dialog.get_by_role("button", name="创建草稿", exact=True).click()
                expect(page.locator("#edit-status")).to_have_text("已保存")
                current_id = api.get(
                    "/records", params={"project_id": project["id"], "kind": "Artifact"}
                ).json()["data"][0]["data"]["timeline_id"]
                current = api.get("/records/" + current_id).json()["data"]
                remote = dict(current["data"], name="协作者的剪辑")
                response = api.patch(
                    "/records/" + current_id,
                    json={
                        "expected_revision": current["version"],
                        "data": remote,
                        "refs": current["refs"],
                    },
                )
                response.raise_for_status()
                page.locator(".room-clip").first.click()
                page.locator("#clip-volume").evaluate(
                    "e=>{e.value='0.5';e.dispatchEvent(new Event('input'))}"
                )
                action("save-timeline").click()
                expect(dialog.locator("h2")).to_have_text("其他人已保存新版本")
                dialog.locator("[data-action=fork-conflict]").click()
                expect(dialog).to_have_count(0)
                expect(page.locator(".room-title h1")).to_contain_text("副本")
                check(
                    api.get("/records/" + current_id).json()["data"]["data"]["name"]
                    == "协作者的剪辑",
                    "conflict resolution overwrote collaborator",
                )
                checks.append(
                    "concurrent revision rejects overwrite / compare remote / save local edits as independent copy"
                )
                profile = "iPhone 15" if engine == "webkit" else "Pixel 7"
                mobile_touch_edit(
                    browser, profile, pw.devices[profile], project, base, api, output, report
                )
                checks.append(
                    "emulated phone touch / project subtitle UI write / server verification / decoded frame and filmstrip pixel checks"
                )
                check(not errors, errors)
                check(
                    all(u.split("#")[-1] == "editor" for u in routes if u.startswith(base)), routes
                )
                report["routes"] = routes
                report["browser_version"] = browser.version
                report["passed"] = True
            except Exception:
                page.screenshot(path=str(output / "failure.png"))
                report["visible_text"] = page.locator("body").inner_text()[-2000:]
                raise
            finally:
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
    run(args.output.resolve(), args.round, args.engine)
