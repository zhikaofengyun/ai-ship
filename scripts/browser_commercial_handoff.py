"""Run the commercial package, creator draft and post-observation flow in a real browser."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import zipfile

import httpx
from playwright.sync_api import sync_playwright, expect
from browser_regression import server


def seed(base, workspace):
    with httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=60) as c:

        def post(route, body):
            response = c.post(route, json=body)
            response.raise_for_status()
            return response.json()["data"]

        raw = post("/projects", {"name": "商品交接验收", "market": "US"})
        pid = raw["id"]

        def make(kind, data, refs=None):
            return post(
                "/records", {"kind": kind, "project_id": pid, "data": data, "refs": refs or []}
            )

        path = workspace / "fixture.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=128x72:r=12",
                "-t",
                "1",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                str(path),
            ],
            check=True,
        )
        res = c.post(
            "/media/import",
            params={"filename": "coproduction.mp4", "project_id": pid},
            content=path.read_bytes(),
        )
        res.raise_for_status()
        vid = res.json()["data"]["version"]
        timeline = make(
            "Timeline",
            {
                "name": "商品展示",
                "locked": True,
                "duration": 1,
                "format": {"width": 128, "height": 72, "fps": 24},
                "clips": [
                    {
                        "id": "only",
                        "asset_id": vid["id"],
                        "source_in": 0,
                        "source_out": 1,
                        "timeline_start": 0,
                        "track": 0,
                    }
                ],
            },
            [{"id": vid["id"], "version": vid["version"]}],
        )
        rendered = post(
            "/media/render",
            {"project_id": pid, "timeline_id": timeline["id"], "expected_revision": 1},
        )["artifact"]
        for kind in ("content_check", "rights_check"):
            post(
                "/production/approvals",
                {
                    "subject_id": rendered["id"],
                    "subject_version": 1,
                    "kind": kind,
                    "decision": "approved",
                    "evidence": "本机生成测试素材审核",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        source = make("Source", {"name": "本地核对凭据", "provenance": "人工查阅的测试记录"})
        product = post(
            "/production/products/import",
            {
                "project_id": pid,
                "source_id": source["id"],
                "csv_text": "SKU,price,currency,quantity,valid_until,market,shop_product_id\nDEMO,9.90,USD,8,2099-05-01T00:00:00Z,US,product-1",
            },
        )["records"][0]
        acct = make(
            "Account",
            {
                "name": "创作者账号",
                "provider": "tiktok",
                "platform_account_id": "creator-demo",
                "identity_namespace": "shop_creator",
                "identity_market": "US",
            },
        )
        pub = post(
            "/production/publications",
            {
                "project_id": pid,
                "artifact_id": rendered["id"],
                "account_id": acct["id"],
                "title": "测试创意",
            },
        )
        return pid, product, pub


def run(output, engine):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp, server(Path(tmp), output) as base, sync_playwright() as pw:
        pid, product, pub = seed(base, Path(tmp))
        browser = (
            pw.webkit.launch(executable_path=os.environ.get("AI_SHIP_WEBKIT"))
            if engine == "webkit"
            else pw.chromium.launch(
                executable_path=os.environ.get("AI_SHIP_CHROMIUM") or shutil.which("chromium")
            )
        )
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        page.set_default_timeout(12000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        d = page.locator("#dialog[open]")

        def open_hub():
            page.goto(base + "/#delivery")
            page.get_by_role("button", name="商品交接", exact=True).click()

        def close():
            d.get_by_role("button", name="关闭", exact=True).click()

        open_hub()
        d.get_by_role("button", name="准备待挂商品包").click()
        d.locator("[name=products]").select_option(product["id"])
        d.locator("[name=market]").fill("US")
        d.locator("[name=kind]").select_option("paid_partnership")
        d.locator("[name=disclosure_text]").fill("测试付费合作")
        d.get_by_role("button", name="生成并核验交接包").click()
        expect(d).to_contain_text("账号待定")
        with page.expect_download() as dl:
            d.get_by_role("link", name="下载商品交接包").click()
        zip_path = dl.value.path()
        checksum = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        with zipfile.ZipFile(zip_path) as z:
            assert json.loads(z.read("commercial-metadata.json"))["products"][0]["price"] == "9.90"
        d.get_by_role("button", name="登记收讫").click()
        d.locator("[name=recipient]").fill("验收人员")
        d.locator("[name=checksum]").fill(checksum)
        d.locator("[name=evidence]").fill("已对照接收文件摘要")
        d.get_by_role("button", name="登记收讫", exact=True).click()
        expect(d).to_contain_text("已登记收讫")
        close()
        open_hub()
        d.get_by_role("button", name="平台草稿人工挂商品").click()
        d.locator("[name=market]").fill("US")
        d.locator("[name=until]").fill("2099-01-01T00:00:00Z")
        d.locator("[name=evidence]").fill("已核对创作者及类目资格")
        d.get_by_role("button", name="保存待挂商品意图").click()
        expect(d).to_have_count(0)
        with httpx.Client(base_url=base + "/api/v1", trust_env=False) as client:
            state = client.get("/records/" + pub["id"]).json()["data"]
            receipt = client.post(
                "/production/publications/" + pub["id"] + "/receipt",
                json={
                    "expected_revision": state["version"],
                    "platform_post_id": "local-observed",
                    "post_url": "https://example.test/post/local-observed",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "evidence": "人工回填测试帖子",
                },
            )
            receipt.raise_for_status()
        page.reload()
        open_hub()
        d.get_by_role("button", name="核对最终帖子商品卡").click()
        d.locator("[name=ids]").fill("product-1")
        d.locator("[name=kind]").select_option("paid_partnership")
        d.locator("[name=observed_text]").fill("测试付费合作")
        d.locator("[name=evidence]").fill("逐项核对显示文案与商品卡")
        d.get_by_role("button", name="记录人工观测").click()
        expect(d).to_have_count(0)
        with httpx.Client(base_url=base + "/api/v1", trust_env=False) as c:
            post = c.get("/records/" + receipt.json()["data"]["post"]["id"]).json()["data"]
            assert post["data"]["commercial_observation"]["status"] == "matched_as_observed"
        assert not errors, errors
        browser.close()
        print(
            json.dumps(
                {
                    "engine": engine,
                    "checks": [
                        "prepare",
                        "download_members",
                        "receipt",
                        "bind_creator",
                        "observe_final_post",
                    ],
                    "page_errors": errors,
                }
            )
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    a = p.parse_args()
    run(a.output, a.engine)
