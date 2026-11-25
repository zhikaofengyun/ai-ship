"""Review a recorded negative result from real local inference against its actual input frame."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import ROOT, server
from comfy_protocol_fixture import sample_generator


def data(response):
    response.raise_for_status()
    return response.json()["data"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt_path = ROOT / "evidence/batch-production/mixed-routing/inference.json"
    receipt = json.loads(receipt_path.read_text())
    record = next(r for r in receipt["items"] if r["route"] == "generated")
    clip = receipt_path.with_name("model-output.webm")
    segment_dir = receipt_path.parent.parent / "video-segment"
    segment_report = json.loads((segment_dir / "inference.json").read_text())
    segment_clip = segment_dir / "candidate.webm"
    source = ROOT / "evidence/batch-production/real-footage/coffee-excerpt.mp4"
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha(clip) == record["model_output"]["sha256"] and sha(source) == receipt["source_sha256"]
    assert sha(segment_clip) == segment_report["candidate"]["sha256"]
    with (
        tempfile.TemporaryDirectory() as td,
        sample_generator(clip, segment_clip=segment_clip) as (env, issued),
        server(Path(td), output, fixture_env=env) as base,
        httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=30) as client,
        sync_playwright() as pw,
    ):
        project = data(client.post("/projects", json={"name": "咖啡器具 · 生成审片"}))["id"]
        asset = data(
            client.post(
                "/media/import",
                params={"project_id": project, "filename": "咖啡烘焙实拍 · Keria T.mp4"},
                content=source.read_bytes(),
            )
        )["asset"]
        gen = "/production/generation"
        job = data(
            client.post(
                gen,
                json={
                    "project_id": project,
                    "idempotency_key": "recorded-negative-review",
                    "slot": "咖啡杯与烘焙器具",
                    "profile_id": "reference-protocol",
                    "seed": record["seed"],
                    "prompt": "保留白色咖啡杯与原画面主体",
                    "reference_asset_id": asset["id"],
                    "reference_asset_version": asset["version"],
                    "reference_time_seconds": 1.239,
                },
            )
        )["job"]
        jid = job["id"]
        ready = data(client.post(gen + "/" + jid + "/refresh"))["job"]
        assert ready["data"]["stage"] == "ready", ready["data"]
        assert issued[0]["fixture_observed_image_sha256"] == record["reference_input"]["png_sha256"]
        api_path = gen + "/" + jid + "/visual-review"
        ctx_info = data(client.get(api_path, params={"project_id": project}))
        assert ctx_info["candidate"]["content_sha256"] == sha(clip)
        exe = os.getenv("AI_SHIP_" + args.engine.upper())
        if args.engine == "chromium" and not exe:
            exe = shutil.which("chromium-headless-shell") or shutil.which("chromium")
        browser = getattr(pw, args.engine).launch(executable_path=exe)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('ship-project', " + json.dumps(project) + ")")
        page.goto(base + "/#production/generation")
        origin = page.locator("[data-generation-job]").filter(has_text="咖啡杯与烘焙器具")
        open_button = origin.get_by_role("button", name="对照审片")
        dlg = page.locator("dialog[open]")
        reviews = []

        def pick_frame(value):
            time = dlg.get_by_label("候选时码（秒）")
            with page.expect_response(
                lambda res: "visual-review/candidate.png" in res.url
                and f"time_seconds={value}" in res.url
            ) as changed:
                time.fill(str(value))
            return changed.value.headers["x-frame-sha256"]

        def write(decision, reason):
            dlg.get_by_label("审片结论").select_option(decision)
            dlg.get_by_label("判断依据").fill(reason)
            with page.expect_response(
                lambda res: res.url.endswith("/visual-review") and res.request.method == "POST"
            ) as submitted:
                dlg.get_by_role("button", name="保存审片结果").click()
            response = submitted.value
            assert response.status == 200, response.text()
            reviews.append(response.json()["data"]["audit"])
            expect(dlg.locator("[data-last-decision]")).to_contain_text(reason)
            expect(dlg.locator("[data-review-notice]")).to_have_text("审片结果已保存。")

        open_button.click()
        ref = dlg.get_by_alt_text("生成模型当时收到的参考帧")
        cand = dlg.get_by_alt_text("所选候选时码的实际画面")
        expect(ref).to_be_visible()
        picked = pick_frame(0.375)
        expect(cand).to_be_visible()
        assert ref.evaluate(
            "async i=>{await i.decode();return [i.naturalWidth,i.naturalHeight]}"
        ) == [432, 768]
        assert cand.evaluate(
            "async i=>{await i.decode();return [i.naturalWidth,i.naturalHeight]}"
        ) == [160, 160]
        expect(dlg.locator("[data-caption=reference]")).to_contain_text("1.2 秒")
        expect(dlg.locator("[data-caption=candidate]")).to_contain_text("0.375 秒")
        page.screenshot(path=str(output / "review-desktop.png"))
        write("needs_revision", "白色杯具与实拍参考的结构不一致，需重新生成。")
        assert reviews[-1]["data"]["candidate_frame_sha256"] == picked
        assert reviews[-1]["data"]["reference_sha256"] == record["reference_input"]["png_sha256"]
        pick_frame(0.125)
        dlg.get_by_role("button", name="回到上次审片画面").click()
        expect(dlg.get_by_label("候选时码（秒）")).to_have_value("0.375")
        dlg.get_by_role("button", name="关闭", exact=True).click()
        expect(origin.get_by_role("button", name="用作制作素材")).to_be_disabled()
        page.reload()
        expect(origin.locator("[data-job-review-state]")).to_contain_text("退回修改")
        page.set_viewport_size({"width": 390, "height": 844})
        open_button.click()
        expect(dlg.get_by_label("候选时码（秒）")).to_have_value("0.375")
        expect(cand).to_be_visible()
        expect(dlg.locator("[data-last-decision]")).to_contain_text("退回修改")
        assert dlg.evaluate("e=>e.scrollWidth<=e.clientWidth+1")
        for image in (ref, cand):
            bbox = image.bounding_box()
            assert bbox and bbox["x"] >= 0 and bbox["x"] + bbox["width"] <= 390
        dlg.get_by_label("审片结论").select_option("reject")
        dlg.get_by_label("判断依据").fill("候选画面已丢失参考杯具；保留原片，拒绝该结果。")
        page.screenshot(path=str(output / "review-mobile.png"))
        write("reject", "候选画面已丢失参考杯具；保留原片，拒绝该结果。")
        assert [r["version"] for r in reviews] == [1, 2]
        final = data(client.get(api_path, params={"project_id": project}))
        assert (
            final["last_decision"]["decision"] == "reject"
            and final["last_decision"]["time_seconds"] == 0.375
        )
        # Submit the exact interval, prompt and seed from the recorded motion-input
        # inference through the same compact form operators use.
        dlg.get_by_role("button", name="关闭", exact=True).click()
        form = page.locator("[data-generation-form]")
        form.get_by_label("生成模型").select_option("segment-protocol")
        form.get_by_label("项目参考视频").select_option(
            json.dumps([asset["id"], asset["version"]], separators=(",", ":"))
        )
        recorded_input = segment_report["input"]
        form.get_by_label("连续参考入点（秒）").fill(str(recorded_input["reference_time_seconds"]))
        form.get_by_label("连续参考出点（秒）").fill(str(recorded_input["reference_end_seconds"]))
        form.get_by_label("分镜位置（可选）").fill("输入片段对照")
        form.get_by_label("画面描述").fill(recorded_input["prompt"])
        form.get_by_label("随机种子").fill(str(recorded_input["seed"]))
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/production/generation")
        ) as submission:
            form.get_by_role("button", name="生成候选").click()
        assert submission.value.status == 200, submission.value.text()
        payload = submission.value.request.post_data_json
        assert payload["reference_time_seconds"] == 1.0 and payload["reference_end_seconds"] == 1.5
        sj = submission.value.json()["data"]["job"]
        sjid = sj["id"]
        ready_seg = data(client.post(gen + "/" + sjid + "/refresh"))["job"]
        assert ready_seg["data"]["stage"] == "ready", ready_seg
        wire = issued[-1]["fixture_observed_segment_sha256"]
        assert (
            ready_seg["data"]["reference_input"]["segment_sha256"]
            == wire
            == segment_report["reference"]["sha256"]
        )
        seg_info = data(
            client.get(gen + "/" + sjid + "/visual-review", params={"project_id": project})
        )
        assert seg_info["candidate"]["content_sha256"] == sha(segment_clip)
        page.reload()
        source_btn = (
            page.locator("[data-generation-job]")
            .filter(has_text="输入片段对照")
            .get_by_role("button", name="对照审片")
        )
        source_btn.click()
        player = dlg.locator("[data-reference-player]")
        with page.expect_response(lambda r: "visual-review/reference.mp4" in r.url) as transfer:
            player.locator("summary").click()
        assert (
            transfer.value.status == 200
            and hashlib.sha256(transfer.value.body()).hexdigest() == wire
        )
        video = player.locator("video")
        expect(video).to_have_attribute("src", re.compile(r"^blob:"))
        # Attempt playback of the exact lossless MP4 in each engine. Independent source PNGs
        # must still be selectable for browsers lacking that specific H.264 profile.
        played = video.evaluate("""async v => {
          v.muted = true;
          let played = false;
          try {
            await Promise.race([
              (async () => {
                await v.play();
                if (!v.currentTime) await new Promise(resolve => v.addEventListener("timeupdate", resolve, {once:true}));
              })(),
              new Promise((_, reject) => setTimeout(() => reject(Error('decode timeout')), 4000))
            ]);
            played = v.currentTime > 0;
          } catch {}
          v.pause();
          return {played, media_error:v.error?.code || null};
        }""")
        with page.expect_response(
            lambda r: "visual-review/reference.png" in r.url and "time_seconds=0.26" in r.url
        ) as segment_frame:
            dlg.get_by_label("参考时码（秒）").fill("0.26")
        left = segment_frame.value.headers
        assert float(left["x-frame-pts"]) == 0.25
        candidate_sha = pick_frame(0.375)
        write("needs_revision", "杯盘位置仍可辨认，器皿边沿与背景纹理出现形变，需保留原片细节。")
        snap = reviews[-1]["data"]
        assert snap["reference_wire_sha256"] == wire
        assert snap["reference_sha256"] == left["x-frame-sha256"]
        assert (
            snap["reference_time_seconds"] == 0.26 and snap["reference_frame_pts_seconds"] == 0.25
        )
        assert snap["candidate_frame_sha256"] == candidate_sha
        expect(dlg.locator("[data-caption=reference]")).to_contain_text("原片")
        dlg.get_by_role("button", name="关闭", exact=True).click()
        source_btn.click()
        expect(dlg.get_by_label("参考时码（秒）")).to_have_value("0.26")
        expect(dlg.get_by_label("候选时码（秒）")).to_have_value("0.375")
        expect(dlg.locator("[data-last-decision]")).to_contain_text("参考 0.26 秒")
        assert not errors, errors
        report = {
            "engine": args.engine,
            "browser": browser.version,
            "source_sha256": sha(source),
            "model_output_sha256": sha(clip),
            "recorded_inference": "The protocol fixture replays the repository’s recorded four-frame VP9 output from the cup-fidelity failure, without loading model weights.",
            "reference_frame_sha256": record["reference_input"]["png_sha256"],
            "candidate_frame_sha256": picked,
            "candidate_frame_pts_seconds": reviews[1]["data"]["candidate_frame_pts_seconds"],
            "review_revisions": [
                {
                    "version": r["version"],
                    "decision": r["data"]["decision"],
                    "reason": r["data"]["reason"],
                }
                for r in reviews[:2]
            ],
            "segment_playback_and_review": {
                "wire_sha256": wire,
                "recorded_model_output_sha256": sha(segment_clip),
                "submitted_source_interval": [
                    payload["reference_time_seconds"],
                    payload["reference_end_seconds"],
                ],
                "native_playback_advanced": played["played"],
                "native_player_error": played["media_error"],
                "source_frame_sha256": snap["reference_sha256"],
                "input_pts_seconds": snap["reference_frame_pts_seconds"],
                "original_pts_seconds": float(left["x-source-frame-pts"]),
                "candidate_frame_sha256": snap["candidate_frame_sha256"],
                "decision": snap["decision"],
            },
            "viewports": [1440, 390],
            "page_errors": errors,
        }
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
