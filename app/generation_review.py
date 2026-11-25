"""Human assessment of a pinned generator input and decoded candidate at an observed PTS."""

from fractions import Fraction
from pathlib import Path
from hashlib import sha256
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_approval, actor_still_authorized
from .errors import DomainError
from .generation_frames import checked_time, decode
from .media import check_current_media_rights, resolve_media
from .production import digest, record, ref, revise
from .selection import SelectionBody, Label, Note, on_request
from .store import get_store

router = APIRouter()
PREFIX = "generation_review_"


def audit_id(actor, job):
    return PREFIX + digest([actor.tenant_id, job["project_id"], job["id"]])


def audit_head(actor, job):
    try:
        return record(actor, audit_id(actor, job), "Audit", project=job["project_id"])
    except DomainError as exc:
        if exc.code != "NOT_FOUND":
            raise
        return None


def attach_heads(actor, project, jobs):
    if not jobs:
        return []
    by_id = {audit_id(actor, j): j for j in jobs if j["data"].get("reference_input")}
    heads = {}
    ids = list(by_id)
    with get_store().connection() as conn:
        for start in range(0, len(ids), 350):
            part = ids[start : start + 350]
            query = """SELECT r.id,r.latest_version,v.data_json FROM records r JOIN revisions v
              ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
              WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Audit' AND r.id IN ("""
            for row in conn.execute(
                query + ",".join("?" for _ in part) + ")", [actor.tenant_id, project, *part]
            ):
                d = json.loads(row["data_json"])
                heads[by_id[row["id"]]["id"]] = {
                    "version": row["latest_version"],
                    **{k: d[k] for k in ("decision", "reason", "time_seconds")},
                }
    return [{**j, "visual_review": heads[j["id"]]} if j["id"] in heads else j for j in jobs]


def ensure_use_allowed(conn, tenant, media):
    """Review applies to the actual candidate bytes, including descendants that consume them."""
    sha = media["data"].get("content_hash")
    if not sha:
        return
    row = conn.execute(
        """SELECT r.id FROM reference_index link
        JOIN records r ON r.tenant_id=link.tenant_id AND r.id=link.source_id AND r.latest_version=link.source_version
        JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
        WHERE link.tenant_id=? AND link.target_id=? AND link.field_name='visual_review_subject'
        AND r.kind='Audit' AND r.id GLOB 'generation_review_*'
        AND json_extract(v.data_json,'$.candidate_sha256')=?
        AND json_extract(v.data_json,'$.decision') IN ('reject','needs_revision') LIMIT 1""",
        (tenant, media["id"], sha),
    ).fetchone()
    if row:
        raise DomainError(
            "GENERATION_VISUAL_REVIEW_HOLD",
            "该生成素材的画面审阅尚未放行，请查看审阅理由或替换素材。",
            409,
            {"review_id": row["id"], "media_id": media["id"]},
        )


def media_pin(actor, project, pin):
    item, blob, path = resolve_media(
        get_store(), actor, pin["id"], project_id=project, purpose="inspect"
    )
    if ref(item) != pin:
        pinned = record(actor, pin["id"], pin["kind"], pin["version"], project)
        if pinned["data"].get("content_hash") != blob["data"]["checksum"]:
            raise DomainError("REVIEW_MEDIA_CHANGED", "原审阅版本与当前可见的媒体字节不一致。", 409)
        check_current_media_rights(get_store(), actor, pinned, purpose="inspect")
        item = pinned
    return item, blob, path


def output_origin(actor, job, asset):
    sid = asset["data"].get("source_id")
    if sid:
        origin = record(actor, sid, "Source", project=job["project_id"])
        return (origin["data"].get("generation_call_ref") or {}).get("id")
    return None


def separate_legacy_output(actor, job):
    """Rebind a historically deduplicated candidate only on an explicit refresh."""
    project = job["project_id"]
    asset_pin = job["data"]["asset_ref"]
    asset = record(actor, asset_pin["id"], "Asset", asset_pin["version"], project)
    if output_origin(actor, job, asset) == job["id"]:
        return job
    receipt = job["data"].get("import_entry_ref")
    if not receipt:
        raise DomainError("GENERATION_ORIGIN_UNVERIFIED", "缺少原生成导入回执，不能补记来源。", 409)
    entry = record(actor, receipt["id"], "ImportEntry", receipt["version"], project)
    data = entry["data"]
    origin = record(actor, data["source_id"], "Source", project=project)
    if (
        data.get("asset_id") != asset["id"]
        or data.get("content_hash") != asset["data"]["content_hash"]
        or (origin["data"].get("generation_call_ref") or {}).get("id") != job["id"]
    ):
        raise DomainError(
            "GENERATION_ORIGIN_UNVERIFIED", "存量回执未证明本任务生成了这些字节。", 409
        )
    store = get_store()
    with store.transaction():
        current = record(actor, job["id"], "ToolCall", project=project)
        if current["version"] != job["version"]:
            raise DomainError("REVISION_CONFLICT", "重新核对时任务版本已变化。", 409)
        _, blob, _ = media_pin(actor, project, asset_pin)
        version_pin = job["data"]["version_ref"]
        version = record(actor, version_pin["id"], "AssetVersion", version_pin["version"], project)
        if (
            blob["data"]["checksum"] != data["content_hash"]
            or version["data"].get("asset_id") != asset["id"]
            or version["data"].get("content_hash") != data["content_hash"]
        ):
            raise DomainError("GENERATION_ORIGIN_UNVERIFIED", "原导入回执摘要与当前内容不符。", 409)
        check_current_media_rights(store, actor, origin, purpose="inspect")
        scope = {"tenant_id": actor.tenant_id, "project_id": project, "actor_id": actor.actor_id}
        location = store.create("BlobLocation", blob["data"], refs=[ref(origin)], **scope)
        fields = {
            "name": Path(data["relative_path"]).stem,
            "filename": data["relative_path"],
            "content_hash": data["content_hash"],
            "size_bytes": data["size_bytes"],
            "media_type": asset["data"]["media_type"],
            "probe": asset["data"]["probe"],
            "blob_location_id": location["id"],
            "source_id": origin["id"],
            "status": "available",
            "rights_status": origin["data"].get("rights_status", "unverified"),
        }
        independent = store.create("Asset", fields, refs=[ref(origin), ref(location)], **scope)
        original = store.create(
            "AssetVersion",
            {
                "asset_id": independent["id"],
                "variant": "original",
                "filename": fields["filename"],
                "blob_location_id": location["id"],
                "content_hash": fields["content_hash"],
                "probe": fields["probe"],
                "status": "available",
                "parameter_origins": {},
                "original_immutable": True,
            },
            refs=[ref(independent), ref(location), ref(origin)],
            **scope,
        )
        return revise(
            actor,
            job,
            {
                "asset_ref": ref(independent),
                "version_ref": ref(original),
                "original_shared_asset_ref": asset_pin,
            },
            "由原始生成导入回执核实并区分曾按相同字节共用的来源。",
        )


def inputs(actor, project, jid):
    from .generation import load

    job = load(actor, jid)
    if job["project_id"] != project:
        raise DomainError("PROJECT_MISMATCH", "任务不属于当前项目。", 409)
    d = job["data"]
    r = d.get("reference_input") or {}
    wire_sha = r.get("segment_sha256") if r.get("kind") == "video_segment" else r.get("png_sha256")
    if (
        d.get("stage") != "ready"
        or not wire_sha
        or (d.get("reference_upload") or {}).get("verified_sha256") != wire_sha
    ):
        raise DomainError(
            "VISUAL_REVIEW_UNAVAILABLE", "须完成带参考图的生成并核实已传送字节。", 409
        )
    source, blob, source_path = media_pin(actor, project, r["asset_ref"])
    if blob["data"]["checksum"] != r["original_sha256"]:
        raise DomainError("REVIEW_MEDIA_CHANGED", "输入原片字节已变化。", 409)
    r = {**r, "is_still": r.get("kind") != "video_segment", "duration_seconds": 0.0}
    prepared = r.get("segment_ref") if r.get("kind") == "video_segment" else r.get("frame_ref")
    if r.get("kind") == "video_segment" and not prepared:
        raise DomainError("REVIEW_MEDIA_CHANGED", "缺少实际发送的参考视频版本。", 409)
    if prepared:
        reference_version, frame_blob, source_path = media_pin(actor, project, prepared)
        if not r["is_still"]:
            r["duration_seconds"] = reference_version["data"]["probe"]["duration"]
        if frame_blob["data"]["checksum"] != wire_sha:
            raise DomainError("REVIEW_MEDIA_CHANGED", "已发送参考帧字节已变化。", 409)
    asset, _, _ = media_pin(actor, project, d["asset_ref"])
    if output_origin(actor, job, asset) != job["id"]:
        raise DomainError(
            "GENERATION_ORIGIN_SHARED",
            "存量候选与其他来源共用素材。请先核对任务以恢复独立的生成来源。",
            409,
        )
    version, blob, path = media_pin(actor, project, d["version_ref"])
    if version["data"].get("asset_id") != asset["id"] or blob["data"]["checksum"] != asset[
        "data"
    ].get("content_hash"):
        raise DomainError("REVIEW_MEDIA_CHANGED", "生成结果与任务固定的原件不一致。", 409)
    probe = version["data"]["probe"]
    candidate = {
        "asset_ref": ref(asset),
        "version_ref": ref(version),
        "content_sha256": blob["data"]["checksum"],
        "width": probe["width"],
        "height": probe["height"],
        "duration_seconds": probe["duration"],
        "is_still": probe["is_still"],
    }
    return job, r, source_path, candidate, path


def reference_image(ri, path, at):
    from .generation import normalized_still

    if ri.get("kind") == "video_segment":
        checked_time({"has_video": True, "duration": ri["duration_seconds"]}, at)
        png, timing = decode(path, at, display_aspect=True)
        source_pts = ri.get("source_frame_pts_seconds") or []
        fps = ri.get("reference_fps")
        if fps:
            pos = (
                Fraction(timing["frame_pts_ticks"])
                * Fraction(timing["time_base"])
                * Fraction(str(fps))
            )
            index = round(pos)
            if abs(pos - index) <= Fraction(1, 1000) and 0 <= index < len(source_pts):
                timing["source_frame_pts_seconds"] = source_pts[index]
        return png, timing
    if at != 0:
        raise DomainError("REFERENCE_FRAME_RANGE", "参考静帧不包含其他显示时刻。")
    png = path.read_bytes() if ri.get("frame_ref") else normalized_still(path)
    if sha256(png).hexdigest() != ri["png_sha256"]:
        raise DomainError("REFERENCE_FRAME_CHANGED", "预览 PNG 与实际传给生成器的字节不一致。", 409)
    return png, {
        "frame_pts_seconds": ri.get("frame_pts_seconds"),
        "kind": "video_frame" if ri.get("frame_ref") else "still",
    }


def candidate_frame(candidate, path, at):
    from .generation import normalized_still

    if candidate["is_still"]:
        if at != 0:
            raise DomainError("REFERENCE_FRAME_RANGE", "静帧只能选择 0 秒。")
        return normalized_still(path, display_aspect=True), {
            "kind": "still",
            "frame_pts_seconds": 0,
        }
    checked_time({"has_video": True, "duration": candidate["duration_seconds"]}, at)
    return decode(path, at, display_aspect=True)


@router.get("/{jid}/visual-review")
def details(jid: str, project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    with get_store().read_snapshot():
        job, r, _, candidate, _ = inputs(actor, project_id, jid)
        head = audit_head(actor, job)
    return ok(
        {
            "job": ref(job),
            "reference": r,
            "candidate": candidate,
            "review_version": head["version"] if head else 0,
            "last_decision": head["data"] if head else None,
        },
        request,
    )


@router.get(
    "/{jid}/visual-review/reference.png",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def reference_png(
    jid: str,
    project_id: str,
    time_seconds: float = Query(0, ge=0, lt=86400, allow_inf_nan=False),
    actor: Actor = Depends(current_actor),
):
    _, r, path, _, _ = inputs(actor, project_id, jid)
    png, timing = reference_image(r, path, time_seconds)
    inputs(actor, project_id, jid)
    return Response(
        png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-SHA256": sha256(png).hexdigest(),
            "X-Frame-PTS": str(timing["frame_pts_seconds"] or 0),
            **(
                {"X-Source-Frame-PTS": str(timing["source_frame_pts_seconds"])}
                if "source_frame_pts_seconds" in timing
                else {}
            ),
        },
    )


@router.get(
    "/{jid}/visual-review/reference.mp4",
    response_class=FileResponse,
    responses={200: {"content": {"video/mp4": {}}}},
)
def reference_video(jid: str, project_id: str, actor: Actor = Depends(current_actor)):
    _, r, path, _, _ = inputs(actor, project_id, jid)
    if r.get("kind") != "video_segment":
        raise DomainError("REFERENCE_VIDEO_REQUIRED", "此生成任务使用静帧参考。", 422)
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store", "X-Reference-SHA256": r["segment_sha256"]},
    )


@router.get(
    "/{jid}/visual-review/candidate.png",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def candidate_png(
    jid: str,
    project_id: str,
    time_seconds: float = Query(0, ge=0, lt=86400, allow_inf_nan=False),
    actor: Actor = Depends(current_actor),
):
    _, _, _, candidate, path = inputs(actor, project_id, jid)
    png, frame = candidate_frame(candidate, path, time_seconds)
    inputs(actor, project_id, jid)
    return Response(
        png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-SHA256": sha256(png).hexdigest(),
            "X-Frame-PTS": str(frame["frame_pts_seconds"]),
        },
    )


class VisualDecision(SelectionBody):
    project_id: Label
    request_id: Label
    expected_job_version: int = Field(ge=1)
    expected_review_version: int = Field(ge=0)
    time_seconds: float = Field(ge=0, lt=86400)
    reference_time_seconds: float = Field(default=0, ge=0, lt=86400)
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_frame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accept", "reject", "needs_revision"]
    reason: Note


@router.post("/{jid}/visual-review")
def decide(jid: str, body: VisualDecision, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    job, r, source_path, candidate, path = inputs(actor, body.project_id, jid)
    reference_png_bytes, reference_timing = reference_image(
        r, source_path, body.reference_time_seconds
    )
    png, frame = candidate_frame(candidate, path, body.time_seconds)
    if (
        sha256(reference_png_bytes).hexdigest() != body.reference_sha256
        or sha256(png).hexdigest() != body.candidate_frame_sha256
    ):
        raise DomainError(
            "VISUAL_REVIEW_PIXELS_CHANGED", "提交的图片摘要与实际核验字节不一致，请重新对照。", 409
        )

    def apply():
        if not actor_still_authorized(actor, frozenset({"owner", "reviewer"})):
            raise DomainError("IDENTITY_REVOKED", "审阅身份已撤销。", 403)
        current, current_r, _, current_candidate, _ = inputs(actor, body.project_id, jid)
        previous = audit_head(actor, current)
        revision = previous["version"] if previous else 0
        if (
            current["version"] != body.expected_job_version
            or ref(current) != ref(job)
            or current_r != r
            or candidate != current_candidate
            or revision != body.expected_review_version
        ):
            raise DomainError(
                "VISUAL_REVIEW_CONFLICT", "任务、素材或审阅已更改，请刷新后重新判断。", 409
            )
        data = {
            "name": "生成候选画面审阅",
            "feature_id": "G05",
            "job_ref": ref(job),
            "candidate_ref": candidate["version_ref"],
            "candidate_sha256": candidate["content_sha256"],
            "reference_asset_ref": r["asset_ref"],
            "reference_frame_ref": r.get("frame_ref"),
            "reference_sha256": body.reference_sha256,
            "reference_wire_sha256": r.get("segment_sha256") or r["png_sha256"],
            "reference_segment_ref": r.get("segment_ref"),
            "reference_time_seconds": body.reference_time_seconds,
            "reference_frame_pts_seconds": reference_timing["frame_pts_seconds"],
            "reference_timing": reference_timing,
            "time_seconds": body.time_seconds,
            "candidate_frame_pts_seconds": frame["frame_pts_seconds"],
            "candidate_frame_timing": frame,
            "candidate_frame_sha256": body.candidate_frame_sha256,
            "decision": body.decision,
            "reason": body.reason,
            "principal": actor.actor_id,
        }
        refs = [
            ref(job),
            r["asset_ref"],
            *([r["frame_ref"]] if r.get("frame_ref") else []),
            *([r["segment_ref"]] if r.get("segment_ref") else []),
            *[
                {**p, "field": "visual_review_subject"}
                for p in [candidate["asset_ref"], candidate["version_ref"]]
            ],
        ]
        audit = (
            revise(actor, previous, data, body.reason, refs)
            if previous
            else get_store().create(
                "Audit",
                data,
                tenant_id=actor.tenant_id,
                project_id=body.project_id,
                actor_id=actor.actor_id,
                refs=refs,
                record_id=audit_id(actor, job),
                reason=body.reason,
            )
        )
        return {"audit": audit, "review_version": audit["version"]}

    return ok(on_request("generation-visual-review-" + jid, body, actor, apply), request)
