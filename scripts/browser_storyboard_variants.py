"""N04 through the real production workspace, including reload, adoption and media readback."""

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
from playwright.sync_api import expect, sync_playwright

from browser_regression import VIEWPORTS, server
from browser_opening_text import ffmpeg


def fixture(path, word, position, frequency):
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=white:s=480x240:r=25:d=1.6",
        "-f",
        "lavfi",
        "-i",
        f"sine=f={frequency}:d=1.6",
        "-vf",
        f"drawbox=x={position}:y=145:w=110:h=84:color=black:t=fill,drawtext=fontcolor=black:fontsize=60:text={word}:x=(w-tw)/2:y=28",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",
        "-c:a",
        "aac",
        path,
    )


def run(output, round_id, engine):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": False,
        "round": str(round_id),
        "engine": engine,
        "checks": [],
        "screenshots": [],
        "page_errors": [],
        "scope": "Local same-project storyboard selection, locked primary interval, measured source comparisons, independent readback of decoded order/streams. No audience or platform conclusions.",
    }
    try:
        with (
            tempfile.TemporaryDirectory() as td,
            server(Path(td), output) as base,
            sync_playwright() as pw,
            httpx.Client(base_url=base, trust_env=False, timeout=20) as http,
        ):
            t = Path(td)
            files = [
                (t / "a.mp4", "LEAF", 6, 125),
                (t / "b.mp4", "RIVER", 130, 500),
                (t / "c.mp4", "MOUNTAIN", 260, 2000),
            ]
            for f, word, pos, hz in files:
                fixture(f, word, pos, hz)
            launch = (
                {"executable_path": os.getenv("AI_SHIP_WEBKIT")}
                if engine == "webkit"
                else {
                    "executable_path": os.getenv("AI_SHIP_CHROMIUM")
                    or shutil.which("chromium-headless-shell")
                    or shutil.which("chromium"),
                    "chromium_sandbox": True,
                }
            )
            browser = getattr(pw, engine).launch(**launch)
            width, height, *_ = VIEWPORTS.get(str(round_id), VIEWPORTS["1"])
            page = browser.new_page(
                viewport={"width": width, "height": height}, locale="zh-CN", accept_downloads=True
            )
            page.set_default_timeout(20000)
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)))

            def action(button, url):
                with page.expect_response(
                    lambda r: r.url == base + "/api/v1" + url and r.request.method == "POST",
                    timeout=90000,
                ) as response:
                    button.click()
                r = response.value
                assert r.ok, (r.status, r.text())
                return r.json()["data"]

            def snap(name):
                path = output / (name + ".png")
                page.screenshot(path=str(path), scale="css", animations="disabled")
                report["screenshots"].append(path.name)

            page.goto(base)
            page.get_by_role("button", name="新建项目", exact=True).click()
            form = page.locator("#dialog[open] form[data-form]")
            form.locator("[name=name]").fill("镜头变体验收")
            form.locator("[name=goals]").fill("固定开场，为后半段选择另一条素材")
            form.locator("button[type=submit]").click()
            expect(page).to_have_url(re.compile(r"#production$"))
            page.locator("[data-production-files]").set_input_files([str(x[0]) for x in files])
            expect(page.locator(".director-source input:checked")).to_have_count(3, timeout=45000)
            ids = {
                page.get_by_role("checkbox", name=f.name, exact=True).get_attribute(
                    "data-source"
                ): word
                for f, word, _, _ in files
            }
            page.get_by_role("checkbox", name="c.mp4", exact=True).uncheck()
            page.locator("#production-goal").fill("固定开场并选择不同的结尾")
            page.locator("[name=target_seconds]").fill("2")
            page.get_by_text("输出画幅", exact=True).click()
            page.locator("[name=width]").fill("480")
            page.locator("[name=height]").fill("240")
            page.locator("[name=fps]").select_option("25")
            initial = action(
                page.locator("#production-form button[type=submit]"), "/production/auto-edits"
            )
            run_id = initial["session"]["id"]
            shots = initial["plan"]["data"]["shots"]
            assert len(shots) == 2
            vurl = "/production/variants/" + run_id
            # Hold the initial history request while a person opens the source picker.
            pending_history = []
            page.route(base + "/api/v1" + vurl, lambda route: pending_history.append(route))
            page.locator("[data-variant-group] > summary").click()
            page.get_by_text("可替换素材", exact=True).click()
            page.locator("[name=variant_pool]").filter(visible=True).last.wait_for()
            assert pending_history
            with page.expect_response(lambda r: r.url == base + "/api/v1" + vurl):
                pending_history[0].continue_()
            page.unroute(base + "/api/v1" + vurl)
            page.locator("[data-variant-form]").get_by_role(
                "checkbox", name="c.mp4", exact=True
            ).check()
            page.get_by_text("锁定镜头与比较条件", exact=True).click()
            page.locator('[name=variant_lock][value="0"]').check()
            page.locator("[name=variant_count]").fill("3")
            fam = action(page.get_by_role("button", name="比较镜头方案"), vurl)
            assert fam["actual"] == 1 and fam["requested"] == 3, fam
            assert fam["locked_slots"] == [0] and fam["audience_overlap"] == "unknown"
            c = fam["candidates"][0]
            assert (
                c["shots"][0]["asset_id"] == shots[0]["asset_id"]
                and ids[c["shots"][1]["asset_id"]] == "MOUNTAIN"
            )
            assert all(x > 0 for x in c["vs_base"].values())
            report["checks"].append(
                "Uploaded three audiovisual sources; built a two-shot AI plan, held its opening and returned one admissible option out of three requested, with four measured differences."
            )
            panel = page.locator("[data-variant-group]")
            panel.locator("section details > summary").first.click()
            snap("candidate")
            page.reload()
            panel = page.locator("[data-variant-group]")
            panel.locator(":scope > summary").click()
            panel.locator("section details > summary").first.click()
            choice = panel.locator("form[data-plan]").first
            choice.locator("[name=review]").fill("保留片头并新增山景及同期声，已核对。")
            edited = action(choice.locator("button"), vurl + "/" + fam["id"] + "/select")
            cid = edited["session"]["id"]
            assert edited["plan"]["id"] == c["plan"]["id"]
            assert (
                edited["session"]["data"]["base_session_ref"]["id"] == run_id
                and not edited["timeline"]
            )
            report["checks"].append(
                "Reloaded the persisted family and opened its selected child proposal on the same page with parent version and human decision intact."
            )
            auto = "/production/auto-edits/" + cid
            draft = action(page.locator('[data-action="approve"]'), auto + "/approve")
            assert [s["asset_id"] for s in draft["timeline"]["data"]["clips"]] == [
                s["asset_id"] for s in c["shots"]
            ]
            done = action(page.locator('[data-action="render"]'), auto + "/render")
            assert done["session"]["data"]["stage"] == "rendered"
            with page.expect_download() as dl:
                page.get_by_role("link", name="下载 MP4").click()
            movie = output / "variant.mp4"
            dl.value.save_as(str(movie))
            words = []
            for i, s in enumerate(edited["plan"]["data"]["shots"]):
                frame = output / f"slot-{i+1}.png"
                ffmpeg(
                    "-ss",
                    str(s["timeline_start"] + 0.18),
                    "-i",
                    movie,
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    frame,
                )
                ocr = subprocess.run(
                    ["tesseract", str(frame), "stdout", "-l", "eng", "--psm", "11"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                decoded = " ".join(ocr.stdout.upper().split())
                target = ids[s["asset_id"]]
                assert target in decoded, (target, decoded)
                words.append(decoded)
            ffmpeg("-i", movie, "-map", "0:a:0", "-f", "null", "-")
            parent = http.get("/api/v1/production/auto-edits/" + run_id).json()["data"]
            assert parent["timeline"] is None and parent["plan"] == initial["plan"]
            report["checks"].append(
                "Approved and rendered the child via existing controls, decoded both video segments and the audio stream, matched their expected on-screen words and re-read the unchanged parent."
            )
            snap("actual-export")
            report.update(
                candidate=c,
                initial_plan=initial["plan"]["id"],
                selected_run=cid,
                output_sha256=hashlib.sha256(movie.read_bytes()).hexdigest(),
                decoded_words=words,
            )
            assert not report["page_errors"], report["page_errors"]
            report["passed"] = True
            browser.close()
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    p.add_argument("--round", default="1")
    a = p.parse_args()
    run(a.output.resolve(), a.round, a.engine)
