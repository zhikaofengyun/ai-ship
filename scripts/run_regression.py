"""One full local-scope regression round, with immutable evidence per invocation.

Run five separate rounds for release verification. Each round includes ALL tests,
source/contracts, JavaScript, OpenAPI drift and exhaustive feature/route browser
traversal plus real media/workflow operations. No external completion is implied.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def fingerprint():
    paths = []
    for folder in ("app", "web", "contracts", "scripts", "tests", ".github"):
        paths += [
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc")
        ]
    paths += [
        ROOT / p
        for p in (
            "requirements.txt",
            "requirements-dev.txt",
            "constraints.txt",
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "Dockerfile",
            "compose.yaml",
        )
    ]
    values = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)
    }
    combined = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return {"sha256": combined, "files": values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", required=True, type=int, choices=range(1, 6))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--webkit",
        action="store_true",
        help="Also run the complete editing session in Playwright WebKit.",
    )
    parser.add_argument(
        "--live-ai",
        action="store_true",
        help="Also run installed language and diffusion model weights locally.",
    )
    args = parser.parse_args()
    output = (args.output or ROOT / "evidence" / "ai-production" / f"round-{args.round}").resolve()
    if output.exists():
        parser.error("证据目录已存在。保留旧结果，使用新的 --output 目录。")
    output.mkdir(parents=True)
    initial = fingerprint()
    (output / "source-fingerprint.json").write_text(json.dumps(initial, indent=2) + "\n")
    report = {
        "round": args.round,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": initial["sha256"],
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in (
                    "fastapi",
                    "uvicorn",
                    "pydantic",
                    "httpx",
                    "jsonschema",
                    "black",
                    "opentimelineio",
                    "playwright",
                )
            },
            "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
            "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        },
        "checks": [],
        "scope": "完整已实现本地功能、所有测试、全部源目录合同与UI路线遍历；非178功能全部业务验收。",
        "not_verified": [
            "付费云端供应商、真实平台OAuth/自动发布/回执/指标",
            "尚未实现的专用路线与外部适配器",
            "macOS/iOS Safari、Firefox、物理 iPhone 与辅助技术组合",
            "原生Liquid Glass逐像素一致",
            "分布式多主机与生产负载",
            "全部路线语义门槛、所有控件组合、专业剪辑软件和真实平台往返",
        ],
    }
    commands = [
        ("source-contract-integrity", [sys.executable, "scripts/check_contracts.py"], 90),
        (
            "all-backend-media-cross-domain-tests",
            [
                sys.executable,
                "scripts/run_tests.py",
                "--report",
                str(output / "unit-report.json"),
                "--order-seed",
                str(args.round),
            ],
            600,
        ),
        (
            "all-original-feature-workflow-crosswalk",
            [sys.executable, "scripts/feature_workflow_audit.py", "--check"],
            90,
        ),
        (
            "new-module-production-forms",
            [
                sys.executable,
                "scripts/browser_production_actions.py",
                "--output",
                str(output / "module-forms"),
            ],
            300,
        ),
        (
            "brief-shot-selection-and-editor-handoff",
            [sys.executable, "scripts/browser_selection.py", "--output", str(output / "selection")],
            300,
        ),
        (
            "single-screen-edit-session",
            [
                sys.executable,
                "scripts/browser_edit_session.py",
                "--round",
                str(args.round),
                "--output",
                str(output / "studio"),
            ],
            480,
        ),
        (
            "workspace-route-execution-and-revisions",
            [
                sys.executable,
                "scripts/browser_workspace_actions.py",
                "--output",
                str(output / "workspace"),
            ],
            300,
        ),
        ("all-browser-module-syntax", ["node", "scripts/check-web.mjs"], 90),
        (
            "python-formatting",
            [sys.executable, "-m", "black", "--check", "app", "tests", "scripts"],
            90,
        ),
        ("browser-formatting", ["npm", "run", "format:check"], 90),
        ("openapi-snapshot-drift", [sys.executable, "scripts/export_contracts.py", "--check"], 90),
        (
            "typed-controls-and-imported-dom-boundaries",
            [
                sys.executable,
                "scripts/browser_typed_controls.py",
                "--output",
                str(output / "typed-controls"),
            ],
            600,
        ),
        (
            "exhaustive-browser-and-real-media",
            [
                sys.executable,
                "scripts/browser_regression.py",
                "--round",
                str(args.round),
                "--output",
                str(output / "browser"),
            ],
            900,
        ),
    ]
    for name, script in [
        ("scoring-policy-release", "browser_score_policies.py"),
        ("comment-feedback", "browser_comment_feedback.py"),
    ]:
        commands.append(
            (name, [sys.executable, "scripts/" + script, "--output", str(output / name)], 300)
        )
    if args.webkit:
        commands.append(
            (
                "webkit-creative-and-source-manifest",
                [
                    sys.executable,
                    "scripts/browser_creative.py",
                    "--output",
                    str(output / "creative"),
                ],
                300,
            )
        )
        commands.append(
            (
                "webkit-single-screen-edit-session",
                [
                    sys.executable,
                    "scripts/browser_edit_session.py",
                    "--engine",
                    "webkit",
                    "--round",
                    str(args.round),
                    "--output",
                    str(output / "webkit"),
                ],
                480,
            )
        )
    else:
        report["not_verified"].append("WebKit 剪辑浏览器回归")
    for engine in (["chromium", "webkit"] if args.webkit else ["chromium"]):
        for prefix, script, folder in (
            ("ai-production", "browser_ai_production.py", "director"),
            ("workspace-layout", "browser_workspace_layout.py", "layout"),
            ("visual-transitions", "browser_clip_transitions.py", "joins"),
            ("opening-text", "browser_opening_text.py", "opening"),
            ("storyboard-variants", "browser_storyboard_variants.py", "variants"),
            ("voice-and-narration", "browser_voice_studio.py", "voice"),
        ):
            commands.append(
                (
                    engine + "-" + prefix,
                    [
                        sys.executable,
                        "scripts/" + script,
                        "--engine",
                        engine,
                        "--round",
                        str(args.round),
                        "--output",
                        str(output / (folder + "-" + engine)),
                    ],
                    480,
                )
            )
        for suffix, script in [
            ("source-index-and-soundtrack", "browser_sound_index.py"),
            ("account-quota", "browser_account_quota.py"),
            ("render-recovery", "browser_render_recovery.py"),
            ("commercial-handoff", "browser_commercial_handoff.py"),
            ("asset-lifecycle-and-families", "browser_asset_lifecycle.py"),
        ]:
            name = engine + "-" + suffix
            commands.append(
                (
                    name,
                    [
                        sys.executable,
                        "scripts/" + script,
                        "--engine",
                        engine,
                        "--output",
                        str(output / name),
                    ],
                    480,
                )
            )
    if args.live_ai:
        commands.append(
            (
                "actual-local-language-model",
                [
                    sys.executable,
                    "scripts/verify_local_director.py",
                    "--output",
                    str(output / "language-model"),
                ],
                420,
            )
        )
    else:
        report["not_verified"].append("本轮未装载真实语言和扩散模型权重")
    env = {**os.environ, "AI_SHIP_MEDIA_EVIDENCE_DIR": str(output / "media")}

    def run_gate(entry):
        name, command, timeout = entry
        process_env = env
        if name.startswith("webkit-"):
            process_env = env.copy()
            for target, override in (
                ("LD_LIBRARY_PATH", "AI_SHIP_WEBKIT_LIBRARY_PATH"),
                ("GST_PLUGIN_PATH_1_0", "AI_SHIP_WEBKIT_GST_PLUGIN_PATH"),
            ):
                if os.environ.get(override):
                    process_env[target] = os.environ[override]
        start = time.monotonic()
        with (output / (name + ".log")).open("w") as log:
            try:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=process_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        item = {
            "name": name,
            "command": command,
            "exit_code": code,
            "passed": code == 0,
            "seconds": round(time.monotonic() - start, 3),
            "log": name + ".log",
        }
        print(json.dumps({"round": args.round, **item}, ensure_ascii=False), flush=True)
        return item

    # Each gate has its own output directory; browser suites launch independent
    # loopback services, SQLite databases and temporary media. Limit concurrent
    # heavy work so that decoders and browsers retain memory headroom.
    with ThreadPoolExecutor(max_workers=2) as pool:
        report["checks"] = list(pool.map(run_gate, commands))
    if args.live_ai:
        report["checks"].append(
            run_gate(
                (
                    "actual-local-multi-model-review",
                    [
                        sys.executable,
                        "scripts/verify_local_team.py",
                        "--output",
                        str(output / "model-team"),
                    ],
                    600,
                )
            )
        )
        report["checks"].append(
            run_gate(
                (
                    "actual-local-opening-text",
                    [
                        sys.executable,
                        "scripts/verify_opening_text.py",
                        "--output",
                        str(output / "opening-model"),
                    ],
                    420,
                )
            )
        )
        # Run the real diffusion worker alone to leave CPU memory for its temporal model.
        report["checks"].append(
            run_gate(
                (
                    "actual-local-diffusion-model",
                    [
                        sys.executable,
                        "scripts/verify_generation_worker.py",
                        "--seed",
                        str(48170 + args.round),
                        "--output",
                        str(output / "diffusion-model"),
                    ],
                    2400,
                )
            )
        )
    report["unchanged_during_round"] = fingerprint()["sha256"] == initial["sha256"]
    report["passed"] = (
        all(check["passed"] for check in report["checks"]) and report["unchanged_during_round"]
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    evidence_files = [p for p in output.rglob("*") if p.is_file()]
    report["evidence_hashes"] = {
        str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(evidence_files)
    }
    (output / "round-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "round": args.round,
                "passed": report["passed"],
                "report": str(output / "round-report.json"),
            }
        ),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
