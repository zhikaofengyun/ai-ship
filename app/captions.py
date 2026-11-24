"""Plain, sentence-timed subtitle interchange for the Timeline editor (Atlas E04)."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from html import escape, unescape
from pathlib import PurePath
import re
from typing import Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .api import ok
from .auth import current_actor
from .errors import DomainError
from .media import MAX_DURATION

router = APIRouter(prefix="/media/captions", tags=["captions"])
MAX_CUES = 400
MAX_TEXT = 2000
TIMING = re.compile(r"^([^\s]+)[ \t]+-->[ \t]+([^\s]+)(.*)$")
TIMESTAMP = re.compile(r"^(?:(\d{2,}):)?([0-5]\d):([0-5]\d)([.,])(\d{3})$")
MARKUP = re.compile(r"<(?:/?[a-zA-Z]|\d{2}:|!)|\{\\")
FORBIDDEN_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ParseCaptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=1000000)
    language: str | None = Field(default=None, min_length=1, max_length=40)


class ExportCue(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")
    start: Decimal = Field(ge=0, le=MAX_DURATION)
    end: Decimal = Field(gt=0, le=MAX_DURATION)
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class ExportCaptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captions: list[ExportCue] = Field(min_length=1, max_length=MAX_CUES)
    format: Literal["srt", "vtt"]


class ImportedCue(BaseModel):
    start: float
    end: float
    text: str
    timing_precision: Literal["cue"]
    source_format: Literal["srt", "vtt"]
    source_filename: str
    language: str | None = None


class ParsedTrack(BaseModel):
    format: Literal["srt", "vtt"]
    filename: str
    captions: list[ImportedCue]


class ExportedTrack(BaseModel):
    content: str
    filename: str
    mime_type: str


T = TypeVar("T")


class CaptionResponse(BaseModel, Generic[T]):
    data: T
    trace_id: str | None


def invalid(line: int, reason: str):
    raise DomainError("INVALID_CAPTION_FILE", f"字幕第 {line} 行：{reason}", 422, {"line": line})


def parse_time(raw: str, fmt: str, line: int) -> int:
    match = TIMESTAMP.fullmatch(raw)
    if (
        not match
        or (fmt == "srt" and (match[1] is None or match[4] != ","))
        or (fmt == "vtt" and match[4] != ".")
    ):
        invalid(line, "时间码格式无效")
    hours, minutes, seconds, _, ms = match.groups()
    # Reject unbounded integer strings before conversion.
    if hours and len(hours) > 4:
        invalid(line, "时间码超出工程范围")
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(ms)


def parse_document(body: ParseCaptions) -> dict:
    name = PurePath(body.filename.replace("\\", "/")).name
    fmt = PurePath(name).suffix[1:].lower()
    if fmt not in {"srt", "vtt"}:
        invalid(1, "请选择 UTF-8 编码的 .srt 或 .vtt 文件")
    text = body.content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    index = 0
    if fmt == "vtt":
        if not re.fullmatch(r"WEBVTT(?:[ \t][^\n]*)?", lines[0]) or "-->" in lines[0]:
            invalid(1, "缺少有效的 WEBVTT 文件头")
        if len(lines) > 1 and lines[1].strip():
            invalid(2, "不支持带元数据的文件头，请导出普通文本字幕")
        index = 1

    cues = []
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        first = index
        while index < len(lines) and lines[index].strip():
            index += 1
        block = lines[first:index]
        if fmt == "vtt" and re.fullmatch(r"NOTE(?:[ \t].*)?", block[0]):
            continue
        if fmt == "vtt" and block[0] in {"STYLE", "REGION"}:
            invalid(first + 1, "此字幕含样式或分区，请先导出普通文本字幕")
        header = 0
        if "-->" not in block[0]:
            if fmt == "srt" and not block[0].strip().isdecimal():
                invalid(first + 1, "序号或时间码无效")
            header = 1
        if header >= len(block):
            invalid(first + 1, "缺少时间范围")
        line = first + header + 1
        timing = TIMING.fullmatch(block[header].strip())
        if not timing:
            invalid(line, "请使用开始时间 --> 结束时间")
        if timing[3].strip():
            invalid(line, "此字幕含定位或排版参数，请先导出普通文本字幕")
        start = parse_time(timing[1], fmt, line)
        end = parse_time(timing[2], fmt, line)
        if start >= end or end > MAX_DURATION * 1000:
            invalid(line, "结束时间必须晚于开始时间且在一小时工程范围内")
        if fmt == "vtt" and cues and start < round(cues[-1]["start"] * 1000):
            invalid(line, "WebVTT 各句开始时间必须依次排列")
        cue_lines = block[header + 1 :]
        for offset, cue_line in enumerate(cue_lines, 1):
            if "-->" in cue_line or MARKUP.search(cue_line):
                invalid(line + offset, "含嵌套时间、标记或样式，请先导出普通文本字幕")
        payload = "\n".join(cue_lines)
        if fmt == "vtt":
            payload = unescape(payload)
        if not payload.strip() or len(payload) > MAX_TEXT or FORBIDDEN_TEXT.search(payload):
            invalid(line, "字幕为空、包含控制字符或超过 2000 字")
        if len(cues) >= MAX_CUES:
            invalid(line, "单条时间线最多支持 400 条字幕")
        cue = {
            "start": start / 1000,
            "end": end / 1000,
            "text": payload,
            "timing_precision": "cue",
            "source_format": fmt,
            "source_filename": name,
        }
        if body.language:
            cue["language"] = body.language
        cues.append(cue)
    if not cues:
        invalid(1, "文件中没有可用的字幕")
    return {"format": fmt, "filename": name, "captions": cues}


def timestamp_text(ms: int, fmt: str) -> str:
    seconds, fraction = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    separator = "," if fmt == "srt" else "."
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{fraction:03}"


def export_document(body: ExportCaptions) -> dict:
    fmt = body.format
    blocks = ["WEBVTT"] if fmt == "vtt" else []
    ordered = sorted(body.captions, key=lambda c: c.start)
    for index, cue in enumerate(ordered, 1):
        start, end = (
            int((time * 1000).to_integral_value(rounding=ROUND_HALF_UP))
            for time in (cue.start, cue.end)
        )
        text = cue.text.replace("\r\n", "\n").replace("\r", "\n")
        if (
            end <= start
            or any(not line.strip() for line in text.split("\n"))
            or FORBIDDEN_TEXT.search(text)
        ):
            raise DomainError(
                "INVALID_CAPTION_EXPORT", f"第 {index} 句时长不足一毫秒或包含空行、无效字符。"
            )
        if fmt == "srt" and (MARKUP.search(text) or "-->" in text):
            raise DomainError(
                "INVALID_CAPTION_EXPORT", f"第 {index} 句含 SRT 控制标记，请选择 WebVTT。"
            )
        if fmt == "vtt":
            text = escape(text, quote=False)
        prefix = f"{index}\n" if fmt == "srt" else ""
        blocks.append(
            f"{prefix}{timestamp_text(start, fmt)} --> {timestamp_text(end, fmt)}\n{text}"
        )
    return {
        "content": "\n\n".join(blocks) + "\n",
        "filename": f"captions.{fmt}",
        "mime_type": (
            "text/vtt;charset=utf-8" if fmt == "vtt" else "application/x-subrip;charset=utf-8"
        ),
    }


@router.post(
    "/parse", response_model=CaptionResponse[ParsedTrack], response_model_exclude_none=True
)
def parse_captions(body: ParseCaptions, request: Request, actor=Depends(current_actor)):
    return ok(parse_document(body), request)


@router.post("/export", response_model=CaptionResponse[ExportedTrack])
def export_captions(body: ExportCaptions, request: Request, actor=Depends(current_actor)):
    return ok(export_document(body), request)
