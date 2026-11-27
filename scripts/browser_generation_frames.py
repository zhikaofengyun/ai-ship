"""Check single-shot and batch reference frames in the real workbench, with a local protocol fixture."""

import argparse
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import ROOT, server
from comfy_protocol_fixture import sample_generator


def data(response):
    response.raise_for_status()
    return response.json()["data"]


def select_frame(page, form, asset, single, when, source, output):
    form.get_by_label("参考来源").select_option("video")
    choice = form.get_by_label("项目参考视频")
    option = (
        json.dumps([asset["id"], asset["version"]], separators=(",", ":"))
        if single
        else asset["id"]
    )
    assert option not in choice.locator("option").evaluate_all("(items) => items.map(x => x.value)")
    form.get_by_role("button", name="检索全部参考素材").click()
    dialog = page.locator("dialog[open]")
    query = "咖啡烘焙" if single else asset["id"]
    dialog.get_by_label("名称或素材编号").fill(query)

    def searched(response):
        url = urlsplit(response.url)
        return (
            url.path.endswith("/generation/reference/assets")
            and parse_qs(url.query).get("q") == [query]
            and parse_qs(url.query).get("media_type") == ["video"]
        )

    with page.expect_response(searched) as search_response:
        dialog.get_by_role("button", name="查找", exact=True).click()
    hits = data_from_browser(search_response.value)["items"]
    assert any(r["id"] == asset["id"] and r["version"] == asset["version"] for r in hits)
    pick = dialog.locator("[data-pick]")
    expect(pick).to_have_count(1)
    if not single:
        bounds = dialog.bounding_box()
        assert (
            bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= page.viewport_size["width"] + 0.5
        )
        page.screenshot(path=output / "lookup-narrow.png")
    pick.click()
    expect(dialog).to_have_count(0)
    expect(choice).to_have_value(option)
    time = form.get_by_label("源视频时码（秒）")
    preview = form.get_by_alt_text("选定源时码的实际参考帧")
    expect(preview).to_be_visible()
    time.fill("9")
    time.press("Tab")
    expect(preview).to_have_count(0)
    assert not time.evaluate("el => el.checkValidity()")

    def matches(response):
        url = urlsplit(response.url)
        return url.path.endswith("/generation/reference/frame") and parse_qs(url.query).get(
            "time_seconds"
        ) == [when]

    with page.expect_response(matches) as changed:
        time.fill(when)
        time.press("Tab")
    response = changed.value
    assert response.status == 200 and response.headers["content-type"] == "image/png"
    image = response.body()
    digest = hashlib.sha256(image).hexdigest()
    assert digest == response.headers["x-frame-sha256"]
    pts = Decimal(response.headers["x-frame-pts"])
    number = int(Decimal(when) * 25)
    assert pts == Decimal(number) / 25
    full_decode = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            str(source),
            "-vf",
            f"select=eq(n\\,{number})",
            "-filter_threads",
            "1",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgba",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    preview_pixels = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            "pipe:0",
            "-pix_fmt",
            "rgba",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        input=image,
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout
    assert preview_pixels == full_decode.stdout
    expect(preview).to_be_visible()
    assert preview.evaluate(
        "async img => {await img.decode(); return img.naturalWidth === 432 && img.naturalHeight === 768}"
    )
    return {
        "requested_seconds": float(when),
        "frame_pts_seconds": float(pts),
        "frame_number": number,
        "png_sha256": digest,
    }


def check_job(job, frame, asset, source_sha, issued):
    proof = job["data"]["reference_input"]
    assert (
        proof["asset_ref"]["id"] == asset["id"]
        and proof["asset_ref"]["version"] == asset["version"]
    )
    assert proof["source_time_seconds"] == frame["requested_seconds"]
    assert proof["frame_pts_seconds"] == frame["frame_pts_seconds"]
    assert proof["original_sha256"] == source_sha
    assert proof["png_sha256"] == issued["fixture_observed_image_sha256"] == frame["png_sha256"]
    return {
        **frame,
        "frame_asset": proof["frame_ref"]["id"],
        "job_id": job["id"],
        "provider_sha256": issued["fixture_observed_image_sha256"],
    }


def run(output, engine):
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / "evidence/batch-production/real-footage/coffee-excerpt.mp4"
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = json.loads(source.with_name("coffee-evidence.json").read_text())
    assert source_sha == evidence["attributed_download_sha256"]
    report = {
        "engine": engine,
        "source_sha256": source_sha,
        "author": evidence["source"]["author"],
        "license": evidence["source"]["license"],
        "source_page": evidence["source"]["description_url"],
        "provider": "Local ComfyUI HTTP fixture returned the source video; this checks actual decoded input pixels and upload bytes, not inference quality.",
        "page_errors": [],
    }
    with (
        tempfile.TemporaryDirectory() as td,
        sample_generator(
            source, segment_clip=ROOT / "evidence/batch-production/video-segment/candidate.webm"
        ) as (env, issued),
        server(Path(td), output, fixture_env=env) as base,
        httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=30) as api,
        sync_playwright() as pw,
    ):
        project = data(
            api.post("/records", json={"kind": "Project", "data": {"name": "咖啡实拍取帧"}})
        )["id"]
        asset = data(
            api.post(
                "/media/import",
                params={"project_id": project, "filename": "咖啡烘焙 · Keria T · CC BY-SA 4.0.mp4"},
                content=source.read_bytes(),
            )
        )["asset"]
        info = data(
            api.get(
                "/production/assets/admission",
                params={"project_id": project, "source_id": asset["data"]["source_id"]},
            )
        )
        data(
            api.post(
                "/production/assets/admission",
                json={
                    "project_id": project,
                    "request_id": "source-reviewed",
                    "source": {k: info["source"][k] for k in ("id", "version")},
                    "expected_revision": info["revision"],
                    "action": "approve",
                    "allowed_purposes": ["inspect", "process", "export"],
                    "reason": "Local frame verification, Keria T's CC BY-SA 4.0 attributed excerpt.",
                },
            )
        )
        asset = data(api.get("/records/" + asset["id"]))
        for n in range(501):
            data(
                api.post(
                    "/records",
                    json={
                        "project_id": project,
                        "kind": "Script",
                        "data": {"text": f"较新的试剪脚本 {n}"},
                    },
                )
            )
        initial = data(api.get("/records", params={"project_id": project, "limit": 500}))
        assert len(initial) == 500 and all(r["id"] != asset["id"] for r in initial)
        report["asset_lookup"] = {
            "newer_project_scripts": 501,
            "homepage_records": len(initial),
            "source_in_homepage": False,
            "single": "filename search",
            "batch": "ID search",
        }
        exe = os.getenv("AI_SHIP_" + engine.upper())
        if engine == "chromium" and not exe:
            exe = shutil.which("chromium-headless-shell") or shutil.which("chromium")
        browser = getattr(pw, engine).launch(executable_path=exe)
        report["browser_version"] = browser.version
        page = browser.new_page(viewport={"width": 1440, "height": 920}, locale="zh-CN")
        page.set_default_timeout(15000)
        page.add_init_script("localStorage.setItem('ship-project'," + json.dumps(project) + ")")
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
        page.goto(base + "/#production")
        page.locator(".director-generation summary").click()
        form = page.locator("[data-generation-form]")
        form.get_by_label("生成模型").select_option("reference-protocol")
        frame = select_frame(page, form, asset, True, "1.238", source, output)
        page.reload()
        page.locator(".director-generation summary").click()
        expect(form.get_by_label("源视频时码（秒）")).to_have_value("1.238")
        expect(form.get_by_alt_text("选定源时码的实际参考帧")).to_be_visible()
        form.get_by_label("画面描述").fill("保留已选咖啡烘焙画面的构图")
        form.get_by_alt_text("选定源时码的实际参考帧").scroll_into_view_if_needed()
        page.screenshot(path=output / "single.png")
        # Keep the PTS fixed while changing the selected instant. A blur-time repaint
        # used to replace the button and swallow this first click before dispatch.
        frame["preview_requested_seconds"] = frame["requested_seconds"]
        frame["requested_seconds"] = 1.239
        form.get_by_label("源视频时码（秒）").fill("1.239")
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/production/generation")
        ) as submitted:
            form.get_by_role("button", name="生成候选").click()
        job = data_from_browser(submitted.value)["job"]
        report["single"] = check_job(job, frame, asset, source_sha, issued[0])
        page.locator("[data-action=gen-refresh]").first.click()
        expect(page.locator("[data-generation-job]").first).to_contain_text("文件已到库")
        form.get_by_label("生成模型").select_option("browser-protocol")
        expect(form.get_by_label("源视频时码（秒）")).to_have_count(0)
        page.locator('.nav-item[href="#production/batches"]').click()
        page.locator("[data-action=switch-kind]").click()
        form = page.locator(".batch-form")
        form.locator("[name=name]").fill("实拍时码参考 · 批次")
        form.get_by_label("生成服务").select_option("reference-protocol")
        form.get_by_label("画面描述").fill("用所选实拍帧引导咖啡画面")
        form.get_by_label("使用时长（秒）").fill("0.5")
        form.locator("[name=count]").fill("1")
        page.set_viewport_size({"width": 390, "height": 844})
        frame = select_frame(page, form, asset, False, "6.716", source, output)
        page.reload()
        expect(form.get_by_label("源视频时码（秒）")).to_have_value("6.716")
        expect(form.get_by_alt_text("选定源时码的实际参考帧")).to_be_visible()
        preview = form.get_by_alt_text("选定源时码的实际参考帧")
        preview.scroll_into_view_if_needed()
        box = preview.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390.5
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.locator("#toasts .toast")).to_have_count(0)
        page.screenshot(path=output / "batch-narrow.png")
        frame["preview_requested_seconds"] = frame["requested_seconds"]
        frame["requested_seconds"] = 6.717
        form.get_by_label("源视频时码（秒）").fill("6.717")
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/production/batches")
        ) as submitted:
            form.get_by_role("button", name="提交批量剪辑").click()
        response = submitted.value
        submitted_slot = response.request.post_data_json["slots"][0]["generate"]
        assert (
            submitted_slot["reference_asset_version"] == asset["version"]
            and submitted_slot["reference_time_seconds"] == frame["requested_seconds"]
        )
        batch_id = data_from_browser(response)["id"]
        expect(page.locator(".batch-progress")).to_have_attribute("value", "1", timeout=60000)
        batch = data(api.get("/production/batches/" + batch_id))
        assert batch["counts"]["succeeded"] == 1, batch
        result = batch["items"][0]
        job_id = result["generation_id"]
        jobs = data(api.get("/production/generation", params={"project_id": project}))["jobs"]
        job = next(item for item in jobs if item["id"] == job_id)
        assert len(issued) == 2 and job["data"]["stage"] == "ready"
        report["batch"] = {
            **check_job(job, frame, asset, source_sha, issued[1]),
            "batch_id": batch_id,
            "timeline_id": result["timeline_id"],
        }
        assert report["single"]["png_sha256"] != report["batch"]["png_sha256"]
        report["asset_lookup"]["reopened_draft_preserved_pin_and_time"] = {
            "single": True,
            "batch": True,
        }
        report["one_click_submit_from_focused_time_field"] = {"single": True, "batch": True}
        # A compact submission opens the full-width queue. Resume the same saved composition.
        page.get_by_role("button", name="返回编排").click()
        expect(form.get_by_label("源视频时码（秒）")).to_have_value("6.717")
        form.get_by_role("button", name="混用原片与生成").click()
        form.get_by_role("button", name="检索全部素材").click()
        dlg = page.locator("dialog[open]")
        dlg.get_by_label("名称或镜头标注").fill("咖啡烘焙")
        dlg.get_by_label("检索方式").select_option("source")
        dlg.get_by_role("button", name="查找", exact=True).click()
        dlg.get_by_role("button", name="加入制作").first.click()
        form.get_by_label("出点（秒）").fill("0.6")
        form.get_by_label("本批作品数").fill("2")
        form.get_by_label("交付目标").select_option("render")
        form.locator(".batch-options summary").click()
        form.get_by_label("宽度").fill("320")
        form.get_by_label("高度").fill("240")
        form.get_by_label("帧率").select_option("25")
        with page.expect_response(
            lambda r: r.url.endswith("/production/batches/estimate")
        ) as estimated:
            form.get_by_role("button", name="核算生成用量").click()
        assert data_from_browser(estimated.value) == {
            "generation_calls": 1,
            "slot_calls": [1],
            "workflow_calls": 0,
        }
        expect(form.locator("[data-forecast]")).to_contain_text("预计生成 1 次")
        assert len(issued) == 2
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=output / "mixed-narrow.png")
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/production/batches")
        ) as submitted:
            form.get_by_role("button", name="提交批量渲染").click()
        mixed = data_from_browser(submitted.value)
        expect(page.locator(".batch-progress")).to_have_attribute("value", "2", timeout=60000)
        detail = data(api.get("/production/batches/" + mixed["id"]))
        assert detail["counts"]["succeeded"] == 2 and len(issued) == 3, detail
        mixed_rows = []
        for item in detail["items"]:
            video = api.get("/media/artifacts/" + item["artifact_id"] + "/file")
            video.raise_for_status()
            subprocess.run(
                ["ffmpeg", "-v", "error", "-xerror", "-i", "pipe:0", "-f", "null", "-"],
                input=video.content,
                check=True,
            )
            mixed_rows.append(
                {
                    "ordinal": item["ordinal"],
                    "generator": bool(item["generation_jobs"]),
                    "mp4_sha256": hashlib.sha256(video.content).hexdigest(),
                    "mp4_bytes": len(video.content),
                }
            )
        assert {row["generator"] for row in mixed_rows} == {True, False}
        report["mixed_slot"] = {"estimate": 1, "submissions": 1, "decoded": mixed_rows}
        queue_job = page.locator(".batch-rows li").filter(has=page.locator("details"))
        queue_job.locator("details > summary").click()
        queue_job.get_by_role("button", name="对照审片").click()
        review = page.locator("dialog[open]")
        expect(review.get_by_alt_text("生成模型当时收到的参考帧")).to_be_visible()
        expect(review.get_by_alt_text("所选候选时码的实际画面")).to_be_visible()
        # This protocol fixture echoes the complete original clip. Compare the same PTS on
        # both sides before accepting this exact-match control through the compact batch queue.
        with page.expect_response(
            lambda r: "visual-review/candidate.png" in r.url and "time_seconds=6.717" in r.url
        ) as selected:
            review.get_by_label("候选时码（秒）").fill("6.717")
        matched = selected.value.headers["x-frame-sha256"]
        assert matched == frame["png_sha256"]
        reason = "同一时码的参考帧与原片回传夹具逐像素一致。"
        review.get_by_label("审片结论").select_option("accept")
        review.get_by_label("判断依据").fill(reason)
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/visual-review")
        ) as reviewed:
            review.get_by_role("button", name="保存审片结果").click()
        decision = data_from_browser(reviewed.value)["audit"]["data"]
        assert decision["decision"] == "accept" and decision["reference_sha256"] == matched
        assert decision["candidate_frame_sha256"] == matched and decision["reason"] == reason
        review.get_by_role("button", name="关闭", exact=True).click()
        report["batch_queue_reference_review"] = {"decision": "accept", "matched_frame": matched}
        # Switch the existing mixed slot from a frame to a continuous source and retain both routes.
        page.get_by_role("button", name="返回编排").click()
        form.get_by_label("生成服务").select_option("segment-protocol")
        form.get_by_role("button", name="检索全部参考素材").click()
        lookup = page.locator("dialog[open]")
        lookup.get_by_label("名称或素材编号").fill(asset["id"])
        lookup.get_by_role("button", name="查找", exact=True).click()
        lookup.locator("[data-pick]").first.click()
        form.get_by_label("连续参考入点（秒）").fill("1")
        form.get_by_label("连续参考出点（秒）").fill("1.5")
        form.get_by_label("连续参考出点（秒）").press("Tab")
        page.reload()
        expect(form.get_by_label("连续参考入点（秒）")).to_have_value("1")
        expect(form.get_by_label("连续参考出点（秒）")).to_have_value("1.5")
        expect(form.get_by_label("实拍参考区间")).to_be_visible()
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/production/batches")
        ) as submitted:
            form.get_by_role("button", name="提交批量渲染").click()
        submit = submitted.value
        gen = submit.request.post_data_json["slots"][0]["generate"]
        assert gen["reference_time_seconds"] == 1 and gen["reference_end_seconds"] == 1.5
        assert (
            gen["reference_asset_id"] == asset["id"]
            and gen["reference_asset_version"] == asset["version"]
        )
        bid = data_from_browser(submit)["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            page_data = data(api.get("/production/batches/" + bid))
            if page_data["counts"]["failed"] or page_data["counts"]["succeeded"] == 2:
                break
            time.sleep(0.25)
        assert page_data["counts"]["succeeded"] == 2 and len(issued) == 4, page_data
        sha = issued[-1]["fixture_observed_segment_sha256"]
        frozen = ROOT / "evidence/batch-production/video-segment/reference.mp4"
        assert sha == hashlib.sha256(frozen.read_bytes()).hexdigest()
        report["continuous_mixed_slot"] = {
            "submitted_interval": [1, 1.5],
            "confirmed_input_sha256": sha,
            "completed": 2,
            "provider_calls": 1,
        }
        assert not report["page_errors"]
        browser.close()
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(engine, report["browser_version"], "single + narrow batch passed", flush=True)


def data_from_browser(response):
    assert response.status == 200, response.text()
    return response.json()["data"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    args = parser.parse_args()
    run(args.output.resolve(), args.engine)
