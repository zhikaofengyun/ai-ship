"""L04/V04 timed, reviewable speech candidates on the selected media's source clock."""

from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import wave

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt

from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_approval, require_write
from .captions import ExportCaptions, export_document
from .errors import DomainError
from .media import SAFE_FORMATS, hash_file, probe_media, resolve_media, run_process
from .production import record, ref, save
from .selection import ChosenVersion, Label, Note, SelectionBody, on_request
from .store import get_store

router = APIRouter(prefix="/production/speech", tags=["speech recognition"])
VERSION = "0.3.45"
PROTOCOL = "vosk-source-captions-v1"
MAX_WINDOW = 600


def models():
    try:
        configured = json.loads(os.getenv("AI_SHIP_ASR_MODELS", "{}"))
        if not isinstance(configured, dict):
            raise ValueError()
        result = {}
        for name, d in configured.items():
            if (
                not re.fullmatch("[a-zA-Z0-9._-]{1,80}", name)
                or not isinstance(d, dict)
                or not Path(d["path"]).is_absolute()
                or not re.fullmatch("[a-f0-9]{64}", d["sha256"])
                or not re.fullmatch("[A-Za-z0-9_-]{2,30}", d["language"])
            ):
                raise ValueError()
            result[name] = {
                "path": Path(d["path"]),
                "sha256": d["sha256"],
                "language": d["language"],
            }
        return result
    except (KeyError, ValueError, TypeError):
        raise DomainError("ASR_CONFIGURATION", "本机识别模型配置无效。", 503)


def installed():
    try:
        return version("vosk") == VERSION
    except PackageNotFoundError:
        return False


@router.get("/models")
def available(request: Request, actor: Actor = Depends(current_actor)):
    return ok(
        {
            "engine": "vosk",
            "required_version": VERSION,
            "installed": installed(),
            "max_window_seconds": MAX_WINDOW,
            "models": [
                {
                    "id": name,
                    "language": d["language"],
                    "sha256": d["sha256"],
                    "available": d["path"].is_dir() and installed(),
                }
                for name, d in models().items()
            ],
        },
        request,
    )


class Recognition(SelectionBody):
    project_id: Label
    request_id: Label
    source: ChosenVersion
    model_id: Label
    start_seconds: float = Field(0, ge=0, le=3600)
    end_seconds: float | None = Field(None, gt=0, le=3600)


def source_bytes(actor, pid, pin):
    source, blob, path = resolve_media(
        get_store(), actor, pin.id, project_id=pid, purpose="process"
    )
    if source["version"] != pin.version:
        raise DomainError("ASR_STALE_SOURCE", "音源版本改变，请重新选择。", 409)
    checksum = blob["data"].get("checksum")
    if not checksum or source["data"].get("content_hash", checksum) != checksum:
        raise DomainError("ASR_BYTES_CHANGED", "当前音源内容与登记摘要不同。", 409)
    return source, blob, path


def calculate(actor, body):
    source, blob, path = source_bytes(actor, body.project_id, body.source)
    probe = probe_media(path)
    if not probe["has_audio"]:
        raise DomainError("NO_AUDIO", "所选文件没有可识别的音轨。", 422)
    profile = models().get(body.model_id)
    if not profile or not profile["path"].is_dir() or not installed():
        raise DomainError("ASR_UNAVAILABLE", "此识别引擎或已固定的本地模型尚未安装。", 503)
    lo = body.start_seconds
    hi = body.end_seconds if body.end_seconds is not None else probe["duration"]
    if not lo < hi <= probe["duration"] + 1e-6 or hi - lo > MAX_WINDOW:
        raise DomainError("ASR_WINDOW", "请在音源时长内选择不超过十分钟的连续区间。", 422)
    with TemporaryDirectory(prefix="ai-ship-asr-") as temp:
        wav = Path(temp) / "analysis.wav"
        # Normalize container start, preserve leading silence and fill source timestamp gaps.
        run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                SAFE_FORMATS,
                "-copyts",
                "-start_at_zero",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-vn",
                "-af",
                f"aresample=16000:async=1:first_pts=0,atrim=start={lo:.6f}:end={hi:.6f},asetpts=PTS-STARTPTS",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(wav),
            ],
            min(150, max(30, int(hi))),
        )
        with wave.open(str(wav)) as data:
            duration = data.getnframes() / 16000
        checksum = hash_file(wav)
        try:
            raw, _ = run_process(
                [
                    sys.executable,
                    "-m",
                    "app.speech_worker",
                    str(profile["path"]),
                    "--pin",
                    profile["sha256"],
                    "--wav",
                    str(wav),
                    "--language",
                    profile["language"],
                ],
                min(300, max(40, int((hi - lo) * 2))),
            )
            groups = json.loads(raw)
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError) and exc.code == "MEDIA_BUSY":
                raise
            raise DomainError(
                "ASR_INFERENCE_FAILED", "本地引擎或模型指纹核验失败，未形成识别结果。", 409
            ) from exc
    cues = []
    for group in groups:
        words = []
        for word in group["words"]:
            start, end, conf = map(float, (word["start"], word["end"], word["conf"]))
            if not all(math.isfinite(n) for n in (start, end, conf)) or not 0 <= conf <= 1:
                raise DomainError("ASR_INVALID_TIMING", "引擎返回无效词级证据。", 409)
            a, b = round(lo + max(0, start), 6), round(lo + min(duration, hi - lo, end), 6)
            if a < b:
                words.append({"word": word["word"], "start": a, "end": b, "confidence": conf})
        if words:
            text = ("" if profile["language"].lower().startswith("zh") else " ").join(
                w["word"] for w in words
            )
            cues.append(
                {"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}
            )
    if len(cues) > 400:
        raise DomainError("ASR_CUE_LIMIT", "候选超过字幕轨上限，请缩短识别区间。", 422)
    return (
        source,
        blob,
        {
            "feature_id": "V04",
            "protocol": PROTOCOL,
            "status": "awaiting_review" if cues else "no_speech_detected",
            "source": ref(source),
            "source_sha256": blob["data"]["checksum"],
            "analysis_wav_sha256": checksum,
            "source_window": [lo, hi],
            "clock": "source_presentation_seconds",
            "audio_stream": next(
                x["index"] for x in probe["streams"] if x["codec_type"] == "audio"
            ),
            "engine": "vosk",
            "engine_version": VERSION,
            "model_id": body.model_id,
            "model_sha256": profile["sha256"],
            "language": profile["language"],
            "cues": cues,
        },
    )


@router.post("/recognize")
def recognize(body: Recognition, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    # Inference and FFmpeg are outside the database write lease, including under batch production.
    store = get_store()
    source_bytes(actor, body.project_id, body.source)
    with store.connection() as db:
        existing = db.execute(
            "SELECT 1 FROM command_results WHERE tenant_id=? AND idempotency_key=?",
            (actor.tenant_id, "speech-recognize:" + body.request_id),
        ).fetchone()
    observed = None if existing else calculate(actor, body)

    def commit():
        if not actor_still_authorized(actor):
            raise DomainError("FORBIDDEN", "识别期间身份失效。", 403)
        current, blob, _ = source_bytes(actor, body.project_id, body.source)
        if current["hash"] != observed[0]["hash"] or blob["hash"] != observed[1]["hash"]:
            raise DomainError("ASR_STALE_SOURCE", "提交前音源发生变化。", 409)
        return {
            "candidate": save(
                actor,
                "Audit",
                body.project_id,
                observed[2],
                [ref(current), ref(blob)],
                "本地语音识别候选",
            )
        }

    return ok(on_request("speech-recognize", body, actor, commit), request)


class ReviewedCue(SelectionBody):
    index: StrictInt = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)


class Review(SelectionBody):
    project_id: Label
    request_id: Label
    candidate: ChosenVersion
    reason: Note
    cues: list[ReviewedCue] = Field(min_length=1, max_length=400)


@router.post("/review")
def review(body: Review, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def commit():
        item = record(
            actor,
            body.candidate.id,
            "Audit",
            version=body.candidate.version,
            project=body.project_id,
        )
        d = item["data"]
        if d.get("protocol") != PROTOCOL or d.get("status") != "awaiting_review":
            raise DomainError("ASR_NO_CANDIDATES", "没有可采纳的识别候选。", 409)
        current, _, _ = source_bytes(
            actor, body.project_id, ChosenVersion(**{k: d["source"][k] for k in ("id", "version")})
        )
        ids = [cue.index for cue in body.cues]
        if ids != sorted(set(ids)) or ids[-1] >= len(d["cues"]):
            raise DomainError("ASR_REVIEW_INDEX", "候选须按原顺序选择且不能重复。", 422)
        cues = [
            {
                "start": d["cues"][c.index]["start"],
                "end": d["cues"][c.index]["end"],
                "text": c.text.strip(),
            }
            for c in body.cues
        ]
        if any(not c["text"] for c in cues):
            raise DomainError("ASR_EMPTY_CAPTION", "字幕不能为空。")
        # Validate before committing. This is the same exporter the Timeline editor uses.
        export_document(ExportCaptions(captions=cues, format="srt"))
        approval = save(
            actor,
            "Approval",
            body.project_id,
            {
                "kind": "content_check",
                "status": "approved",
                "protocol": PROTOCOL,
                "subject_id": item["id"],
                "subject_kind": "Audit",
                "subject_version": item["version"],
                "subject_hash": item["hash"],
                "principal": actor.actor_id,
                "reason": body.reason,
                "source": ref(current),
                "selected_indices": ids,
                "captions": cues,
                "clock": d["clock"],
                "model_sha256": d["model_sha256"],
            },
            [ref(item), ref(current)],
            body.reason,
        )
        return {"approval": approval}

    source = record(
        actor, body.candidate.id, "Audit", version=body.candidate.version, project=body.project_id
    )
    if source["data"].get("protocol") == PROTOCOL:
        pin = source["data"]["source"]
        source_bytes(actor, body.project_id, ChosenVersion(id=pin["id"], version=pin["version"]))
    return ok(on_request("speech-review", body, actor, commit), request)


@router.get("/reviews/{approval_id}/captions")
def captions(
    approval_id: str, project_id: str, request: Request, actor: Actor = Depends(current_actor)
):
    approved = record(actor, approval_id, "Approval", project=project_id)
    data = approved["data"]
    if (
        data.get("protocol") != PROTOCOL
        or data.get("status") != "approved"
        or not data.get("captions")
    ):
        raise DomainError("ASR_REVIEW_REQUIRED", "请先逐句审核并保存。", 409)
    source = data["source"]
    source_bytes(actor, project_id, ChosenVersion(id=source["id"], version=source["version"]))
    document = export_document(ExportCaptions(captions=data["captions"], format="srt"))
    document["filename"] = f"speech-{approved['id']}.srt"
    return ok(document, request)
