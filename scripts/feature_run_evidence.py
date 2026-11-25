"""Validate pinned execution receipts and independently reviewed feature acceptance."""

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

FLAGS = ("full_business_acceptance", "production_verified", "external_live_verified")
DOMAINS = {"local", "production", "external_live"}
# Fixed adapters are intentionally narrow. A heterogeneous historical report cannot be
# reassigned to a different feature, a whole criterion, or a production environment.
LEGACY = {
    "speech-v1": (
        "evidence/local-speech/report.json",
        {
            "/engine/installed": True,
            "/english/engine_version": "0.3.45",
            "/mandarin/cues/0/text": "广州市房地产中介协会分析",
            "/mandarin/cues/0/start": 0.45,
            "/delayed_mandarin/cues/0/start": 1.95,
            "/digital_silence/cues": [],
            "/no_audio_track/code": "NO_AUDIO",
            "/candidate_export_blocked": 422,
            "/model_pin_mismatch/status": 409,
            "/review_to_editor/saved_timeline_caption/timing_precision": "cue",
            "/review_to_editor/saved_timeline_caption/start": 1.95,
        },
        {"L04": ["Q01", "Q02"], "V04": ["Q01", "Q03"]},
        "固定中英文录音产生源时钟词级候选，延迟音轨、数字静音、审核和剪辑器字幕回读已实测。",
    ),
    "frame-search-v1": (
        "evidence/batch-production/visual-search/result.json",
        {
            "/passed": True,
            "/checks/model_change_isolated": True,
            "/checks/literal_fallback_available": True,
            "/checks/revoked_processing_grant_excluded": True,
            "/browsers/0/editor_source_seconds": 3.4,
            "/browsers/1/editor_source_seconds": 3.4,
        },
        {"S02": ["Q02", "Q03", "Q04"]},
        "固定测试素材的跨模型隔离、无向量字面检索及双浏览器源帧定位。",
    ),
    "team-loop-v1": (
        "evidence/batch-production/agent-loop.json",
        {
            "/passed": True,
            "/stage": "blocked",
            "/review_rounds/0/blocker/code": "TEAM_EVIDENCE_REQUIRED",
            "/review_rounds/1/blocker/code": "TEAM_EVIDENCE_REQUIRED",
            "/frame_qc/checks/full_decode": True,
            "/frame_qc/content_review_required": True,
        },
        {"C09": ["Q03", "Q06"]},
        "三套本地权重的两轮评议被原片证据拦截，另行人工修订的成片通过解码。",
    ),
    "replay-browser-v1": (
        "evidence/recorded-response/result.json",
        {
            "/fixture": "HTTP loopback synthetic model acknowledgements",
            "/provider_calls_during_setup": 2,
            "/provider_calls_during_replay": 0,
            "/different_prompt_messages": True,
            "/literal_contains_result": [False, True],
            "/page_errors": [],
        },
        {"D08": ["Q02"], "H10": ["Q02"]},
        "浏览器读取冻结的回环模型回执，逐字段重新评测时没有新的模型请求。",
    ),
    "directory-browser-v1": (
        "evidence/batch-production/browser/directory-import.json",
        {
            "/checks/2": "two stable physical paths -> one immutable asset and two provenance receipts",
            "/checks/3": "reopening restores persisted intake state",
            "/page_errors": [],
        },
        {"A05": ["Q05", "Q06"]},
        "两个物理路径复用一个不可变素材，同时保留各自来源；重新打开能读取导入状态。",
    ),
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def criteria(feature):
    op = feature["operation"]
    return (
        {r["id"] for r in feature["routes"]}
        | {"control:" + c["key"] for c in op["controls"]}
        | {
            f"{prefix}{i:02d}"
            for prefix, key in [("S", "steps"), ("Q", "qualityGates"), ("F", "failurePaths")]
            for i in range(1, len(op[key]) + 1)
        }
    )


def digest(data):
    return sha256(data).hexdigest()


def runtime_fingerprint(root):
    """Content fingerprint of shipped runtime and dependency declarations, independent of evidence commits."""
    files = {
        p
        for name in ("app", "web")
        for p in (root / name).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    }
    files.update(p for p in root.glob("requirements*.txt") if p.is_file())
    for name in (
        "contracts/atlas.json",
        "constraints.txt",
        "Dockerfile",
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "scripts/run.py",
    ):
        if (root / name).is_file():
            files.add(root / name)
    return digest(
        b"".join(
            p.relative_to(root).as_posix().encode() + b"\0" + sha256(p.read_bytes()).digest()
            for p in sorted(files)
        )
    )


def trusted_report(root, entry):
    name = entry["path"]
    path = PurePosixPath(name)
    require(
        "\\" not in name
        and not path.is_absolute()
        and len(path.parts) >= 2
        and path.parts[0] == "evidence"
        and ".." not in path.parts
        and str(path) == name
        and path.suffix == ".json",
        "Expected a repository-relative evidence JSON path",
    )
    target = root.joinpath(*path.parts)
    require(
        target.resolve() == root.resolve().joinpath(*path.parts) and target.is_file(),
        "Missing report or symbolic link: " + name,
    )
    contents = target.read_bytes()
    require(digest(contents) == entry["sha256"], "Evidence SHA-256 mismatch: " + name)
    return json.loads(contents)


def pointer(report, address):
    value = report
    for token in address.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def json_equal(a, b):
    # JSON booleans must not compare equal to numbers, including inside nested arrays.
    return json.dumps(a, ensure_ascii=False, sort_keys=True, allow_nan=False) == json.dumps(
        b, ensure_ascii=False, sort_keys=True, allow_nan=False
    )


def stamped(value):
    require(
        isinstance(value, str)
        and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None,
        "Timestamp needs a UTC offset",
    )


def validate_index(root, atlas, index, atlas_sha):
    require(
        index["format"] == "atlas-evidence-index-1" and index["atlas_sha256"] == atlas_sha,
        "Evidence index targets a different Atlas",
    )
    features = {f["id"]: f for f in atlas["features"]}
    runs = defaultdict(list)
    approvals = defaultdict(dict)
    by_id = set()
    files = set()

    def load(entry):
        rid = entry["id"]
        require(
            re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", rid)
            and rid not in by_id
            and entry["path"] not in files,
            "Duplicate or invalid evidence ID/path",
        )
        report = trusted_report(root, entry)
        files.add(entry["path"])
        by_id.add(rid)
        return report

    for entry in index["runs"]:
        require(
            set(entry) == {"id", "profile", "path", "sha256"},
            "Run bindings come from the measured report, never an index override",
        )
        report = load(entry)
        require(
            ("passed" not in report or report["passed"] is True)
            and ("page_errors" not in report or report["page_errors"] == []),
            "Report check failed: explicit execution failure or browser error",
        )
        profile = entry["profile"]
        if profile == "criterion-run-v1":
            require(
                report["format"] == "atlas-criterion-run-1"
                and report["id"] == entry["id"]
                and report["atlas_sha256"] == atlas_sha
                and report["passed"] is True,
                "Unsuccessful or misbound formal run",
            )
            feature = report["feature_id"]
            require(
                feature in features and report["environment"] in DOMAINS,
                "Unknown feature or execution environment",
            )
            for key in ("command", "executor"):
                require(
                    isinstance(report[key], str) and report[key].strip(),
                    "Missing formal run " + key,
                )
            stamped(report["finished_at"])
            require(
                re.fullmatch("[a-f0-9]{64}", report["runtime_sha256"]),
                "Missing runtime fingerprint",
            )
            matrix = criteria(features[feature])
            checks = report["checks"]
            require(isinstance(checks, list) and checks, "Missing measured checks")
            measurement = trusted_report(root, report["measurement"])
            require(
                report["measurement"]["path"] != entry["path"]
                and measurement["run_id"] == report["id"]
                and measurement["feature_id"] == feature
                and isinstance(report["target"], str)
                and report["target"].strip()
                and measurement["target"] == report["target"]
                and measurement["environment"] == report["environment"]
                and measurement["runtime_sha256"] == report["runtime_sha256"]
                and measurement["passed"] is True,
                "Raw measurement is not a passed result from this run, feature, environment and runtime",
            )
            for key in {
                "production": ("deployment_id",),
                "external_live": ("service", "external_receipt_id"),
            }.get(report["environment"], ()):
                require(
                    isinstance(measurement.get(key), str) and measurement[key].strip(),
                    "Missing live environment witness: " + key,
                )
            found = set()
            for c in checks:
                require(
                    c["criterion"] in matrix
                    and c["criterion"] not in found
                    and c["extent"] in {"partial", "complete"}
                    and c["passed"] is True,
                    "Unknown, repeated or failed criterion",
                )
                require(
                    isinstance(c["observation"], str)
                    and c["observation"].strip()
                    and c["extent"] == measurement["observations"][c["criterion"]]["extent"]
                    and json_equal(
                        c["expected"], measurement["observations"][c["criterion"]]["actual"]
                    ),
                    "Observed and expected values disagree",
                )
                found.add(c["criterion"])
            scope = {
                feature: [
                    {
                        "criterion": c["criterion"],
                        "extent": c["extent"],
                        "observation": c["observation"],
                    }
                    for c in checks
                ]
            }
            context = {
                k: report[k]
                for k in (
                    "executor",
                    "finished_at",
                    "target",
                    "environment",
                    "runtime_sha256",
                    "command",
                    "measurement",
                )
            }
        else:
            require(profile in LEGACY, "Unknown execution-report adapter")
            path, assertions, bindings, note = LEGACY[profile]
            require(entry["path"] == path, "Adapter/path mismatch")
            for address, expected in assertions.items():
                require(
                    json_equal(pointer(report, address), expected),
                    f"Report check failed: {path} {address}",
                )
            scope = {
                f: [{"criterion": g, "extent": "partial", "observation": note} for g in gates]
                for f, gates in bindings.items()
            }
            context = {"environment": "local", "runtime_sha256": None}
        for feature, checks in scope.items():
            require(
                all(c["criterion"] in criteria(features[feature]) for c in checks),
                "Criterion is outside this feature",
            )
            runs[feature].append(
                {
                    "id": entry["id"],
                    "path": entry["path"],
                    "sha256": entry["sha256"],
                    "profile": profile,
                    **context,
                    "checks": checks,
                }
            )

    runtime = runtime_fingerprint(root) if index["reviews"] else None
    for entry in index["reviews"]:
        require(
            set(entry) == {"id", "path", "sha256"}, "Review scope must come from its pinned report"
        )
        review = load(entry)
        fid, flag = review["feature_id"], review["decision"]
        require(
            review["format"] == "atlas-acceptance-review-1"
            and review["id"] == entry["id"]
            and review["atlas_sha256"] == atlas_sha
            and review["accepted"] is True
            and fid in features
            and flag in FLAGS,
            "Invalid acceptance review",
        )
        require(
            not any(
                v["runtime_sha256"] == review["runtime_sha256"]
                for v in approvals[fid].get(flag, [])
            )
            and isinstance(review["reviewer"], str)
            and review["reviewer"].strip()
            and isinstance(review["rationale"], str)
            and review["rationale"].strip(),
            "Duplicate or unsigned review decision",
        )
        stamped(review["reviewed_at"])
        eligible = {
            r["id"]: r
            for r in runs[fid]
            if r["profile"] == "criterion-run-v1"
            and r["runtime_sha256"] == review["runtime_sha256"]
            and r["executor"] != review["reviewer"]
            and (
                flag == FLAGS[0]
                or r["target"] == review.get("target")
                and r["environment"]
                == {"production_verified": "production", "external_live_verified": "external_live"}[
                    flag
                ]
            )
        }
        basis = review["basis"]
        require(
            isinstance(basis, list) and basis and len({b["id"] for b in basis}) == len(basis),
            "Missing or duplicate review basis",
        )
        covered = set()
        for b in basis:
            r = eligible.get(b["id"])
            require(
                r and r["sha256"] == b["sha256"],
                "Acceptance basis must pin an independent matching feature, runtime and environment",
            )
            require(
                datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
                >= datetime.fromisoformat(r["finished_at"].replace("Z", "+00:00")),
                "Review precedes execution",
            )
            covered.update(c["criterion"] for c in r["checks"] if c["extent"] == "complete")
        require(
            covered == criteria(features[fid]),
            "Whole-feature acceptance requires every route, control, step, quality and failure condition",
        )
        approvals[fid].setdefault(flag, []).append(
            {
                "id": entry["id"],
                "path": entry["path"],
                "sha256": entry["sha256"],
                "runtime_sha256": review["runtime_sha256"],
                "basis": basis,
                **({"target": review["target"]} if flag != FLAGS[0] else {}),
                "current": runtime == review["runtime_sha256"],
            }
        )
    for fid, decisions in approvals.items():
        if any(
            v["current"] for k, versions in decisions.items() if k != FLAGS[0] for v in versions
        ):
            require(
                any(v["current"] for v in decisions.get(FLAGS[0], [])),
                "Environment-wide promotion also requires current whole-feature acceptance",
            )
    return runs, approvals
