"""Exercise the scoring-policy release controls against a real isolated server."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright
from browser_regression import server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory() as tmp,
        server(Path(tmp), out) as base,
        sync_playwright() as pw,
        httpx.Client(base_url=base + "/api/v1", trust_env=False) as api,
    ):

        def post(path, **data):
            res = api.post(path, json=data)
            res.raise_for_status()
            return res.json()["data"]

        project = post("/projects", name="素材策略测试")
        pid = project["id"]
        ids, assets = [], []
        for i in range(4):
            asset = post(
                "/records",
                kind="AssetVersion",
                project_id=pid,
                data={"name": f"样片 {i}"},
                reason="隔离测试",
            )
            preferred = not i % 2
            dims = dict.fromkeys(
                (
                    "quality",
                    "relevance",
                    "fatigue",
                    "confidence",
                    "tag_completeness",
                    "performance",
                ),
                0.2 if preferred else 0.8,
            )
            dims["quality"] = 0.9 if preferred else 0.3
            score = post(
                "/production/assets/score",
                project_id=pid,
                asset_version_id=asset["id"],
                dimensions=dims,
            )
            ids.append(score["id"])
            assets.append(asset["id"])
        executable = os.getenv("AI_SHIP_" + args.engine.upper())
        if args.engine == "chromium" and not executable:
            executable = shutil.which("chromium-headless-shell") or shutil.which("chromium")
        options = {"chromium_sandbox": True} if args.engine == "chromium" else {}
        browser = getattr(pw, args.engine).launch(executable_path=executable, **options)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base)
            dlg = page.locator("dialog[open]")

            def feature(code):
                page.goto(base + "/#module/learn/" + code)
                page.locator(".feature-title [data-action=feature-action]").click()
                expect(dlg).to_be_visible()

            def submit():
                dlg.locator("form[data-form] button[type=submit]").click()

            feature("X02")
            dlg.locator("[name=name]").fill("画质优先")
            dlg.locator("[name=hypothesis]").fill("测试清晰度优先策略")
            for key in ("relevance", "fatigue", "confidence", "tag_completeness", "performance"):
                dlg.locator(f"[name={key}]").fill("0")
            submit()
            expect(dlg).to_have_count(0)
            feature("X03")
            dlg.locator("[name=candidate]").select_option(index=1)
            for i in range(2):
                row = dlg.locator("[data-case]").nth(i)
                row.locator("[name=preferred]").select_option(ids[i * 2])
                row.locator("[name=comparison]").select_option(ids[i * 2 + 1])
                row.locator("[name=reason]").fill("同对中经过人工核定的清晰度优先")
            submit()
            expect(dlg.get_by_role("status")).to_contain_text("通过")
            dlg.get_by_role("button", name="关闭", exact=True).click()
            feature("X06")
            dlg.locator("[data-approve]").click()
            dlg.locator("[name=exp]").select_option(index=1)
            dlg.locator("[name=reason]").fill("仅限当前项目素材评分")
            submit()
            expect(dlg).to_have_count(0)
            feature("X06")
            dlg.locator("[data-trial-start]").click()
            dlg.locator("[name=approval]").select_option(index=1)
            dlg.locator("[name=percent]").fill("50")
            dlg.locator("[name=stop_condition]").fill("抽检排序失真时停止")
            submit()
            expect(dlg.locator("[data-trial-summary]")).to_contain_text("50% 候选分流 · 试用中")
            arms = {"baseline": [set(), 0, 0], "candidate": [set(), 0, 0]}

            expect(dlg.locator("[data-activate]")).to_be_disabled()

            def observe(aid, dimensions):
                observed = post(
                    "/production/assets/score",
                    project_id=pid,
                    asset_version_id=aid,
                    dimensions=dimensions,
                )
                arm = observed["data"]["trial_assignment"]["arm"]
                arms[arm][0].add(aid)
                arms[arm][1] += 1
                arms[arm][2] += int(observed["data"]["observed_score"] is not None)

            for aid in assets:
                observe(aid, {"quality": 0.6})
            observe(assets[0], {})
            # At 50% assignment a small cohort can legitimately land in one arm.
            # Enroll fresh independent units until each arm has an observed score.
            for n in range(128):
                if all(stats[2] for stats in arms.values()):
                    break
                extra = post(
                    "/records",
                    kind="AssetVersion",
                    project_id=pid,
                    data={"name": f"独立试用素材 {n}"},
                    reason="试用分组验收",
                )
                observe(extra["id"], {"quality": 0.8})
            assert all(stats[2] for stats in arms.values())
            page.set_viewport_size({"width": 390, "height": 844})
            dlg.locator("[data-trial-refresh]").click()
            for arm, arm_label in (("baseline", "基线"), ("candidate", "候选")):
                row = dlg.locator("tbody tr").filter(has_text=arm_label)
                expect(row.locator("td").nth(0)).to_have_text(str(len(arms[arm][0])))
                expect(row.locator("td").nth(1)).to_have_text(str(len(arms[arm][0])))
                expect(row.locator("td").nth(2)).to_have_text(str(arms[arm][1]))
                expect(row.locator("td").nth(3)).to_have_text(str(arms[arm][2]))
            assert dlg.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
            page.screenshot(path=str(out / "limited-trial.png"))
            dlg.locator("[data-trial-stop]").click()
            dlg.locator("[name=decision]").select_option("recommend_release")
            dlg.locator("[name=reason]").fill("小范围核对结束")
            submit()
            expect(dlg.locator("[data-trial-summary]")).to_contain_text("已停止")
            expect(dlg.locator("[data-trial-summary]")).to_contain_text("小范围核对结束")
            expect(dlg.locator("[data-trial-summary]")).to_contain_text("评审结论 · 建议全量生效")
            assert (
                api.get("/production/score-policies/" + pid).json()["data"]["pointer"]["version"]
                == 1
            )
            page.set_viewport_size({"width": 1440, "height": 960})
            dlg.locator("[data-activate]").click()
            dlg.locator("[name=approval]").select_option(index=1)
            submit()
            expect(dlg).to_have_count(0)
            live = api.get("/production/score-policies/" + pid).json()["data"]["pointer"]
            assert (
                live["version"] == 2
                and live["data"]["active"]["id"] != live["data"]["previous"]["id"]
            )
            feature("X07")
            dlg.locator("[name=reason]").fill("恢复首轮素材排序基线")
            submit()
            expect(dlg.get_by_role("status")).to_contain_text("已恢复")
            restored = api.get("/production/score-policies/" + pid).json()["data"]["pointer"]
            assert (
                restored["version"] == 3 and restored["data"]["active"] == live["data"]["previous"]
            )
            dlg.get_by_role("button", name="关闭", exact=True).click()
            page.goto(base + "/#module/assetops/U03")
            page.locator(".feature-title [data-action=feature-action]").click()
            dlg.locator("[name=asset_version_id]").select_option(assets[0])
            dlg.locator("[name=quality]").fill("0.8")
            dlg.locator("[name=weight_mode]").select_option("manual")
            for weight in dlg.locator('input[name^="weight_"]').all():
                weight.fill("0")
            observed_requests = []

            def intercept(route):
                key = route.request.post_data_json["request_id"]
                reply = route.fetch()
                content = reply.json()
                observed_requests.append((key, reply.status, content.get("data", {}).get("id")))
                if len(observed_requests) == 2:
                    # The isolated server committed the score but the caller lost the reply.
                    route.abort("failed")
                else:
                    route.fulfill(response=reply)

            page.route("**/api/v1/production/assets/score", intercept)
            submit()
            expect(dlg.locator("form[data-form] button[type=submit]")).to_be_enabled()
            dlg.locator("[name=weight_quality]").fill("1")
            submit()
            expect(dlg.locator("form[data-form] button[type=submit]")).to_be_enabled()
            submit()
            expect(dlg).to_have_count(0)
            invalid, committed, replayed = observed_requests
            assert invalid[1] >= 400 and invalid[0] != committed[0]
            assert committed == replayed and committed[1] == 200 and committed[2]
            assert not errors, errors
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "features": ["X02", "X03", "X06", "X07"],
                        "pointer_versions": [1, 2, 3],
                        "browser": browser.version,
                        "trial": {
                            "percent": 50,
                            "units": sum(len(a[0]) for a in arms.values()),
                            "scores": sum(a[1] for a in arms.values()),
                            "measured": sum(a[2] for a in arms.values()),
                            "stopped": True,
                            "review": "recommend_release",
                        },
                        "retried_score_reused_record": True,
                        "page_errors": errors,
                    }
                )
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
