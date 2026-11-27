"""Submit an administrator-frozen local ComfyUI video workflow and reconcile its actual output."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import urlparse
from typing import Literal
import uuid

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import Field, StrictInt

from . import generation_frames, generation_review, generation_segments
from .api import ok
from .auth import Actor, current_actor
from .auto_edits import producer, ensure_lease, require_live, load_session
from .errors import DomainError
from .media import (
    MAX_BYTES,
    check_current_media_rights,
    import_saved_file,
    probe_media,
    technical_qc,
    run_process,
    resolve_media,
    media_mime,
)
from .production import Body, digest, record, ref, revise, save
from .store import get_store

router = APIRouter(prefix="/production/generation", tags=["local-video-generation"])
router.include_router(generation_review.router)
OPERATIONS = {"video": "comfy_video_generation", "image": "comfy_image_generation"}
STILL_CODECS = {".png": "png", ".jpg": "mjpeg", ".jpeg": "mjpeg", ".webp": "webp"}
FINAL = {"ready", "failed", "cancelled"}


class Generate(Body):
    project_id: str
    source_run_id: str | None = None
    profile_id: str
    reference_asset_id: str | None = Field(default=None, min_length=1)
    reference_asset_version: StrictInt | None = Field(default=None, ge=1)
    reference_time_seconds: float | None = Field(default=None, strict=True, ge=0, lt=3600)
    reference_end_seconds: float | None = Field(default=None, strict=True, gt=0, le=3600)
    prompt: str = Field(min_length=1, max_length=6000)
    seed: StrictInt = Field(ge=0, le=2147483647)
    slot: str = Field(default="", max_length=160)
    idempotency_key: str = Field(min_length=8, max_length=150)


def has_dependency(graph, output_node, input_node):
    """Check that the reference node can reach the selected output through actual Comfy links."""
    pending = [output_node]
    visited = set()
    while pending:
        node = pending.pop()
        if node == input_node:
            return True
        if node in visited:
            continue
        visited.add(node)
        for value in graph[node]["inputs"].values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in graph
                and type(value[1]) is int
            ):
                pending.append(value[0])
    return False


def profiles():
    try:
        raw = json.loads(os.getenv("AI_SHIP_COMFY_PROFILES") or "{}")
        if not isinstance(raw, dict):
            raise ValueError()
        for cfg in raw.values():
            if not isinstance(cfg, dict):
                raise ValueError()
            uri = urlparse(cfg["base_url"])
            if (
                uri.scheme != "http"
                or uri.hostname not in {"127.0.0.1", "::1"}
                or any((uri.username, uri.password, uri.query, uri.fragment))
            ):
                raise ValueError()
            workflow = cfg["workflow"]
            if (
                not isinstance(cfg["model"], str)
                or not cfg["model"].strip()
                or not isinstance(workflow, dict)
                or not workflow
            ):
                raise ValueError()
            if cfg.get("output_kind", "video") not in OPERATIONS:
                raise ValueError()
            if str(cfg["output_node"]) not in workflow:
                raise ValueError()
            for field in ("text_input", "seed_input"):
                pair = cfg[field]
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not all(isinstance(s, str) for s in pair)
                ):
                    raise ValueError()
                node, key = pair
                value = workflow[node]["inputs"][key]
                expected = str if field == "text_input" else int
                if type(value) is not expected or not has_dependency(
                    workflow, str(cfg["output_node"]), node
                ):
                    raise ValueError()
            if "video_input" in cfg:
                if (
                    "image_input" in cfg
                    or type(cfg.get("reference_fps")) is not int
                    or not 1 <= cfg["reference_fps"] <= 30
                    or type(cfg.get("max_reference_frames")) is not int
                    or not 2 <= cfg["max_reference_frames"] <= 32
                    or cfg.get("output_kind", "video") != "video"
                ):
                    raise ValueError()
            if "image_input" in cfg or "video_input" in cfg:
                video = "video_input" in cfg
                binding = cfg["video_input" if video else "image_input"]
                if not isinstance(binding, list) or len(binding) != 2:
                    raise ValueError()
                node, key = binding
                if (
                    not isinstance(node, str)
                    or key != ("file" if video else "image")
                    or cfg["text_input"] == binding
                    or workflow[node]["class_type"] != ("LoadVideo" if video else "LoadImage")
                    or not isinstance(workflow[node]["inputs"][key], str)
                    or node == str(cfg["output_node"])
                    or not has_dependency(workflow, str(cfg["output_node"]), node)
                ):
                    raise ValueError()
        return raw
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DomainError(
            "GENERATOR_CONFIG_INVALID", "本地生成工作流配置不完整，需核对节点、输入和输出。", 503
        )


def profile(pid):
    cfg = profiles().get(pid)
    if not cfg:
        raise DomainError("GENERATOR_UNAVAILABLE", "尚未登记此视频生成工作流。", 503)
    return cfg


@router.get("/profiles")
def available(request: Request, actor: Actor = Depends(current_actor)):
    return ok(
        {
            "profiles": [
                {
                    "id": key,
                    "label": cfg.get("label") or key,
                    "model": cfg["model"],
                    "output_kind": cfg.get("output_kind", "video"),
                    "requires_reference": "image_input" in cfg or "video_input" in cfg,
                    **(
                        {
                            "reference_kind": "video_segment",
                            "reference_fps": cfg["reference_fps"],
                            "max_reference_frames": cfg["max_reference_frames"],
                        }
                        if "video_input" in cfg
                        else {}
                    ),
                    "status": "registered_not_probed",
                }
                for key, cfg in profiles().items()
            ]
        },
        request,
    )


def load(actor, id):
    job = record(actor, id, "ToolCall")
    if job["data"].get("operation_kind") not in OPERATIONS.values():
        raise DomainError("GENERATION_NOT_FOUND", "不是视频生成任务。", 404)
    return job


def result(actor, job):
    pin = job["data"].get("asset_ref")
    return {
        "job": generation_review.attach_heads(actor, job["project_id"], [job])[0],
        "asset": (
            record(actor, pin["id"], "Asset", pin["version"], job["project_id"]) if pin else None
        ),
    }


def update(actor, job, stage, issue=None, **extra):
    return revise(actor, job, {"stage": stage, "issue": issue, **extra}, "记录生成任务及来源回执")


def issue(code, message):
    return {"code": code, "message": message}


def client(cfg):
    return httpx.Client(
        base_url=cfg["base_url"].rstrip("/") + "/",
        timeout=httpx.Timeout(25, connect=4),
        trust_env=False,
        follow_redirects=False,
    )


@router.get("")
def jobs(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    record(actor, project_id, "Project")
    items = get_store().list(
        actor.tenant_id, kind="ToolCall", project_id=project_id, q="comfy_", limit=2000
    )
    return ok(
        {
            "jobs": generation_review.attach_heads(
                actor,
                project_id,
                [r for r in items if r["data"].get("operation_kind") in OPERATIONS.values()],
            )
        },
        request,
    )


@router.get("/reference/assets")
def reference_assets(
    project_id: str,
    media_type: Literal["image", "video"],
    request: Request,
    q: str = Query("", max_length=300),
    after: str = Query("", max_length=180),
    limit: int = Query(50, ge=1, le=100),
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    store = get_store()
    kind = (
        "json_extract(v.data_json,'$.probe.is_still')=1"
        if media_type == "image"
        else "json_extract(v.data_json,'$.probe.has_video')=1 AND coalesce(json_extract(v.data_json,'$.probe.is_still'),0)=0"
    )
    where = [
        "r.tenant_id=?",
        "r.project_id=?",
        "r.kind='Asset'",
        "json_type(v.data_json,'$.blob_location_id')='text'",
        kind,
    ]
    params = [actor.tenant_id, project_id]
    if q.strip():
        where.append(
            "(instr(lower(r.id),lower(?)) OR instr(lower(coalesce(json_extract(v.data_json,'$.name'),'')),lower(?)) OR instr(lower(coalesce(json_extract(v.data_json,'$.filename'),'')),lower(?)))"
        )
        params.extend([q.strip()] * 3)
    with store.read_snapshot() as db:
        if after:
            anchor = db.execute(
                "SELECT created_at FROM records WHERE tenant_id=? AND project_id=? AND kind='Asset' AND id=?",
                (actor.tenant_id, project_id, after),
            ).fetchone()
            if not anchor:
                raise DomainError("REFERENCE_CURSOR", "分页位置不属于当前项目。", 422)
            where.append("(r.created_at < ? OR (r.created_at=? AND r.id<?))")
            params.extend([anchor[0], anchor[0], after])
        rows = db.execute(
            "SELECT r.id FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version WHERE "
            + " AND ".join(where)
            + " ORDER BY r.created_at DESC,r.id DESC LIMIT ?",
            [*params, limit + 1],
        ).fetchall()
        items = []
        for row in rows[:limit]:
            asset = store.get(row["id"], actor.tenant_id)
            if asset["data"].get("storage_tier") in {"local_cold", "quarantine"}:
                continue
            try:
                check_current_media_rights(store, actor, asset, purpose="process")
            except DomainError:
                continue
            items.append(asset)
        return ok(
            {
                "items": items,
                "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
            },
            request,
        )


def pinned_reference(actor, project, pin):
    if not pin:
        return
    item, blob, path = resolve_media(get_store(), actor, pin["id"], project_id=project)
    if item["kind"] != "Asset":
        raise DomainError("REFERENCE_ASSET_REQUIRED", "参考输入须选取本项目的原件素材。", 422)
    if item["version"] != pin["version"]:
        raise DomainError(
            "REFERENCE_REVISION_CHANGED", "参考素材修订已变化，请核对原件并重新选定版本。", 409
        )
    return item, blob, path


def reference_filename(actor, project, sha, suffix=".png"):
    return "atlas-ref-" + digest([actor.tenant_id, project])[:16] + "-" + sha + suffix


@router.get(
    "/reference/frame", response_class=Response, responses={200: {"content": {"image/png": {}}}}
)
def preview_frame(
    project_id: str,
    asset_id: str,
    version: int = Query(ge=1),
    time_seconds: float = Query(ge=0, lt=3600, allow_inf_nan=False),
    actor: Actor = Depends(current_actor),
):
    require_live(actor)
    item, blob, path = pinned_reference(actor, project_id, {"id": asset_id, "version": version})
    generation_frames.checked_time(item["data"]["probe"], time_seconds)
    evidence, png = generation_frames.pixels(
        actor, project_id, item, blob, path, time_seconds, persist=False
    )
    pinned_reference(actor, project_id, ref(item))
    return Response(
        png,
        media_type="image/png",
        headers={
            "X-Frame-PTS": str(evidence["frame_pts_seconds"]),
            "X-Frame-SHA256": evidence["png_sha256"],
        },
    )


def bind_http(http, actor, job, *, cancel=False):
    def guard(request):
        require_live(actor)
        if request.method == "POST" and request.url.path.rstrip("/").endswith(
            ("/prompt", "/upload/image")
        ):
            active = load(actor, job["id"])["data"]
            if active.get("cancel_requested") or active["stage"] in FINAL:
                raise DomainError(
                    "GENERATION_CANCELLED_BEFORE_PROMPT", "任务已停止，未继续提交推理。", 409
                )
        if digest(profile(job["data"]["profile_id"])) != job["data"]["profile_hash"]:
            raise DomainError("GENERATOR_PROFILE_CHANGED", "生成配置已变化。", 409)
        if not cancel:
            p = job["data"].get("reference_input") or {}
            if p:
                _, blob, _ = pinned_reference(actor, job["project_id"], p["asset_ref"])
                if blob["data"]["checksum"] != p["original_sha256"]:
                    raise DomainError("REFERENCE_REVISION_CHANGED", "参考原件指纹已变化。", 409)
                if p.get("segment_ref"):
                    clip, b, _ = resolve_media(
                        get_store(), actor, p["segment_ref"]["id"], project_id=job["project_id"]
                    )
                    if (
                        ref(clip) != p["segment_ref"]
                        or b["data"]["checksum"] != p["segment_sha256"]
                    ):
                        raise DomainError(
                            "REFERENCE_SEGMENT_CHANGED", "参考区间修订或指纹已变化。", 409
                        )
                if p.get("frame_ref"):
                    frame, b, _ = resolve_media(
                        get_store(), actor, p["frame_ref"]["id"], project_id=job["project_id"]
                    )
                    if ref(frame) != p["frame_ref"] or b["data"]["checksum"] != p["png_sha256"]:
                        raise DomainError(
                            "REFERENCE_FRAME_CHANGED", "参考帧版本或指纹已变化。", 409
                        )

    http.event_hooks["request"].append(guard)


def prepare_reference(actor, cfg, body):
    selected = (
        body.reference_asset_id is not None
        or body.reference_asset_version is not None
        or body.reference_time_seconds is not None
        or body.reference_end_seconds is not None
    )
    if "image_input" not in cfg and "video_input" not in cfg:
        if selected:
            raise DomainError("REFERENCE_UNSUPPORTED", "此生成工作流未配置参考输入。", 422)
        return None, None
    if not body.reference_asset_id or not body.reference_asset_version:
        raise DomainError("REFERENCE_REQUIRED", "此工作流需要指定本项目参考原件及其准确修订。", 422)
    pin = {"id": body.reference_asset_id, "version": body.reference_asset_version}
    item, blob, path = pinned_reference(actor, body.project_id, pin)
    if "video_input" in cfg:
        evidence, data = generation_segments.prepare(
            actor,
            body.project_id,
            item,
            blob,
            path,
            body.reference_time_seconds,
            body.reference_end_seconds,
            cfg,
        )
        return {
            **evidence,
            "filename": reference_filename(
                actor, body.project_id, evidence["segment_sha256"], ".mp4"
            ),
        }, data
    if body.reference_end_seconds is not None:
        raise DomainError("REFERENCE_SEGMENT_UNSUPPORTED", "此配置仅接受单帧参考。", 422)
    if body.reference_time_seconds is not None:
        generation_frames.checked_time(item["data"].get("probe", {}), body.reference_time_seconds)
        evidence, png = generation_frames.pixels(
            actor, body.project_id, item, blob, path, body.reference_time_seconds, persist=True
        )
        return {
            **evidence,
            "filename": reference_filename(actor, body.project_id, evidence["png_sha256"]),
        }, png
    png = normalized_still(path)
    sha = hashlib.sha256(png).hexdigest()
    filename = reference_filename(actor, body.project_id, sha)
    return {
        "asset_ref": ref(item),
        "original_sha256": blob["data"]["checksum"],
        "png_sha256": sha,
        "width": int.from_bytes(png[16:20], "big"),
        "height": int.from_bytes(png[20:24], "big"),
        "bytes": len(png),
        "filename": filename,
    }, png


def normalized_still(path, *, display_aspect=False):
    probe = probe_media(path)
    if not probe["is_still"] or probe["video_codec"] not in {"png", "mjpeg", "webp", "bmp"}:
        raise DomainError("REFERENCE_IMAGE_REQUIRED", "参考素材须是可验证的单帧原图。", 422)
    if probe["width"] * probe["height"] > 64 * 1024 * 1024:
        raise DomainError("REFERENCE_IMAGE_LIMIT", "参考图超过本地生成器可解码像素上限。", 422)
    check_single_frame(path)
    # Normalise decoded pixels only, excluding metadata, file paths and original names.
    png, _ = run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map_metadata",
            "-1",
            "-frames:v",
            "1",
            "-vf",
            generation_frames.preview_scale(display_aspect),
            "-pix_fmt",
            "rgba",
            "-c:v",
            "png",
            "-threads",
            "1",
            "-f",
            "image2pipe",
            "pipe:1",
        ],
        timeout=30,
        binary_output=True,
    )
    if len(png) > 50 * 1024 * 1024 or png[:8] != bytes.fromhex("89504e470d0a1a0a"):
        raise DomainError("REFERENCE_IMAGE_LIMIT", "规范化参考图片超过工作器容量或编码无效。", 422)
    return png


def upload_reference(http, reference, data):
    name = reference["filename"]
    try:
        response = http.post(
            "upload/image",
            data={"type": "input", "overwrite": "false"},
            files={
                "image": (
                    name,
                    data,
                    "video/mp4" if reference.get("segment_sha256") else "image/png",
                )
            },
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or any(
            value.get(k) != v for k, v in {"name": name, "subfolder": "", "type": "input"}.items()
        ):
            raise ValueError("wrong ComfyUI reference descriptor")
        # A matching descriptor alone cannot prove that the file served to the reference node is intact.
        params = {"filename": name, "type": "input", "subfolder": ""}
        received = hashlib.sha256()
        size = 0
        with http.stream("GET", "view", params=params) as stored:
            stored.raise_for_status()
            for chunk in stored.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > len(data):
                    raise ValueError("unexpected reference size")
                received.update(chunk)
        if size != len(data) or received.hexdigest() != (
            reference.get("segment_sha256") or reference["png_sha256"]
        ):
            raise ValueError("reference pixels did not round-trip")
        return {
            "descriptor": {"name": name, "subfolder": "", "type": "input"},
            "verified_sha256": received.hexdigest(),
            "verified_bytes": size,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise DomainError(
            "REFERENCE_UPLOAD_FAILED", "参考文件上传回执无效，未提交推理。", 422
        ) from exc


def workflow_for(cfg, body, reference=None):
    graph = deepcopy(cfg["workflow"])
    for mapping, value in (("text_input", body.prompt), ("seed_input", body.seed)):
        node, param = cfg[mapping]
        graph[node]["inputs"][param] = value
    if reference:
        node, param = cfg.get("video_input") or cfg["image_input"]
        graph[node]["inputs"][param] = reference["filename"]
    return graph


@router.post("")
def generate(body: Generate, request: Request, actor: Actor = Depends(current_actor)):
    require_live(actor)
    record(actor, body.project_id, "Project")
    store = get_store()
    rid = "gen_" + digest([actor.tenant_id, body.project_id, body.idempotency_key])[:36]
    # Preserve request hashes created before optional image references were introduced.
    omitted = {
        k
        for k in (
            "reference_asset_id",
            "reference_asset_version",
            "reference_time_seconds",
            "reference_end_seconds",
        )
        if getattr(body, k) is None
    }
    payload_hash = digest(body.model_dump(exclude=omitted))

    def existing():
        try:
            stored = load(actor, rid)
        except DomainError as exc:
            if exc.code != "NOT_FOUND":
                raise
            return None
        if stored["data"]["request_hash"] != payload_hash:
            raise DomainError("IDEMPOTENCY_CONFLICT", "此生成键已有不同的提示词或参数。", 409)
        return ok(result(actor, stored), request)

    replay = existing()
    if replay:
        return replay
    cfg = profile(body.profile_id)
    reference, png = prepare_reference(actor, cfg, body)
    graph = workflow_for(cfg, body, reference)
    scope_parts = [body.project_id, cfg["base_url"], graph]
    if reference:
        scope_parts.append(reference["asset_ref"])
    scope = digest(scope_parts)
    with store.transaction():
        replay = existing()
        if replay:
            return replay
        with store.connection() as c:
            pending = c.execute(
                """SELECT r.id FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id
                AND v.version=r.latest_version WHERE r.tenant_id=? AND r.project_id=? AND r.kind='ToolCall'
                AND json_extract(v.data_json,'$.generation_scope')=? AND
                json_extract(v.data_json,'$.stage') NOT IN ('ready','cancelled','failed') LIMIT 1""",
                (actor.tenant_id, body.project_id, scope),
            ).fetchone()
        if pending:
            raise DomainError(
                "GENERATION_RECONCILE_FIRST",
                "相同输入已有在途生成请求，请先核对回执。",
                409,
                {"job_id": pending["id"]},
            )
        pins = []
        if body.source_run_id:
            parent = load_session(actor, body.source_run_id)
            if parent["project_id"] != body.project_id:
                raise DomainError("PROJECT_MISMATCH", "制作来源属于其他项目。", 409)
            pins = [ref(parent)]
            if parent["data"].get("plan_ref"):
                pins.append(parent["data"]["plan_ref"])
        if reference:
            pins.append(reference["asset_ref"])
            if reference.get("segment_ref"):
                pins.append(reference["segment_ref"])
            if reference.get("frame_ref"):
                pins.append(reference["frame_ref"])
        reqid = str(uuid.uuid4())
        job = store.create(
            "ToolCall",
            {
                "name": body.slot.strip() or body.prompt.strip()[:100],
                "operation_kind": OPERATIONS[cfg.get("output_kind", "video")],
                "output_kind": cfg.get("output_kind", "video"),
                "stage": "submitting",
                "source_run_id": body.source_run_id,
                "issue": None,
                "request_hash": payload_hash,
                "reference_asset_ref": reference["asset_ref"] if reference else None,
                "reference_input": reference,
                "generation_scope": scope,
                "profile_id": body.profile_id,
                "model": cfg["model"],
                "prompt": body.prompt,
                "seed": body.seed,
                "slot": body.slot,
                "graph_hash": digest(graph),
                "profile_hash": digest(cfg),
                "requested_prompt_id": reqid,
                "remote_id": None,
                "client_id": str(uuid.uuid4()),
                "output_node": str(cfg["output_node"]),
                "billing_scope": "self_hosted_compute",
                "charge": None,
            },
            tenant_id=actor.tenant_id,
            project_id=body.project_id,
            actor_id=actor.actor_id,
            record_id=rid,
            refs=pins,
        )
    try:
        require_live(actor)
        with client(cfg) as http:
            bind_http(http, actor, job)
            if reference:
                pinned_reference(actor, body.project_id, reference["asset_ref"])
                receipt = upload_reference(http, reference, png)
                with store.transaction():
                    current = load(actor, rid)
                    job = current
                    if current["data"]["stage"] not in FINAL:
                        job = update(
                            actor, current, current["data"]["stage"], reference_upload=receipt
                        )
                require_live(actor)
                pinned_reference(actor, body.project_id, reference["asset_ref"])
                if job["data"].get("cancel_requested") or job["data"]["stage"] in FINAL:
                    raise DomainError(
                        "GENERATION_CANCELLED_BEFORE_PROMPT",
                        "任务在参考文件准备期间取消，未提交推理。",
                        409,
                    )
            response = http.post(
                "prompt",
                json={"prompt": graph, "prompt_id": reqid, "client_id": job["data"]["client_id"]},
            )
        if response.status_code in {400, 401, 403, 404, 422}:
            changes = (
                "failed",
                issue(
                    "WORKFLOW_REJECTED",
                    f"本地工作流返回 HTTP {response.status_code}，请核对节点与权重。",
                ),
                {"provider_http_status": response.status_code},
            )
        else:
            response.raise_for_status()
            value = response.json()
            remote = value.get("prompt_id")
            if (
                not isinstance(remote, str)
                or not remote
                or len(remote) > 160
                or not all(c.isalnum() or c in "-_" for c in remote)
            ):
                raise ValueError("missing valid prompt id")
            changes = ("queued", None, {"remote_id": remote})
    except DomainError as e:
        stage = "cancelled" if e.code == "GENERATION_CANCELLED_BEFORE_PROMPT" else "failed"
        changes = (stage, issue(e.code, e.message), {"prompt_submitted": False})
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        changes = (
            "outcome_unknown",
            issue("SUBMIT_OUTCOME_UNKNOWN", "没有收到可验证的排队回执，需核对原任务，切勿重发。"),
            {},
        )
    with store.transaction():
        current = load(actor, rid)
        if current["data"]["stage"] not in FINAL and current["data"]["stage"] in {
            "submitting",
            "outcome_unknown",
            "cancel_requested",
        }:
            stage, message, fields = changes
            job = update(
                actor,
                current,
                (
                    "cancel_requested"
                    if current["data"].get("cancel_requested") and stage == "queued"
                    else stage
                ),
                message,
                **fields,
            )
        else:
            job = current
    return ok(result(actor, job), request)


def json_reply(http, path):
    response = http.get(path)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("invalid response")
    return value


def queue_row(row, data):
    # Comfy queue entries carry prompt_id at index 1 and extra_data.client_id at index 3.
    # Some versions hide extra_data, so a server-returned/requested id is also matched.
    if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], str):
        return False
    return row[1] in {data["remote_id"], data["requested_prompt_id"]} or (
        len(row) > 3 and isinstance(row[3], dict) and row[3].get("client_id") == data["client_id"]
    )


def locate(http, data):
    remote = data["remote_id"] or data["requested_prompt_id"]
    history = json_reply(http, "history/" + remote)
    if isinstance(history.get(remote), dict):
        return remote, "history", history[remote]
    queue = json_reply(http, "queue")
    for name, stage in (("queue_running", "running"), ("queue_pending", "queued")):
        rows = queue.get(name)
        if not isinstance(rows, list):
            raise ValueError("missing queue lists")
        for row in rows:
            if queue_row(row, data):
                return row[1], stage, None
    if not data["remote_id"]:
        history = json_reply(http, "history?max_items=100")
        for remote, item in history.items():
            if isinstance(item, dict) and queue_row(item.get("prompt"), data):
                return remote, "history", item
    return remote, "unknown", None


def output_descriptor(item, node, kind):
    outputs = item.get("outputs", {}).get(node, {})
    if not isinstance(outputs, dict):
        raise ValueError("missing outputs")
    choices = []
    for key in ("video", "videos", "gifs", "images"):
        for entry in outputs.get(key, []) if isinstance(outputs.get(key), list) else []:
            if not isinstance(entry, dict) or entry.get("type") != "output":
                continue
            filename, sub = entry.get("filename"), entry.get("subfolder", "")
            if (
                not isinstance(filename, str)
                or not filename
                or filename != PurePosixPath(filename).name
                or "\\" in filename
                or not isinstance(sub, str)
                or PurePosixPath(sub).is_absolute()
                or ".." in PurePosixPath(sub).parts
                or "\\" in sub
            ):
                raise ValueError("bad output reference")
            suffixes = set(STILL_CODECS) if kind == "image" else {".mp4", ".webm", ".mov", ".mkv"}
            if Path(filename).suffix.lower() in suffixes:
                choice = {"filename": filename, "subfolder": sub, "type": "output"}
                if choice not in choices:
                    choices.append(choice)
    if len(choices) != 1:
        raise DomainError("OUTPUT_AMBIGUOUS", "指定节点须产生一个类型相符的有效输出，未入库。", 422)
    return choices[0]


def check_single_frame(path):
    # A container probing as an image does not establish that it contains only a single frame.
    decoded, _ = run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "2",
            "-fps_mode",
            "passthrough",
            "-f",
            "framemd5",
            "pipe:1",
        ],
        timeout=20,
    )
    frames = [line for line in decoded.splitlines() if line.strip() and not line.startswith("#")]
    if len(frames) != 1:
        raise DomainError("GENERATED_FILE_INVALID", "静态图片候选必须恰好有一帧可完整解码。")


def verify_generated(path, kind):
    probe = probe_media(path)
    suffix = path.suffix.lower()
    if kind == "video":
        family = probe["format_name"].split(",")
        fits = (
            "matroska" in family
            if suffix in {".webm", ".mkv"}
            else "mov" in family or "mp4" in family
        )
        if not probe["has_video"] or probe["is_still"] or probe["duration"] < 0.04 or not fits:
            raise DomainError("GENERATED_FILE_INVALID", "结果未通过所声明视频格式和时长核验。")
        qc = technical_qc(
            path,
            {"has_video": True, "width": probe["width"], "height": probe["height"]},
            probe["duration"],
        )
    else:
        if not probe["is_still"] or probe["video_codec"] != STILL_CODECS.get(suffix):
            raise DomainError("GENERATED_FILE_INVALID", "文件字节与声明的静态图像格式不符。")
        check_single_frame(path)
        qc = {
            "checks": {"full_decode": True, "single_frame": True, "codec_matches_extension": True},
            "probe": probe,
            "media_type": media_mime(probe),
            "publish_ready": False,
        }
    return probe, qc


def receive(actor, job, http, descriptor):
    with tempfile.TemporaryDirectory(prefix="ai-ship-generation-") as td:
        path = Path(td) / ("candidate" + Path(descriptor["filename"]).suffix.lower())
        count = 0
        with http.stream("GET", "view", params=descriptor) as resp, path.open("wb") as file:
            resp.raise_for_status()
            for part in resp.iter_bytes(1024 * 1024):
                count += len(part)
                if count > MAX_BYTES:
                    raise DomainError("GENERATED_FILE_TOO_LARGE", "生成文件超出本地单素材容量。")
                file.write(part)
        kind = job["data"].get("output_kind", "video")
        probe, qc = verify_generated(path, kind)
        require_live(actor)
        with get_store().transaction():
            job = load(actor, job["id"])
            if job["data"].get("cancel_requested"):
                return update(actor, job, "cancelled", provider_terminal="completed_after_cancel")
            if job["data"]["stage"] == "ready":
                return job
            pinned_reference(actor, job["project_id"], job["data"].get("reference_asset_ref"))
            inp = import_saved_file(
                get_store(),
                actor,
                job["project_id"],
                descriptor["filename"],
                path,
                "本地生成工作流输出；内容真实性和使用权待审。",
                generated_by=job,
            )
            usage = save(
                actor,
                "Cost",
                job["project_id"],
                {
                    "category": "local_generation",
                    "actual": None,
                    "currency": "USD",
                    "unit": (
                        "generated_still_count" if kind == "image" else "generated_video_seconds"
                    ),
                    "quantity": 1 if kind == "image" else probe["duration"],
                    "pricing_status": "unconfigured",
                    "missing_reason": "自托管推理，未配置 GPU 和人工单价。",
                },
                [ref(job), ref(inp["asset"])],
            )
            return update(
                actor,
                job,
                "ready",
                asset_ref=ref(inp["asset"]),
                version_ref=ref(inp["version"]),
                import_entry_ref=ref(inp["import_entry"]),
                cost_ref=ref(usage),
                technical_qc=qc,
                provider_terminal="success",
            )


def poll(actor, job, cancel):
    cfg = profile(job["data"]["profile_id"])
    if digest(cfg) != job["data"]["profile_hash"]:
        raise DomainError(
            "GENERATOR_PROFILE_CHANGED", "生成端配置已改变；恢复原配置才能核对原任务。", 409
        )
    data = job["data"]
    with client(cfg) as http:
        bind_http(http, actor, job, cancel=cancel)
        remote, state, item = locate(http, data)
        if state == "unknown":
            return update(
                actor,
                job,
                "cancel_requested" if cancel else "outcome_unknown",
                issue("REMOTE_NOT_OBSERVED", "当前队列与可见历史中未找到原任务，保留请求身份。"),
            )
        if state in {"queued", "running"}:
            if cancel and state == "queued":
                response = http.post("queue", json={"delete": [remote]})
                response.raise_for_status()
                _, after, _ = locate(http, {**data, "remote_id": remote})
                if after == "unknown":
                    return update(
                        actor,
                        job,
                        "cancelled",
                        remote_id=remote,
                        provider_terminal="removed_from_observed_queue",
                    )
            return update(
                actor,
                job,
                "cancel_requested" if cancel else state,
                remote_id=remote,
                observed_remote_state=state,
            )
        status = item.get("status") or {}
        if status.get("status_str") == "error" or any(
            isinstance(m, list) and m and m[0] in {"execution_error", "execution_interrupted"}
            for m in status.get("messages", [])
        ):
            return update(
                actor,
                job,
                "cancelled" if cancel else "failed",
                issue("GENERATION_STOPPED", "生成器报告任务中断或失败，未接收素材。"),
                remote_id=remote,
                provider_terminal="error",
            )
        if status.get("completed") is not True or status.get("status_str") != "success":
            return update(
                actor,
                job,
                "cancel_requested" if cancel else "outcome_unknown",
                issue("GENERATION_UNCONFIRMED", "历史未提供可核验的成功终态。"),
                remote_id=remote,
            )
        if cancel:
            return update(
                actor,
                job,
                "cancelled",
                remote_id=remote,
                provider_terminal="completed_after_cancel",
            )
        if data.get("remote_id") != remote:
            job = update(actor, job, "running", remote_id=remote)
        return receive(
            actor,
            job,
            http,
            output_descriptor(item, data["output_node"], data.get("output_kind", "video")),
        )


def reconcile(id, request, actor, *, cancel):
    require_live(actor)
    store = get_store()
    job = load(actor, id)
    with producer(actor, id) as token:
        job = load(actor, job["id"])
        if job["data"]["stage"] in FINAL:
            if token and not cancel and job["data"]["stage"] == "ready":
                job = generation_review.separate_legacy_output(actor, job)
            return ok(result(actor, job), request)
        if not token:
            # A slow download could be in progress. The receiving worker checks this durable flag before adoption.
            if cancel:
                with store.transaction():
                    job = load(actor, id)
                    if job["data"]["stage"] not in FINAL and not job["data"].get(
                        "cancel_requested"
                    ):
                        job = update(actor, job, "cancel_requested", cancel_requested=True)
            return ok(result(actor, job), request)
        if cancel and not job["data"].get("cancel_requested"):
            job = update(actor, job, "cancel_requested", cancel_requested=True)
        try:
            ensure_lease(actor, id, token)
            job = poll(actor, job, bool(job["data"].get("cancel_requested")))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            with store.transaction():
                job = load(actor, id)
                if job["data"]["stage"] not in FINAL:
                    job = update(
                        actor,
                        job,
                        (
                            "cancel_requested"
                            if job["data"].get("cancel_requested")
                            else "outcome_unknown"
                        ),
                        issue(
                            "GENERATION_RECEIPT_UNAVAILABLE",
                            "尚未取得有效生成回执或完整文件，可再核对此任务。",
                        ),
                    )
        except DomainError as exc:
            if exc.code in {
                "REVISION_CONFLICT",
                "STALE_FENCE",
                "GENERATOR_PROFILE_CHANGED",
                "GENERATOR_UNAVAILABLE",
                "IDENTITY_REVOKED",
            }:
                raise
            job = update(actor, load(actor, id), "failed", issue(exc.code, exc.message))
        return ok(result(actor, job), request)


@router.post("/{id}/refresh")
def refresh(id: str, request: Request, actor: Actor = Depends(current_actor)):
    return reconcile(id, request, actor, cancel=False)


@router.post("/{id}/cancel")
def cancel(id: str, request: Request, actor: Actor = Depends(current_actor)):
    return reconcile(id, request, actor, cancel=True)


class Hold(Body):
    duration: float = Field(ge=1, le=60)
    fps: Literal[24, 25, 30, 50, 60] = 25


@router.post("/{id}/hold")
def hold_still(id: str, body: Hold, request: Request, actor: Actor = Depends(current_actor)):
    require_live(actor)
    job = load(actor, id)
    if job["data"].get("output_kind", "video") != "image" or job["data"]["stage"] != "ready":
        raise DomainError("NO_READY_IMAGE", "需要一张已完成并通过解码的静态图片。", 409)
    project = job["project_id"]
    store = get_store()
    pin = job["data"]["asset_ref"]
    source, _, path = resolve_media(store, actor, pin["id"], project_id=project)
    if source["version"] != pin["version"]:
        raise DomainError("STALE_IMAGE", "图片事实有新版本，请核对来源。", 409)
    frames = round(body.duration * body.fps)
    settings = {
        "requested_duration": body.duration,
        "duration": frames / body.fps,
        "fps": body.fps,
        "frames": frames,
    }
    key = digest([pin["id"], pin["version"], settings])
    with producer(actor, id) as token:
        if not token:
            raise DomainError("GENERATION_BUSY", "同一生成素材有在途操作，请稍后再试。", 409)
        job = load(actor, id)
        previous = job["data"].get("holds", {}).get(key)
        if previous:
            asset, _, _ = resolve_media(store, actor, previous["id"], project_id=project)
            return ok({"job": job, "asset": asset}, request)
        with tempfile.TemporaryDirectory(prefix="ai-ship-still-hold-") as td:
            video = Path(td) / "held-frame.mp4"
            run_process(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-loop",
                    "1",
                    "-framerate",
                    str(body.fps),
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    str(frames),
                    "-vf",
                    "scale=w='min(1920,max(2,iw))':h='min(1920,max(2,ih))':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-threads",
                    "2",
                    "-movflags",
                    "+faststart",
                    str(video),
                ],
                timeout=180,
            )
            probe, qc = verify_generated(video, "video")
            if abs(probe["duration"] - settings["duration"]) > 0.5 / body.fps:
                raise DomainError("HOLD_DURATION_MISMATCH", "定格衍生视频与请求帧数不符。")
            with store.transaction():
                require_live(actor)
                ensure_lease(actor, id, token)
                current, _, _ = resolve_media(store, actor, source["id"], project_id=project)
                if current["version"] != source["version"]:
                    raise DomainError("STALE_IMAGE", "定格处理期间图片来源已变更。", 409)
                job = load(actor, id)
                name = (
                    Path(source["data"].get("filename") or "image").stem
                    + f"-{frames}f-{body.fps}fps.mp4"
                )
                item = import_saved_file(
                    store,
                    actor,
                    project,
                    name,
                    video,
                    "人工指定的静帧持续时长；无运动生成。",
                    generated_by=job,
                    held_from=source,
                )
                transform = save(
                    actor,
                    "TransformRun",
                    project,
                    {
                        "name": "静帧定格",
                        "status": "succeeded",
                        "operation": "still_hold",
                        "settings": settings,
                        "technical_qc": qc,
                        "preserves_source": True,
                    },
                    [ref(source), ref(item["asset"]), ref(job)],
                )
                held = ref(item["asset"])
                job = update(
                    actor,
                    job,
                    "ready",
                    hold_ref=held,
                    hold_params=settings,
                    last_hold_transform=ref(transform),
                    holds={**job["data"].get("holds", {}), key: held},
                )
        return ok({"job": job, "asset": item["asset"]}, request)
