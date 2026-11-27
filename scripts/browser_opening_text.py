"""Exercise an explicit opening-frame requirement through the actual production workspace."""

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

EXPECTED = "FRESH LIME"
# The free-text preference deliberately disagrees with the literal requirement. Local visual
# planning ranks sources by observed goal words, so the conflict uses no stubbed model or UI.
GOAL = "Prefer RED TEST BOX. Keep both available recordings in the sequence."
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        capture_output=True,
        check=True,
        timeout=45,
    ).stdout


def clip(path, words):
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=640x360:r=25:d=1.6",
        "-vf",
        f"drawtext=fontfile={FONT}:text={words}:fontcolor=black:fontsize=76:x=(w-tw)/2:y=(h-th)/2",
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        path,
    )


def ocr_first_frame(movie, image):
    # Select the first decoded presentation frame; never seek to the middle of the clip.
    ffmpeg("-i", movie, "-map", "0:v:0", "-frames:v", "1", "-threads", "1", image)
    result = subprocess.run(
        ["tesseract", str(image), "stdout", "-l", "eng", "--psm", "6"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return " ".join(result.stdout.upper().split())


def run(output, round_id, engine):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "round": str(round_id),
        "engine": engine,
        "passed": False,
        "checks": [],
        "page_errors": [],
        "screenshots": [],
        "scope": "Local visual analysis, printed synthetic source labels, actual HTTP/SQLite, ffmpeg and Tesseract. No external provider or product-identity validation.",
    }
    route = "/api/v1/production/auto-edits"
    try:
        with (
            tempfile.TemporaryDirectory() as td,
            server(Path(td), output) as base,
            sync_playwright() as browser_tools,
            httpx.Client(base_url=base, trust_env=False, timeout=15) as api,
        ):
            td = Path(td)
            fixtures = [
                (td / "neutral-one.mp4", "RED TEST BOX"),
                (td / "neutral-two.mp4", EXPECTED),
            ]
            for i, (path, words) in enumerate(fixtures):
                clip(path, words)
                assert ocr_first_frame(path, td / f"known-{i}.png") == words
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
            browser = getattr(browser_tools, engine).launch(**launch)
            width, height, *_ = VIEWPORTS.get(str(round_id), VIEWPORTS["1"])
            report["browser_version"] = browser.version
            page = browser.new_page(
                viewport={"width": width, "height": height}, locale="zh-CN", accept_downloads=True
            )
            page.set_default_timeout(15000)
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
            loc = page.locator

            def shot(name):
                path = output / (name + ".png")
                page.screenshot(path=str(path), scale="css", animations="disabled")
                report["screenshots"].append(
                    {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                )

            def submit(button, suffix=""):
                with page.expect_response(
                    lambda r: r.url == base + route + suffix and r.request.method == "POST",
                    timeout=90000,
                ) as response:
                    button.click()
                r = response.value
                assert r.ok, (r.status, r.text())
                return r.json()["data"]

            def detail(run_id):
                response = api.get(route + "/" + run_id)
                response.raise_for_status()
                return response.json()["data"]

            page.goto(base)
            page.get_by_role("button", name="新建项目", exact=True).click()
            dialog = loc("#dialog[open]")
            dialog.locator("[name=name]").fill("首镜实帧核对")
            dialog.locator("[name=goals]").fill(GOAL)
            dialog.locator("form[data-form] button[type=submit]").click()
            expect(page).to_have_url(re.compile(r"#production/batches$"))
            page.locator('.batch-head a[href="#production"]').click()
            expect(page).to_have_url(re.compile("#production$"))
            loc("[data-production-files]").set_input_files([str(f[0]) for f in fixtures])
            expect(loc(".director-source input:checked")).to_have_count(2, timeout=45000)
            ids = [
                page.get_by_role("checkbox", name=path.name).get_attribute("data-source")
                for path, _ in fixtures
            ]
            expect(loc("[name=mode]")).to_have_value("local_vision")
            loc("[name=target_seconds]").fill("3.2")
            page.get_by_text("输出画幅", exact=True).click()
            loc("[name=width]").fill("640")
            loc("[name=height]").fill("360")
            loc("[name=fps]").select_option("25")
            page.get_by_text("首镜文字要求", exact=True).click()
            loc("[name=opening_text]").fill(EXPECTED)
            original = submit(loc("#production-form button[type=submit]"))
            rid = original["session"]["id"]
            check = original["plan"]["data"]["opening_check"]
            report["input_check"] = check
            assert (
                original["session"]["data"]["stage"] == "blocked" and original["timeline"] is None
            )
            assert check["expected_text"] == EXPECTED and check["status"] == "conflict"
            assert check["observation"]["asset_id"] == ids[0]
            assert [w["text"].upper() for w in check["observation"]["words"]] == [
                "RED",
                "TEST",
                "BOX",
            ]
            match = next(alt for alt in check["alternatives"] if alt["asset_id"] == ids[1])
            review = loc('[data-opening-check="conflict"]')
            expect(review).to_contain_text("RED TEST BOX")
            expect(review).to_contain_text(EXPECTED)
            expect(loc('[data-action="approve"]')).to_have_count(0)
            expect(loc('[data-action="render"]')).to_have_count(0)
            wrong_check = check.copy()
            shot("blocked-opening")
            report["checks"].append(
                "Two real videos; OCR observed RED TEST BOX first, explicit FRESH LIME conflicts; UI exposes the actual observation and withholds adoption/render."
            )
            pick = review.locator('[data-action="replace-opening"]').filter(
                has_text=fixtures[1][0].name
            )
            expect(pick).to_have_count(1)
            pick.click()
            expect(dialog.locator("[name=asset_id]")).to_have_value(ids[1])
            assert float(dialog.locator("[name=source_in]").input_value()) == match["source_in"]
            assert float(dialog.locator("[name=source_out]").input_value()) == match["source_out"]
            expect(dialog.locator("[name=reason]")).not_to_be_empty()
            assert detail(rid)["plan"] == original["plan"]
            candidate = submit(
                dialog.get_by_role("button", name="保留原案并创建候选", exact=True),
                "/" + rid + "/replace-shot",
            )
            cid = candidate["session"]["id"]
            assert cid != rid and candidate["session"]["data"]["stage"] == "review"
            assert candidate["plan"]["data"]["opening_check"]["status"] == "matched"
            assert candidate["plan"]["data"]["shots"][0]["asset_id"] == ids[1]
            assert candidate["plan"]["data"]["shots"][1:] == original["plan"]["data"]["shots"][1:]
            expect(loc('[data-opening-check="matched"]')).to_be_visible()
            expect(loc('[data-action="approve"]')).to_be_visible()
            shot("matched-candidate")
            report["checks"].append(
                "Clicked the measured suggested alternative, verified its source revision/range, submitted a new candidate and retained the old plan and other shots."
            )
            approved = submit(loc('[data-action="approve"]'), "/" + cid + "/approve")
            assert (
                approved["timeline"]
                and approved["timeline"]["data"]["clips"][0]["asset_id"] == ids[1]
            )
            movie = submit(loc('[data-action="render"]'), "/" + cid + "/render")
            assert movie["session"]["data"]["stage"] == "rendered" and movie["artifact"]
            expect(loc('[data-stage="rendered"]')).to_be_visible()
            with page.expect_download(timeout=30000) as dl:
                page.get_by_role("link", name="下载 MP4").click()
            path = output / "corrected-opening.mp4"
            dl.value.save_as(str(path))
            ffmpeg("-xerror", "-i", path, "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-")
            decoded = ocr_first_frame(path, output / "first-decoded-frame.png")
            assert decoded == EXPECTED, decoded
            export_check = movie["artifact"]["data"]["qc"]["opening_check"]
            assert export_check["timeline_id"] == approved["timeline"]["id"]
            assert movie["timeline"]["data"]["locked"] is True
            assert (
                export_check["timeline_version"]
                == movie["session"]["data"]["timeline_ref"]["version"]
            )
            assert export_check["timeline_version"] == movie["artifact"]["data"]["timeline_version"]
            assert export_check["origin"]["asset_id"] == ids[1]
            for reading in (export_check["preflight"], export_check["encoded_first_frame"]):
                assert reading["at"] == 0 and reading["status"] == "read"
                assert " ".join(w["text"].upper() for w in reading["words"]) == EXPECTED
            saved = detail(rid)
            assert saved["plan"] == original["plan"] and saved["timeline"] is None
            assert saved["plan"]["data"]["opening_check"] == wrong_check
            report["checks"].append(
                "Adopted and rendered through the browser, downloaded the MP4, decoded all streams and independently read FRESH LIME from the actual first presentation frame."
            )
            report["corrected"] = {
                "original": rid,
                "candidate": cid,
                "blocked_observation": check,
                "matched_observation": candidate["plan"]["data"]["opening_check"],
                "export_check": export_check,
                "download_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "first_frame_ocr": decoded,
                "artifact": movie["artifact"]["id"],
            }
            page.get_by_role("button", name="新建任务", exact=True).click()
            expect(loc("#production-form")).to_be_visible()
            opening = loc("[name=opening_text]")
            if opening.input_value():
                if not opening.is_visible():
                    page.get_by_text("首镜文字要求", exact=True).click()
                opening.fill("")
            loc("#production-goal").fill("Use RED TEST BOX. Allow any opening shot.")
            for cb in loc(".director-source input").all():
                cb.check()
            free = submit(loc("#production-form button[type=submit]"))
            assert (
                "opening_check" not in free["plan"]["data"]
                and free["session"]["data"]["stage"] == "review"
            )
            expect(loc("[data-opening-check]")).to_have_count(0)
            expect(loc('[data-action="approve"]')).to_be_visible()
            report["checks"].append(
                "Free-text legacy task with no literal constraint still returns an editable proposal and exposes adoption."
            )
            page.get_by_role("button", name="新建任务", exact=True).click()
            expect(loc("#production-form")).to_be_visible()
            loc("#production-goal").fill(GOAL)
            if not opening.is_visible():
                page.get_by_text("首镜文字要求", exact=True).click()
            opening.fill("ORANGE PEEL")
            for cb in loc(".director-source input").all():
                cb.check()
            missing = submit(loc("#production-form button[type=submit]"))
            missing_check = missing["plan"]["data"]["opening_check"]
            assert missing["session"]["data"]["stage"] == "blocked" and missing["timeline"] is None
            assert missing_check["status"] == "unverified" and not missing_check["alternatives"]
            expect(loc('[data-opening-check="unverified"]')).to_contain_text("ORANGE PEEL")
            expect(loc('[data-action="replace-opening"]')).to_have_count(0)
            expect(loc('[data-action="approve"]')).to_have_count(0)
            expect(loc('[data-action="render"]')).to_have_count(0)
            shot("unverified-opening")
            report["unverified"] = missing_check
            report["checks"].append(
                "No source shows ORANGE PEEL. The UI reports unverified, offers no unmeasured alternative, and withholds approval and rendering."
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
    args = p.parse_args()
    run(args.output.resolve(), args.round, args.engine)
