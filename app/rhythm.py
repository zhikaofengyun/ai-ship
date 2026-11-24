"""Manual beat, downbeat and section annotations against the actual project audio clock."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .media import probe_media, resolve_media
from .production import ref, revise, save
from .selection import ChosenVersion, Label, SelectionBody, live, on_request
from .store import get_store

router = APIRouter(prefix="/production/music", tags=["music"])


class RhythmMarker(SelectionBody):
    type: Literal["beat", "downbeat", "section"]
    time: Decimal = Field(ge=0, lt=3600)
    label: str = Field(default="", max_length=120)


class ManualRhythm(SelectionBody):
    project_id: Label
    request_id: Label
    audio: ChosenVersion
    music: ChosenVersion | None = None
    name: Label
    use_markers: bool
    markers: list[RhythmMarker] = Field(max_length=4096)


@router.post("/markers")
def save_markers(body: ManualRhythm, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        music = live(actor, body.music, "Music", body.project_id) if body.music else None
        audio, _, path = resolve_media(
            get_store(), actor, body.audio.id, project_id=body.project_id
        )
        if audio["kind"] != "Audio" or audio["version"] != body.audio.version:
            raise DomainError("AUDIO_VERSION_MISMATCH", "请重新选择当前音轨版本。", 409)
        if music and (
            music["data"].get("analysis_method") != "manual_marks"
            or music["data"].get("audio_ref") != ref(audio)
        ):
            raise DomainError("MUSIC_SOURCE_MISMATCH", "不能用另一首音轨覆盖已有打点。", 409)
        probe = probe_media(path)
        stream = next((s for s in probe["streams"] if s.get("codec_type") == "audio"), {})
        rate = int(stream.get("sample_rate") or 0)
        if rate <= 0 or not probe["has_audio"] or probe["duration"] <= 0:
            raise DomainError("AUDIO_CLOCK_MISSING", "无法验证此音轨的采样率或时长。")
        duration = Decimal(str(probe["duration"]))
        markers = []
        seen = set()
        for marker in sorted(body.markers, key=lambda item: item.time):
            if marker.time >= duration:
                raise DomainError("MARKER_OUT_OF_RANGE", "标记必须位于实际音轨范围内。")
            sample = int((marker.time * rate).to_integral_value(rounding=ROUND_HALF_UP))
            key = (marker.type, sample)
            if key in seen:
                raise DomainError("DUPLICATE_MARKER", "同一采样位置已经有同类标记。")
            seen.add(key)
            markers.append(
                {
                    "type": marker.type,
                    "time": float(marker.time),
                    "sample": sample,
                    "label": marker.label.strip(),
                }
            )
        if body.use_markers and not markers:
            raise DomainError("NO_RHYTHM_MARKERS", "请先打点，或关闭卡点。")
        groups = {"beats": "beat", "downbeats": "downbeat", "sections": "section"}
        clock = {
            "unit": "seconds",
            "origin": "start_of_playable_audio",
            "sample_rate_hz": rate,
            "duration_seconds": probe["duration"],
            "sample_map": "nearest_sample_to_human_playback_time",
        }
        outputs = {}
        data = {}
        for key, marker_type in groups.items():
            typed = [m for m in markers if m["type"] == marker_type]
            data[key] = typed
            outputs[key] = {
                "status": "human_marked" if typed else "unmarked",
                "provider": "human",
                "revision": "manual-playback-1",
                "clock": clock,
                "confidence_basis": "人工试听标记" if typed else None,
            }
        for key in ["onsets", "emotion"]:
            outputs[key] = {
                "status": "not_run",
                "provider": None,
                "revision": None,
                "clock": None,
                "confidence_basis": None,
            }
        data.update(
            {
                "name": body.name,
                "analysis_method": "manual_marks",
                "analysis_version": (music["version"] + 1) if music else 1,
                "audio_version_id": audio["id"],
                "audio_ref": ref(audio),
                "duration": probe["duration"],
                "markers": markers,
                "use_markers": body.use_markers,
                "analysis_clock": clock,
                "analysis_outputs": outputs,
                "bpm": None,
            }
        )
        changed = (
            revise(actor, music, data, "修订人工节奏标记", [ref(audio)])
            if music
            else save(actor, "Music", body.project_id, data, [ref(audio)])
        )
        return {"music": changed}

    return ok(on_request("music-markers", body, actor, apply), request)
