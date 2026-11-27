"""First-play cohort observations joined to the verified final post's render time map."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import current_dependency_problem, digest, record, ref, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, live, on_request

router = APIRouter()


class RetentionPoint(SelectionBody):
    at_seconds: float = Field(ge=0, le=86400)
    retained: StrictInt = Field(ge=0, le=10**12)


class RetentionMapping(SelectionBody):
    project_id: Label
    request_id: Label
    post: ChosenVersion
    source: Note
    window_start: str
    window_end: str
    observed_at: str
    denominator: StrictInt = Field(gt=0, le=10**12)
    traffic: Literal["organic", "paid", "mixed", "unknown"]
    points: list[RetentionPoint] = Field(min_length=2, max_length=512)


def final_mapping(actor, project, post):
    post_data = post["data"]
    if post_data.get("final_media_status") != "verified_same":
        raise DomainError("POST_MAPPING_REQUIRED", "请先以下载件核对帖子最终成片。", 409)
    checks = post_data.get("media_mapping_evidence_refs") or []
    if len(checks) != 1 or checks[0].get("kind") != "Audit":
        raise DomainError("POST_MAPPING_REQUIRED", "缺少最终媒体核验回执。", 409)
    receipt = record(actor, checks[0]["id"], "Audit", checks[0]["version"], project)
    data = receipt["data"]
    if (
        data.get("method") != "human_identified_download_and_exact_hash"
        or (data.get("post_ref") or {}).get("id") != post["id"]
        or (data.get("submitted_artifact") or {}).get("id") != post_data.get("artifact_id")
    ):
        raise DomainError("POST_MAPPING_REQUIRED", "核验回执与该帖子或成片不一致。", 409)
    artifact_pin = data["submitted_artifact"]
    artifact = record(actor, artifact_pin["id"], "Artifact", artifact_pin["version"], project)
    if not data.get("content_hash") or data["content_hash"] != artifact["data"].get("content_hash"):
        raise DomainError("POST_MAPPING_REQUIRED", "最终媒体字节摘要与渲染结果不一致。", 409)
    event_id = post_data.get("correlation_evidence")
    if not post_data.get("platform_post_id") or not event_id:
        raise DomainError("POST_EVIDENCE", "帖子身份缺少来源回执。", 409)
    event_pin = next(
        (p for p in post["refs"] if p["id"] == event_id and p["kind"] == "PlatformEvent"), None
    )
    if not event_pin:
        raise DomainError("POST_EVIDENCE", "帖子缺少固定版本的平台事件。", 409)
    event = record(actor, event_id, "PlatformEvent", event_pin["version"], project)
    if event["data"].get("publication_id") != post_data.get("publication_id") or not event[
        "data"
    ].get("evidence"):
        raise DomainError("POST_EVIDENCE", "平台回执与帖子不匹配。", 409)
    account = record(actor, post_data.get("account_id") or "", "Account", project=project)
    if current_dependency_problem(account):
        raise DomainError("ACCOUNT_NOT_CURRENT", "账号已撤回，不能使用其反馈。", 409)
    if not artifact["data"].get("render_job_id"):
        raise DomainError("RENDER_MAP_REQUIRED", "该成片缺少可追溯的渲染时间图。", 409)
    job = record(actor, artifact["data"]["render_job_id"], "RenderJob", project=project)
    if job["data"].get("artifact_id") != artifact["id"] or job["data"].get("status") != "succeeded":
        raise DomainError("RENDER_MAP_REQUIRED", "成片缺少已完成的渲染回执。", 409)
    manifest = record(actor, job["data"]["render_manifest_id"], "RenderManifest", project=project)
    mapping = record(actor, manifest["data"]["time_map_id"], "TimeMap", project=project)
    if (
        manifest["data"].get("output_hash") != data["content_hash"]
        or manifest["data"].get("artifact_id") != artifact["id"]
        or mapping["data"].get("output_ref") != ref(artifact)
        or mapping["data"].get("mapping_stage") != "realized"
    ):
        raise DomainError("RENDER_MAP_REQUIRED", "渲染清单与该最终成片不一致。", 409)
    return mapping, [post, event, receipt, account, artifact, manifest, mapping]


def interval_contributors(segments, lo, hi):
    """Use realized microsecond boundaries only; never split a platform metric between layers."""
    rows = []
    for segment in segments:
        begin, end = segment["output_interval"]
        a, b = max(begin, lo), min(end, hi)
        if a >= b:
            continue
        for layer in segment.get("contributions", []):
            curve = layer.get("mapping_curve", {}).get("kind")
            if curve not in {"linear", "hold", "constant"}:
                raise DomainError(
                    "UNSUPPORTED_RENDER_MAP", "当前仅支持本地渲染的线性或定格时间段。", 409
                )
            start, stop = layer["source_interval"]
            rate = (stop - start) / (end - begin) if curve == "linear" else 0
            rows.append(
                {
                    "source_ref": layer["source_ref"],
                    "clip_use_id": layer["clip_use_id"],
                    "role": layer["role"],
                    "output_interval_seconds": [a / 1e6, b / 1e6],
                    "source_interval_seconds": [
                        (start + (a - begin) * rate) / 1e6,
                        (start + (b - begin) * rate) / 1e6,
                    ],
                    "mapping_quality": layer.get("precision", "unknown"),
                    "occlusion_evaluated": layer.get("occlusion_evaluated", False),
                }
            )
    return rows


@router.post("/retention-mapping")
def map_retention(body: RetentionMapping, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        post = live(actor, body.post, "Post", body.project_id)
        mapping, dependencies = final_mapping(actor, body.project_id, post)
        start, end, seen = map(timestamp, (body.window_start, body.window_end, body.observed_at))
        published = timestamp(post["data"].get("actual_published_at"))
        if not (published <= start < end <= seen <= datetime.now(timezone.utc)):
            raise DomainError(
                "RETENTION_WINDOW", "窗口须在发布后，观测须在窗口结束后且不晚于当前时刻。"
            )
        data = mapping["data"]
        microseconds = {"num": 1, "den": 1000000}
        if data.get("target_timebase") != microseconds or any(
            t != microseconds for t in data.get("source_timebase", {}).values()
        ):
            raise DomainError("UNSUPPORTED_RENDER_MAP", "不支持该渲染时间映射的时基。", 409)
        segments = data.get("segments") or []
        points = [(round(p.at_seconds * 1e6), p.retained) for p in body.points]
        if not segments or points[-1][0] > segments[-1]["output_interval"][1]:
            raise DomainError("RETENTION_TIME_RANGE", "观测位置超出该最终成片时长。")
        intervals = []
        for (lo, first), (hi, last) in zip(points, points[1:]):
            if lo >= hi or not last <= first <= body.denominator:
                raise DomainError(
                    "RETENTION_COHORT",
                    "首次播放同一队列人数须非递增且不超过分母；观测位置须严格递增。",
                )
            contributors = interval_contributors(segments, lo, hi)
            intervals.append(
                {
                    "start_seconds": lo / 1e6,
                    "end_seconds": hi / 1e6,
                    "start_retained": first,
                    "end_retained": last,
                    "change_percentage_points": 100 * (last - first) / body.denominator,
                    "contributors": contributors,
                    "scope": "observed_output_interval",
                }
            )
        worst = min(intervals, key=lambda x: x["change_percentage_points"])
        summary = {
            "largest_observed_drop": {
                k: worst[k] for k in ("start_seconds", "end_seconds", "change_percentage_points")
            },
            "overlapping_sources": list(
                dict.fromkeys(c["source_ref"]["id"] for c in worst["contributors"])
            ),
            "denominator": body.denominator,
            "traffic": body.traffic,
            "observed_at": body.observed_at,
        }
        inputs = body.model_dump(exclude={"request_id", "project_id"})
        insight = save(
            actor,
            "Insight",
            body.project_id,
            {
                "name": "首播留存与成片区间",
                "analysis_dataset_hash": digest(inputs),
                "scope": {"level": "post_interval", "clip_attribution": "co_occurrence_only"},
                "observed_curve": inputs,
                "retention_summary": summary,
                "intervals": intervals,
                "verification": "user_supplied_platform_observations",
                "causal_claim": False,
                "uncertainty": "人数为同一首播队列的人工输入。源时间依本地渲染映射，未测遮挡与单镜头曝光。重叠素材共享同一观测。",
            },
            [ref(x) for x in dependencies],
            "固定原始留存与最终媒体时间映射",
        )
        return {"insight": insight}

    return ok(on_request("retention-mapping", body, actor, apply), request)
