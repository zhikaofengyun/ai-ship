"""Verify the five current release rounds and derive the acceptance report."""

from datetime import datetime
import hashlib
import json
from pathlib import Path

from run_regression import fingerprint

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "ai-production"
SPECIAL = {
    "Chromium 制作台": "director-chromium/report.json",
    "WebKit 制作台": "director-webkit/report.json",
    "Chromium 自由布局": "layout-chromium/report.json",
    "WebKit 自由布局": "layout-webkit/report.json",
    "Chromium 转场": "joins-chromium/report.json",
    "WebKit 转场": "joins-webkit/report.json",
    "Chromium 首镜实帧": "opening-chromium/report.json",
    "WebKit 首镜实帧": "opening-webkit/report.json",
    "本地语言模型实推": "language-model/report.json",
    "真实语言模型首镜核验": "opening-model/report.json",
    "真实三模型复核与裁决": "model-team/report.json",
    "本地扩散模型实推": "diffusion-model/report.json",
    "数据控件与旧工作流": "typed-controls/report.json",
    "模块表单": "module-forms/report.json",
    "分镜交接与创意选优": "selection/report.json",
    "单屏剪辑": "studio/report.json",
    "工作区": "workspace/workspace-actions.json",
    "WebKit 剪辑": "webkit/report.json",
}


def load(path):
    return json.loads(path.read_text())


def main():
    current = fingerprint()["sha256"]
    complete_cases = previous_end = gate_names = None
    rows = []
    for n in range(1, 6):
        folder = EVIDENCE / f"round-{n}"
        data = load(folder / "round-report.json")
        u = load(folder / "unit-report.json")
        b = load(folder / "browser/browser-report.json")
        assert (
            data["round"] == n
            and data["passed"]
            and data["unchanged_during_round"]
            and data["source_sha256"] == current
        )
        start = datetime.fromisoformat(data["started_at"])
        end = datetime.fromisoformat(data["finished_at"])
        assert end > start and (previous_end is None or start >= previous_end)
        previous_end = end
        assert (
            u["passed"]
            and u["tests_run"] == len(u["cases"])
            and not (u["errors"] or u["failures"] or u["skipped"])
        )
        cases = {c["id"] for c in u["cases"] if c["status"] == "passed"}
        assert len(cases) == u["tests_run"] and (complete_cases is None or cases == complete_cases)
        complete_cases = cases
        gates = [c["name"] for c in data["checks"] if c["passed"]]
        assert len(gates) == len(data["checks"]) and (gate_names is None or gates == gate_names)
        gate_names = gates
        assert (
            b["passed"]
            and not b["page_errors"]
            and not b["console_errors"]
            and not b["unexpected_failed_requests"]
        )
        assert (
            len(set(b["modules"])) == 23
            and len({f["id"] for f in b["features"]}) == 178
            and len(set(b["routes"])) == 534
        )
        assert sum(f["control_count"] for f in b["features"]) == 564
        files = dict(SPECIAL)
        if (folder / "music/report.json").exists():
            files["人工音乐打点"] = "music/report.json"
        extra = {}
        for name, path in files.items():
            obj = load(folder / path)
            assert obj["passed"] and not obj.get("errors") and not obj.get("page_errors")
            extra[name] = len(obj["checks"])
        for path, digest in data["evidence_hashes"].items():
            assert hashlib.sha256((folder / path).read_bytes()).hexdigest() == digest, path
        rows.append(
            dict(
                round=n,
                seconds=round((end - start).total_seconds(), 1),
                gates=len(gates),
                tests=u["tests_run"],
                browser_checks=len(b["checks"]),
                suite_checks=extra,
                viewport=b["viewport_variant"],
                pictures=len(list(folder.rglob("*.png"))),
                hashes=len(data["evidence_hashes"]),
                exclusions=data["not_verified"],
                path=str(folder.relative_to(ROOT)),
            )
        )
    summary = {
        "passed": True,
        "source_sha256": current,
        "gates": gate_names,
        "rounds": rows,
        "catalog": {"modules": 23, "features": 178, "routes": 534, "controls": 742},
        "feature_acceptance": load(ROOT / "contracts/implementation.json")["counts"],
    }
    (EVIDENCE / "verification-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# AI 制作台、可调工作区与生成链路 · 五轮验收",
        "",
        "每轮分别启动真实 FastAPI、SQLite、Chromium、WebKit、llama.cpp 与 ComfyUI，重复运行同一组代码和测试。时间线操作从界面提交，MP4 和工程文件下载后另行读取。源 HTML 的设计要求和当前可执行能力在 [逐项核对](feature-workflows.md) 中并列。",
        "",
        "| 轮次 | 桌面 / 手机 | 全部测试用例 | 完整检查组 | 主工作区检查 | 专项检查 | PNG 证据 | 耗时 | 原始证据 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        vp = r["viewport"]
        view = " / ".join("×".join(map(str, vp[k])) for k in ("desktop", "mobile"))
        lines.append(
            f'| {r["round"]} | {view} | {r["tests"]} | {r["gates"]} | {r["browser_checks"]} | {sum(r["suite_checks"].values())} | {r["pictures"]} | {r["seconds"]} 秒 | [执行记录](../{r["path"]}/round-report.json) |'
        )
    lines += ["", "## 每轮覆盖与未验项目", ""]
    for r in rows:
        prefix = "../" + r["path"]
        files = dict(SPECIAL)
        if "人工音乐打点" in r["suite_checks"]:
            files["人工音乐打点"] = "music/report.json"
        links = "、".join(
            f"[{k} {r['suite_checks'][k]} 组]({prefix}/{v})" for k, v in files.items()
        )
        lines += [
            f"### 第 {r['round']} 轮",
            "",
            f"用独立数据库和测试顺序 {r['round']} 运行了全部 {r['tests']} 个后端用例。浏览器逐一访问 23 个模块、178 项功能和 534 条路线，检查了 564 个参数控件及 178 个路线选择器。共核验 {r['hashes']} 个证据文件的内容校验值。",
            "",
            f"逐项证据见[后端用例]({prefix}/unit-report.json)、[主界面与全目录]({prefix}/browser/browser-report.json)、{links}。",
            "",
            "本轮未检验" + "、".join(r["exclusions"]) + "。逐项功能状态以源需求核对表为准。",
            "",
        ]
    (ROOT / "docs/ai-production-regression.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps([(r["round"], r["tests"], r["gates"], r["pictures"], r["hashes"]) for r in rows])
    )


if __name__ == "__main__":
    main()
