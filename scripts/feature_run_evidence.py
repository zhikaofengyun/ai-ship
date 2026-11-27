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
    "semantic-family-real-clip-v1": (
        "evidence/material-intake/semantic-families/result.json",
        {
            "/audit/algorithm": "clip_bidirectional_frame_coverage_complete_link_v1",
            "/audit/feature_id": "S04",
            "/audit/route_id": "S04-R3",
            "/audit/space": "clip-b32/98c718df886ac03f7aeb914c9c6e2a9850f141cb071cadd6f65cc7a812c4715d",
            "/audit/threshold": 0.8,
            "/audit/groups/0/members": [
                "assetversion_44224fe26bb94fb69ba96c0909b9c476",
                "assetversion_8615afddc1c14e2c9c232c28af7241d1",
            ],
            "/audit/unmatched": [
                "assetversion_1206239bfed444199578316780990a8a",
                "assetversion_2c7f6e8bd745418fbb7a6bccfee4ca68",
            ],
            "/audit/groups/0/minimum_score": 0.9495369480037302,
            "/audit/groups/0/pairs/0/left_pts_us": 1000000,
            "/audit/groups/0/pairs/0/right_pts_us": 0,
            "/confirmed/id": "cluster_00ff120d53884c02bf30bf3636d653a7",
            "/retired/id": "cluster_00ff120d53884c02bf30bf3636d653a7",
            "/confirmed/version": 1,
            "/retired/version": 2,
            "/inputs/assetversion_2c7f6e8bd745418fbb7a6bccfee4ca68/filename": "astronaut.png",
            "/inputs/assetversion_8615afddc1c14e2c9c232c28af7241d1/filename": "coffee.png",
            "/inputs/assetversion_1206239bfed444199578316780990a8a/filename": "flower.mp4",
            "/inputs/assetversion_44224fe26bb94fb69ba96c0909b9c476/filename": "coffee-pan.mp4",
        },
        {"S04": ["S04-R3", "Q01"]},
        "四份实物素材中，咖啡原图与其移动裁切片同组，最低双向帧匹配分为 0.94954。宇航员与花卉没有入组；确认后的素材族从 v1 退役为 v2。",
    ),
    "material-ingest-real-frames-v1": (
        "evidence/material-intake/result.json",
        {
            "/automatic_analysis_queue/completed": 3,
            "/top_visual_match/name": "astronaut.png",
            "/top_visual_match/frame_match/source_seconds": 0.0,
            "/top_visual_match/frame_match/sample_rgb_sha256": "d4ad02fb59752482acc8d64c6b474d2da8839208cb526d7525044e639d2ede5e",
            "/machine_candidates/astronaut.png": [],
            "/machine_candidates/coffee.png/1/label": "杯装饮品",
            "/machine_candidates/flower.mp4/2/source_seconds": 2.535867,
            "/machine_candidates/flower.mp4/2/label": "花卉植物",
            "/page_errors": [],
        },
        {"L01": ["Q02"], "L06": ["Q02"], "S02": ["Q01"]},
        "3 份素材自动入列并完成分析。宇航员图可由实际帧检索命中且没有标签候选；咖啡图和花卉视频有附时码与帧摘要的候选。",
    ),
    "same-photo-two-prompts-v1": (
        "evidence/batch-production/directed-still/report.json",
        {
            "/estimate/generation_calls": 2,
            "/items/0/prompt": "Keep the cup, saucer, tablecloth and table from the reference photograph.\nA gentle lateral slide past the cup handle.",
            "/items/0/seed": 48185,
            "/items/0/route": "generated",
            "/items/0/decoded_frames": 12,
            "/items/1/prompt": "Keep the cup, saucer, tablecloth and table from the reference photograph.\nA subtle camera push toward the cup rim.",
            "/items/1/seed": 48186,
            "/items/1/route": "generated",
            "/items/1/decoded_frames": 12,
            "/items/0/uploaded_reference_sha256": "45272eb21acc21077f73c4d6d2f888ea5e801b68be6453c981bb0d004c51b0b5",
            "/items/1/uploaded_reference_sha256": "45272eb21acc21077f73c4d6d2f888ea5e801b68be6453c981bb0d004c51b0b5",
            "/items/0/model_output/distinct_decoded_frames": 4,
            "/items/1/model_output/distinct_decoded_frames": 4,
            "/items/2/route": "camera",
            "/items/2/decoded_frames": 12,
        },
        {"G02": ["Q04"], "G03": ["Q01", "Q02"], "N04": ["Q01"]},
        "同一授权实拍 PNG 发送给两个不同提示和种子的真实小模型任务，各得四个不同解码帧。实拍项、两生成项均编码为成片。两个生成项均未通过商品与运镜目检。",
    ),
    "sample-photo-motion-v1": (
        "evidence/batch-production/controlled-photo/report.json",
        {
            "/items/0/motion": "horizontal",
            "/items/1/motion": "vertical",
            "/items/0/distinct_decoded_frames": 48,
            "/items/1/distinct_decoded_frames": 48,
            "/items/0/measured_translation/dx": -100,
            "/items/0/measured_translation/dy": 0,
            "/items/1/measured_translation/dx": 0,
            "/items/1/measured_translation/dy": -184,
            "/items/0/template/locks/0/path": "clips.0.keyframes",
            "/items/1/template/locks/0/path": "clips.0.keyframes",
            "/items/0/generation_calls": 0,
            "/items/1/generation_calls": 0,
        },
        {"E05": ["Q01"], "E06": ["Q03"]},
        "同一咖啡原图经带人工关键帧锁的横移、纵移样片各渲染 48 个不同帧，位移分别为 −100 和 −184 像素，模型调用为零。",
    ),
    "team-resume-real-roles-v1": (
        "evidence/team-recovery/cpu-role-receipts.json",
        {
            "/original_receipt_reused": True,
            "/issue/code": "INDEPENDENT_REVIEW_REQUIRED",
            "/decision_count": 0,
            "/cost_records": 3,
            "/result": "output_invalid",
            "/roles/2/role": "judge",
        },
        {"H06": ["Q01", "Q11"], "C09": ["Q06"], "C11": ["Q05"]},
        "两个本地模型服务承担三个角色，中断后复用原回执。照抄前序内容的裁决遭拦截，共三份费用记录，零条通过决定。",
    ),
    "two-round-real-model-edit-v1": (
        "evidence/team-recovery/two-round-edit.json",
        {
            "/models/director/model": "qwen2.5-1.5b",
            "/models/reviewer/model": "qwen2.5-0.5b",
            "/models/judge/model": "smollm2-360m",
            "/decisions/5/role": "judge",
            "/decisions/5/round": 2,
            "/loop_stop": "repeated_rejected_cut",
            "/manual_recovery/at": 0.8,
            "/manual_recovery/observed_ocr/0/text": "FRESH",
            "/manual_recovery/observed_ocr/1/text": "LIME",
            "/frame_qc/checks/full_decode": True,
            "/frame_qc/content_review_required": True,
        },
        {"C09": ["Q01", "Q06"], "C11": ["Q01", "Q04"]},
        "三种本地权重运行两轮后重复提出被拒镜头，原案停止。人工按 0.8 秒处的 FRESH LIME 原片 OCR 纠正后，衍生作品通过全片解码。",
    ),
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


for browser, version in (("chromium", "158.0.8127.0"), ("webkit", "26.5")):
    LEGACY[f"matched-placement-{browser}-v1"] = (
        f"evidence/matched-placements/{browser}.json",
        {
            "/browser": version,
            "/fixture": "Two local FFmpeg MP4s, each independently downloaded and verified against its synthetic post and observed first-play cohort.",
            "/interval_seconds": [0, 0.5],
            "/paid_posts": 0,
            "/organic_posts": 2,
            "/evidence/0/conditional_retention": "80.0%",
            "/evidence/1/conditional_retention": "55.0%",
            "/matched_comparison/published_days": 1,
            "/matched_comparison/difference_pp": -25.0,
            "/matched_comparison/baseline": {
                "posts": 1,
                "entrants": 100,
                "retained": 80,
                "other_source_posts": 0,
            },
            "/matched_comparison/candidate": {
                "posts": 1,
                "entrants": 100,
                "retained": 55,
                "other_source_posts": 0,
            },
            "/matched_comparison/causal_claim": False,
            "/counterevidence_and_stop_rule_preserved": True,
            "/returned_to_post_review": True,
        },
        {"M09": ["Q02", "Q03", "Q06"]},
        f"{browser} {version} 在两条本地编码成片核对后，按合成帖子和留存回执比较同日首个半秒。"
        "基准 80/100、候选 55/100，差值为 −25 个百分点。付费过滤无样本。"
        "编辑保存复用假设、反证、下一版改法和停止条件。此记录只覆盖夹具中的局部行为。",
    )

    LEGACY[f"candidate-review-{browser}-v1"] = (
        f"evidence/batch-production/mixed-routing/visual-review/{browser}.json",
        {
            "/engine": browser,
            "/browser": version,
            "/page_errors": [],
            "/viewports": [1440, 390],
            "/source_sha256": "afacd0313fdcd967f135c6220a326c47c84038cf987b59af9b655a50aec21b48",
            "/model_output_sha256": "efe61be7110cf102d4776968b6a5bafe42b2606772063a56cb56ba73dbd51eb5",
            "/reference_frame_sha256": "697ead1cdd4c8f6580b1c91d68facc3463d250289393a82b022f5484c3f80c59",
            "/candidate_frame_sha256": "c530d7297095eb309303b9678fbcb54f394f18e572405abe1e688fec6d854ec6",
            "/candidate_frame_pts_seconds": 0.375,
            "/review_revisions/0/version": 1,
            "/review_revisions/0/decision": "needs_revision",
            "/review_revisions/1/version": 2,
            "/review_revisions/1/decision": "reject",
            "/segment_playback_and_review/native_playback_advanced": True,
            "/segment_playback_and_review/native_player_error": None,
            "/segment_playback_and_review/wire_sha256": "609639a45d818fa1ccc8a5753725f5db6558b8a96203428ed18071465cd6d874",
            "/segment_playback_and_review/recorded_model_output_sha256": "8ff0f21468e96bd8629c648b09089063b21acabdbce6ea626ef8a9b2cd82c727",
            "/segment_playback_and_review/submitted_source_interval": [1, 1.5],
            "/segment_playback_and_review/input_pts_seconds": 0.25,
            "/segment_playback_and_review/original_pts_seconds": 1.24,
            "/segment_playback_and_review/decision": "needs_revision",
        },
        {"G05": ["Q01", "Q05"], "G06": ["Q04"]},
        f"{browser} {version} 对照实拍杯具和已记录的真实模型输出；该浏览器轮次使用已生成字节。"
        "输出 0.375 秒的显示帧有摘要，先保存待修订再保存拒绝及原因，保留两版。连续参考的回放实测将片段 0.25 秒对应回原片 1.24 秒并保存修订决定。",
    )


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
