"""Exercise automatic analysis after upload, a manual rescan, and image retrieval in Chromium."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.request

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server
from verify_visual_search import FIXTURES


def verify(output, fixtures):
    fixtures.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    result = {"sources": {}}
    with (
        tempfile.TemporaryDirectory() as workspace,
        server(
            Path(workspace) / "data", output, fixture_env={"AI_SHIP_MATERIAL_WORKER": "1"}
        ) as base,
        httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=90) as client,
    ):

        def post(path, body):
            response = client.post(path, json=body)
            response.raise_for_status()
            return response.json()["data"]

        pid = post("/records", {"kind": "Project", "data": {"name": "素材自动分析验证"}})["id"]
        for name, (url, checksum) in FIXTURES.items():
            path = fixtures / name
            if not path.exists():
                with urllib.request.urlopen(url, timeout=40) as stream:
                    path.write_bytes(stream.read(16 * 1024 * 1024))
            binary = path.read_bytes()
            assert hashlib.sha256(binary).hexdigest() == checksum, name
            response = client.post(
                "/media/import", params={"project_id": pid, "filename": name}, content=binary
            )
            response.raise_for_status()
            result["sources"][name] = {"source": url, "sha256": checksum}

        deadline = time.monotonic() + 120
        while True:
            history = client.get("/production/asset-index/history", params={"project_id": pid})
            history.raise_for_status()
            queue = history.json()["data"]["analysis_queue"]
            if queue.get("completed") == len(FIXTURES):
                break
            if queue.get("failed") or time.monotonic() > deadline:
                raise AssertionError(queue)
            time.sleep(0.5)
        result["automatic_analysis_queue"] = queue
        hit = post(
            "/production/asset-index/query",
            {
                "project_id": pid,
                "channel": "visual",
                "text": "an astronaut wearing a space suit",
                "visual_threshold": 0.15,
            },
        )["items"][0]
        assert hit["name"] == "astronaut.png" and "frame_match" in hit, hit
        result["top_visual_match"] = {k: hit[k] for k in ("name", "semantic_score", "frame_match")}
        all_items = post("/production/asset-index/query", {"project_id": pid})["items"]
        revisions = {
            t["vocabulary"]
            for item in all_items
            for t in item["tags"]
            if t.get("review_state") == "suggested"
        }
        assert len(revisions) == 1, revisions
        result["vocabulary_sha256"] = revisions.pop()
        result["machine_candidates"] = {
            item["name"]: [
                {
                    "label": t["label"],
                    "source_seconds": t["source_seconds"],
                    "cosine": t["cosine"],
                    "rgb_sha256": t["sample_rgb_sha256"],
                }
                for t in item["tags"]
                if t.get("review_state") == "suggested"
            ]
            for item in all_items
        }
        assert "杯装饮品" in [t["label"] for t in result["machine_candidates"]["coffee.png"]]
        assert "花卉植物" in [t["label"] for t in result["machine_candidates"]["flower.mp4"]]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium")
            )
            page = browser.new_page(viewport={"width": 1400, "height": 960}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script("localStorage.setItem('ship-project'," + json.dumps(pid) + ")")
            page.goto(base + "/#module/search/S02")
            page.locator("[data-action=feature-action]").click()
            dlg = page.locator("dialog[open]")
            dlg.get_by_text("索引版本", exact=True).click()
            dlg.get_by_role("button", name="批量分析素材").click()
            status = dlg.locator("[data-index-history] p[role=status]")
            expect(status).to_contain_text("复用 3 份", timeout=60_000)
            result["browser_bulk_status"] = status.inner_text()
            dlg.locator("[name=text]").fill("an astronaut wearing a space suit")
            dlg.locator("[name=channel]").select_option("visual")
            dlg.get_by_role("button", name="查找", exact=True).click()
            expect(dlg.locator("[data-hits] li").first).to_contain_text("astronaut.png")
            dlg.locator("[data-preview]").first.click()
            expect(dlg.locator("#indexed-preview img")).to_have_js_property("naturalWidth", 512)
            result["browser_image_width"] = dlg.locator("#indexed-preview img").evaluate(
                "e=>e.naturalWidth"
            )
            result["browser_version"] = browser.version
            result["page_errors"] = errors
            assert not errors, errors
            browser.close()
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    args = parser.parse_args()
    verify(args.output.resolve(), args.fixture_dir.resolve())
