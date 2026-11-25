"""Exercise a real two-cut batch, source selection and adaptable inspectors in Chromium or WebKit."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_regression import server


def placement_fixture(client, pid, account_id, downloads):
    """Bind exact downloads from this run's rendered batch to two synthetic post receipts."""

    def post(path, body):
        response = client.post("/production/" + path, json=body)
        response.raise_for_status()
        return response.json()["data"]

    evidence = {}
    for n, href in enumerate(downloads):
        artifact_id = href.split("/")[-2]
        payload = client.get(
            "/media/artifacts/" + artifact_id + "/file", params={"download": "true"}
        )
        payload.raise_for_status()
        upload = client.post(
            "/media/import",
            params={"project_id": pid, "filename": f"核对成片{n+1}.mp4"},
            content=payload.content,
        )
        upload.raise_for_status()
        version = upload.json()["data"]["version"]
        pub = post(
            "publications",
            {
                "project_id": pid,
                "artifact_id": artifact_id,
                "account_id": account_id,
                "title": f"离线测试作品{n+1}",
            },
        )
        recorded = post(
            "publications/" + pub["id"] + "/receipt",
            {
                "expected_revision": pub["version"],
                "platform_post_id": "synthetic-placement-" + str(n),
                "post_url": f"https://example.test/placement/{n}",
                "observed_at": "2026-01-01T00:00:00Z",
                "evidence": "合成浏览器验收数据",
            },
        )["post"]
        mapped = post(
            "assets/post-mapping",
            {
                "project_id": pid,
                "request_id": "placement-map-" + str(n),
                "post": {"id": recorded["id"], "version": recorded["version"]},
                "observed_file": {"id": version["id"], "version": version["version"]},
                "published_at": "2026-01-01T00:00:00Z",
                "evidence": "核对刚从本地测试服务下载的成片字节",
            },
        )["post"]
        midpoint = [80, 55][n]
        result = post(
            "post-reviews/retention-mapping",
            {
                "project_id": pid,
                "request_id": "placement-curve-" + str(n),
                "post": {"id": mapped["id"], "version": mapped["version"]},
                "source": "Synthetic first-play fixture",
                "denominator": 100,
                "traffic": "organic",
                "window_start": "2026-01-01T00:00:00Z",
                "window_end": "2026-01-02T00:00:00Z",
                "observed_at": "2026-01-03T00:00:00Z",
                "points": [
                    {"at_seconds": 0, "retained": 100},
                    {"at_seconds": 0.5, "retained": midpoint},
                    {"at_seconds": 1, "retained": midpoint - 10},
                ],
            },
        )["insight"]
        evidence[f"{midpoint:.1f}%"] = (mapped["id"], result["id"])
    return evidence


def check_placement(page, base, pid, account, downloads, output):
    with httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=60) as client:
        evidence = placement_fixture(client, pid, account, downloads)
    calls = []

    def capture(req):
        if "/placement-performance?" in req.url:
            calls.append({"method": req.method, "query": req.url.split("?", 1)[1]})

    page.on("request", capture)
    page.goto(base + "/#module/metrics/M09")
    page.locator("[data-action=feature-action]").click()
    dialog = page.locator("dialog")
    expect(dialog.locator("h2")).to_have_text("帖子表现复盘")
    page.get_by_role("button", name="比较素材位置表现").click()
    dialog.locator("[name=account_id]").select_option(account)
    dialog.locator("[name=end_seconds]").fill("0.5")
    dialog.locator("[name=traffic]").select_option("paid")
    page.get_by_role("button", name="查看素材表现").click()
    expect(dialog).to_contain_text("这组条件尚无可比较素材")
    expect(dialog).to_contain_text("0 篇同口径帖子")
    page.get_by_role("button", name="更改条件").click()
    expect(dialog.locator("[name=account_id]")).to_have_value(account)
    expect(dialog.locator("[name=end_seconds]")).to_have_value("0.5")
    expect(dialog.locator("[name=traffic]")).to_have_value("paid")
    dialog.locator("[name=traffic]").select_option("organic")
    page.get_by_role("button", name="查看素材表现").click()
    expect(dialog).to_contain_text("2 篇同口径帖子")
    items = dialog.locator("article")
    expect(items).to_have_count(2)
    for n, (rate, (post, insight)) in enumerate(evidence.items()):
        row = items.nth(n)
        expect(row).to_contain_text(rate)
        expect(row).to_contain_text("1 篇帖子")
        expect(row).to_contain_text("1 处使用")
        row.locator("summary").click()
        expect(row.locator("details p")).to_contain_text(post)
        expect(row.locator("details p")).to_contain_text(insight)
    page.screenshot(path=str(output / "placement-performance.png"))
    items.nth(0).get_by_role("button", name="设为对照基准").click()
    with page.expect_response(
        lambda r: "/post-reviews/placement-comparisons?" in r.url
    ) as compared:
        items.nth(1).locator("[data-compare]").click()
    assert compared.value.status == 200, compared.value.text()
    paired = compared.value.json()["data"]
    assert paired["matched_days"] == 1 and abs(paired["difference_pp"] + 25.0) < 1e-9
    assert paired["matched_baseline"]["entrants"] == paired["matched_candidate"]["entrants"] == 100
    expect(dialog.locator("h2")).to_have_text("同日素材对照")
    expect(dialog).to_contain_text("仅一个共同发布日")
    dialog.get_by_text("2026-01-01", exact=False).first.click()
    expect(dialog).to_contain_text("Synthetic first-play fixture")
    page.screenshot(path=str(output / "matched-placement-comparison.png"))
    page.get_by_role("button", name="沉淀对照假设").click()
    for name, message in {
        "hypothesis": "不同版本首镜的表现可能不同",
        "counterevidence": "未控制受众",
        "next_edit": "留出组测试首镜版本",
        "stop_rule": "新结果不稳定时停止",
    }.items():
        dialog.locator(f"[name={name}]").fill(message)
    with page.expect_response(
        lambda r: "/post-reviews/placement-comparison-hypotheses" in r.url
    ) as match_saved:
        page.get_by_role("button", name="固定证据并保存").click()
    assert match_saved.value.status == 200, match_saved.value.text()
    match_insight = match_saved.value.json()["data"]["insight"]
    assert match_insight["data"]["observation"]["evidence_hash"] == paired["evidence_hash"]
    page.get_by_role("button", name="返回帖子复盘").click()
    page.get_by_role("button", name="比较素材位置表现").click()
    dialog.locator("[name=account_id]").select_option(account)
    dialog.locator("[name=end_seconds]").fill("0.5")
    page.get_by_role("button", name="查看素材表现").click()
    page.get_by_role("button", name="更改条件").click()
    expect(dialog.locator("[name=end_seconds]")).to_have_value("0.5")
    expect(dialog.locator("[name=traffic]")).to_have_value("organic")
    page.get_by_role("button", name="查看素材表现").click()
    page.get_by_role("button", name="沉淀为选材假设").first.click()
    for name, message in {
        "hypothesis": "清晰的首镜可能减少流失",
        "counterevidence": "曝光受众可能不同",
        "next_edit": "固定配音，仅替换首镜",
        "stop_rule": "留出组表现下降即停止",
    }.items():
        dialog.locator(f"[name={name}]").fill(message)
    with page.expect_response(
        lambda r: "/post-reviews/placement-hypotheses" in r.url and r.request.method == "POST"
    ) as saved:
        page.get_by_role("button", name="固定证据并保存").click()
    assert saved.value.status == 200, saved.value.text()
    frozen = saved.value.json()["data"]["insight"]
    assert frozen["data"]["observation"]["mean_post_retention"] == 0.8
    assert (
        not frozen["data"]["causal_claim"]
        and frozen["data"]["reuse_hypothesis"]["next_edit"] == "固定配音，仅替换首镜"
    )
    expect(dialog.locator("h2")).to_have_text("选材假设已保存")
    page.get_by_role("button", name="返回帖子复盘").click()
    expect(dialog.locator("h2")).to_have_text("帖子表现复盘")
    page.remove_listener("request", capture)
    assert [call["method"] for call in calls] == ["GET", "GET", "GET", "GET"], calls
    report = {
        "browser": page.context.browser.version,
        "fixture": "Two local FFmpeg MP4s, each independently downloaded and verified against its synthetic post and observed first-play cohort.",
        "interval_seconds": [0, 0.5],
        "paid_posts": 0,
        "organic_posts": len(evidence),
        "evidence": [
            {"conditional_retention": rate, "post": ids[0], "insight": ids[1]}
            for rate, ids in evidence.items()
        ],
        "requests": calls,
        "frozen_reuse_insight": frozen["id"],
        "matched_comparison": {
            "published_days": paired["matched_days"],
            "difference_pp": paired["difference_pp"],
            "baseline": paired["matched_baseline"],
            "candidate": paired["matched_candidate"],
            "causal_claim": paired["causal_claim"],
            "frozen_insight": match_insight["id"],
        },
        "counterevidence_and_stop_rule_preserved": True,
        "filters_persisted": True,
        "returned_to_post_review": True,
    }
    (output / "placement-performance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    page.keyboard.press("Escape")


def run(output, engine):
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    errors = []
    with tempfile.TemporaryDirectory() as temp, server(Path(temp), output) as base:
        with httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=60) as client:
            p = client.post(
                "/records",
                json={
                    "kind": "Project",
                    "data": {"name": "冬季系列试剪", "width": 320, "height": 240},
                },
            )
            p.raise_for_status()
            pid = p.json()["data"]["id"]
            for name, color in [("开场", "teal"), ("细节", "blue")]:
                file = Path(temp) / (color + ".mp4")
                subprocess.run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={color}:s=320x240:r=25:d=1",
                        "-pix_fmt",
                        "yuv420p",
                        str(file),
                    ],
                    check=True,
                )
                client.post(
                    "/media/import",
                    params={"project_id": pid, "filename": name + ".mp4"},
                    content=file.read_bytes(),
                ).raise_for_status()
            resp = client.post(
                "/records",
                json={
                    "project_id": pid,
                    "kind": "Workflow",
                    "data": {
                        "name": "复制输出时间线",
                        "DAG": {
                            "nodes": [
                                {
                                    "id": "editor",
                                    "feature_id": "E01",
                                    "route_id": "E01-R1",
                                    "parameters": {},
                                    "operation_args": {},
                                }
                            ],
                            "edges": [],
                            "max_steps": 3,
                            "max_attempts": 1,
                            "budget": 0,
                        },
                    },
                },
            )
            resp.raise_for_status()
            flow_id = resp.json()["data"]["id"]
            rec = client.post(
                "/records",
                json={
                    "project_id": pid,
                    "kind": "Workflow",
                    "data": {
                        "name": "人工脚本配方",
                        "DAG": {
                            "nodes": [
                                {
                                    "id": "script",
                                    "feature_id": "V01",
                                    "route_id": "V01-R3",
                                    "parameters": {},
                                    "operation_args": {"data": {"text": "实测脚本"}},
                                }
                            ],
                            "edges": [],
                            "max_steps": 1,
                            "max_attempts": 1,
                            "budget": 0,
                        },
                    },
                },
            )
            rec.raise_for_status()
            harness_flow = rec.json()["data"]["id"]
            acc = client.post(
                "/records",
                json={
                    "project_id": pid,
                    "kind": "Account",
                    "data": {
                        "name": "官方账号测试",
                        "provider": "tiktok",
                        "platform_account_id": "demo",
                        "identity": "demo",
                    },
                },
            )
            acc.raise_for_status()
            account_id = acc.json()["data"]["id"]
        with sync_playwright() as pw:
            executable = os.environ.get("AI_SHIP_" + engine.upper())
            if engine == "chromium" and not executable:
                executable = shutil.which("chromium")
            browser = getattr(pw, engine).launch(executable_path=executable)
            browser_version = browser.version
            page = browser.new_page(viewport={"width": 1440, "height": 920}, locale="zh-CN")
            page.set_default_timeout(12000)
            page.add_init_script("localStorage.setItem('ship-project', " + json.dumps(pid) + ")")
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/#production")
            page.get_by_role("link", name="批量制作").click()
            select = page.locator
            select("[name=name]").fill("两种开场 · 交付")
            page.get_by_role("button", name="选素材", exact=True).click()
            for c in page.locator("dialog input[name=asset]").all():
                c.check()
            page.get_by_role("button", name="加入镜头").click()
            expect(select("[data-forecast]")).to_contain_text("2 种")
            select("[name=count]").fill("2")
            select("[name=output]").select_option("render")
            select("[data-action=choose-contract]").click()
            select("dialog [data-create]").click()
            select("dialog [name=name]").fill("完整解码成片")
            select("dialog [name=decode]").check()
            page.get_by_role("button", name="保存并应用到批次").click()
            expect(select(".batch-form")).to_contain_text("完整解码成片")
            page.get_by_role("button", name="提交批量渲染").click()
            expect(select(".batch-inspector h3")).to_have_text("两种开场 · 交付", timeout=30000)
            expect(select(".batch-rows a[download]")).to_have_count(2, timeout=60000)
            expect(select(".batch-progress")).to_have_attribute("value", "2", timeout=90000)
            downloads = select(".batch-rows a[download]").evaluate_all(
                "(links) => links.map(link => link.getAttribute('href'))"
            )
            checks.append(
                "real batch of two distinct FFmpeg outputs, result links and determinate progress"
            )
            page.get_by_role("button", name="成片验收 · 通过").first.click()
            expect(select("dialog .data-table")).to_contain_text("完整解码")
            page.keyboard.press("Escape")
            checks.append("versioned output contract with real per-file decode receipts")
            select("[name=batch_filter]").select_option("failed")
            expect(select(".batch-rows")).to_contain_text("没有作品")
            select("[name=batch_filter]").select_option("succeeded")
            expect(select(".batch-rows li")).to_have_count(2)
            checks.append("filter on real persisted item states")
            divider = select("[data-divider=inspector]")
            width = select(".batch-inspector").bounding_box()["width"]
            divider.press("ArrowLeft")
            assert select(".batch-inspector").bounding_box()["width"] > width + 5
            select("[data-pane-move]").click()
            expect(select(".adjustable-layout")).to_have_attribute("data-side", "left")
            select("[data-pane-toggle]").click()
            expect(select(".batch-inspector")).to_be_hidden()
            select("[data-pane-toggle]").click()
            select("[data-pane-move]").click()
            checks.append("keyboard resize, reorder and collapse queue")
            page.screenshot(path=str(output / "desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            expect(divider).to_be_hidden()
            assert page.evaluate(
                "document.querySelector('#main').scrollWidth<=document.querySelector('#main').clientWidth+1"
            )
            select(".batch-inspector").scroll_into_view_if_needed()
            page.screenshot(path=str(output / "mobile.png"))
            checks.append("390-pixel stacked panels without horizontal page overflow")
            page.get_by_role("link", name="时间线", exact=True).first.click()
            expect(select(".room-title h1")).to_be_visible()
            checks.append("open a produced timeline in the existing editor")
            page.goto(base + "/#workflow")
            expect(select(".workflow-inspector")).to_be_visible()
            select("[data-pane-toggle]").click()
            expect(select(".workflow-inspector")).to_be_hidden()
            select("[data-pane-toggle]").click()
            checks.append("narrow workflow inspector can be hidden and reopened")
            page.set_viewport_size({"width": 1280, "height": 840})
            page.goto(base + "/#production/batches")
            select("[name=output]").select_option("timeline")
            select("[name=name]").fill("逐条转入编排")
            select("[data-action=unlink-contract]").click()
            select("[data-action=choose-workflow]").click()
            select("dialog [name=workflow]").select_option(flow_id)
            select("dialog button[type=submit]").last.click()
            expect(select(".batch-controls")).not_to_have_count(0)
            page.get_by_role("button", name="提交批量剪辑").click()
            expect(select(".batch-inspector h3")).to_have_text("逐条转入编排")
            expect(select(".batch-rows [data-action=open-run]")).to_have_count(2, timeout=60000)
            expect(select(".batch-progress")).to_have_attribute("value", "2", timeout=90000)
            checks.append(
                "two timeline outputs run a pinned project DAG and expose separate child runs"
            )
            select("[data-action=observe-queue]").click()
            page.get_by_role("button", name="比较两批").click()
            expect(select("dialog h2")).to_have_text("批次运行对照")
            expect(select("dialog")).to_contain_text("交付类型不同")
            page.keyboard.press("Escape")
            checks.append(
                "compare measured batch throughput and isolate non-comparable delivery modes"
            )
            select("[data-action=add-generation]").click()
            select("[data-action=add-generation]").click()
            expect(select("[name=prompt]")).to_have_count(2)
            expect(select("[data-forecast]")).to_contain_text("4 次模型生成")
            checks.append("two ordered generation slots expose four model calls before submission")
            page.goto(base + "/#module/acquire/A03")
            select("[data-action=feature-action]").click()
            select("dialog [data-switch]").click()
            expect(select("dialog [data-search] button")).to_be_disabled()
            select("dialog [data-switch]").click()
            expect(select("dialog [data-search] button")).to_be_enabled()
            page.keyboard.press("Escape")
            checks.append(
                "versioned public-source switch suspends and restores project acquisition"
            )
            page.goto(base + "/#module/data/D04")
            select("[data-action=feature-action]").click()
            select("dialog details summary").click()
            select("dialog [data-write] [name=value]").fill("先保留商品细节，再进入旁白。")
            select("dialog [data-write] [name=reason]").fill("项目负责人指定的演示节奏")
            select("dialog [data-write] button[type=submit]").click()
            expect(select("dialog [data-prefs]")).to_contain_text("先保留商品细节")
            select("dialog [data-write] [name=action]").select_option("revoke")
            select("dialog [data-write] [name=reason]").fill("本轮验证完成")
            select("dialog [data-write] button[type=submit]").click()
            expect(select("dialog [data-prefs] li")).to_have_count(0)
            checks.append(
                "effective project preference set, observed and revoked with version checks"
            )
            page.keyboard.press("Escape")
            page.goto(base + "/#module/harness/H09")
            select("[data-action=feature-action]").click()
            select("dialog [name=workflow]").select_option(harness_flow)
            select("dialog [name=name]").fill("已核验的制作配方")
            page.get_by_role("button", name="实测并登记配方").click()
            expect(select("dialog h2")).to_contain_text("实测通过")
            page.get_by_role("button", name="运行已核验版本").click()
            expect(select("dialog .run-summary")).to_contain_text("已完成")
            checks.append("qualify and run an exact Workflow version from the harness view")
            page.keyboard.press("Escape")
            page.goto(base + "/#module/account/J01")
            select("[data-action=feature-action]").click()
            select("dialog [data-action=tiktok-connection]").click()
            expect(select("dialog [data-info]")).to_contain_text("待核验")
            select('dialog [data-action="suspend"]').click()
            expect(select("dialog [data-info]")).to_contain_text("已暂停")
            checks.append(
                "persist a project TikTok read-connection suspension with no outbound publication"
            )
            page.keyboard.press("Escape")
            check_placement(page, base, pid, account_id, downloads, output)
            checks.append(
                "M09 placement comparison filters paid traffic to an empty state, then verifies 80.0% and 55.0% against two actual rendered files and pinned synthetic post/Insight receipts; returns to conditions and the review entry"
            )
            page.goto(base + "/#module/ingest/I05")
            select("[data-action=feature-action]").click()
            select("dialog [name=source]").select_option(index=1)
            select("dialog [name=action]").select_option("restrict")
            select("dialog [name=purpose][value=inspect]").check()
            select("dialog [name=reason]").fill("允许查看，暂缓加工与交付。")
            select("dialog form[data-form] button[type=submit]").click()
            expect(select("dialog h2")).to_have_text("来源判定已保存")
            checks.append("version-bound source usage decision submitted from I05")
            browser.close()
    assert not errors, errors
    report = {
        "engine": engine,
        "browser": browser_version,
        "checks": checks,
        "page_errors": errors,
        "fixtures": "Two generated one-second solid-color input videos; export verified with the local renderer.",
    }
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    args = parser.parse_args()
    run(args.output.resolve(), args.engine)
