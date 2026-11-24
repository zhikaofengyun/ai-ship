"""Restart a new API process after an actual encoded output lost its worker; recover in Y04."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from hashlib import sha256
from tempfile import TemporaryDirectory
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from browser_regression import ROOT, server

sys.path.insert(0, str(ROOT))


def crash_fixture(workspace):
    from app import media
    from app.auth import LOCAL_ACTOR
    from app.store import get_store
    from tests.test_render_recovery import make_timeline

    store = get_store()
    project = store.create("Project", {"name": "中断恢复回归"}, tenant_id="local", actor_id="test")
    timeline, _ = make_timeline(store, project["id"], workspace)

    def stop_worker(output, *args):
        if output.stat().st_size <= 0:
            raise RuntimeError("The synthetic worker must have produced real bytes before stoppage")
        os._exit(73)  # simulate abrupt loss before any QC or artifact transaction

    with patch("app.media.technical_qc", side_effect=stop_worker):
        media.render_timeline(
            store,
            LOCAL_ACTOR,
            {"project_id": project["id"], "timeline_id": timeline["id"], "expected_revision": 1},
        )


def verify(engine, output):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        crash = subprocess.run(
            [sys.executable, __file__, "--crash-fixture", tmp],
            env={
                **os.environ,
                "AI_SHIP_MODE": "local",
                "AI_SHIP_DB": str(directory / "browser.sqlite3"),
                "AI_SHIP_DATA_DIR": tmp,
                "TMPDIR": tmp,
            },
        )
        assert crash.returncode == 73, crash.returncode
        with server(directory, output) as base, sync_playwright() as pw:
            browser = (
                pw.chromium.launch(
                    executable_path=os.getenv("AI_SHIP_CHROMIUM")
                    or shutil.which("chromium-headless-shell")
                    or shutil.which("chromium"),
                    chromium_sandbox=True,
                )
                if engine == "chromium"
                else pw.webkit.launch(executable_path=os.getenv("AI_SHIP_WEBKIT"))
            )
            page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/#module/render/Y04")
            page.get_by_role("button", name="核验与恢复渲染", exact=True).click()
            dialog = page.locator("#dialog[open]")
            expect(dialog).to_contain_text("24000/1001 fps")
            first_id = dialog.locator("[name=attempt]").input_value()
            dialog.get_by_label("恢复原因").fill("原工作进程已停止，重新验证全片")
            dialog.get_by_label("已核对运行状态", exact=False).check()
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/recover")
            ) as response:
                dialog.get_by_role("button", name="重新渲染全片").click()
            result = response.value.json()["data"]
            expect(dialog).to_contain_text("全片解码通过")
            expect(dialog.locator("video")).to_have_attribute("src", result["preview_url"])
            stream = page.request.get(base + result["preview_url"])
            assert stream.status == 200
            assert sha256(stream.body()).hexdigest() == result["manifest"]["data"]["output_hash"]
            assert (
                result["qc"]["checks"]["full_decode"] and result["qc"]["checks"]["exact_frame_rate"]
            )
            assert result["job"]["data"]["previous_attempt"]["id"] == first_id
            dialog.locator("[name=attempt]").select_option(first_id)
            dialog.get_by_role("button", name="查看后续尝试").click()
            expect(dialog.locator("[name=attempt]")).to_have_value(result["job"]["id"])
            dialog.get_by_label("恢复原因").fill("核对完整已生成版本的实际画面和声音")
            dialog.get_by_role("button", name="重新渲染全片").click()
            expect(dialog).to_contain_text("完整画面和音频解码哈希一致", timeout=20000)
            page.screenshot(path=str(output / "recovered.png"))
            assert not errors, errors
            browser.close()
        return {
            "engine": engine,
            "passed": True,
            "source": "worker exited after real encode",
            "full_decode": True,
            "hash_verified": True,
            "direct_rerun_decoded_streams_match": True,
        }


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--output", type=Path)
    args.add_argument("--engine", choices=["chromium", "webkit"])
    args.add_argument("--crash-fixture", type=Path)
    ns = args.parse_args()
    if ns.crash_fixture:
        crash_fixture(ns.crash_fixture)
    elif ns.output:
        reports = [
            verify(e, ns.output if ns.engine else ns.output / e)
            for e in ([ns.engine] if ns.engine else ["chromium", "webkit"])
        ]
        result = reports[0] if ns.engine else reports
        (ns.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    else:
        args.error("Select --output or --crash-fixture")
