"""Exercise an installed llama.cpp server and GGUF with real Atlas production and FFmpeg."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_auto_edits import AutoEditTests
from app.local_director import local_director


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        type=Path,
        default=os.environ.get("AI_SHIP_LLAMA_SERVER"),
        required=not os.environ.get("AI_SHIP_LLAMA_SERVER"),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=os.environ.get("AI_SHIP_TEST_GGUF"),
        required=not os.environ.get("AI_SHIP_TEST_GGUF"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    with args.model.open("rb") as f:
        model_sha = hashlib.file_digest(f, "sha256").hexdigest()
    version = subprocess.run(
        [str(args.server), "--version"], capture_output=True, text=True, check=True
    )
    report = {
        "passed": False,
        "engine": (version.stdout + version.stderr).strip(),
        "model_file": args.model.name,
        "model_sha256": model_sha,
        "checks": [],
    }
    out = args.output / "report.json"
    with (args.output / "llama-server.log").open("w") as log, ExitStack() as workers:
        fixture = AutoEditTests()
        try:
            fixture.setUp()
            endpoint, alias = workers.enter_context(
                local_director(args.server, args.model, log=log)
            )
            candidates = fixture.client.get("/api/v1/providers/availability").json()["data"][
                "providers"
            ]
            assert any(
                c["id"] == endpoint and alias in c["models"] and c["configured"] for c in candidates
            )
            report["checks"].append(
                "workbench launcher starts authenticated GGUF; registered model is available to a new project"
            )
            profile = fixture.create(
                "ModelProfile",
                {
                    "name": "本机模型导演",
                    "provider": endpoint,
                    "model_id": alias,
                    "structured_output": True,
                },
            )
            request = {
                **fixture.body,
                "goal": "Make a short FRESH LIME product cut. Prioritize the source with the words FRESH LIME. Use only measured footage, with no added claims.",
                "target_seconds": 3.2,
                "mode": "model",
                "model_profile_id": profile["id"],
            }
            response = fixture.post("", request)
            report["stage"] = response["session"]["data"]["stage"]
            report["issue"] = response["session"]["data"].get("issue")
            calls = fixture.store.list("local", kind="ToolCall")
            report["model_calls"] = [
                {
                    k: v
                    for k, v in c["data"].items()
                    if k
                    in {
                        "status",
                        "model",
                        "model_id",
                        "receipt",
                        "result",
                        "usage",
                        "response_model",
                        "provider_request_id",
                        "response_hash",
                        "provider_ref",
                    }
                }
                for c in calls
            ]
            report["cost_records"] = [
                {
                    k: v
                    for k, v in c["data"].items()
                    if k in {"status", "currency", "usage", "actual", "billed", "actual_amount"}
                }
                for c in fixture.store.list("local", kind="Cost")
            ]
            assert len(calls) == 1 and report["stage"] == "review", report["issue"]
            report["checks"].append(
                "llama.cpp GGUF CPU inference through production API / validated source IDs and real intervals"
            )
            plan = response["plan"]
            report["plan"] = plan["data"]
            assert (
                response["timeline"] is None
                and fixture.post("", request)["plan"]["id"] == plan["id"]
            )
            assert len(fixture.store.list("local", kind="ToolCall")) == 1
            report["checks"].append(
                "same request recovers the same proposal without repeating the model call"
            )
            suffix = "/" + response["session"]["id"]
            edit = fixture.post(
                suffix + "/approve", {"expected_revision": response["session"]["version"]}
            )
            movie = fixture.post(
                suffix + "/render",
                {
                    "expected_revision": edit["session"]["version"],
                    "timeline_revision": edit["timeline"]["version"],
                },
            )
            assert movie["session"]["data"]["stage"] == "rendered", movie["session"]["data"].get(
                "issue"
            )
            data = fixture.client.get(
                "/api/v1/media/artifacts/" + movie["artifact"]["id"] + "/file"
            )
            data.raise_for_status()
            moviefile = args.output / "model-cut.mp4"
            moviefile.write_bytes(data.content)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(moviefile),
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
            report["checks"].append(
                "version approval / frozen render / decoded H.264 + AAC / persisted technical QC"
            )
            report["movie_bytes"] = len(data.content)
            report["artifact_sha256"] = hashlib.sha256(data.content).hexdigest()
            report["qc"] = movie["artifact"]["data"]["qc"]
            report["passed"] = True
        finally:
            workers.close()
            if hasattr(fixture, "temp"):
                fixture.tearDown()
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
