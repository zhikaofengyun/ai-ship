"""Open the normal capture intake page and verify receipts against actual imported bytes."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

import httpx
from playwright.sync_api import sync_playwright, expect

from browser_regression import server


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp:
        temp = Path(tmp)
        card = temp / "capture"
        card.mkdir()
        first = card / "镜头一.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=navy:s=96x64:r=10:d=0.4",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                str(first),
            ],
            check=True,
        )
        second = card / "镜头备份.mp4"
        shutil.copyfile(first, second)
        cfg = json.dumps(
            [
                dict(
                    tenant_id="local",
                    project_id="*",
                    alias="capture",
                    path=str(card),
                    stable_seconds=1,
                )
            ]
        )
        with (
            server(temp, output, fixture_env={"AI_SHIP_CAPTURE_ROOTS": cfg}) as base,
            httpx.Client(base_url=base + "/api/v1", trust_env=False) as client,
            sync_playwright() as pw,
        ):
            p = client.post("/records", json={"kind": "Project", "data": {"name": "实拍入库"}})
            p.raise_for_status()
            pid = p.json()["data"]["id"]
            browser = pw.chromium.launch(
                executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium")
            )
            page = browser.new_page(viewport={"width": 1350, "height": 900}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script("localStorage.setItem('ship-project'," + json.dumps(pid) + ")")
            page.goto(base + "/#assets")
            page.get_by_role("button", name="采集目录", exact=True).click()
            page.locator("dialog textarea").fill(
                first.name + "\t" + sha256(first.read_bytes()).hexdigest() + "\n" + second.name
            )
            page.get_by_role("button", name="观察并导入").click()
            expect(page.locator("dialog [data-progress]")).to_contain_text(
                "已导入 1", timeout=30000
            )
            expect(page.locator("dialog [data-progress]")).to_contain_text("已复用 1")
            expect(page.locator("dialog table tbody tr")).to_have_count(2)
            page.screenshot(path=output / "capture-directory.png")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="采集目录", exact=True).click()
            expect(page.locator("dialog [data-progress]")).to_contain_text("已复用 1")
            a = client.get("/records", params={"project_id": pid, "kind": "Asset"}).json()["data"]
            sources = client.get("/records", params={"project_id": pid, "kind": "Source"}).json()[
                "data"
            ]
            assert len(a) == 1 and len(sources) == 2
            assert a[0]["data"]["content_hash"] == sha256(first.read_bytes()).hexdigest()
            assert {s["data"]["directory_origin"]["relative_path"] for s in sources} == {
                first.name,
                second.name,
            }
            assert not errors, errors
            result = {
                "checks": [
                    "normal media library entry",
                    "optional manifest with SHA-256",
                    "two stable physical paths -> one immutable asset and two provenance receipts",
                    "reopening restores persisted intake state",
                ],
                "page_errors": errors,
            }
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(result, ensure_ascii=False))
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
