"""OTIO hard cuts, gaps and validated two-source visual transitions. Only the bundled JSON serializer is used.
The adapter never follows external media URLs. All clips resolve to this project.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import math
import re
from pathlib import PurePosixPath
from uuid import uuid4
from typing import Generic, Literal, TypeVar
from fractions import Fraction

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
import opentimelineio as otio

from .api import Body, ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .media import (
    MAX_DURATION,
    MAX_PIXELS,
    even_dimension,
    check_current_media_rights,
    normalize_clip,
    ref,
)
from .production import record
from .store import get_store
from .transitions import EPS, KINDS, resolve_transitions, transition_spec

router = APIRouter(prefix="/projects/{project_id}/timeline-exchange", tags=["timeline-exchange"])
NS = "ai_ship"
CAPTION_KEYS = {
    "text",
    "start",
    "end",
    "font_size",
    "x",
    "y",
    "color",
    "id",
    "source_format",
    "source_filename",
    "language",
    "timing_precision",
}
VISUAL = {"x", "y", "volume", "opacity", "fit", "keyframes", "locked"}
CLIP_KEYS = VISUAL | {
    "id",
    "asset_id",
    "asset_version_id",
    "source_in",
    "shot_ref",
    "source_out",
    "timeline_start",
    "track",
    "speed",
    "transition_in",
}
TIMELINE_KEYS = {
    "name",
    "locked",
    "locked_at",
    "locked_by",
    "status",
    "clips",
    "captions",
    "audio_tracks",
    "format",
    "duration",
    "exchange_receipt",
    "execution_context",
    "origin_execution_context",
}
MAX_DOC_BYTES = 1_500_000


class ExportInput(Body):
    timeline_id: str
    expected_revision: int = Field(ge=1)


class InspectInput(Body):
    content: str = Field(min_length=1, max_length=MAX_DOC_BYTES)
    bindings: dict[str, str] = Field(default_factory=dict, max_length=80)


class ImportInput(InspectInput):
    request_id: str = Field(min_length=1, max_length=120)
    acknowledge_metadata: bool = False


class ExportedTimeline(Body):
    content: str
    name: str
    notes: list[str]
    adapter_version: str


class MissingSource(Body):
    slot: str
    name: str
    kind: Literal["Audio", "Video"]


class ExchangeReport(Body):
    name: str
    unresolved: list[MissingSource]
    notes: list[str]
    clip_count: int
    tracks: int


T = TypeVar("T")


class ExchangeResponse(BaseModel, Generic[T]):
    data: T
    trace_id: str | None


def incompatible(message):
    raise DomainError("OTIO_UNSUPPORTED", message, 422)


def plain(value):
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [plain(v) for v in value]
    return value


def secs(value, label):
    n = value.to_seconds()
    if not math.isfinite(n) or n < 0 or n > MAX_DURATION:
        incompatible(f"{label}超出可编辑时长。")
    return n


def otio_range(start, end, rate):
    return otio.opentime.TimeRange(
        otio.opentime.RationalTime(start * rate, rate),
        otio.opentime.RationalTime((end - start) * rate, rate),
    )


def format_values(fmt):
    if not isinstance(fmt, dict) or set(fmt) - {"width", "height", "fps"}:
        incompatible("画幅包含当前交换器无法保留的设置。")
    width = even_dimension(fmt.get("width"), "width", 1280)
    height = even_dimension(fmt.get("height"), "height", 720)
    if width * height > MAX_PIXELS:
        incompatible("画幅超出本地编码器限制。")
    fps = fmt.get("fps", 30)
    if isinstance(fps, dict):
        if set(fps) != {"num", "den"} or any(
            type(v) is not int or not 1 <= v <= 100000 for v in fps.values()
        ):
            incompatible("有理数帧率的分子和分母无效。")
        rate = Fraction(fps["num"], fps["den"])
    elif type(fps) in (float, int) and math.isfinite(fps):
        rate = Fraction(str(fps))
    else:
        incompatible("输出帧率无效。")
    if not 1 <= rate <= 60:
        incompatible("输出帧率超出编码器范围。")
    return {"width": width, "height": height, "fps": fps}


def exchange_rate(fmt):
    fps = fmt["fps"]
    return fps["num"] / fps["den"] if isinstance(fps, dict) else fps


def captions(values):
    if not isinstance(values, list) or len(values) > 400:
        incompatible("字幕数量超出交换范围。")
    for c in values:
        if (
            not isinstance(c, dict)
            or set(c) - CAPTION_KEYS
            or not isinstance(c.get("text"), str)
            or len(c["text"]) > 2000
        ):
            incompatible("字幕包含当前交换器无法保留的文字样式。")
        if (
            any(
                type(c.get(k)) not in (int, float) or not math.isfinite(c[k])
                for k in ("start", "end")
            )
            or not 0 <= c["start"] < c["end"] <= MAX_DURATION
        ):
            incompatible("字幕时间区间无效。")
        for k in ("x", "y", "font_size"):
            if k in c and (type(c[k]) not in (float, int) or not math.isfinite(c[k])):
                incompatible("字幕位置或字号无效。")
        if c.get("timing_precision", "cue") != "cue" or c.get("source_format", "srt") not in (
            "srt",
            "vtt",
        ):
            incompatible("字幕时码或来源格式无法保留。")
        if "color" in c and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(c["color"])):
            incompatible("字幕颜色无效。")
        if c.get("language") is not None and (
            not isinstance(c["language"], str) or len(c["language"]) > 40
        ):
            incompatible("字幕语言无效。")
        for k in ("color", "id", "source_filename"):
            if k in c and (not isinstance(c[k], str) or len(c[k]) > 180):
                incompatible("字幕属性无效。")
    return values


def validated_shot(pin, actor, project_id, media):
    if (
        not isinstance(pin, dict)
        or not isinstance(pin.get("id"), str)
        or type(pin.get("version")) is not int
    ):
        incompatible("镜头来源版本无效。")
    shot = record(actor, pin["id"], "Shot", version=pin["version"], project=project_id)
    if shot["data"].get("asset_version_id") != media["id"]:
        incompatible("镜头来源与素材版本不匹配。")
    return shot


def validate_exchange_transitions(clips, rate):
    """A transition refers to exactly one predecessor on the same lane."""
    if not any(c.get("transition_in") for c in clips):
        return
    if len({c["id"] for c in clips}) != len(clips):
        incompatible("转场工程里的片段标识必须唯一。")
    resolve_transitions(clips, rate)


def export_lane(track, entries, rate, alignments, notes):
    # OTIO cuts clips at a nominal edit point and extends both neighbours by
    # their transition handles. AI Ship stores both full overlapping ranges.
    ordered = sorted(entries, key=lambda entry: entry[0]["timeline_start"])
    before = [0.0] * len(ordered)
    after = [0.0] * len(ordered)
    transitions = {}
    for i, (clip, _) in enumerate(ordered):
        incoming = clip.get("transition_in")
        if not incoming:
            continue
        duration = incoming["duration"]
        old = alignments.get(clip["id"], {})
        offset = duration / 2
        if isinstance(old, dict) and all(
            old.get(k) == incoming[k] for k in ("from_id", "kind", "duration")
        ):
            saved = old.get("in_offset")
            if type(saved) in (float, int) and math.isfinite(saved) and 0 <= saved <= duration:
                offset = saved
        before[i] = offset
        after[i - 1] = duration - offset
        custom = incoming["kind"] != "dissolve"
        transition = otio.schema.Transition(
            name=incoming["kind"],
            transition_type=(
                otio.schema.TransitionTypes.Custom
                if custom
                else otio.schema.TransitionTypes.SMPTE_Dissolve
            ),
            in_offset=otio.opentime.RationalTime(offset * rate, rate),
            out_offset=otio.opentime.RationalTime((duration - offset) * rate, rate),
        )
        if custom:
            transition.metadata[NS] = {"kind": incoming["kind"]}
            notes.add(
                "擦除和经黑白场转场用 OTIO Custom_Transition 与 AI Ship 类型扩展表示；第三方编辑器需要支持对应效果。"
            )
        transitions[i] = transition
    cursor = 0.0
    for i, (clip, item) in enumerate(ordered):
        start = clip["timeline_start"] + before[i]
        if start < cursor - EPS:
            incompatible("同轨片段存在未声明的交叠，无法保留其剪切关系。")
        if start > cursor + EPS:
            track.append(otio.schema.Gap(source_range=otio_range(0, start - cursor, rate)))
        if i in transitions:
            track.append(transitions[i])
        source_start = clip["source_in"] + before[i]
        source_end = clip["source_out"] - after[i]
        if source_end <= source_start:
            incompatible("转场无法为相邻镜头保留有效剪切区间。")
        item.source_range = otio_range(source_start, source_end, rate)
        track.append(item)
        cursor = start + source_end - source_start


def imported_transitions(track, audio):
    transitions = {}
    for i, t in enumerate(track):
        if not isinstance(t, otio.schema.Transition):
            continue
        if (
            audio
            or i == 0
            or i + 1 == len(track)
            or not all(isinstance(track[j], otio.schema.Clip) for j in (i - 1, i + 1))
        ):
            incompatible("转场必须位于同一画面轨上两段相邻的原片之间。")
        # OTIO 0.18.1 preserves unrecognised JSON attributes without exposing
        # Transition.enabled as a Python property. Check the serialized object
        # before interpreting newer producers' transition semantics.
        raw = json.loads(otio.core.serialize_json_to_string(t))
        known = {
            "OTIO_SCHEMA",
            "name",
            "metadata",
            "in_offset",
            "out_offset",
            "transition_type",
            "enabled",
        }
        if set(raw) - known or raw.get("enabled", True) is not True:
            incompatible("转场已禁用或含有当前交换器不支持的字段。")
        ns = plain(t.metadata.get(NS, {}))
        if not isinstance(ns, dict) or set(ns) - {"kind"}:
            incompatible("转场扩展字段无法识别。")
        if (
            t.transition_type == otio.schema.TransitionTypes.SMPTE_Dissolve
            and ns.get("kind", "dissolve") == "dissolve"
        ):
            kind = "dissolve"
        elif (
            t.transition_type == otio.schema.TransitionTypes.Custom
            and ns.get("kind") in KINDS
            and ns["kind"] != "dissolve"
        ):
            kind = ns["kind"]
        else:
            incompatible("未知 OTIO 转场类型，导入前请指定可保留的转场。")
        if t.in_offset.rate <= 0 or t.out_offset.rate <= 0:
            incompatible("转场有无效的偏移时基。")
        incoming = secs(t.in_offset, "转场入偏移")
        outgoing = secs(t.out_offset, "转场出偏移")
        spec = transition_spec(
            {"kind": kind, "from_id": "previous", "duration": incoming + outgoing}
        )
        transitions[i] = dict(spec, in_offset=incoming, out_offset=outgoing)
    return transitions


def export_document(project_id, actor, item):
    store = get_store()
    check_current_media_rights(store, actor, item, "export")
    data = item["data"]
    if any(data[k] for k in set(data) - TIMELINE_KEYS):
        incompatible("工程含有此适配器无法保留的轨道、锁或效果。原工程仍可使用。")
    fmt = format_values(data["format"])
    caps = captions(data.get("captions", []))
    sequence = otio.schema.Timeline(name=data.get("name", "剪辑"))
    source_metadata = {
        "format": fmt,
        "captions": caps,
        "timeline_id": item["id"],
        "revision": item["version"],
    }
    sequence.metadata[NS] = source_metadata
    tracks = defaultdict(list)
    warnings = set()
    if caps:
        warnings.add("字幕存于 AI Ship 扩展字段，其他剪辑软件可能不显示。")
    for key, is_audio, max_count in (("clips", False, 64), ("audio_tracks", True, 16)):
        items = data.get(key, [])
        if len(items) > max_count:
            incompatible("片段数量超过当前交换器限制。")
        for i, c in enumerate(items):
            if set(c) - CLIP_KEYS or c.get("speed", 1) != 1:
                incompatible("当前交换器支持原速片段。变速或未识别的片段参数须先处理。")
            normalized = normalize_clip(store, actor, project_id, c, i, is_audio)
            media = normalized["record"]
            check_current_media_rights(store, actor, media, "export")
            pinned = next((r for r in item["refs"] if r["id"] == media["id"]), None)
            if pinned and pinned["version"] != media["version"]:
                incompatible("素材引用已升级，请先核对工程中的版本。")
            if media["kind"] == "Asset":
                originals = [
                    candidate
                    for candidate in store.list(
                        actor.tenant_id, "AssetVersion", project_id, limit=2000
                    )
                    if candidate["data"].get("asset_id") == media["id"]
                    and candidate["data"].get("variant") == "original"
                    and candidate["data"].get("blob_location_id")
                    == media["data"].get("blob_location_id")
                ]
                if len(originals) != 1:
                    incompatible("此旧版片段缺少唯一的原始素材版本。请在检查器重新关联素材。")
                media = originals[0]
                check_current_media_rights(store, actor, media, "export")
            if media["kind"] not in {"AssetVersion", "Audio"}:
                incompatible("请先将交付文件作为项目素材导入。")
            p = media["data"]
            name = PurePosixPath(
                str(p.get("filename") or p.get("name") or "素材").replace("\\", "/")
            ).name
            path = "media/" + name
            source = otio.schema.ExternalReference(target_url=path)
            available = normalized["probe"].get("duration")
            if available:
                source.available_range = otio_range(0, available, exchange_rate(fmt))
            source.metadata[NS] = {
                "record_id": media["id"],
                "revision": media["version"],
                "path": path,
            }
            clip = otio.schema.Clip(
                name=name,
                media_reference=source,
                source_range=otio_range(
                    normalized["source_in"], normalized["source_out"], exchange_rate(fmt)
                ),
            )
            props = {k: c[k] for k in VISUAL if k in c}
            clip.metadata[NS] = {"properties": props}
            if c.get("shot_ref"):
                shot = validated_shot(c["shot_ref"], actor, project_id, media)
                clip.metadata[NS] = {"properties": props, "shot_ref": ref(shot)}
                warnings.add("分镜来源保存在 AI Ship 扩展字段中。")
            if any(
                c.get(k, default) != default
                for k, default in {
                    "x": 0,
                    "y": 0,
                    "volume": 1,
                    "opacity": 1,
                    "fit": "contain",
                    "keyframes": {},
                    "locked": False,
                }.items()
            ):
                warnings.add("画面、音量或锁定设置存于 AI Ship 扩展字段，第三方编辑器可能忽略。")
            tracks[(is_audio, normalized["track"])].append((normalized, clip))
    if not tracks:
        incompatible("工程还没有可交换的音视频片段。")
    rate = exchange_rate(fmt)
    validate_exchange_transitions(
        [n for (audio, _), entries in tracks.items() if not audio for n, _clip in entries], rate
    )
    receipt = data.get("exchange_receipt") or {}
    alignments = receipt.get("transition_alignments", {}) if isinstance(receipt, dict) else {}
    if not isinstance(alignments, dict):
        incompatible("OTIO 转场时码记录无效。")
    for (audio, lane), clips in sorted(tracks.items()):
        track = otio.schema.Track(
            name=("音轨" if audio else "画面轨") + str(lane + 1), kind="Audio" if audio else "Video"
        )
        track.metadata[NS] = {"lane": lane}
        export_lane(track, clips, rate, alignments, warnings)
        sequence.tracks.append(track)
    content = otio.adapters.write_to_string(sequence, adapter_name="otio_json")
    if len(content.encode()) > MAX_DOC_BYTES:
        incompatible("交换文件超过 1.5 MB。")
    return {
        "content": content,
        "name": sequence.name,
        "notes": sorted(warnings),
        "adapter_version": otio.__version__,
    }


def parse(text):
    try:
        if len(text.encode()) > MAX_DOC_BYTES:
            raise ValueError()
        raw = json.loads(text)
        if not isinstance(raw, dict) or raw.get("OTIO_SCHEMA") != "Timeline.1":
            raise ValueError()
        sequence = otio.core.deserialize_json_from_string(text)
        if not isinstance(sequence, otio.schema.Timeline):
            raise ValueError()
    except (ValueError, RuntimeError, TypeError, RecursionError) as exc:
        raise DomainError("INVALID_OTIO", "文件不是可读取的 OTIO 时间线。") from exc
    return sequence


def inspect_document(project_id, actor, body):
    project = record(actor, project_id, "Project")
    store = get_store()
    seq = parse(body.content)
    if seq.global_start_time and secs(seq.global_start_time, "工程起点") != 0:
        incompatible("无法保留非零全局起始时码。")
    notes = set()

    def examine(item):
        if (
            getattr(item, "effects", [])
            or getattr(item, "markers", [])
            or not getattr(item, "enabled", True)
        ):
            incompatible("工程包含效果、标记或禁用轨道，当前交换器无法保留。")
        if set(item.metadata) - {NS}:
            notes.add("第三方自定义元数据不会写入新草稿。")

    examine(seq)
    examine(seq.tracks)
    if seq.tracks.source_range or len(seq.tracks) > 16:
        incompatible("无法保留堆栈裁切或超过 16 条轨道的工程。")
    root = plain(seq.metadata.get(NS, {}))
    if not isinstance(root, dict):
        incompatible("工程扩展字段无效。")
    basefmt = project["data"]
    defaults = {
        "width": basefmt.get("width", 1080),
        "height": basefmt.get("height", 1920),
        "fps": 30,
    }
    fmt = format_values(root.get("format", defaults))
    result = {
        "name": str(seq.name or "导入剪辑")[:220],
        "format": fmt,
        "locked": False,
        "status": "draft",
        "clips": [],
        "audio_tracks": [],
        "captions": captions(root.get("captions", [])),
    }
    refs = {}
    missing = []
    counts = {"Video": 0, "Audio": 0}
    lanes = set()
    seen_slots = set()
    geometry = []
    alignments = {}
    for tr_index, track in enumerate(seq.tracks):
        if (
            not isinstance(track, otio.schema.Track)
            or track.kind not in counts
            or track.source_range
        ):
            incompatible("仅支持不含嵌套、整轨裁切的音频和视频轨。")
        examine(track)
        audio = track.kind == "Audio"
        meta = plain(track.metadata.get(NS, {}))
        if not isinstance(meta, dict):
            incompatible("轨道扩展字段无效。")
        lane = meta.get("lane", (2 if audio else 0) + counts[track.kind])
        if type(lane) is not int or not 0 <= lane <= 100 or (track.kind, lane) in lanes:
            incompatible("轨道编号无效或重复。")
        lanes.add((track.kind, lane))
        counts[track.kind] += 1
        cursor = 0
        transitions = imported_transitions(track, audio)
        clip_ids = {
            i: "clip-" + uuid4().hex for i, c in enumerate(track) if isinstance(c, otio.schema.Clip)
        }
        for index, item in enumerate(track):
            examine(item)
            if isinstance(item, otio.schema.Transition):
                continue
            if not isinstance(item, (otio.schema.Clip, otio.schema.Gap)) or not item.source_range:
                incompatible("嵌套片段和未指定源区间暂不能回导。")
            duration = secs(item.source_range.duration, "片段时长")
            if duration <= 0 or cursor + duration > MAX_DURATION:
                incompatible("片段时长或轨道总时长无效。")
            if isinstance(item, otio.schema.Gap):
                if index == len(track) - 1:
                    incompatible("末尾空白需先转换为媒体片段。")
                cursor += duration
                continue
            key = f"{tr_index}.{index}"
            seen_slots.add(key)
            if len(seen_slots) > 80:
                incompatible("片段总数超过当前交换器限制。")
            nominal_start = secs(item.source_range.start_time, "源入点")
            incoming = transitions.get(index - 1)
            before = incoming["in_offset"] if incoming else 0.0
            outgoing = transitions.get(index + 1)
            after = outgoing["out_offset"] if outgoing else 0.0
            start = nominal_start - before
            end = nominal_start + duration + after
            real_start = cursor - before
            if min(start, real_start) < -EPS:
                incompatible("转场缺少入点前的源画面余量，或越过时间线起点。")
            start = max(0, start)
            real_start = max(0, real_start)
            timing = {
                "id": clip_ids[index],
                "timeline_start": real_start,
                "duration": end - start,
                "track": lane,
            }
            if incoming:
                spec = {
                    "from_id": clip_ids[index - 2],
                    "kind": incoming["kind"],
                    "duration": incoming["duration"],
                }
                timing["transition_in"] = spec
                alignments[timing["id"]] = {**spec, "in_offset": before}
            if not audio:
                geometry.append(timing)
            reference = item.media_reference
            if len(item.media_references()) != 1 or not isinstance(
                reference, (otio.schema.ExternalReference, otio.schema.MissingReference)
            ):
                incompatible("暂不支持多表示、生成器或图像序列引用。")
            examine(reference)
            if incoming or outgoing:
                available = reference.available_range
                if available and (
                    start < secs(available.start_time, "可用源起点") - EPS
                    or end > secs(available.end_time_exclusive(), "可用源终点") + EPS
                ):
                    incompatible("转场需要的源画面超出了 OTIO 引用声明的可用区间。")
            media_meta = plain(reference.metadata.get(NS, {}))
            if not isinstance(media_meta, dict):
                incompatible("媒体引用扩展字段无效。")
            rid = body.bindings.get(key)
            version = None
            if (
                not rid
                and isinstance(reference, otio.schema.ExternalReference)
                and reference.target_url == media_meta.get("path")
            ):
                rid = media_meta.get("record_id")
                version = media_meta.get("revision")
                if type(version) is not int or version < 1:
                    rid = None
            if rid and not isinstance(rid, str):
                incompatible("来源标识无效。")
            media = None
            if rid:
                try:
                    media = record(actor, rid, version=version, project=project_id)
                except DomainError as exc:
                    if exc.status not in (404, 409):
                        raise
            if not media:
                missing.append(
                    {
                        "slot": key,
                        "name": str(item.name or "未命名片段")[:180],
                        "kind": "Audio" if audio else "Video",
                    }
                )
                cursor += duration
                continue
            if media["kind"] not in {"AssetVersion", "Audio"} or not media["data"].get(
                "blob_location_id"
            ):
                incompatible("请绑定项目内的实际素材版本或音轨。")
            if store.get(media["id"], actor.tenant_id)["version"] != media["version"]:
                incompatible("来源版本已有变化，请重新指定素材后核对。")
            check_current_media_rights(store, actor, media)
            binding = (
                {"asset_id": media["data"]["asset_id"], "asset_version_id": media["id"]}
                if media["kind"] == "AssetVersion"
                else {"asset_id": media["id"]}
            )
            clip_meta = plain(item.metadata.get(NS, {}))
            if not isinstance(clip_meta, dict):
                incompatible("片段元数据无效。")
            props = clip_meta.get("properties", {})
            if not isinstance(props, dict) or set(props) - VISUAL:
                incompatible("片段扩展属性无法保留。")
            if "locked" in props and not isinstance(props["locked"], bool):
                incompatible("片段锁无效。")
            clip = {
                **binding,
                **props,
                "id": clip_ids[index],
                "source_in": start,
                "source_out": end,
                "timeline_start": real_start,
                "track": lane,
            }
            if incoming:
                clip["transition_in"] = timing["transition_in"]
            available_duration = media["data"].get("probe", {}).get("duration")
            if (incoming or outgoing) and available_duration and end > available_duration + EPS:
                incompatible("所绑定的实际素材缺少转场出点所需的源画面。")
            if clip_meta.get("shot_ref"):
                shot = validated_shot(clip_meta["shot_ref"], actor, project_id, media)
                clip["shot_ref"] = ref(shot)
                refs[shot["id"]] = ref(shot)
            normalize_clip(store, actor, project_id, clip, index, audio)
            refs[media["id"]] = ref(media)
            if binding["asset_id"] != media["id"]:
                parent = record(actor, binding["asset_id"], "Asset", project=project_id)
                refs[parent["id"]] = ref(parent)
            result["audio_tracks" if audio else "clips"].append(clip)
            cursor += duration
    validate_exchange_transitions(geometry, exchange_rate(fmt))
    if alignments:
        result["exchange_receipt"] = {"transition_alignments": alignments}
    if set(body.bindings) - seen_slots:
        incompatible("文件变化后需重新选择素材。")
    if not seen_slots or len(result["clips"]) > 64 or len(result["audio_tracks"]) > 16:
        incompatible("需要有效的剪辑片段，最多支持 64 个画面和 16 个声音片段。")
    if (
        root.get("timeline_id")
        and isinstance(root.get("timeline_id"), str)
        and type(root.get("revision")) is int
    ):
        try:
            parent = record(
                actor, root["timeline_id"], "Timeline", version=root["revision"], project=project_id
            )
        except DomainError:
            parent = None
        if parent:
            refs[parent["id"]] = ref(parent)
    return (
        result,
        list(refs.values()),
        {
            "name": result["name"],
            "unresolved": missing,
            "notes": sorted(notes),
            "clip_count": len(seen_slots),
            "tracks": len(seq.tracks),
        },
    )


@router.post("/export", response_model=ExchangeResponse[ExportedTimeline])
def export_timeline(
    project_id: str, body: ExportInput, request: Request, actor: Actor = Depends(current_actor)
):
    item = record(
        actor, body.timeline_id, "Timeline", version=body.expected_revision, project=project_id
    )
    return ok(export_document(project_id, actor, item), request)


@router.post("/inspect", response_model=ExchangeResponse[ExchangeReport])
def inspect_timeline(
    project_id: str, body: InspectInput, request: Request, actor: Actor = Depends(current_actor)
):
    _, _, report = inspect_document(project_id, actor, body)
    return ok(report, request)


@router.post("/import")
def import_timeline(
    project_id: str, body: ImportInput, request: Request, actor: Actor = Depends(current_actor)
):
    require_write(actor)
    store = get_store()

    def commit():
        data, refs, report = inspect_document(project_id, actor, body)
        if report["unresolved"]:
            raise DomainError("OTIO_UNRESOLVED", "需要为所有离线片段指定项目内的来源。", 409)
        if report["notes"] and not body.acknowledge_metadata:
            raise DomainError("OTIO_LOSS_REVIEW", "请先核对不能保留的元数据。", 409)
        data["exchange_receipt"] = {
            **data.get("exchange_receipt", {}),
            "format": "otio_json",
            "adapter_version": otio.__version__,
            "source_sha256": sha256(body.content.encode()).hexdigest(),
            "notes": report["notes"],
        }
        return store.create(
            "Timeline",
            data,
            tenant_id=actor.tenant_id,
            project_id=project_id,
            actor_id=actor.actor_id,
            refs=refs,
            reason="审阅并回导 OTIO 为独立剪辑草稿",
        )

    key = "otio:" + actor.actor_id + ":" + body.request_id
    item = store.idempotent(
        actor.tenant_id, key, key, {"project": project_id, "body": body.model_dump()}, commit
    )
    return ok({k: v for k, v in item.items() if k != "idempotent_replay"}, request)
