"""V03-R2. Synthesize a pinned English script with an approved built-in Flite voice."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
import wave

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_approval, require_write
from .errors import DomainError
from .media import check_current_media_rights, persist_blob, probe_media, run_process, tool_versions
from .production import record, ref, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, live, on_request
from .store import get_store

router = APIRouter(prefix="/production/narration", tags=["narration"])
Voice = Literal["slt", "kal", "rms", "awb"]
PURPOSE = "project_narration_and_local_export"


class Operation(SelectionBody):
    project_id: Label
    request_id: Label


class SourceReview(Operation):
    voice: Voice
    evidence: Note
    expires_at: Label


class Synthesis(Operation):
    script: ChosenVersion
    approval: ChosenVersion
    tempo: float = Field(ge=0.5, le=2.0, default=1)


@router.post("/source-reviews")
def review_source(body: SourceReview, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def execute():
        project = record(actor, body.project_id, "Project")
        if timestamp(body.expires_at) <= datetime.now(timezone.utc):
            raise DomainError("VOICE_SOURCE_EXPIRED", "请填写仍有效的声音使用期限。")
        scope = {"purpose": PURPOSE, "market": project["data"].get("market")}
        source = save(
            actor,
            "Source",
            body.project_id,
            {
                "name": "Flite · " + body.voice,
                "provenance": "local FFmpeg/libflite built-in voice",
                "voice": body.voice,
                "engine": "flite",
                "language": "en",
                "status": "recorded",
                "scope": scope,
                "rights_evidence": body.evidence,
                "expires_at": body.expires_at,
            },
        )
        approval = save(
            actor,
            "Approval",
            body.project_id,
            {
                "name": "本项目本地配音用途审核",
                "kind": "rights_check",
                "operation": "voice_source_review",
                "status": "approved",
                "subject_id": source["id"],
                "subject_hash": source["hash"],
                "subject_version": source["version"],
                "scope": scope,
                "expires_at": body.expires_at,
                "principal": actor.actor_id,
                "evidence": body.evidence,
            },
            [ref(source)],
        )
        return {"source": source, "approval": approval}

    return ok(on_request("voice-source-review", body, actor, execute), request)


def produce(actor, project_id, script_pin, approval_pin, tempo):
    store = get_store()
    project = record(actor, project_id, "Project")
    script = live(actor, script_pin, "Script", project_id)
    if script["data"].get("localization_method") == "human_reviewed_price":
        with store.connection() as conn:
            attested = conn.execute(
                """SELECT 1 FROM records r JOIN revisions v ON r.id=v.record_id
                   AND r.tenant_id=v.tenant_id AND r.latest_version=v.version
                   WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Audit'
                   AND json_extract(v.data_json,'$.action')='script.reading'
                   AND json_extract(v.data_json,'$.script_id')=?
                   AND json_extract(v.data_json,'$.script_hash')=? LIMIT 1""",
                (actor.tenant_id, project_id, script["id"], script["hash"]),
            ).fetchone()
        if not attested:
            raise DomainError("READING_REVIEW_REQUIRED", "此版译稿的读法尚未获得复核。", 409)
        for key, kind in (("source_script", "Script"), ("localization_product", "Product")):
            value = script["data"][key]
            live(actor, ChosenVersion(id=value["id"], version=value["version"]), kind, project_id)
    approval = live(actor, approval_pin, "Approval", project_id)
    detail = approval["data"]
    if (
        detail.get("kind") != "rights_check"
        or detail.get("operation") != "voice_source_review"
        or detail.get("status") != "approved"
        or detail.get("scope") != {"purpose": PURPOSE, "market": project["data"].get("market")}
    ):
        raise DomainError("VOICE_SOURCE_REVIEW_REQUIRED", "需要当前市场和用途的声音来源审核。", 409)
    source = live(
        actor,
        ChosenVersion(id=detail["subject_id"], version=detail["subject_version"]),
        "Source",
        project_id,
    )
    sd = source["data"]
    if (
        detail["subject_hash"] != source["hash"]
        or sd.get("engine") != "flite"
        or sd.get("voice") not in {"slt", "kal", "rms", "awb"}
    ):
        raise DomainError("VOICE_SOURCE_CHANGED", "声音版本已变化，请重新审核。", 409)
    text = script["data"].get("text")
    language = script["data"].get("language", project["data"].get("language", ""))
    if not isinstance(language, str) or language.lower().split("-")[0] != "en":
        raise DomainError("VOICE_LANGUAGE_UNSUPPORTED", "当前 Flite 音色仅支持明确标为英语的稿件。")
    if (
        not isinstance(text, str)
        or not 1 <= len(text.strip()) <= 3000
        or not any(c.isalpha() for c in text)
        or any(c.isalpha() and not c.isascii() for c in text)
        or "\x00" in text
    ):
        raise DomainError(
            "VOICE_SCRIPT_INVALID", "请提供最多 3000 字符的英文可读稿，有其他语言的段落需另行录制。"
        )
    refs = [ref(script), ref(source), ref(approval)]
    for item in (script, source):
        check_current_media_rights(store, actor, item)
    with TemporaryDirectory(prefix="ai-ship-voice-", dir="/tmp") as work:
        script_file = Path(work) / "script.txt"
        script_file.write_text(text, encoding="utf-8")
        wav = Path(work) / "narration.wav"
        run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"flite=textfile={script_file}:voice={sd['voice']}",
                "-af",
                f"atempo={tempo:.8f}",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(wav),
            ],
            timeout=90,
        )
        with wave.open(str(wav), "rb") as f:
            count, rate = f.getnframes(), f.getframerate()
            pcm = f.readframes(count)
            if (
                count <= 0
                or not any(pcm)
                or len(pcm) != count * f.getnchannels() * f.getsampwidth()
            ):
                raise DomainError("VOICE_SYNTHESIS_EMPTY", "输出没有可核验的有效采样。")
        probe = probe_media(wav)
        if not actor_still_authorized(actor):
            raise DomainError("IDENTITY_REVOKED", "生成结束时执行身份已失效。", 403)
        for item in (source, script, approval):
            check_current_media_rights(store, actor, item)
        if timestamp(detail["expires_at"]) <= datetime.now(timezone.utc):
            raise DomainError("VOICE_SOURCE_EXPIRED", "生成期间声音用途许可已到期。", 409)
        location = persist_blob(wav, actor.tenant_id)
        blob = save(
            actor,
            "BlobLocation",
            project_id,
            {**location, "purpose": "audio", "mime_type": "audio/wav"},
            refs,
        )
    job = save(
        actor,
        "ToolCall",
        project_id,
        {
            "name": "本地英语配音",
            "tool": "ffmpeg/flite",
            "status": "succeeded",
            "voice": sd["voice"],
            "tempo": tempo,
            "language": language,
            "tool_versions": tool_versions(),
            "script_version": ref(script),
            "output_sha256": location["checksum"],
            "sample_count": count,
        },
        [*refs, ref(blob)],
    )
    audio = save(
        actor,
        "Audio",
        project_id,
        {
            "name": str(script["data"].get("name") or "英文配音")[:220] + " · " + sd["voice"],
            "filename": "narration.wav",
            "content_hash": location["checksum"],
            "blob_location_id": blob["id"],
            "source_id": source["id"],
            "script_version": ref(script),
            "generation_call": ref(job),
            "voice": sd["voice"],
            "tempo": tempo,
            "sample_count": count,
            "sample_rate": rate,
            "duration": count / rate,
            "probe": probe,
            "status": "available",
            "alignment_status": "requires_listening",
        },
        [*refs, ref(blob), ref(job)],
    )
    return {"audio": audio, "call": job, "source": source}


@router.post("/synthesize")
def synthesize(body: Synthesis, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    return ok(
        on_request(
            "voice-synthesis",
            body,
            actor,
            lambda: produce(actor, body.project_id, body.script, body.approval, body.tempo),
        ),
        request,
    )
