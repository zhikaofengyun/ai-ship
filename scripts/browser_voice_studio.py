"""Exercise VoiceMap, measured source evidence and actual export from the production screen."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

from browser_regression import VIEWPORTS, server


def ffmpeg(*args):
    return subprocess.check_output(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], timeout=40
    )


def wait_playback(player, minimum):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if player.evaluate("(el)=>el.currentTime") > minimum:
            return
        time.sleep(0.06)
    raise AssertionError("The visible player did not advance beyond " + str(minimum))


def run(output, round_id, engine):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "round": round_id,
        "browser": engine,
        "checks": [],
        "screenshots": [],
        "page_errors": [],
        "passed": False,
        "scope": "Real FastAPI, SQLite, FFmpeg and human-attested sample/visual labels. The fixture uses intelligible synthetic speech. Does not demonstrate speech recognition, automatic SKU recognition or real commercial rights.",
    }
    try:
        with (
            tempfile.TemporaryDirectory() as tmp,
            server(Path(tmp), output) as base,
            sync_playwright() as tools,
        ):
            src = Path(tmp) / "Voice squares.mp4"
            ffmpeg(
                "-f",
                "lavfi",
                "-i",
                "color=white:s=160x120:r=25:d=2,drawbox=x=12:y=34:w=45:h=45:c=blue:t=fill,drawbox=x=86:y=34:w=45:h=45:c=blue:t=fill",
                "-f",
                "lavfi",
                "-i",
                "flite=text='two blue squares':voice=slt,apad=pad_dur=2",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                src,
            )
            args = (
                {"executable_path": os.getenv("AI_SHIP_WEBKIT")}
                if engine == "webkit"
                else {
                    "executable_path": os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium"),
                    "chromium_sandbox": True,
                }
            )
            browser = getattr(tools, engine).launch(**args)
            w, h, *_ = VIEWPORTS.get(str(round_id), VIEWPORTS["1"])
            page = browser.new_page(
                viewport={"width": w, "height": h}, locale="zh-CN", accept_downloads=True
            )
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
            page.set_default_timeout(20000)
            dialog = page.locator("#dialog[open]")

            def shot(name):
                dest = output / (name + ".png")
                page.screenshot(path=str(dest), animations="disabled", scale="css")
                report["screenshots"].append(
                    {"file": dest.name, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
                )

            def button(act):
                return dialog.locator('[data-v-action="' + act + '"]')

            def find(name):
                return dialog.locator('[name="' + name + '"]')

            def submit(action, path):
                with page.expect_response(
                    lambda r: r.url.split("?")[0].endswith(path) and r.request.method == "POST",
                    timeout=90000,
                ) as resp:
                    button(action).click()
                res = resp.value
                assert res.ok, (res.url, res.status, res.text())
                return res.json()["data"]

            page.goto(base)
            page.get_by_role("button", name="新建项目", exact=True).click()
            find("name").fill("声画实测")
            find("goals").fill("Match spoken words to two blue squares")
            dialog.locator("form[data-form] button[type=submit]").click()
            expect(page).to_have_url(re.compile("#production$"))
            page.locator("[data-production-files]").set_input_files(str(src))
            expect(page.locator(".director-source input:checked")).to_have_count(1, timeout=40000)
            page.get_by_text("声音与叙事", exact=True).last.click()
            page.get_by_role("button", name="口播与画面对齐", exact=True).click()
            dialog.get_by_text("从原片取出口播", exact=True).click()
            find("audio-source").select_option(index=1)
            submit("extract", "/extract-audio")
            expect(dialog.locator("audio")).to_be_visible()
            find("words").fill("two")
            find("csubject").fill("蓝色方块")
            find("cquantity").fill("2")
            find("cunit").fill("个")
            find("start").fill("0")
            find("end").fill("0.34")
            dialog.locator("audio").evaluate("(el)=>el.play()")
            wait_playback(dialog.locator("audio"), 0.12)
            dialog.locator("audio").evaluate("(el)=>el.pause()")
            find("heard").check()
            button("add").click()
            dialog.locator("details[data-v-edit] summary").click()
            find("words").fill("blue squares")
            find("csubject").fill("蓝色方块")
            find("cquantity").fill("2")
            find("cunit").fill("个")
            find("start").fill("0.6")
            find("end").fill("1.2")
            find("heard").check()
            button("add").click()
            m = submit("save", "/voice-studio/maps")["map"]
            r = submit("match", "/voice-studio/plans")
            assert (
                r["plan"]["data"]["slots"][0]["time_ok"]
                and not r["plan"]["data"]["slots"][0]["semantic_ok"]
            )
            expect(button("adopt")).to_have_count(0)
            shot("unfilled-semantic-slot")
            report["checks"].append(
                "Real audio extraction/playback, separately confirmed acoustic range, empty visual slot blocks adoption."
            )
            for count in (1, 2):
                dialog.locator('[data-step="visual"]').click()
                find("vsubject").fill("蓝色方块")
                find("vquantity").fill(str(count))
                find("vunit").fill("个")
                find("end").fill("1.8")
                find("frame").fill("0.6")
                find("note").fill("候选数量 " + str(count))
                dialog.locator("video").evaluate("(e)=>{e.currentTime=.6;return e.play()}")
                wait_playback(dialog.locator("video"), 0.65)
                dialog.locator("video").evaluate("(e)=>e.pause()")
                submit("observe", "/voice-studio/observations")
                expect(button("match")).to_be_visible()
            p = submit("match", "/voice-studio/plans")["plan"]
            slot = p["data"]["slots"][0]
            assert (
                sum("quantity" in c["differences"] for c in slot["candidates"]) == 1
                and slot["selected"]
            )
            find("lock-0").select_option(slot["selected"]["id"])
            p = submit("match", "/voice-studio/plans")["plan"]
            assert p["data"]["slots"][0]["locked"]
            shot("locked-visual-binding")
            report["checks"].append(
                "Competing explicit visual observations, quantity counterexample, measured media/frames, locked choice survives replanning."
            )
            dialog.locator("[data-rhythm] summary").click()
            find("tolerance").fill("200")
            solved = submit("rhythm", "/voice-studio/rhythm")["plan"]
            rate = solved["data"]["rhythm"]["rate"]
            edge = solved["data"]["rhythm"]["boundaries"][0]
            assert edge["cut_sample"] == round(0.34 * rate)
            report["checks"].append(
                "Saved a sample-clock cut at the locked first shot boundary, inside the speech gap and 200 ms tolerance."
            )
            adopted = submit("adopt", "/voice-studio/adopt")
            assert adopted["bindings"][0]["data"]["clip_use_ids"] == ["voice-1"]
            artifact = submit("export", "/media/render")["artifact"]
            expect(dialog.get_by_role("link", name="下载成片")).to_be_visible()
            file = output / "voice-cut.mp4"
            with page.expect_download() as download:
                dialog.get_by_role("link", name="下载成片").click()
            download.value.save_as(str(file))
            probe = json.loads(
                subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(file)]
                )
            )
            assert {s["codec_name"] for s in probe["streams"]} >= {"h264", "aac"}
            audio = ffmpeg("-i", file, "-vn", "-f", "s16le", "-")
            assert len(set(audio)) > 20
            shot("actual-export")
            report["checks"].append(
                "Adopted original speech and chosen visual, froze and rendered on the same screen, downloaded an independently decoded H.264/AAC file."
            )
            report["clock"] = m["data"]["clock"]
            report["binding"] = adopted["bindings"][0]["data"]
            report["movie"] = {
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                "bytes": file.stat().st_size,
                "artifact_hash": artifact["data"]["content_hash"],
            }
            dialog.get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="生成或导入口播", exact=True).click()
            find("text").fill("Listen to the two blue squares.")
            find("evidence").fill("合成试验项目的音色使用审核")
            find("expires").fill("2035-05-01")
            with page.expect_response(
                lambda res: res.url.endswith("/narration/synthesize")
            ) as response:
                dialog.get_by_role("button", name="生成并试听").click()
            assert response.value.ok, response.value.text()
            narration = response.value.json()["data"]["audio"]
            expect(dialog.get_by_role("heading", name="试听配音")).to_be_visible()
            dialog.locator("audio").evaluate("(el)=>el.play()")
            wait_playback(dialog.locator("audio"), 0.15)
            dialog.get_by_role("button", name="对齐口播与画面").click()
            expect(dialog.locator("[name=audio]")).to_have_value(narration["id"])
            expect(dialog.locator("[name=script]")).to_have_value(
                narration["data"]["script_version"]["id"]
            )
            report["checks"].append(
                "Created an English script and time-bounded voice review, played the actual synthesized WAV and opened voice alignment on that exact audio and script."
            )
            report["passed"] = not report["page_errors"]
            assert report["passed"]
            browser.close()
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round", default="1")
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    a = parser.parse_args()
    run(a.output.resolve(), a.round, a.engine)
