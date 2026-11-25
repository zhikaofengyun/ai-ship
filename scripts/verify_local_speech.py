"""Opt-in real bilingual ASR and human review over ordinary production interfaces."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.speech_worker import model_digest


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    profiles = {
        key: {"path": str(path.resolve()), "sha256": model_digest(path), "language": lang}
        for key, path, lang in [
            ("en", args.english_model, "en-US"),
            ("zh", args.chinese_model, "zh-CN"),
        ]
    }
    report = {
        "models": {k: {a: b for a, b in v.items() if a != "path"} for k, v in profiles.items()}
    }
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        silence, silent_video, delayed = (
            work / "silence.wav",
            work / "no-audio.mp4",
            work / "delayed.mp4",
        )

        def ff(*opts):
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *map(str, opts)], check=True)

        ff("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "2", "-c:a", "pcm_s16le", silence)
        ff(
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=96x64:r=4:d=7",
            "-an",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            silent_video,
        )
        ff(
            "-i",
            silent_video,
            "-itsoffset",
            "1.5",
            "-i",
            args.chinese_audio,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            delayed,
        )
        with (
            server(
                work,
                work,
                fixture_env={
                    "AI_SHIP_ASR_MODELS": json.dumps(
                        {**profiles, "wrong-pin": {**profiles["en"], "sha256": "0" * 64}}
                    )
                },
            ) as base,
            httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=120) as client,
        ):

            def value(r):
                assert r.is_success, (r.status_code, r.text)
                return r.json()["data"]

            project = value(client.post("/projects", json={"name": "本地双语字幕验收"}))
            pid = project["id"]

            def upload(path):
                rec = value(
                    client.post(
                        "/media/import",
                        params={"project_id": pid, "filename": path.name},
                        content=path.read_bytes(),
                    )
                )["version"]
                return {"id": rec["id"], "version": rec["version"]}

            def recog(path, model):
                body = {
                    "project_id": pid,
                    "source": upload(path),
                    "request_id": path.stem,
                    "model_id": model,
                }
                return client.post("/production/speech/recognize", json=body), body

            report["engine"] = value(client.get("/production/speech/models"))
            items = {}
            for key, path, model in [
                ("english", args.english_audio, "en"),
                ("mandarin", args.chinese_audio, "zh"),
                ("delayed_mandarin", delayed, "zh"),
                ("digital_silence", silence, "en"),
            ]:
                r, body = recog(path, model)
                item = value(r)["candidate"]
                d = item["data"]
                assert d["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
                assert d["model_sha256"] == profiles[model]["sha256"]
                assert (
                    value(client.post("/production/speech/recognize", json=body))["candidate"]["id"]
                    == item["id"]
                )
                items[key] = item
                report[key] = {
                    k: d[k]
                    for k in (
                        "clock",
                        "source_sha256",
                        "analysis_wav_sha256",
                        "status",
                        "source_window",
                        "cues",
                        "engine_version",
                        "audio_stream",
                    )
                }
            assert (
                report["english"]["source_sha256"]
                == "59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e"
            )
            assert (
                report["mandarin"]["source_sha256"]
                == "2f9fc9c912bb71c85fb286cb88b599c81efb8f727c727a5ea8f6d1c89c55ac13"
            )
            assert "what you can do for your country" in " ".join(
                c["text"] for c in report["english"]["cues"]
            )
            assert "房地产" in "".join(c["text"] for c in report["mandarin"]["cues"])
            delta = (
                report["delayed_mandarin"]["cues"][0]["start"]
                - report["mandarin"]["cues"][0]["start"]
            )
            assert 1.25 <= delta <= 1.75, delta
            assert (
                report["digital_silence"]["cues"] == []
                and report["digital_silence"]["status"] == "no_speech_detected"
            )
            r, _ = recog(silent_video, "zh")
            assert r.status_code == 422 and "NO_AUDIO" in r.text
            report["no_audio_track"] = {"status": r.status_code, "code": "NO_AUDIO"}
            pin = items["delayed_mandarin"]
            bounds = {
                "project_id": pid,
                "request_id": "crop",
                "model_id": "zh",
                "source": {k: pin["data"]["source"][k] for k in ("id", "version")},
                "start_seconds": 1.5,
                "end_seconds": 5.7,
            }
            subset = value(client.post("/production/speech/recognize", json=bounds))["candidate"][
                "data"
            ]
            assert (
                subset["source_window"] == [1.5, 5.7]
                and abs(subset["cues"][0]["start"] - pin["data"]["cues"][0]["start"]) <= 0.12
            )
            report["nonzero_input_window"] = {
                "bounds": subset["source_window"],
                "cues": subset["cues"],
            }
            mismatch = client.post(
                "/production/speech/recognize",
                json={**bounds, "model_id": "wrong-pin", "request_id": "wrong-model-pin"},
            )
            assert mismatch.status_code == 409 and "ASR_INFERENCE_FAILED" in mismatch.text
            report["model_pin_mismatch"] = {"status": 409, "code": "ASR_INFERENCE_FAILED"}
            r = client.get(
                "/production/speech/reviews/" + pin["id"] + "/captions", params={"project_id": pid}
            )
            assert not r.is_success
            report["candidate_export_blocked"] = r.status_code
            if args.chromium:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        executable_path=str(args.chromium.resolve()), chromium_sandbox=True
                    )
                    page = browser.new_page(
                        viewport={"width": 1200, "height": 920}, accept_downloads=True
                    )
                    page.add_init_script(
                        "localStorage.setItem('ship-project'," + json.dumps(pid) + ")"
                    )
                    page.goto(base + "/#module/voice/V04")
                    page.get_by_role("button", name="识别对白与逐句审核", exact=True).click()
                    page.locator("[name=source]").select_option(pin["data"]["source"]["id"])
                    page.locator("[name=model]").select_option("zh")
                    page.get_by_role("button", name="识别此区间").click()
                    expect(page.get_by_role("heading", name="逐句审核源音字幕")).to_be_visible()
                    corrected = "广州市房地产中介协会分析。"
                    page.locator("[name=cue-0]").fill(corrected)
                    page.locator("[name=reason]").fill("已对照原声核实词句及标点")
                    page.screenshot(path=str(output / "human-review.png"))
                    page.get_by_role("button", name="保存审核", exact=True).click()
                    expect(page.get_by_role("heading", name="已审核的源音字幕")).to_be_visible()
                    with page.expect_download() as downloaded:
                        page.get_by_role("button", name="下载 SRT 字幕").click()
                    content = downloaded.value.path().read_text()
                    filename = downloaded.value.suggested_filename
                    assert filename.startswith("speech-") and filename.endswith(".srt")
                    assert corrected in content
                    parsed = value(
                        client.post(
                            "/media/captions/parse",
                            json={"filename": filename, "content": content, "language": "zh"},
                        )
                    )
                    assert parsed["captions"][0]["text"] == corrected
                    assert (
                        abs(parsed["captions"][0]["start"] - pin["data"]["cues"][0]["start"])
                        < 0.0011
                    )
                    report["review_to_editor"] = {
                        "engine": browser.version,
                        "reviewed_text": corrected,
                        "caption_interchange": parsed,
                    }
                    reviewed = work / filename
                    downloaded.value.save_as(reviewed)
                    page.keyboard.press("Escape")
                    page.goto(base + "/#editor")
                    page.locator(".room-bin-row").filter(has_text=delayed.name).locator(
                        '[data-action="add-asset"]'
                    ).click()
                    expect(page.locator(".room-clip")).to_have_count(1)
                    with page.expect_file_chooser() as chooser:
                        page.locator('[data-action="import-captions"]').first.click()
                    chooser.value.set_files(str(reviewed))
                    expect(page.locator(".timeline-caption")).to_have_count(1)
                    page.locator('[data-action="save-timeline"]').first.click()
                    expect(page.locator("#edit-status")).to_have_text("已保存")
                    page.reload()
                    expect(page.locator(".timeline-caption")).to_have_count(1)
                    saved = value(
                        client.get("/records", params={"project_id": pid, "kind": "Timeline"})
                    )[0]
                    caption = saved["data"]["captions"][0]
                    assert (
                        caption["text"] == corrected
                        and caption["start"] == parsed["captions"][0]["start"]
                    )
                    assert caption["source_filename"] == filename
                    report["review_to_editor"]["saved_timeline_caption"] = caption
                    page.screenshot(path=str(output / "timeline-import.png"))
                    browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for opt in ["english-model", "chinese-model", "english-audio", "chinese-audio", "output"]:
        parser.add_argument("--" + opt, required=True, type=Path)
    parser.add_argument("--chromium", type=Path)
    run(parser.parse_args())
