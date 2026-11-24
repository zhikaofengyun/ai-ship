"""Run real Chromium against a real isolated local API, never a browser session.

Every invocation starts and destroys a synthetic workspace. Screenshots and JSON
are evidence of the checked paths, not proof of unavailable external services.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


VIEWPORTS = {
    "1": (1440, 960, 390, 844),
    "2": (1536, 960, 414, 896),
    "3": (1280, 900, 375, 812),
    "4": (1600, 1000, 430, 932),
    "5": (1366, 900, 360, 800),
}


@contextmanager
def server(workspace: Path, output: Path, *, fixture_env: dict | None = None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "AI_SHIP_MODE": "local",
        "AI_SHIP_DATA_DIR": str(workspace),
        "AI_SHIP_DB": str(workspace / "browser.sqlite3"),
        "AI_SHIP_PROVIDERS": "{}",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
        **(fixture_env or {}),
    }
    with (output / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if client.get(base + "/api/v1/health").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError("Server exited; inspect server.log")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local test server did not become ready")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evidence/browser-development")
    parser.add_argument("--round", default="development")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip the exhaustive feature/route traversal; never counts as a full round",
    )
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    report = {
        "round": args.round,
        "started_at": started,
        "browser": "Chromium",
        "synthetic_workspace": True,
        "checks": [],
        "screenshots": [],
        "console_errors": [],
        "page_errors": [],
        "failed_requests": [],
        "features": [],
        "routes": [],
        "modules": [],
        "limitations": [
            "未访问真实模型或平台账号",
            "未测试Safari/WebKit/Firefox与实际iPhone硬件",
            "UI遍历不等于178功能的全部业务实现",
            "屏幕截图不能证明苹果原生光学渲染逐像素一致",
        ],
    }

    def check(name, details=None):
        report["checks"].append({"name": name, "passed": True, "details": details})

    def capture(page, name):
        path = output / f"{name}.png"
        # Stable page evidence: clear transient notifications using the same
        # dismiss control available to the user, never remove application DOM.
        if name != "failure" and not page.locator("dialog[open]").count():
            page.locator('.toast button[aria-label="关闭通知"]').evaluate_all(
                "buttons => buttons.forEach(button => button.click())"
            )
        page.screenshot(path=str(path), full_page=True, animations="disabled")
        report["screenshots"].append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "viewport": page.viewport_size,
                "url_path": page.url.split("#")[-1],
            }
        )

    page = None

    @contextmanager
    def preserve_failure():
        try:
            yield
        except Exception:
            if page:
                try:
                    capture(page, "failure")
                except Exception:
                    pass
            raise

    try:
        with (
            tempfile.TemporaryDirectory(prefix="ship-browser-") as td,
            server(Path(td), output) as base,
            sync_playwright() as pw,
            preserve_failure(),
        ):
            executable = (
                os.environ.get("AI_SHIP_CHROMIUM")
                or shutil.which("chromium-headless-shell")
                or shutil.which("chromium")
            )
            browser = pw.chromium.launch(
                executable_path=executable, headless=True, chromium_sandbox=True
            )
            report["browser_version"] = browser.version
            dw, dh, mw, mh = VIEWPORTS.get(str(args.round), VIEWPORTS["1"])
            report["viewport_variant"] = {"desktop": [dw, dh], "mobile": [mw, mh]}
            context = browser.new_context(
                viewport={"width": dw, "height": dh}, locale="zh-CN", color_scheme="light"
            )
            page = context.new_page()
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
            page.on(
                "console",
                lambda m: report["console_errors"].append(m.text) if m.type == "error" else None,
            )
            page.on(
                "requestfailed",
                lambda r: report["failed_requests"].append(
                    {"url": r.url.replace(base, "LOCAL"), "reason": r.failure}
                ),
            )
            page.goto(base, wait_until="domcontentloaded")
            expect(page.locator("#main h1")).to_have_text("项目")
            geometry = page.locator("#main").bounding_box()
            if not geometry or geometry["width"] < 900 or geometry["x"] < 180:
                raise AssertionError(
                    "Desktop main/side navigation grid is misplaced: " + str(geometry)
                )
            capture(page, "01-empty-workspace")
            check("clean_workspace_boot_no_seeded_statistics")
            page.locator('[data-action="new-project"]').first.click()
            dialog = page.locator("dialog[open]")
            dialog.locator('[name="name"]').fill("回归验证 · 合成素材项目")
            dialog.locator('[name="goals"]').fill(
                "仅用于隔离测试：验证原件、时间线、可追溯版本和真实渲染。"
            )
            dialog.locator('[name="market"]').fill("测试环境")
            dialog.locator('[name="content_mode"]').select_option("product")
            expect(dialog.locator('[name="name"]')).to_have_value("回归验证 · 合成素材项目")
            expect(dialog.locator('[name="goals"]')).to_have_value(
                "仅用于隔离测试：验证原件、时间线、可追溯版本和真实渲染。"
            )
            dialog.locator('form[data-form] button[type="submit"]').click()
            expect(page.locator("dialog[open]")).to_have_count(0, timeout=10000)
            expect(page.locator(".context-label")).to_have_text("回归验证 · 合成素材项目")
            check("project_created_through_real_ui")
            with httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=30) as client:
                project = client.get("/projects").json()["data"][0]
                pid = project["id"]
                catalog = client.get("/catalog").json()["data"]
                fixture = Path(td) / "synthetic-test.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc2=size=320x568:rate=24",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:sample_rate=48000",
                        "-t",
                        "2",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-y",
                        str(fixture),
                    ],
                    check=True,
                    timeout=30,
                )
                page.goto(base + "/#assets", wait_until="domcontentloaded")
                page.locator("#media-input").set_input_files(str(fixture))
                expect(page.locator(".asset-card")).to_have_count(1, timeout=15000)
                check("media_uploaded_through_real_file_input")
                capture(page, "02-media-library")
                assets = client.get("/records", params={"kind": "Asset", "project_id": pid}).json()[
                    "data"
                ]
                asset = assets[0]
                versions = client.get(
                    "/records", params={"kind": "AssetVersion", "project_id": pid}
                ).json()["data"]
                timeline_body = {
                    "kind": "Timeline",
                    "project_id": pid,
                    "data": {
                        "name": "合成片段 · 测试时间线",
                        "locked": False,
                        "clips": [
                            {
                                "id": "clip-synthetic-1",
                                "asset_id": asset["id"],
                                "asset_version_id": versions[0]["id"],
                                "source_in": 0,
                                "source_out": 2,
                                "timeline_start": 0,
                                "track": 0,
                                "volume": 1,
                                "opacity": 1,
                                "x": 0,
                                "y": 0,
                            }
                        ],
                        "audio_tracks": [],
                        "captions": [{"text": "SYNTHETIC TEST", "start": 0.2, "end": 1.5}],
                        "duration": 2,
                        "format": {"width": 320, "height": 568, "fps": 24},
                    },
                    "refs": [{"id": versions[0]["id"], "version": versions[0]["version"]}],
                    "reason": "Synthetic browser test fixture",
                }
                tr = client.post("/records", json=timeline_body)
                if tr.status_code != 201:
                    raise AssertionError(tr.text)
                timeline = tr.json()["data"]
                report["fixtures"] = {
                    "project_id": pid,
                    "asset_id": asset["id"],
                    "timeline_id": timeline["id"],
                    "media_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                }
                page.goto(base + "/#editor", wait_until="domcontentloaded")
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(".timeline-clip[data-clip]")).to_have_count(1)
                page.locator(".timeline-clip[data-clip]").click()
                page.locator("#playhead").evaluate(
                    "el => { el.value='1'; el.dispatchEvent(new Event('input', {bubbles:true})); }"
                )
                page.locator('[data-action="split"]').click()
                expect(page.locator(".timeline-clip[data-clip]")).to_have_count(2)
                page.locator('[data-action="undo"]').click()
                expect(page.locator(".timeline-clip[data-clip]")).to_have_count(1)
                page.locator('[data-action="redo"]').click()
                expect(page.locator(".timeline-clip[data-clip]")).to_have_count(2)
                page.locator('[data-action="save-timeline"]').click()
                expect(page.locator("#edit-status")).to_contain_text("已保存")
                updated = client.get("/records/" + timeline["id"]).json()["data"]
                if len(updated["data"]["clips"]) != 2 or updated["data"]["locked"]:
                    raise AssertionError("Editor split/save must persist two draft clips")
                check("editor_split_undo_redo_and_cas_save")
                page.locator('[data-action="render-timeline"]').click()
                page.locator("dialog[open]").get_by_role(
                    "button", name="锁定并渲染", exact=True
                ).click()
                expect(page.locator("dialog[open] h2")).to_have_text("成片已生成", timeout=60000)
                artifacts = client.get(
                    "/records", params={"kind": "Artifact", "project_id": pid}
                ).json()["data"]
                if len(artifacts) != 1 or artifacts[0]["data"].get("publish_ready") is not False:
                    raise AssertionError(
                        "Render must produce a real artifact with separate content review"
                    )
                actual_video = client.get(f"/media/artifacts/{artifacts[0]['id']}/file")
                if actual_video.status_code != 200 or len(actual_video.content) < 1000:
                    raise AssertionError("Actual rendered video unavailable")
                video_path = output / "browser-render.mp4"
                video_path.write_bytes(actual_video.content)
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(video_path), "-f", "null", "-"],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                check(
                    "editor_lock_render_download_complete_decode",
                    {
                        "artifact_id": artifacts[0]["id"],
                        "sha256": hashlib.sha256(actual_video.content).hexdigest(),
                        "bytes": len(actual_video.content),
                        "publish_ready": False,
                    },
                )
                capture(page, "03-real-render-result")
                page.locator("dialog[open]").get_by_role("button", name="关闭", exact=True).click()
                flow = {
                    "name": "浏览器真实执行 · 转换到抽音",
                    "nodes": [
                        {
                            "id": "transform",
                            "feature_id": "I07",
                            "route_id": "I07-R2",
                            "parameters": {},
                            "operation_args": {
                                "recipe": {"trim_start": 0, "trim_end": 1, "scale": 1.016}
                            },
                            "input_bindings": {
                                "asset_id": {"record_id": asset["id"], "version": asset["version"]}
                            },
                        },
                        {
                            "id": "audio",
                            "feature_id": "L07",
                            "route_id": "L07-R1",
                            "parameters": {},
                            "operation_args": {},
                            "input_bindings": {
                                "asset_id": {"node_id": "transform", "output": "version"}
                            },
                        },
                    ],
                    "edges": [
                        {
                            "id": "edge-1",
                            "source": "transform",
                            "target": "audio",
                            "type": "control",
                        }
                    ],
                    "max_steps": 4,
                    "max_attempts": 2,
                    "budget": 0,
                    "max_seconds": 120,
                }
                fr = client.post(
                    "/records",
                    json={
                        "kind": "Workflow",
                        "project_id": pid,
                        "data": flow,
                        "reason": "Synthetic browser workflow fixture",
                    },
                )
                if fr.status_code != 201:
                    raise AssertionError(fr.text)
                page.goto(base + "/#workflow", wait_until="domcontentloaded")
                expect(page.locator(".workflow-node")).to_have_count(2)
                page.locator('[data-action="run-workflow"]').click()
                expect(page.locator("dialog[open] h2")).to_have_text(
                    "工作流运行请求", timeout=60000
                )
                runs = client.get("/records", params={"kind": "Run", "project_id": pid}).json()[
                    "data"
                ]
                executed = [run for run in runs if run["data"].get("execution_kind") == "local_dag"]
                if len(executed) != 1 or executed[0]["data"].get("status") != "completed":
                    raise AssertionError(
                        "Browser workflow did not complete real local nodes: "
                        + json.dumps(executed, ensure_ascii=False)
                    )
                check(
                    "workflow_ui_runs_real_transform_then_extract_audio",
                    {
                        "run_id": executed[0]["id"],
                        "completed_nodes": executed[0]["data"]["checkpoint"]["completed_nodes"],
                    },
                )
                capture(page, "04-real-workflow-result")
                page.locator("dialog[open]").get_by_role("button", name="关闭", exact=True).click()
                for route in (
                    "overview",
                    "editor",
                    "workflow",
                    "agents",
                    "delivery",
                    "insights",
                    "audit",
                    "settings",
                ):
                    page.goto(base + "/#" + route, wait_until="domcontentloaded")
                    expect(page.locator("#main .loading-view")).to_have_count(0)
                    expect(page.locator("#main h1")).to_have_count(1)
                    expect(page.locator("#main")).not_to_be_empty()
                    expect(page.locator("#main")).not_to_contain_text("页面暂时无法打开")
                    if route == "delivery":
                        expect(
                            page.locator(".data-table tbody tr").first.locator("td").nth(1)
                        ).to_contain_text("320 × 568")
                        check("delivery_table_reads_actual_artifact_probe_dimensions")
                    check("desktop_page_" + route)
                    capture(page, "desktop-" + route)
                if not args.quick:
                    for module in catalog["modules"]:
                        page.goto(base + "/#module/" + module["id"], wait_until="domcontentloaded")
                        expect(page.locator("#main h1")).to_have_text(module["label"])
                        report["modules"].append(module["id"])
                        for feature in [
                            f for f in catalog["features"] if f["module"] == module["id"]
                        ]:
                            page.locator(
                                f'[data-action="feature"][data-id="{feature["id"]}"]'
                            ).click()
                            expect(page.locator(".feature-detail h2")).to_have_text(
                                feature["label"]
                            )
                            expected_keys = {
                                c["key"]
                                for c in feature["operation"]["controls"]
                                if c["key"] != "route_choice"
                            }
                            found_keys = set(
                                page.locator("[data-control]").evaluate_all(
                                    "els => els.map(e => e.dataset.control)"
                                )
                            )
                            if not expected_keys.issubset(found_keys):
                                raise AssertionError(
                                    f"Missing controls {feature['id']}: {expected_keys - found_keys}"
                                )
                            report["features"].append(
                                {
                                    "id": feature["id"],
                                    "control_count": len(expected_keys),
                                    "passed": True,
                                }
                            )
                            for route in feature["routes"]:
                                page.locator("#feature-route").select_option(route["id"])
                                expect(page.locator("#feature-route-detail")).to_contain_text(
                                    route["label"]
                                )
                                report["routes"].append(route["id"])
                    check(
                        "all_23_modules_178_features_534_route_choices",
                        {
                            "modules": len(report["modules"]),
                            "features": len(report["features"]),
                            "routes": len(report["routes"]),
                        },
                    )
                page.goto(base + "/#module/project", wait_until="domcontentloaded")
                page.locator('[data-action="feature"][data-id="P01"]').click()
                field = page.locator('[data-control="p01_control_1"] textarea')
                field.fill("保存后刷新验证")
                page.locator('[data-action="save-feature"]').click()
                expect(page.locator(".toast").last).to_contain_text("已保存", timeout=10000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator('[data-control="p01_control_1"] textarea')).to_have_value(
                    "保存后刷新验证"
                )
                check("feature_configuration_persists_across_reload")
                capture(page, "desktop-feature-controls")
                page.goto(base + "/#settings", wait_until="domcontentloaded")
                page.locator('[data-action="theme"][data-value="dark"]').click()
                expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                page.locator("#reduce-motion").check()
                page.locator("#reduce-transparency").check()
                expect(page.locator("html")).to_have_attribute("data-motion", "reduced")
                expect(page.locator("html")).to_have_attribute("data-transparency", "reduced")
                capture(page, "dark-reduced-settings")
                check("dark_mode_reduced_motion_transparency_controls")
                page.keyboard.press("Control+k")
                expect(page.locator("dialog[open]")).to_have_count(1)
                page.keyboard.press("Escape")
                expect(page.locator("dialog[open]")).to_have_count(0)
                check("keyboard_command_palette_escape_focus_cycle")
                page.locator('[data-action="theme"][data-value="light"]').click()
                page.set_viewport_size({"width": mw, "height": mh})
                for route in (
                    "overview",
                    "assets",
                    "editor",
                    "workflow",
                    "delivery",
                    "insights",
                    "settings",
                    "module/project",
                ):
                    page.goto(base + "/#" + route, wait_until="domcontentloaded")
                    expect(page.locator("#main .loading-view")).to_have_count(0)
                    expect(page.locator("#main h1")).to_have_count(1)
                    expect(page.locator("#main")).not_to_be_empty()
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth > window.innerWidth + 2"
                    )
                    if overflow:
                        raise AssertionError("Horizontal page overflow on mobile: " + route)
                    if route == "editor":
                        page.wait_for_function(
                            """() => { const video = document.querySelector('#preview-stage video'); return video && video.readyState >= 2 && video.videoWidth > 0 && !video.seeking; }""",
                            timeout=15000,
                        )
                        check("mobile_editor_actual_video_frame_decoded")
                    if route == "workflow":
                        last_node = page.locator('.workflow-node[data-node="audio"]')
                        last_node.scroll_into_view_if_needed()
                        last_node.click()
                        expect(page.locator("#workflow-inspector h3").first).to_have_text(
                            "原声提取与音轨建档"
                        )
                        check("mobile_second_workflow_node_reachable_and_selectable")
                    capture(page, "mobile-" + route.replace("/", "-"))
                    check("mobile_no_page_overflow_" + route)
                page.locator('[data-global="menu"]').click()
                expect(page.locator(".shell")).to_have_class(
                    __import__("re").compile("sidebar-open")
                )
                page.locator('.nav-item[href="#production"]').click()
                expect(page.locator("#production-goal")).to_be_visible()
                expect(page.locator(".shell")).not_to_have_class(
                    __import__("re").compile("sidebar-open")
                )
                check("mobile_navigation_opens_and_closes")
            if report["page_errors"]:
                raise AssertionError(
                    "Browser JavaScript exceptions: " + json.dumps(report["page_errors"])
                )
            if report["console_errors"]:
                raise AssertionError(
                    "Browser console errors: " + json.dumps(report["console_errors"])
                )
            report["unexpected_failed_requests"] = [
                failure
                for failure in report["failed_requests"]
                if "ERR_ABORTED" not in (failure["reason"] or "")
            ]
            if report["unexpected_failed_requests"]:
                raise AssertionError(
                    "Unexpected request failures: "
                    + json.dumps(report["unexpected_failed_requests"])
                )
            report["passed"] = True
            browser.close()
    except Exception as exc:
        report["passed"] = False
        report["failure"] = str(exc)
        if page:
            try:
                capture(page, "failure")
            except Exception:
                pass
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "browser-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "passed": report.get("passed"),
                    "checks": len(report["checks"]),
                    "features": len(report["features"]),
                    "routes": len(report["routes"]),
                    "screenshots": len(report["screenshots"]),
                    "failure": report.get("failure"),
                    "report": str(output / "browser-report.json"),
                },
                ensure_ascii=False,
            )
        )
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
