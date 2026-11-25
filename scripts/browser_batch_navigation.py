"""Check bounded navigation through 10,000 registered cuts using a real API and browser."""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server


def seed(api, directory):
    def create(path, **kwargs):
        response = api.post(path, **kwargs)
        response.raise_for_status()
        return response.json()["data"]

    project = create("/records", json={"kind": "Project", "data": {"name": "队列复查"}})["id"]
    other = create("/records", json={"kind": "Project", "data": {"name": "另一个项目"}})["id"]
    source = directory / "candidate.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=gray:s=160x90:r=25:d=2",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    asset = create(
        "/media/import",
        params={"project_id": project, "filename": source.name},
        content=source.read_bytes(),
    )["version"]
    cuts = [
        {
            "asset_id": asset["id"],
            "version": asset["version"],
            "source_in": i / 10,
            "source_out": (i + 1) / 10,
        }
        for i in range(10)
    ]
    path = "/production/batches"
    for i in range(21):
        create(
            path,
            json={
                "project_id": project,
                "name": f"历史批次 {i:02}",
                "count": 1,
                "slots": [{"choices": cuts[:1]}],
                "idempotency_key": f"historical-{i:02}",
            },
        )
    batch = create(
        path,
        json={
            "project_id": project,
            "name": "一万条候选剪辑",
            "count": 10000,
            "slots": [{"choices": cuts}] * 4,
            "idempotency_key": "navigation-10000",
        },
    )
    # Equal timestamps exercise the ID tie-breaker. A sparse failure and two generation
    # receipts model a queue observed mid-run. No synthetic row represents an encoded file.
    with sqlite3.connect(directory / "browser.sqlite3") as db:
        db.execute(
            "UPDATE production_batches SET created=? WHERE name LIKE '历史批次 %'",
            ("2026-01-01T00:00:00Z",),
        )
        db.execute(
            "UPDATE production_items SET status='failed' WHERE batch=? AND ordinal=9971",
            (batch["id"],),
        )
        db.execute("UPDATE production_batches SET has_generation=1 WHERE id=?", (batch["id"],))
        db.executemany(
            "INSERT INTO production_generation_slots VALUES(?,?,?,?,?,?,?)",
            [(batch["id"], o, 1, f"fixture-{o}", "waiting", None, None) for o in (10, 11)],
        )
    first = api.get(path, params={"project_id": project, "limit": 20}).json()["data"]
    second = api.get(
        path, params={"project_id": project, "limit": 20, "before": first["next_before"]}
    ).json()["data"]
    all_ids = [b["id"] for b in first["batches"] + second["batches"]]
    assert len(set(all_ids)) == len(all_ids) == 22 and second["next_before"] is None
    assert api.get(path, params={"project_id": other, "before": batch["id"]}).status_code == 400
    return project, batch["id"]


def run(output, engine):
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with (
        tempfile.TemporaryDirectory() as temp,
        server(Path(temp), output, fixture_env={"AI_SHIP_BATCH_WORKERS": "0"}) as base,
        httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=30) as api,
        sync_playwright() as pw,
    ):
        pid, bid = seed(api, Path(temp))
        executable = os.environ.get("AI_SHIP_" + engine.upper())
        if engine == "chromium" and not executable:
            executable = shutil.which("chromium")
        browser = getattr(pw, engine).launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.add_init_script("localStorage.setItem('ship-project', " + json.dumps(pid) + ")")
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(base + "/#production/batches")
        q = page.locator(".batch-inspector")
        row = q.locator("[data-ordinal]")
        expect(row).to_have_count(24)
        details = q.locator('[data-ordinal="11"] details')
        details.locator("summary").click()
        # The active row and disclosure survive the background update, including
        # when another row acquires new status or links.
        with sqlite3.connect(Path(temp) / "browser.sqlite3") as db:
            db.execute(
                "UPDATE production_items SET status='failed' WHERE batch=? AND ordinal=10", (bid,)
            )
        expect(q.locator('[data-ordinal="10"]')).to_contain_text("失败", timeout=12000)
        expect(details.locator("summary")).to_be_focused()
        expect(details).to_have_attribute("open", "")
        q.locator("[name=batch_filter]").select_option("failed")
        expect(row).to_have_count(2)
        q.locator("[name=ordinal]").fill("9999")
        q.locator("[name=ordinal]").press("Enter")
        expect(row.first).to_have_attribute("data-ordinal", "9999")
        expect(row).to_have_count(2)
        expect(q.locator("[name=batch_filter]")).to_have_value("")
        expect(q.locator("[data-action=next-page]")).to_be_disabled()
        q.locator("[data-action=prev-page]").click()
        expect(row.first).to_have_attribute("data-ordinal", "9975")
        q.locator("[name=batch_size]").select_option("100")
        expect(row).to_have_count(100)
        q.locator("[data-action=next-page]").click()
        expect(row.first).to_have_attribute("data-ordinal", "101")
        q.locator("[data-action=older-batches]").click()
        expect(q.locator("[data-batch]")).to_have_count(2)
        q.locator("[data-action=newer-batches]").click()
        expect(q.locator("h3")).to_have_text("一万条候选剪辑")
        q.locator("[name=ordinal]").fill("9999")
        q.locator("[name=ordinal]").press("Enter")
        expect(row.first).to_have_attribute("data-ordinal", "9999")
        q.locator("[name=ordinal]").scroll_into_view_if_needed()
        page.screenshot(path=str(output / "desktop.png"))
        for width in (1440, 820, 390):
            page.set_viewport_size({"width": width, "height": 844})
            goto = page.get_by_role("button", name="查看队列", exact=True)
            goto.click()
            expect(q.locator("h2")).to_be_focused()
            expect(page.locator(".batch-main")).to_be_hidden()
            assert (
                abs(
                    q.bounding_box()["width"]
                    - page.locator(".adjustable-layout").bounding_box()["width"]
                )
                < 2
            )
            assert page.locator("#main").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
            q.locator("[name=ordinal]").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"width-{width}.png"))
            q.locator("[data-action=back-to-composer]").click()
            expect(page.locator(".batch-main [name=name]")).to_be_focused()
        page.locator("[data-pane-toggle]").click()
        expect(q).to_be_hidden()
        goto.click()
        expect(q).to_be_visible()
        q.locator("[data-action=back-to-composer]").click()
        expect(q).to_be_hidden()
        assert not errors, errors
        print(
            json.dumps(
                {
                    "engine": engine,
                    "version": browser.version,
                    "queued_cuts": 10000,
                    "history_batches": 22,
                    "viewports": [1440, 820, 390],
                    "page_errors": errors,
                },
                ensure_ascii=False,
            )
        )
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    args = parser.parse_args()
    run(args.output.resolve(), args.engine)
