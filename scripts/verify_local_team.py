"""Real GGUF director, critic and judge through authenticated HTTP production endpoints."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("director", "reviewer", "judge")
MODELS = {
    "director": (
        "qwen2.5-1.5b",
        "183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4",
    ),
    "reviewer": (
        "qwen2.5-0.5b",
        "c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515",
    ),
    "judge": ("smollm2-360m", "2fa3f013dcdd7b99f9b237717fa0b12d75bbb89984cc1274be1471a465bac9c2"),
}


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def wait_http(client, url, process, require=200):
    for _ in range(600):
        if process.poll() is not None:
            raise RuntimeError("HTTP worker stopped before becoming ready")
        try:
            if client.get(url, timeout=0.75).status_code == require:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError("HTTP service did not become ready")


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def call(client, path, body):
    r = client.post(path, json=body)
    r.raise_for_status()
    return r.json()["data"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-rounds", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--server", type=Path, default=os.getenv("AI_SHIP_LLAMA_SERVER"))
    for role in ROLES:
        parser.add_argument(
            "--" + role + "-model",
            type=Path,
            default=os.getenv("AI_SHIP_TEAM_" + role.upper() + "_GGUF"),
        )
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    paths = {role: getattr(a, role + "_model") for role in ROLES}
    if not a.server or not all(paths.values()):
        parser.error("supply a llama-server executable and all three GGUF files")
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "models": {}, "decisions": [], "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-team-") as td, ExitStack() as stack:
            temp = Path(td)
            env = os.environ.copy()
            configs = {}
            keys = {role: secrets.token_urlsafe(30) for role in ROLES}
            origins = {}
            anon = stack.enter_context(httpx.Client(trust_env=False, timeout=10))
            engine = subprocess.run(
                [str(a.server), "--version"], capture_output=True, text=True, check=True
            )
            report["engine"] = (engine.stdout + engine.stderr).strip()
            report["server_sha256"] = sha(a.server)
            for role, path in paths.items():
                name, pinned = MODELS[role]
                actual = sha(path)
                if actual != pinned:
                    raise ValueError(f"{role} weight SHA does not match the frozen ensemble")
                pid = port()
                origin = f"http://127.0.0.1:{pid}"
                origins[role] = origin
                keyfile = temp / (role + ".key")
                keyfile.write_text(keys[role] + "\n")
                keyfile.chmod(0o600)
                envkey = "AI_SHIP_TEST_TEAM_" + role.upper() + "_AUTH"
                env[envkey] = keys[role]
                log = stack.enter_context((out / (role + "-llama.log")).open("w"))
                proc = subprocess.Popen(
                    [
                        str(a.server),
                        "-m",
                        str(path),
                        "--alias",
                        name,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(pid),
                        "--api-key-file",
                        str(keyfile),
                        "--threads",
                        "2",
                        "--threads-batch",
                        "2",
                        "--batch-size",
                        "64",
                        "--ubatch-size",
                        "32",
                        "--ctx-size",
                        "8192",
                        "--parallel",
                        "1",
                        "--seed",
                        "73",
                        "--no-webui",
                        "--no-warmup",
                        "--sleep-idle-seconds",
                        "2",
                    ],
                    stdout=log,
                    stderr=log,
                )
                stack.callback(stop, proc)
                wait_http(anon, origin + "/health", proc)
                with httpx.Client(base_url=origin, trust_env=False, timeout=5) as guard:
                    # Protected completion route verifies actual authentication without inference or billing.
                    denied = guard.post("/v1/chat/completions", json={})
                    assert denied.status_code in (401, 403), (role, denied.status_code)
                configs[role] = {
                    "base_url": origin + "/v1",
                    "transport": "loopback_chat",
                    "api_key_env": envkey,
                    "provider_identity": role + "-cpu",
                    "models": [name],
                    "currency": "USD",
                    "input_price_per_million": 0,
                    "output_price_per_million": 0,
                    "candidate_menu": True,
                    "structured_schema": True,
                    "json_output": True,
                    "context_limit": 8192,
                }
                report["models"][role] = {
                    "model": name,
                    "weights": path.name,
                    "sha256": actual,
                    "bytes": path.stat().st_size,
                    "unauthenticated_completion_http": denied.status_code,
                    "device": "CPU",
                    "idle_unload_seconds": 2,
                }
            owner, viewer, approver = [secrets.token_urlsafe(30) for _ in range(3)]
            env.update(
                AI_SHIP_MODE="team",
                AI_SHIP_DATA_DIR=str(temp / "data"),
                AI_SHIP_DB=str(temp / "data.sqlite3"),
                AI_SHIP_PROVIDERS=json.dumps(configs),
                AI_SHIP_COMFY_PROFILES="{}",
                AI_SHIP_API_KEYS=json.dumps(
                    {
                        k: {"tenant_id": "team-proof", "actor_id": role, "role": role}
                        for k, role in (
                            (owner, "owner"),
                            (viewer, "viewer"),
                            (approver, "reviewer"),
                        )
                    }
                ),
            )
            apiport = port()
            url = f"http://127.0.0.1:{apiport}/api/v1/"
            apilog = stack.enter_context((out / "api.log").open("w"))
            api = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/run.py",
                    "--port",
                    str(apiport),
                    "--data-dir",
                    str(temp / "data"),
                ],
                env=env,
                cwd=ROOT,
                stdout=apilog,
                stderr=apilog,
            )
            stack.callback(stop, api)
            wait_http(anon, url + "health", api, 401)

            def api_client(key):
                return stack.enter_context(
                    httpx.Client(
                        base_url=url,
                        trust_env=False,
                        # A synchronous request can cover three model calls per round.
                        timeout=httpx.Timeout(30, read=3 * a.review_rounds * 120 + 60),
                        headers={"Authorization": "Bearer " + key},
                    )
                )

            writer, reader, reviewer = [api_client(k) for k in (owner, viewer, approver)]
            project = call(
                writer,
                "projects",
                {"name": "Three-model actual project", "width": 320, "height": 180, "budget": 0},
            )
            prof = {}
            for role in ROLES:
                prof[role] = call(
                    writer,
                    "records",
                    {
                        "kind": "ModelProfile",
                        "project_id": project["id"],
                        "data": {
                            "name": role,
                            "model_id": MODELS[role][0],
                            "provider": role,
                            "structured_output": True,
                            "context_limit": 8192,
                            "sampling_parameters": {"max_tokens": 300},
                            "timeout_ms": 120000,
                        },
                    },
                )
            assets = []
            for name in ("SAMPLE", "FRESH LIME"):
                source = ROOT / "evidence/ai-production/ocr" / (name + ".mp4")
                r = writer.post(
                    "media/import",
                    params={"project_id": project["id"], "filename": source.name},
                    content=source.read_bytes(),
                )
                r.raise_for_status()
                assets.append(r.json()["data"]["asset"])
            body = {
                "project_id": project["id"],
                "mode": "team",
                "max_review_rounds": a.review_rounds,
                "goal": "Create a short shot list for FRESH LIME. Prioritize the source with those visible words. No new product claims.",
                "asset_ids": [r["id"] for r in assets],
                "idempotency_key": uuid.uuid4().hex,
                "target_seconds": 2,
                "fps": 25,
                "width": 320,
                "height": 180,
                "model_profile_id": prof["director"]["id"],
                "reviewer_profile_id": prof["reviewer"]["id"],
                "judge_profile_id": prof["judge"]["id"],
                "max_cost": 0.01,
            }
            denied = reader.post("production/auto-edits", json=body)
            assert denied.status_code == 403
            started = time.monotonic()
            d = call(writer, "production/auto-edits", body)
            report.update(
                stage=d["session"]["data"]["stage"],
                issue=d["session"]["data"]["issue"],
                elapsed_seconds=round(time.monotonic() - started, 3),
                project_id=project["id"],
                run_id=d["session"]["id"],
                goal=body["goal"],
                inputs=[
                    {
                        "id": x["id"],
                        "filename": x["data"]["filename"],
                        "version": x["version"],
                        "content_hash": x["data"]["content_hash"],
                    }
                    for x in assets
                ],
            )
            refs = d["session"]["data"].get("call_refs", [])
            for pin in refs:
                role = pin["role"]
                raw = writer.get("records/" + pin["id"], params={"version": pin["version"]}).json()[
                    "data"
                ]
                info = raw["data"]
                cost = writer.get("records/" + info["cost_ref"]).json()["data"]
                report["decisions"].append(
                    {
                        "role": role,
                        "round": pin["round"],
                        "prompt_hash": pin["prompt_hash"],
                        "context_hash": pin["context_hash"],
                        "context_receipt": pin.get("context_receipt"),
                        "call": ref_summary(raw),
                        "provider_request_id": info["provider_request_id"],
                        "requested_model": info["requested_provider_model"],
                        "reported_model": info["actual_provider_model"],
                        "request_hash": info["request_payload_hash"],
                        "measured_tokens": info["usage"],
                        "decision": (info.get("response") or {}).get("json"),
                        "outcome": info["outcome"],
                        "error_code": info.get("error_code"),
                        "cost_status": cost["data"]["status"],
                        "calculated_charge": cost["data"].get("calculated_charge"),
                    }
                )
            history = d["session"]["data"].get("review_rounds", [])
            assert 1 <= len(history) <= a.review_rounds, (report["stage"], report["issue"])
            assert [turn["role"] for turn in report["decisions"]] == list(ROLES) * len(
                history
            ), report
            report["loop_stop"] = d["session"]["data"].get("loop_stop")
            if report["stage"] == "blocked":
                assert (
                    len(history) == a.review_rounds
                    or report["loop_stop"] == "repeated_rejected_cut"
                )
            report["review_rounds"] = [
                {
                    "round": turn["round"],
                    "decision": turn["decision"],
                    "blocker": turn["blocker"],
                    "plan": turn["plan_ref"],
                }
                for turn in history
            ]
            assert len({x["plan"]["id"] for x in report["review_rounds"]}) == len(history)
            for number in range(2, len(history) + 1):
                previous = next(
                    x
                    for x in report["decisions"]
                    if x["role"] == "director" and x["round"] == number - 1
                )
                current = next(
                    x
                    for x in report["decisions"]
                    if x["role"] == "director" and x["round"] == number
                )
                assert current["context_hash"] != previous["context_hash"]

            for turn in report["decisions"]:
                assert (
                    turn["outcome"] == "succeeded"
                    and turn["requested_model"] == turn["reported_model"] == MODELS[turn["role"]][0]
                )
                assert (
                    turn["measured_tokens"]["completion_tokens"] > 0
                    and turn["calculated_charge"] == 0
                )
            suffix = "production/auto-edits/" + d["session"]["id"]
            again = call(writer, "production/auto-edits", body)
            assert again["session"]["id"] == d["session"]["id"]
            n = writer.get(
                "records", params={"project_id": project["id"], "kind": "ToolCall"}
            ).json()["data"]
            assert len(n) == len(ROLES) * len(history)
            report["checks"].extend(
                [
                    "three distinct pinned model files, authenticated localhost model endpoints",
                    "authenticated Atlas viewer denied production write; project owner request reached all three real GGUF models",
                    "source OCR plus scoped evidence, measured token usage, provider identities; no external billing",
                    "repeated request preserves the same run and the original calls from every completed round",
                ]
            )
            if report["stage"] == "blocked":
                code = report["issue"]["code"]
                assert code in {"TEAM_REVISION_REQUIRED", "TEAM_EVIDENCE_REQUIRED"}
                report["review_findings"] = d["plan"]["data"]["reviews"]
                if code == "TEAM_REVISION_REQUIRED":
                    assert report["decisions"][-1]["decision"]["decision"] == "revise"
                else:
                    assert any(v["evidence_problem"] for v in report["review_findings"])
                assert d["timeline"] is None and d["artifact"] is None
                declined = reviewer.post(
                    suffix + "/approve", json={"expected_revision": d["session"]["version"]}
                )
                assert declined.status_code == 409
                report["checks"].append(
                    "independent verdict or measured evidence gate blocked adoption of the original proposal"
                )
                original = d
                observation = next(
                    s
                    for s in original["session"]["data"]["evidence"]
                    if s["name"] == "FRESH LIME.mp4"
                )
                assert "FRESH" in [w["text"] for w in observation["ocr"]["words"]]
                d = call(
                    writer,
                    suffix + "/replace-shot",
                    {
                        "expected_revision": d["session"]["version"],
                        "shot_index": 0,
                        "asset_id": observation["asset_id"],
                        "source_in": 0.2,
                        "source_out": 0.8,
                        "reason": "Authenticated test editor selected a measured source showing FRESH LIME for the opening.",
                    },
                )
                assert (
                    d["session"]["data"]["stage"] == "review"
                    and d["plan"]["data"]["base_review_issue"]["code"] == code
                )
                assert d["session"]["id"] != original["session"]["id"]
                report["manual_recovery"] = {
                    "original_blocked": original["session"]["id"],
                    "new_proposal": d["session"]["id"],
                    "changed_shot": 0,
                    "at": observation["ocr"]["at"],
                    "observed_ocr": observation["ocr"]["words"],
                }
                suffix = "production/auto-edits/" + d["session"]["id"]
            else:
                assert (
                    report["stage"] == "review"
                    and report["decisions"][-1]["decision"]["decision"] == "accept"
                )
            report["proposal"] = {
                k: d["plan"]["data"].get(k)
                for k in ("title", "approach", "shots", "missing", "reviews", "model_selection")
            }
            draft = call(
                reviewer, suffix + "/approve", {"expected_revision": d["session"]["version"]}
            )
            done = call(
                writer,
                suffix + "/render",
                {
                    "expected_revision": draft["session"]["version"],
                    "timeline_revision": draft["timeline"]["version"],
                },
            )
            assert done["session"]["data"]["stage"] == "rendered", done["session"]["data"]["issue"]
            outmovie = out / "ensemble-cut.mp4"
            download = writer.get("media/artifacts/" + done["artifact"]["id"] + "/file")
            download.raise_for_status()
            outmovie.write_bytes(download.content)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(outmovie),
                    "-map",
                    "0:v",
                    "-map",
                    "0:a",
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
            )
            report.update(
                frame_qc=done["artifact"]["data"]["qc"],
                movie_sha256=sha(outmovie),
                timeline=ref_summary(draft["timeline"]),
            )
            report["checks"].append(
                "authenticated test reviewer adopted the eligible proposal; owner rendered, downloaded video/audio fully decoded"
            )
            report["passed"] = True
    finally:
        write(out / "report.json", report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "stage": report.get("stage"),
                "decisions": [x["reported_model"] for x in report["decisions"]],
            },
            ensure_ascii=False,
        )
    )


def ref_summary(item):
    return {k: item[k] for k in ("id", "kind", "version", "hash")}


if __name__ == "__main__":
    main()
