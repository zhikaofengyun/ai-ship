"""Exercise local CLIP, source timecodes, an actual moving video and live revocation over HTTP."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

import httpx
from browser_regression import server

FIXTURES = {
    "astronaut.png": (
        "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png",
        "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5",
    ),
    "coffee.png": (
        "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/coffee.png",
        "cc02f8ca188b167c775a7101b5d767d1e71792cf762c33d6fa15a4599b5a8de7",
    ),
    "flower.mp4": (
        "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
        "0cd83d944a6ca7822b4a8306cecc60a36e859b041f6702c6a1ad9ead78924451",
    ),
}


def verify(out, fixtures, browser):
    out.mkdir(parents=True, exist_ok=True)
    fixtures.mkdir(parents=True, exist_ok=True)
    for name, (url, checksum) in FIXTURES.items():
        file = fixtures / name
        if not file.exists():
            with urllib.request.urlopen(url, timeout=40) as response:
                file.write_bytes(response.read(16 * 1024 * 1024))
        assert hashlib.sha256(file.read_bytes()).hexdigest() == checksum, name
    weights = Path(os.environ["AI_SHIP_CLIP_MODEL_DIR"]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        model = tmp / "weights"
        model.mkdir()
        for name in ("text_model_quantized.onnx", "vision_model_quantized.onnx"):
            (model / name).symlink_to(weights / name)
        shutil.copyfile(weights / "tokenizer.json", model / "tokenizer.json")
        mixed = tmp / "001.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-framerate",
                "10",
                "-loop",
                "1",
                "-t",
                "2",
                "-i",
                str(fixtures / "astronaut.png"),
                "-framerate",
                "10",
                "-loop",
                "1",
                "-t",
                "2",
                "-i",
                str(fixtures / "coffee.png"),
                "-filter_complex_threads",
                "1",
                "-filter_complex",
                "[0:v]scale=320:320:force_original_aspect_ratio=increase,crop=320:320,setsar=1[a];[1:v]scale=320:320:force_original_aspect_ratio=increase,crop=320:320,setsar=1[b];[a][b]concat=n=2:v=1:a=0",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "10",
                "-threads",
                "1",
                str(mixed),
            ],
            check=True,
        )
        with (
            server(
                tmp / "project", out, fixture_env={"AI_SHIP_CLIP_MODEL_DIR": str(model)}
            ) as base,
            httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=90) as client,
        ):

            def post(route, data):
                r = client.post(route, json=data)
                r.raise_for_status()
                return r.json()["data"]

            def project(name):
                return post("/records", {"kind": "Project", "data": {"name": name}})["id"]

            pid = project("视觉检索验证")
            other = project("其他项目")

            def query(q, **more):
                return post(
                    "/production/asset-index/query",
                    {
                        "project_id": pid,
                        "channel": "visual",
                        "text": q,
                        "visual_threshold": 0.20,
                        **more,
                    },
                )

            def build(**more):
                return post(
                    "/production/asset-index/frames/build", {"project_id": pid, "limit": 1, **more}
                )

            sources = {}
            for name, file in [("001.mp4", mixed), ("002.mp4", fixtures / "flower.mp4")]:
                r = client.post(
                    "/media/import",
                    params={"project_id": pid, "filename": name},
                    content=file.read_bytes(),
                )
                r.raise_for_status()
                sources[name] = r.json()["data"]
            assert (
                not query("001.mp4")["vector_available"]
                and query("001.mp4")["items"][0]["name"] == "001.mp4"
            )
            first = build()
            second = build(after=first["next_after"])
            assert (
                first["remaining"] == 1
                and second["remaining"] == 0
                and not first["errors"]
                and not second["errors"]
            )
            assert first["frames"] + second["frames"] == 6 and build()["processed"] == 0
            observations = []
            for text, file, bounds in [
                ("an astronaut wearing a space suit", "001.mp4", (0, 2)),
                ("a cup of coffee", "001.mp4", (2, 4)),
                ("a bee visiting a flower", "002.mp4", (0, 60)),
            ]:
                hit = query(text)["items"][0]
                f = hit["frame_match"]
                assert (
                    not hit["matched"]
                    and hit["name"] == file
                    and bounds[0] <= f["source_seconds"] < bounds[1]
                ), (text, hit)
                assert len(f["sample_rgb_sha256"]) == 64 and f["space"] == first["space"]
                observations.append(
                    {
                        "text": text,
                        "file": file,
                        "source_seconds": f["source_seconds"],
                        "sample_rgb_sha256": f["sample_rgb_sha256"],
                        "cosine": hit["semantic_score"],
                        "literal_matches": hit["matched"],
                    }
                )
            ui = None
            if browser:
                from playwright.sync_api import expect, sync_playwright

                with sync_playwright() as pw:
                    exe = os.getenv("AI_SHIP_" + browser.upper()) or (
                        shutil.which("chromium") if browser == "chromium" else None
                    )
                    br = getattr(pw, browser).launch(executable_path=exe)
                    page = br.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.add_init_script(
                        "localStorage.setItem('ship-project'," + json.dumps(pid) + ")"
                    )
                    page.goto(base + "/#module/search/S02")
                    page.locator("[data-action=feature-action]").click()
                    dlg = page.locator("dialog[open]")
                    dlg.locator("[name=channel]").select_option("visual")
                    dlg.locator("[name=text]").fill("a cup of coffee")
                    dlg.locator("[name=visual_threshold]").fill("0.22")
                    dlg.get_by_role("button", name="查找", exact=True).click()
                    expect(dlg.locator("[data-hits] li").first).to_contain_text("画面")
                    dlg.locator("[data-preview]").first.click()
                    expected = observations[1]["source_seconds"]
                    page.wait_for_function(
                        "t=>{const v=document.querySelector('#indexed-preview video');return v&&Math.abs(v.currentTime-t)<0.08&&v.readyState>=2&&!v.seeking}",
                        arg=expected,
                    )
                    page.screenshot(path=str(out / "frame-search.png"))
                    dlg.get_by_text("登记场景或动作标注", exact=True).click()
                    review = dlg.locator("[data-reviewed-frame]")
                    review.locator("[name=label]").fill("桌面上的咖啡杯")
                    review.locator("[name=end]").fill(str(expected + 0.1))
                    review.locator("[name=evidence]").fill("在源帧中直接观察到咖啡杯。")
                    review.get_by_role("button").click()
                    expect(review.get_by_role("status")).to_contain_text("标注已保存")
                    tags = client.get("/records", params={"project_id": pid, "kind": "Tag"}).json()[
                        "data"
                    ]
                    assert len(tags) == 1
                    proof = tags[0]["data"]["reviewed_source_frame"]
                    assert (
                        proof["source_pts_us"] == round(expected * 1e6)
                        and proof["source_rgb_sha256"] == observations[1]["sample_rgb_sha256"]
                    )
                    assert build()["processed"] == 0
                    assert not errors, errors
                    ui = {
                        "engine": browser,
                        "version": br.version,
                        "reviewed_tag_source_pts_us": proof["source_pts_us"],
                        "observed_seconds": dlg.locator("video").evaluate("v=>v.currentTime"),
                        "page_errors": errors,
                    }
                    dlg.locator("[data-open-source]").click()
                    page.wait_for_function(
                        "t=>{const v=document.querySelector('#preview-stage video');return v&&Math.abs(v.currentTime-t)<0.08&&v.readyState>=2&&!v.seeking}",
                        arg=expected,
                    )
                    ui["editor_source_seconds"] = page.locator("#preview-stage video").evaluate(
                        "v=>v.currentTime"
                    )
                    assert not errors, errors
                    br.close()
            sample = query("a cup of coffee")["items"][0]
            frame = sample["frame_match"]
            attempt = client.post(
                "/production/tags",
                json={
                    "project_id": pid,
                    "asset_version_id": sample["version_id"],
                    "start": frame["source_seconds"],
                    "end": frame["source_seconds"] + 0.1,
                    "label": "test",
                    "category": "scene",
                    "evidence": "test",
                    "reviewed_frame": {
                        **frame,
                        "version": sample["revision"],
                        "sample_rgb_sha256": "0" * 64,
                    },
                },
            )
            assert (
                attempt.status_code == 409
                and attempt.json()["error"]["code"] == "FRAME_EVIDENCE_MISMATCH"
            ), attempt.text
            assert query("a cup of coffee", project_id=other)["count"] == 0
            asset = sources["001.mp4"]["asset"]
            admission = client.get(
                "/production/assets/admission",
                params={"project_id": pid, "source_id": asset["data"]["source_id"]},
            ).json()["data"]
            post(
                "/production/assets/admission",
                {
                    "project_id": pid,
                    "request_id": "clip-deny-a",
                    "source": {k: admission["source"][k] for k in ("id", "version")},
                    "expected_revision": admission["revision"],
                    "action": "restrict",
                    "allowed_purposes": ["inspect"],
                    "reason": "验证撤销加工许可后旧向量被排除",
                },
            )
            assert all(
                v["name"] != "001.mp4"
                for v in query("a cup of coffee", visual_threshold=-1)["items"]
            )
            with (model / "tokenizer.json").open("a") as f:
                f.write("\n")
            different = query("002.mp4")
            assert not different["vector_available"] and different["items"][0]["name"] == "002.mp4"
            new = build()
            assert new["space"] != first["space"] and new["processed"] == 1
            result = {
                "passed": True,
                "space": first["space"],
                "frames": 6,
                "fixtures": {k: {"source": v[0], "sha256": v[1]} for k, v in FIXTURES.items()},
                "queries": observations,
                "checks": {
                    "repeated_build_skips_indexed_sources": True,
                    "different_project_excluded": True,
                    "revoked_processing_grant_excluded": True,
                    "model_change_isolated": True,
                    "mismatched_frame_proof_rejected": True,
                    "literal_fallback_available": True,
                },
                "browser": ui,
            }
            (out / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--fixture-dir", type=Path, required=True)
    p.add_argument("--browser", choices=["chromium", "webkit"])
    a = p.parse_args()
    verify(a.output.resolve(), a.fixture_dir.resolve(), a.browser)
