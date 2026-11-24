"""Y04-R2. Atomically hand off one frozen render attempt; always encode the whole sequence."""

from fractions import Fraction
from pathlib import Path
import re
from shutil import which

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictBool, StrictInt

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import record, ref
from .selection import Label, Note, SelectionBody
from .store import digest, get_store, now

router = APIRouter(prefix="/media/render-jobs", tags=["render recovery"])


class Recovery(SelectionBody):
    project_id: Label
    request_id: Label
    expected_revision: StrictInt = Field(ge=1)
    reason: Note
    interrupt_running: StrictBool = False


def file_digest(path):
    from .media import verified_object_hash

    try:
        stat = path.stat()
        return verified_object_hash(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError:
        raise DomainError("RENDER_CHECKPOINT_UNAVAILABLE", "无法读取冻结渲染所需的本地文件。", 409)


def media_pin(clip_id, record, blob, path):
    return {
        "clip": clip_id,
        "record": ref(record),
        "blob": ref(blob),
        "checksum": file_digest(path),
    }


def runtime_identity(font, tools):
    return {
        "font": {"name": font.name, "sha256": file_digest(font)} if font else None,
        "tools": {
            name: {
                "version": version,
                "binary_hash": file_digest(Path(binary)) if (binary := which(name)) else None,
            }
            for name, version in tools.items()
        },
    }


def checkpoint(timeline, clips, profile, environment):
    return {
        "timeline": {**ref(timeline), "hash": timeline["hash"]},
        "media": [
            media_pin(clip["id"], clip["record"], clip["blob"], clip["path"]) for clip in clips
        ],
        "profile": profile,
        **environment,
    }


def known_job(parent, request, actor):
    store = get_store()
    link = parent["data"].get("continuation")
    if not link:
        return None
    if link["request_hash"] != digest([actor.actor_id, request.model_dump()]):
        raise DomainError(
            "RENDER_ALREADY_RECOVERED",
            "这次任务已有后续尝试，请查看该尝试的状态。",
            409,
            {"successor_id": link["job_id"]},
        )
    job = record(actor, link["job_id"], "RenderJob", project=parent["project_id"])
    result = {"job": job, "idempotent_replay": True}
    if job["data"].get("status") == "succeeded":
        from .media import render_receipt, resolve_media

        artifact, _, _ = resolve_media(
            store,
            actor,
            job["data"]["artifact_id"],
            project_id=job["project_id"],
            purpose="inspect",
        )
        manifest = record(
            actor, job["data"]["render_manifest_id"], "RenderManifest", project=job["project_id"]
        )
        time_map = record(
            actor, manifest["data"]["time_map_id"], "TimeMap", project=job["project_id"]
        )
        result.update(render_receipt(job, artifact, manifest, time_map))
    return result


def reject_unrecoverable(parent, body):
    data = parent["data"]
    if parent["version"] != body.expected_revision:
        raise DomainError("REVISION_CONFLICT", "渲染状态已变化，请重新查看。", 409)
    if data.get("renderer") != "ffmpeg_local" or any(
        data.get(k) for k in ("execution_context", "origin_execution_context")
    ):
        raise DomainError(
            "RENDER_RECOVERY_SCOPE",
            "此入口仅接管独立本地 FFmpeg 任务；工作流节点请从原运行恢复。",
            409,
        )
    if data.get("status") == "running" and not body.interrupt_running:
        raise DomainError("RENDER_STILL_RUNNING", "任务尚有在途尝试，接管前请明确确认。", 409)
    if data.get("status") not in {"running", "failed", "succeeded"}:
        raise DomainError("RENDER_NOT_RECOVERABLE", "请选择完成、失败或待接管的运行中任务。", 409)


def check_baseline(parent, snapshot):
    original = parent["data"].get("checkpoint")
    if original:
        if original != snapshot:
            raise DomainError(
                "RENDER_CHECKPOINT_CHANGED",
                "时间线、素材、精确规格或本地工具/字体与冻结检查点不一致。",
                409,
            )
        return
    # Older jobs have frozen input records and output dimensions but no environment checkpoint.
    # All bytes must still match the explicitly referenced immutable records; never reuse old frames.
    refs = {
        (r["kind"], r["id"], r["version"])
        for r in parent["refs"]
        if r["kind"] not in {"Artifact", "RenderManifest"}
        or r["id"]
        not in {parent["data"].get("artifact_id"), parent["data"].get("render_manifest_id")}
    }
    timeline = snapshot["timeline"]
    needed = {(timeline["kind"], timeline["id"], timeline["version"])}
    needed.update(
        (m["record"]["kind"], m["record"]["id"], m["record"]["version"]) for m in snapshot["media"]
    )
    for item in snapshot["media"]:
        frozen = get_store().get(
            item["record"]["id"], parent["tenant_id"], version=item["record"]["version"]
        )
        recorded_checksum = frozen["data"].get("content_hash")
        if not recorded_checksum:
            old_location = next(
                (
                    r
                    for r in frozen["refs"]
                    if r["id"] == item["blob"]["id"] and r["kind"] == "BlobLocation"
                ),
                None,
            )
            if old_location:
                blob = get_store().get(
                    old_location["id"], parent["tenant_id"], version=old_location["version"]
                )
                recorded_checksum = blob["data"].get("checksum")
        if recorded_checksum != item["checksum"]:
            raise DomainError("RENDER_LEGACY_HASH_UNKNOWN", "旧任务的来源缺少可核对的哈希。", 409)
    if refs != needed:
        raise DomainError(
            "RENDER_LEGACY_INPUTS_CHANGED", "旧任务的实际来源清单无法与当前输入逐项对应。", 409
        )


def completed_baseline(store, actor, parent):
    from .media import resolve_media

    data, project = parent["data"], parent["project_id"]
    if data.get("status") != "succeeded":
        return None
    artifact, _, path = resolve_media(store, actor, data["artifact_id"], project_id=project)
    manifest_id = data["render_manifest_id"]
    receipt = next(
        (r for r in parent["refs"] if r["kind"] == "RenderManifest" and r["id"] == manifest_id),
        None,
    )
    if not receipt:
        raise DomainError("RENDER_BASELINE_UNVERIFIABLE", "完成任务缺少已固定版本的渲染清单。", 409)
    manifest = record(
        actor, manifest_id, "RenderManifest", version=receipt["version"], project=project
    )
    timeline_ref = manifest["data"].get("timeline_ref", {})
    if (
        artifact["kind"] != "Artifact"
        or artifact["data"].get("render_job_id") != parent["id"]
        or (timeline_ref.get("id"), timeline_ref.get("version"))
        != (data["timeline_id"], data["timeline_version"])
        or manifest["data"].get("artifact_id") != artifact["id"]
        or manifest["data"].get("render_job_id") != parent["id"]
        or manifest["data"].get("output_hash") != file_digest(path)
        or not artifact["data"].get("qc", {}).get("checks", {}).get("full_decode")
    ):
        raise DomainError(
            "RENDER_BASELINE_UNVERIFIABLE", "原成片、质检结果与完成清单无法核对。", 409
        )
    return artifact, path


def decoded_streams(path):
    from .media import MAX_RENDER_SECONDS, run_process

    output, _ = run_process(
        [
            "ffmpeg",
            "-nostdin",
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
            "-map",
            "0:a:0",
            "-c:v",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "pcm_s16le",
            "-threads",
            "1",
            "-f",
            "streamhash",
            "-hash",
            "sha256",
            "-",
        ],
        MAX_RENDER_SECONDS,
    )
    streams = dict(re.findall(r"^\d+,([av]),SHA256=([a-f0-9]{64})$", output, re.MULTILINE))
    if set(streams) != {"a", "v"}:
        raise DomainError("RENDER_COMPARE_INCOMPLETE", "无法从完整音画取得对照签名。", 409)
    return streams


def verify_recovered_output(store, actor, parent, output, qc, rate):
    try:
        measured = Fraction(qc["probe"]["fps"])
    except (TypeError, ValueError, ZeroDivisionError):
        measured = None
    if measured != rate:
        raise DomainError("RENDER_FPS_MISMATCH", "实际成片帧率与冻结的输出规格不一致。", 409)
    qc["checks"]["exact_frame_rate"] = True
    baseline = completed_baseline(store, actor, parent)
    if baseline:
        artifact, path = baseline
        signature = decoded_streams(path)
        if decoded_streams(output) != signature:
            raise DomainError(
                "RENDER_CONTENT_MISMATCH", "完整解码的画面或声音与原成片不一致。", 409
            )
        qc["baseline_comparison"] = {
            "artifact": ref(artifact),
            "decoded_video_sha256": signature["v"],
            "decoded_pcm_sha256": signature["a"],
            "format": "yuv420p/pcm_s16le",
        }


def claim(store, actor, data, refs, kwargs, parent, body):
    """A single transaction publishes both the successor and its ownership of the predecessor."""
    with store.transaction():
        parent = record(actor, parent["id"], "RenderJob", project=body.project_id)
        if replay := known_job(parent, body, actor):
            return None, replay
        reject_unrecoverable(parent, body)
        check_baseline(parent, data["checkpoint"])
        completed_baseline(store, actor, parent)
        data.update(
            recovery_route="Y04-R2",
            previous_attempt=ref(parent),
            recovery_reason=body.reason,
            prior_environment_recorded=bool(parent["data"].get("checkpoint")),
            reused_segments=0,
            restarted_at_seconds=0,
        )
        child = store.create("RenderJob", data, refs=[*refs, ref(parent)], **kwargs)
        store.update(
            parent["id"],
            {
                **parent["data"],
                "status": (
                    "superseded"
                    if parent["data"]["status"] == "running"
                    else parent["data"]["status"]
                ),
                "continuation": {
                    "job_id": child["id"],
                    "recorded_at": now(),
                    "request_hash": digest([actor.actor_id, body.model_dump()]),
                },
            },
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            expected_revision=parent["version"],
            reason=body.reason,
        )
        return child, None


@router.post("/{job_id}/recover")
def recover(job_id: str, body: Recovery, request: Request, actor: Actor = Depends(current_actor)):
    from .media import render_timeline

    require_write(actor)
    parent = record(actor, job_id, "RenderJob", project=body.project_id)
    if replay := known_job(parent, body, actor):
        return ok(replay, request)
    reject_unrecoverable(parent, body)
    data = parent["data"]
    snap = data.get("checkpoint")
    profile = snap["profile"] if snap else data.get("profile")
    if not isinstance(profile, dict) or not all(k in profile for k in ("width", "height", "fps")):
        raise DomainError("RENDER_PROFILE_MISSING", "旧任务没有完整输出规格。", 409)
    if not snap and type(profile["fps"]) is float and not profile["fps"].is_integer():
        raise DomainError(
            "RENDER_FPS_UNVERIFIABLE", "旧任务未保存精确分数帧率，请从锁定的时间线重新渲染。", 409
        )
    args = {
        "project_id": body.project_id,
        "timeline_id": data["timeline_id"],
        "expected_revision": data["timeline_version"],
        "profile": profile,
    }
    return ok(render_timeline(get_store(), actor, args, recovery=(parent, body)), request)
