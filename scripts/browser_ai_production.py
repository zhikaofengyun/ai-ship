"""Actual AI-led project entry, source-grounded cut, manual takeover and delivery."""

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
from browser_edit_session import wait_for_browser_value
from comfy_protocol_fixture import sample_generator


def run(output, round_id, engine):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "engine": engine,
        "round": round_id,
        "checks": [],
        "screenshots": [],
        "page_errors": [],
    }
    checks = report["checks"]
    with (
        tempfile.TemporaryDirectory() as td,
        sample_generator(Path(td) / "test-candidate.mp4") as (provider_env, issued),
        server(
            Path(td),
            output,
            fixture_env={
                **provider_env,
                "AI_SHIP_PROVIDERS": json.dumps(
                    {
                        "studio-loopback": {
                            "transport": "loopback_chat",
                            "base_url": "http://127.0.0.1:1/v1",
                            "models": [
                                "director-small",
                                "review-small",
                                "judge-small",
                                "director-small",
                            ],
                            "currency": "USD",
                            "input_price_per_million": 0,
                            "output_price_per_million": 0,
                        }
                    }
                ),
            },
        ) as base,
        sync_playwright() as pw,
    ):
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
        dw, dh, mw, mh = VIEWPORTS.get(str(round_id), VIEWPORTS["1"])
        page = browser.new_page(
            viewport={"width": dw, "height": dh}, locale="zh-CN", accept_downloads=True
        )
        page.set_default_timeout(10000)
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)))

        def snap(name):
            path = output / (name + ".png")
            page.screenshot(path=str(path), scale="css", animations="disabled", timeout=45000)
            report["screenshots"].append(
                {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )

        def check_touch_geometry(selectors):
            for selector in selectors:
                control = page.locator(selector).first
                control.scroll_into_view_if_needed()
                box = control.bounding_box()
                assert box and box["width"] >= 44 and box["height"] >= 44, (selector, box)
                assert (
                    box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"] + 1
                ), (selector, box)

        def check_monitor(label):
            loc = page.locator
            screen = loc(".director-monitor #viewer-screen")
            stage = loc(".director-monitor #preview-stage")
            video = loc(".director-player")
            wait_for_browser_value(
                page, "() => document.querySelector('.director-player')?.readyState >= 2"
            )
            src = video.get_attribute("src")
            screen.scroll_into_view_if_needed()
            width, height = video.evaluate("v=>[v.videoWidth,v.videoHeight]")
            view = screen.bounding_box()
            mode = loc(".director-monitor [data-view-mode]")
            for value, expected in [
                ("fit", min(view["width"], view["height"] * width / height)),
                ("fill", max(view["width"], view["height"] * width / height)),
                ("2", 2 * width),
            ]:
                mode.select_option(value)
                assert abs(stage.bounding_box()["width"] - expected) <= 2, (
                    label,
                    value,
                    expected,
                    stage.bounding_box(),
                )
            loc("[data-view-plus]").click()
            assert stage.bounding_box()["width"] > 2 * width
            loc("[data-view-minus]").click()
            assert abs(stage.bounding_box()["width"] - 2 * width) < 2
            screen.focus()
            screen.press("0")
            expect(mode).to_have_value("fit")
            loc("[data-monitor-volume]").fill("0.5")
            assert abs(video.evaluate("v=>v.volume") - 0.5) < 0.01
            loc("[data-monitor-mute]").click()
            assert video.evaluate("v=>v.muted")
            loc("[data-monitor-mute]").click()
            screen.click()
            wait_for_browser_value(page, "() => !document.querySelector('.director-player').paused")
            loc("[data-monitor-play]").click()
            wait_for_browser_value(page, "() => document.querySelector('.director-player').paused")
            seek = loc("[data-monitor-seek]")
            a, b = (float(seek.get_attribute(k)) for k in ["min", "max"])
            point = round((a + b) / 2, 2)
            seek.fill(str(point))
            assert abs(video.evaluate("v=>v.currentTime") - point) < 0.05
            full = loc(".director-monitor [data-view-full]")
            full.click()
            expect(full).to_have_attribute("aria-pressed", "true")
            assert (
                not loc("dialog[open]").count()
                and screen.bounding_box()["width"] >= page.viewport_size["width"] - 2
            )
            if label == "narrow screen selected source":
                snap("source-fullscreen-narrow")
            full.click()
            expect(full).to_have_attribute("aria-pressed", "false")
            assert video.get_attribute("src") == src
            checks.append(
                label
                + " — fit, fill, actual 200%, zoom step, keyboard reset, direct play, pause, volume, mute, bounded seek and fullscreen back on same file"
            )

        with httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=15) as api:
            page.goto(base)
            page.get_by_role("button", name="新建项目", exact=True).click()
            dialog = page.locator("#dialog[open]")
            dialog.locator("[name=name]").fill("产品实拍 · 剪辑提案")
            goal = "用真实镜头展示产品细节，保留所有无法核实的内容标记。"
            dialog.locator("[name=goals]").fill(goal)
            dialog.locator("form[data-form] button[type=submit]").click()
            expect(page).to_have_url(re.compile("#production$"))
            expect(page.locator("#production-goal")).to_have_value(goal)
            page.set_viewport_size({"width": mw, "height": mh})
            check_touch_geometry(
                [
                    "#production-form button[type=submit]",
                    "[data-action=models]",
                    "[data-action=upload]",
                ]
            )
            page.set_viewport_size({"width": dw, "height": dh})
            expect(page.locator(".nav-cluster")).to_have_count(8)
            expect(page.locator("[data-module-link]")).to_have_count(23)
            assert (
                len(set(page.locator("[data-module-link]").evaluate_all("(ls)=>ls.map(l=>l.href)")))
                == 23
            )
            checks.append(
                "project goal reaches AI production first; eight canonical groups and 23 unique module routes"
            )
            page.locator("#production-form button[type=submit]").click()
            expect(page.locator("[data-production-status]")).to_contain_text("选择至少")
            projects = api.get("/projects").json()["data"]
            pid = projects[0]["id"]

            def runs():
                return api.get("/production/auto-edits", params={"project_id": pid}).json()["data"][
                    "sessions"
                ]

            assert not runs()
            checks.append("missing sources block creation; no spurious run")
            expect(page.locator("[name=mode]")).to_have_value("local_vision")
            for i, (model, display) in enumerate(
                [
                    ("director-small", "本地导演"),
                    ("review-small", "镜头复核"),
                    ("judge-small", "独立裁决"),
                ]
            ):
                page.locator("[data-action=models]").click()
                chooser = dialog.locator("[name=available_model]")
                expect(chooser.locator("option")).to_have_count(3 - i)
                if i == 0:
                    snap("available-models")
                chooser.select_option(label="studio-loopback · " + model)
                dialog.locator("[data-action=bind-available-model]").focus()
                page.keyboard.press("Enter")
                expect(dialog.locator("[name=model_id]")).to_have_value(model)
                expect(dialog.locator("[name=endpoint_ref]")).to_have_value("studio-loopback")
                expect(dialog.locator("[name=credential_ref]")).to_be_empty()
                dialog.locator("[name=name]").fill(display)
                dialog.locator("form[data-form] button[type=submit]").click()
                expect(dialog).to_have_count(0)
            page.reload()
            expect(page.locator("[name=mode]")).to_have_value("team")
            role_ids = [
                page.locator("[name=" + k + "]").input_value()
                for k in ["model_profile_id", "reviewer_profile_id", "judge_profile_id"]
            ]
            profiles = api.get(
                "/records", params={"project_id": pid, "kind": "ModelProfile"}
            ).json()["data"]
            assert (
                len(profiles) == 3
                and len(set(role_ids)) == 3
                and set(role_ids) == {p["id"] for p in profiles}
            )
            assert all(
                p["project_id"] == pid and p["data"]["endpoint_ref"] == "studio-loopback"
                for p in profiles
            )
            page.locator("[data-action=models]").click()
            expect(dialog.locator("[data-available-models]")).to_contain_text("暂无可新增")
            expect(dialog.locator("[data-model]")).to_have_count(3)
            dialog.get_by_role("button", name="关闭").click()
            assert (
                not api.get("/records", params={"project_id": pid, "kind": "ToolCall"}).json()[
                    "data"
                ]
                and not runs()
            )
            checks.append(
                "first-time keyboard server allowlist binding, explicit editable save, deduplication, project persistence, three distinct default models; no inference or charge"
            )
            fixtures = []
            for i, source in enumerate(
                ["testsrc2=size=320x180:rate=25", "smptebars=size=320x180:rate=25"]
            ):
                path = Path(td) / ("实拍校验" + str(i + 1) + ".mp4")
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        source,
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=540:sample_rate=44100",
                        "-t",
                        "2.4",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-threads",
                        "1",
                        "-c:a",
                        "aac",
                        "-y",
                        str(path),
                    ],
                    check=True,
                    timeout=15,
                )
                fixtures.append(str(path))
            page.locator("[data-production-files]").set_input_files(fixtures)
            expect(page.locator(".director-source")).to_have_count(2, timeout=30000)
            page.locator(".director-source input").first.uncheck()
            expect(page.locator(".director-sources h2 small")).to_have_text("1 / 2")
            page.locator(".director-source input").first.check()
            page.locator("[data-action=preview-source]").first.click()
            wait_for_browser_value(
                page, "() => document.querySelector('.director-player')?.readyState>=2"
            )
            page.locator("[name=mode]").select_option("team")
            expect(page.locator("[name=judge_profile_id]")).to_be_visible()
            page.locator("[data-action=models]").click()
            dialog.locator("[data-action=add]").click()
            for k, v in {
                "name": "未连接的模型",
                "provider": "disabled-test",
                "model_id": "offline",
                "endpoint_ref": "disabled-fixture",
            }.items():
                dialog.locator("[name=" + k + "]").fill(v)
            dialog.locator("form[data-form] button[type=submit]").click()
            expect(dialog).to_have_count(0)
            expect(page.locator("[name=judge_profile_id]")).to_be_visible()
            page.locator("[name=mode]").select_option("model")
            page.locator("[name=model_profile_id]").select_option(label="未连接的模型")
            page.locator("[name=max_cost]").fill("0.01")
            page.locator("#production-form button[type=submit]").click()
            expect(page.locator("[data-stage=blocked]")).to_be_visible(timeout=30000)
            assert runs()[0]["data"]["stage"] == "blocked" and not runs()[0]["data"].get(
                "timeline_ref"
            )
            checks.append(
                "provider unconfigured creates a visible blocked task with no timeline and no secret entry"
            )
            page.locator("[data-action=new-task]").last.click()
            page.locator("[name=mode]").select_option("local_vision")
            page.locator("[name=target_seconds]").fill("3")
            page.get_by_text("输出画幅", exact=True).click()
            page.locator("[name=width]").fill("320")
            page.locator("[name=height]").fill("180")
            page.locator("[name=fps]").select_option("25")
            page.reload()
            expect(page.locator("[name=target_seconds]")).to_have_value("3")
            expect(page.locator("[data-source]:checked")).to_have_count(2)
            page.locator("#production-form button[type=submit]").click()
            expect(page.locator("[data-stage=review]")).to_be_visible(timeout=60000)
            report["real_source_count"] = 2
            records = runs()
            found = next(r for r in records if r["data"]["stage"] == "review")
            detail_path = "/production/auto-edits/" + found["id"]

            def detail():
                return api.get(detail_path).json()["data"]

            proposal = detail()
            assert proposal["timeline"] is None and len(proposal["plan"]["data"]["shots"]) == 2
            assert all(len(s["samples"]) >= 1 for s in found["data"]["evidence"])
            expect(page.locator("[data-shot]")).to_have_count(2)
            page.locator("[data-shot]").last.click()
            expect(page.locator("[data-shot]").last).to_have_attribute("aria-pressed", "true")
            wait_for_browser_value(
                page, "() => document.querySelector('.director-player')?.readyState>=2"
            )
            check_monitor("desktop selected source segment")
            page.get_by_text("来源证据 · 2", exact=True).click()
            snap("review-desktop")
            checks.append(
                "real frame evidence, selected sources and grounded per-shot reasoning before approval; refresh retains choices"
            )
            # Only the transport is doubled. Product persistence, video ingestion,
            # source pins, partial replacement, and FFmpeg output run unchanged.
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x180:rate=25",
                    "-t",
                    "2.4",
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    "1",
                    "-y",
                    str(Path(td) / "test-candidate.mp4"),
                ],
                check=True,
                timeout=15,
            )
            page.locator("[data-action=generate-shot]").click()
            gen = page.locator("[data-generation-form]")
            assert "镜头 2" in gen.locator("[name=slot]").input_value()
            gen.locator("[name=prompt]").fill("生成产品第二个镜头的测试提示词")
            gen.locator("[name=seed]").fill("372")
            gen.locator("button[type=submit]").click()
            expect(page.locator(".generation-list")).to_contain_text("排队中")
            page.locator("[data-action=gen-refresh]").click()
            expect(page.locator(".generation-list")).to_contain_text("文件已到库", timeout=30000)
            assert len(issued) == 1 and issued[0]["prompt"]["1"]["inputs"]["seed"] == 372
            job = api.get("/production/generation", params={"project_id": pid}).json()["data"][
                "jobs"
            ][0]
            new_asset = job["data"]["asset_ref"]["id"]
            assert job["data"]["source_run_id"] == found["id"]
            page.locator("[data-action=replace-shot]").click()
            form = dialog.locator("form[data-form]")
            expect(form.locator("[name=asset_id]")).to_have_value(new_asset)
            form.locator("[name=reason]").fill("只替换已选中的第二镜头")
            form.locator("button[type=submit]").click()
            expect(dialog).to_have_count(0)
            expect(page.locator("[data-stage=review]")).to_be_visible(timeout=15000)
            original = proposal
            latest = next(
                r for r in runs() if r["data"]["stage"] == "review" and r["id"] != found["id"]
            )
            detail_path = "/production/auto-edits/" + latest["id"]
            proposal = detail()
            assert proposal["plan"]["data"]["shots"][1]["asset_id"] == new_asset
            assert proposal["plan"]["data"]["shots"][0] == original["plan"]["data"]["shots"][0]
            assert not proposal["timeline"]
            assert (
                api.get("/production/auto-edits/" + found["id"]).json()["data"]["plan"]["data"][
                    "shots"
                ]
                == original["plan"]["data"]["shots"]
            )
            checks.append(
                "explicitly simulated ComfyUI transport: real returned bytes ingested, original task/slot referenced, one shot replaced in new proposal, unselected shot and original preserved"
            )
            report["generator_test_scope"] = (
                "loopback protocol fixture returns actual H.264 footage; no generation model inference in this suite"
            )
            page.set_viewport_size({"width": mw, "height": mh})
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            check_monitor("narrow screen selected source")
            check_touch_geometry(
                [
                    "[data-view-full]",
                    "[data-view-plus]",
                    "[data-view-minus]",
                    "[data-view-mode]",
                    "[data-monitor-play]",
                    "[data-monitor-mute]",
                    "[data-monitor-seek]",
                    "[data-monitor-volume]",
                    "[data-action=approve]",
                ]
            )
            page.locator("[data-action=approve]").scroll_into_view_if_needed()
            snap("review-mobile")
            page.locator("[data-action=approve]").click()
            expect(page.locator("[data-stage=draft]")).to_be_visible()
            expect(page.locator(".director-review h2")).to_be_focused()
            timeline = detail()["timeline"]
            check_url = base + "/#editor/project-timeline/" + timeline["id"]
            page.locator("[data-production-link=takeover]").click()
            expect(page).to_have_url(check_url)
            expect(page.locator(".room-clip")).to_have_count(2)
            # The array order is stable; actual editing order lives on the timeline clock.
            page.set_viewport_size({"width": dw, "height": dh})
            page.locator(".room-clip").nth(1).click()
            page.locator("[data-action=move-prev]").click()
            page.locator("[data-action=save-timeline]").click()
            expect(page.locator("#edit-status")).to_have_text("已保存")
            page.locator(".production-back").click()
            expect(page).to_have_url(re.compile("#production$"))
            expect(page.locator("[data-stage=draft]")).to_be_visible()
            active_names = page.locator(".director-shots strong").all_text_contents()
            proposed_names = [s["name"] for s in proposal["plan"]["data"]["shots"]]
            assert active_names == proposed_names[::-1], (active_names, proposed_names)
            page.locator("[data-action=shot-view][data-view=proposal]").click()
            assert page.locator(".director-shots strong").all_text_contents() == proposed_names
            page.locator("[data-action=shot-view][data-view=current]").click()
            expect(page.locator("[data-action=shot-view][data-view=current]")).to_be_focused()
            page.emulate_media(reduced_motion="reduce")
            page.locator("[data-action=shot-view][data-view=proposal]").click()
            assert (
                page.locator(".director-primary").evaluate("e=>getComputedStyle(e).animationName")
                == "none"
            )
            page.locator("[data-action=shot-view][data-view=current]").click()
            page.emulate_media(reduced_motion="no-preference")
            page.locator("[data-action=reload]").click()
            expect(page.locator("[data-action=reload]")).to_be_enabled()
            assert not page.locator(".director-page[data-scene-enter]").count()
            checks.append(
                "scene change transfers keyboard focus; version tab retains focus; reduced-motion opt-out and unchanged refresh do not animate"
            )
            checks.append(
                "manual time reorder round-trips into production as current clock order; original proposal remains separately inspectable"
            )
            checks.append(
                "mobile approval, exact AI timeline manual takeover and return to same active session"
            )
            page.set_viewport_size({"width": dw, "height": dh})
            page.locator("[data-action=render]").click()
            expect(page.locator("[data-stage=rendered]")).to_be_visible(timeout=65000)
            assert detail()["qc"] and detail()["artifact"]
            page.get_by_text("技术质检", exact=True).click()
            expect(page.locator(".director-output")).to_contain_text("全片解码 · 通过")
            wait_for_browser_value(
                page, "() => document.querySelector('.director-player')?.readyState>=2"
            )
            check_monitor("rendered artifact")
            with page.expect_download(timeout=20000) as download:
                page.get_by_role("link", name="下载 MP4").click()
            movie = output / "directed-result.mp4"
            download.value.save_as(movie)
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(movie), "-f", "null", "-"],
                check=True,
                timeout=20,
            )
            info = json.loads(
                subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "quiet",
                        "-show_entries",
                        "stream=codec_name,width,height:format=duration",
                        "-of",
                        "json",
                        str(movie),
                    ]
                )
            )
            report["movie"] = {
                "bytes": movie.stat().st_size,
                "sha256": hashlib.sha256(movie.read_bytes()).hexdigest(),
                **info,
            }
            assert info["streams"][0]["width"] == 320 and info["streams"][0]["height"] == 180
            assert abs(float(info["format"]["duration"]) - 3) < 0.3
            snap("delivered-desktop")
            checks.append(
                "explicit real render, artifact, QC, in-place playback and full-file decode of downloaded MP4"
            )
            page.locator(".director-delivery summary").click()
            page.locator("[data-action=review-content]").click()
            review = dialog.locator("form[data-form]")
            review.locator("[name=reason]").fill("测试素材尚待核对商品事实。")
            review.locator("[name=decision]").select_option("rejected")
            review.locator("button[type=submit]").click()
            expect(dialog).to_have_count(0)
            page.locator(".director-delivery summary").click()
            expect(page.locator(".director-delivery")).to_contain_text("内容审阅 · 需要修改")
            assert detail()["qc"]["data"]["publish_ready"] is False
            checks.append(
                "post-render scoped human content rejection recorded on this exact artifact without navigating or publishing"
            )
            page.reload()
            expect(page.locator("[data-stage=rendered]")).to_be_visible()
            blocked = next(r for r in records if r["data"]["stage"] == "blocked")
            page.locator(".director-task-picker summary").click()
            page.locator("[name=history]").select_option(blocked["id"])
            expect(page.locator("[data-stage=blocked]")).to_be_visible()
            assert len(runs()) == 3
            checks.append(
                "reload restores completed session and selection of the blocked prior run does not reissue its model request"
            )
            page.locator("[data-action=new-task]").first.click()
            page.locator(".director-generation summary").click()
            expect(gen).to_be_visible()
            gen.locator("[name=profile_id]").select_option("still-protocol")
            gen.locator("[name=prompt]").fill("本地接口验证用关键帧")
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(Path(td) / "test-candidate.mp4"),
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    "-y",
                    str(Path(td) / "test-candidate.png"),
                ],
                check=True,
                timeout=15,
            )
            gen.locator("button[type=submit]").click()
            page.locator("[data-action=gen-refresh]").first.click()
            image = page.locator(".generation-still")
            expect(image).to_be_visible(timeout=20000)
            wait_for_browser_value(
                page, "()=>document.querySelector('.generation-still')?.naturalWidth===320"
            )
            expect(page.locator(".director-source")).to_have_count(3)
            hold = page.locator("[data-hold-form]")
            hold.locator("[name=duration]").fill("1.5")
            hold.locator("button[type=submit]").click()
            expect(page.locator(".director-source")).to_have_count(4, timeout=30000)
            j = next(
                j
                for j in api.get("/production/generation", params={"project_id": pid}).json()[
                    "data"
                ]["jobs"]
                if j["data"].get("output_kind") == "image"
            )
            assert j["data"]["asset_ref"]["id"] != j["data"]["hold_ref"]["id"]
            assert len(issued) == 2
            checks.append(
                "still-image generator transport fixture; actual PNG decoded, excluded from timed clips until explicit 1.5 s MPEG hold, original reference preserved"
            )
            gen.locator("[name=profile_id]").select_option("reference-protocol")
            reference = gen.locator("[name=reference_pin]")
            expect(reference).to_be_visible()
            expect(reference).to_have_value("")
            before = len(issued)
            gen.locator("button[type=submit]").click()
            assert len(issued) == before and not reference.evaluate("el=>el.checkValidity()")
            original = j["data"]["asset_ref"]
            pin = json.dumps([original["id"], original["version"]], separators=(",", ":"))
            reference.select_option(value=pin)
            expect(gen.get_by_alt_text("选中的项目参考图")).to_be_visible()
            upload_path = Path(td) / "外部蓝色参考图.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=164x120",
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    "-y",
                    str(upload_path),
                ],
                check=True,
                timeout=12,
            )
            with page.expect_file_chooser() as chooser:
                gen.locator("[data-action=gen-import-reference]").click()
            chooser.value.set_files(str(upload_path))
            expect(reference.locator("option")).to_have_count(3)
            uploaded_id, uploaded_version = json.loads(reference.input_value())
            assert uploaded_id != original["id"] and uploaded_version >= 1
            wait_for_browser_value(
                page, "()=>document.querySelector('.generation-reference img')?.naturalWidth===164"
            )
            reference.select_option(value=pin)
            gen.locator("[name=prompt]").fill("按已生成的关键帧继续生成视频")
            expect(page.locator(".generation-still")).to_have_count(1)
            snap("image-reference")
            gen.locator("button[type=submit]").click()
            page.locator("[data-action=gen-refresh]").first.click()
            jobs = page.locator(".generation-list li")
            expect(jobs.first).to_contain_text("文件已到库", timeout=25000)
            refjob = next(
                x
                for x in api.get("/production/generation", params={"project_id": pid}).json()[
                    "data"
                ]["jobs"]
                if x["data"]["profile_id"] == "reference-protocol"
            )
            assert (
                refjob["data"]["reference_asset_ref"] == original
                and refjob["data"]["output_kind"] == "video"
            )
            assert len(issued) == 3 and issued[-1]["fixture_observed_image_sha256"]
            gen.locator("[name=profile_id]").select_option("browser-protocol")
            expect(gen.locator("[name=reference_pin]")).to_have_count(0)
            checks.append(
                "reference-conditioned video fixture: required inline project Asset/version selection, actual PNG upload/import preview, immutably bound generated still uploaded to worker and resulting timed video ingested; text-only profile hides reference"
            )
            assert not report["page_errors"], report["page_errors"]
        browser.close()
    report["passed"] = True
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"checks": len(checks), "engine": engine, "out": str(output)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--round", default="1")
    p.add_argument("--engine", choices=["webkit", "chromium"], default="chromium")
    a = p.parse_args()
    run(a.output.resolve(), a.round, a.engine)
