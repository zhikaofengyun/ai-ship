"""Playwright verification of indexed source lookup, rights, mix and final-file review."""

import argparse
import csv
import re
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server


def check_post_review(page, dialog, post, project, artifact, tmp):
    account = post(
        "/records",
        kind="Account",
        project_id=project,
        data={"name": "合成测试账号", "provider": "manual"},
        reason="隔离测试",
    )
    posts = []
    for key in ("sample-a", "sample-b"):
        publication = post(
            "/production/publications",
            project_id=project,
            artifact_id=artifact["id"],
            account_id=account["id"],
            title=key,
        )
        receipt = post(
            "/production/publications/" + publication["id"] + "/receipt",
            expected_revision=1,
            platform_post_id=key,
            post_url="https://example.test/post/" + key,
            observed_at="2026-01-01T00:00:00Z",
            visibility="public",
            evidence="合成测试回执，没有对外发布",
        )
        posts.append(receipt["post"])
    common = {
        "account_id": account["id"],
        "metric_name": "views",
        "unit": "count",
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-01-02T00:00:00Z",
        "observed_at": "2026-01-04T00:00:00Z",
        "source": "合成报表示例",
        "counting_mode": "interval",
        "paid_or_organic": "organic",
        "data_mature_after": "2026-01-03T00:00:00Z",
    }
    rows = [{**common, "post_record_id": r["id"], "value": i} for r, i in zip(posts, (2, 8))]
    file = Path(tmp) / "archive.csv"
    with file.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    page.goto(page.url.split("#")[0] + "#module/metrics/M09")

    def menu():
        page.locator(".feature-title [data-action=feature-action]").click()

    menu()
    dialog.locator("[data-csv]").click()
    dialog.locator("input[type=file]").set_input_files(file)
    dialog.locator("form[data-form] button[type=submit]").click()
    expect(dialog).to_contain_text("新增 2 条")
    dialog.get_by_role("button", name="关闭", exact=True).click()
    menu()
    dialog.locator("[data-compare]").click()
    for field, key in (("baseline", "sample-a"), ("comparison", "sample-b")):
        option = dialog.locator("[name=" + field + "] option").filter(has_text=key)
        dialog.locator("[name=" + field + "]").select_option(option.get_attribute("value"))
    for name, value in {
        "hypothesis": "标题可能影响表现",
        "counterevidence": "时段也可能影响表现",
        "proposed_change": "调整标题",
        "experiment_method": "在同账号同窗口再比较",
        "stop_rule": "观察满一轮停止",
    }.items():
        dialog.locator("[name=" + name + "]").fill(value)
    dialog.locator("form[data-form] button[type=submit]").click()
    expect(dialog).to_contain_text("后者减去参照值为 6")
    expect(dialog).to_contain_text("整帖观测不支持片段归因")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory() as tmp,
        server(Path(tmp), out) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=45) as api,
    ):

        def post(path, **body):
            r = api.post(path, json=body)
            r.raise_for_status()
            return r.json()["data"]

        pid = post("/projects", name="混音与检索", market="GB")["id"]
        source = Path(tmp) / "table-source.mp4"
        audio = Path(tmp) / "clean-tone.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=gray:s=160x160:d=2",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-y",
                str(source),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=260:duration=2",
                "-y",
                str(audio),
            ],
            check=True,
        )
        r = api.post(
            "/media/import",
            params={"project_id": pid, "filename": source.name},
            content=source.read_bytes(),
        )
        r.raise_for_status()
        version = r.json()["data"]["version"]
        timeline = post(
            "/records",
            project_id=pid,
            kind="Timeline",
            data={
                "name": "测试时间线",
                "format": {"width": 160, "height": 160, "fps": 25},
                "status": "draft",
                "locked": False,
                "clips": [
                    {
                        "id": "one",
                        "asset_id": version["id"],
                        "source_in": 0,
                        "source_out": 2,
                        "volume": 1,
                    }
                ],
                "audio_tracks": [],
            },
            refs=[{"id": version["id"], "version": version["version"]}],
            reason="隔离测试",
        )
        opts = (
            dict(
                executable_path=os.getenv("AI_SHIP_CHROMIUM") or shutil.which("chromium"),
                chromium_sandbox=True,
            )
            if args.engine == "chromium"
            else {"executable_path": os.getenv("AI_SHIP_WEBKIT")}
        )
        browser = getattr(pw, args.engine).launch(**opts)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 920})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base)
            dlg = page.locator("dialog[open]")

            def open_feature(module, code):
                page.goto(base + "/#module/" + module + "/" + code)
                page.locator(".feature-title [data-action=feature-action]").click()
                expect(dlg).to_be_visible()

            def close():
                dlg.get_by_role("button", name="关闭", exact=True).click()

            open_feature("search", "S02")
            expect(dlg.locator("[data-hits]")).to_contain_text(source.name)
            dlg.get_by_text("索引版本", exact=True).click()
            dlg.locator("[data-analyze]").click()
            expect(dlg.locator("[data-index-history]")).to_contain_text(
                "语义索引新增", timeout=30000
            )
            dlg.locator("[data-build=shadow]").click()
            expect(dlg.locator("[data-switch]")).to_be_enabled()
            # New source evidence must block the old snapshot, even when its rows
            # and the media files are otherwise unchanged.
            post(
                "/production/tags",
                project_id=pid,
                asset_version_id=version["id"],
                start=0,
                end=1,
                label="桌面参考",
                category="scene",
                evidence="操作员复核原片第一秒",
            )
            dlg.locator("[data-switch]").click()
            expect(dlg.locator("[data-index-status]")).to_contain_text("构建后素材或标注已经变化")
            dlg.get_by_role("button", name="按最新素材重建并启用").click()
            expect(dlg.locator("[data-index-history]")).to_contain_text("正在使用")
            dlg.locator("[name=text]").fill("table-source")
            dlg.locator("[name=channel]").select_option("semantic")
            dlg.locator(".director-index-query button").click()
            expect(dlg.locator("[data-index-status]")).to_contain_text("未登记兼容的向量模型")
            close()
            open_feature("music", "B04")
            expect(dlg.locator("[data-cue]")).to_be_visible()
            close()
            open_feature("music", "B03")
            dlg.locator("[data-sound-file]").set_input_files(str(audio))
            expect(dlg.locator("[data-sound-status]")).to_contain_text("音轨已就绪", timeout=20000)
            dlg.locator("[name=markets]").fill("GB")
            dlg.locator("[name=perpetual]").check()
            dlg.locator("[name=proof]").fill("本测试生成的原创声音，限 GB 项目")
            dlg.locator("[data-rights] button[type=submit]").click()
            expect(dlg.locator("[data-sound-status]")).to_contain_text("音乐与许可已绑定")
            dlg.locator("[name=begin]").fill("0.25")
            dlg.locator("[name=end]").fill("1.75")
            dlg.locator("[name=start]").fill("0.25")
            dlg.locator("[data-cue] button[type=submit]").click()
            expect(dlg.locator("[data-cues]")).to_contain_text("0.25")
            dlg.locator("[data-mixer] button[type=submit]").click()
            expect(dlg.locator("[data-sound-status]")).to_contain_text("混音已生成", timeout=20000)
            expect(dlg.locator("audio")).to_have_attribute(
                "src", re.compile("/api/v1/media/.+/file")
            )
            dlg.locator("[data-sound=adopt]").click()
            expect(dlg.locator("[data-sound-status]")).to_contain_text("已采用")
            close()
            gettl = api.get("/records/" + timeline["id"]).json()["data"]
            assert gettl["version"] == 2 and len(gettl["data"]["audio_tracks"]) == 1
            post(
                "/commands",
                command_id=uuid4().hex,
                command_type="LockTimelineRevision",
                project_id=pid,
                actor_id="local-owner",
                actor_role="owner",
                target_id=timeline["id"],
                expected_revision=2,
                input_version_refs=[{"id": timeline["id"], "version": 2, "kind": "Timeline"}],
                parameters={},
                policy_version=None,
                idempotency_key=uuid4().hex,
                trace_id=uuid4().hex,
                reason="实际编码验收",
            )
            artifact = post(
                "/media/render", project_id=pid, timeline_id=timeline["id"], expected_revision=3
            )["artifact"]
            page.reload()
            open_feature("music", "B06")
            dlg.locator("[data-sound=inspect]").click()
            expect(dlg.locator("[data-sound-status]")).to_contain_text(
                "已读取成片音轨与流时钟", timeout=20000
            )
            dlg.locator("[name=listen-start]").fill("0.25")
            dlg.locator("[name=listen-end]").fill("1.75")
            dlg.locator("[name=observation]").fill(
                "自动化验证使用合成音轨，已实测时码，未进行真人听感检查"
            )
            dlg.locator("[name=decision]").select_option("rework")
            dlg.locator("[data-listen] button[type=submit]").click()
            expect(dlg.locator("[data-sound-status]")).to_contain_text("已绑定")
            close()
            check_post_review(page, dlg, post, pid, artifact, tmp)
            assert not errors, errors
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "engine": args.engine,
                        "checks": [
                            "source-search",
                            "shadow-cutover",
                            "semantic-fallback",
                            "direct-mix-entry",
                            "audio-upload",
                            "rights",
                            "cue",
                            "measured-mix",
                            "timeline-adoption",
                            "encoded-sound-meter",
                            "time-range-review",
                            "archive-csv",
                            "post-level-comparison",
                        ],
                        "artifact": artifact["hash"],
                        "js_errors": errors,
                    }
                )
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
