"""Independent, literal first-frame check for an explicitly requested opening shot."""

import re
import unicodedata
from difflib import SequenceMatcher

from .errors import DomainError


def opening_requirement(goal, value):
    declarations = [
        match[1].strip()
        for line in goal.splitlines()
        if (match := re.fullmatch(r"\s*(?:首镜文字|opening text)\s*[:：]\s*(.*)", line, re.I))
    ]
    if value is not None:
        declarations.append(value.strip())
    if not declarations:
        return None
    if any(not s or len(s) > 120 or not s.isprintable() for s in declarations):
        raise ValueError("首镜文字须为一行 1–120 个可打印字符。")
    if len(set(declarations)) != 1:
        raise ValueError("简报与首镜文字条件不一致，请确认一个要求。")
    return declarations[0]


def normalized(text):
    text = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", text)


def matched_reading(observation, expected):
    # Match contiguous words in a single line. A low-confidence intervening
    # word breaks a phrase. Substrings within longer Latin words do not qualify.
    if observation.get("status") != "read":
        return False
    lines = {}
    for w in observation["words"]:
        lines.setdefault(tuple(w["line"]), []).append(w)
    literal = normalized(expected)
    latin_start = re.match(r"[^\W_]", literal[0]) and ord(literal[0]) < 0x3400
    latin_end = re.match(r"[^\W_]", literal[-1]) and ord(literal[-1]) < 0x3400
    pattern = (
        (r"(?<!\w)" if latin_start else "") + re.escape(literal) + (r"(?!\w)" if latin_end else "")
    )
    for words in lines.values():
        line = normalized(" ".join(w["text"] if w["confidence"] >= 0.8 else "\x00" for w in words))
        if re.search(pattern, line):
            return True
    return False


def frame_observation(path, start, end):
    from .visual_candidates import recognize_text

    reading = recognize_text(path, None, start, frame_receipt=True)
    if reading["at"] is None or not start - 1e-6 <= reading["at"] < end:
        return {"status": "unavailable", "at": None, "words": []}
    return reading


def check_proposal(actor, project, expected, sources, shots):
    from .media import check_revision, resolve_media
    from .store import get_store

    if not expected:
        return {}
    by_id = {s["asset_id"]: s for s in sources}
    first = shots[0]
    cache = {}

    def observe(source, start, end):
        key = (source["asset_id"], start, end)
        if key not in cache:
            record, _, path = resolve_media(
                get_store(), actor, source["asset_id"], project_id=project
            )
            check_revision(record, source["asset_version"])
            cache[key] = {
                "asset_id": source["asset_id"],
                "asset_version": source["asset_version"],
                "source_in": start,
                "source_out": end,
                **frame_observation(path, start, end),
            }
        return cache[key]

    selected = observe(by_id[first["asset_id"]], first["source_in"], first["source_out"])
    alternatives = []
    state = "matched"
    if not matched_reading(selected, expected):
        state = "unverified"
        length = first["source_out"] - first["source_in"]
        for source in sources:
            starts = [s["source_in"] for s in shots if s["asset_id"] == source["asset_id"]]
            starts.extend([0.0, source["ocr"].get("at")])
            for start in dict.fromkeys(starts):
                if start is None or source["duration"] - start < 0.04:
                    continue
                end = min(source["duration"], round(start + length, 6))
                candidate = observe(source, start, end)
                if matched_reading(candidate, expected):
                    alternatives.append(candidate)
                    break
        if alternatives and any(w["confidence"] >= 0.8 for w in selected["words"]):
            state = "conflict"
    check = {
        "expected_text": expected,
        "status": state,
        "observation": selected,
        "alternatives": alternatives,
    }
    result = {"opening_check": check}
    if state != "matched":
        result["review_blocker"] = {
            "code": "OPENING_TEXT_" + state.upper(),
            "message": (
                "首镜实帧读数与指定文字不符，已有可核实的替选区间。"
                if state == "conflict"
                else "当前首镜实帧未核实指定文字，请选有证据的原片区间或调整目标。"
            ),
        }
    return result


def caption_may_supply(expected, caption):
    # Text from a caption must not count as evidence of the original's words.
    # Compare bounded phrases/words; similar glyphs are ambiguous, not a positive match.
    def tokens(text):
        text = normalized(text).translate(str.maketrans({"0": "o", "1": "l", "5": "s", "ı": "i"}))
        return re.findall(r"[^\W_]+", text)

    wanted, shown = tokens(expected), tokens(caption)
    if not wanted:
        return any(ch in normalized(caption) for ch in normalized(expected))
    for word in wanted:
        for other in shown:
            if word == other or (word in other and any("\u3400" <= ch <= "\u9fff" for ch in word)):
                return True
            if (
                min(len(word), len(other)) >= 3
                and abs(len(word) - len(other)) <= 1
                and SequenceMatcher(None, word, other, autojunk=False).ratio() >= 0.79
            ):
                return True
    # A publisher could split a target word into multiple subtitle tokens.
    return "".join(wanted) in "".join(shown)


def check_visible_origin(expected, clips, captions):
    visible = [
        c
        for c in clips
        if not c["audio_only"]
        and c["timeline_start"] == 0
        and (c["opacity"] > 0 or c["keyframes"].get("opacity"))
    ]
    if len(visible) != 1 or any(
        c["start"] == 0 and caption_may_supply(expected, c["text"]) for c in captions
    ):
        raise DomainError(
            "OPENING_TEXT_UNVERIFIED",
            "当前时间线首帧为空、含有叠层，或字幕可能提供相同或形近文字，无法核实其原片来源。",
            409,
            {"expected_text": expected},
        )
    c = visible[0]
    observation = frame_observation(c["path"], c["source_in"], c["source_out"])
    if not matched_reading(observation, expected):
        raise DomainError(
            "OPENING_TEXT_UNVERIFIED",
            "当前时间线所用原片首帧未核实指定文字。",
            409,
            {
                "expected_text": expected,
                "asset_id": c["record"]["id"],
                "asset_version": c["record"]["version"],
                "observation": observation,
            },
        )
    return {
        "asset_id": c["record"]["id"],
        "asset_version": c["record"]["version"],
        "source_in": c["source_in"],
        "source_out": c["source_out"],
        "observation": observation,
    }


def check_output(path, expected, duration):
    reading = frame_observation(path, 0.0, duration)
    if reading.get("at") != 0 or not matched_reading(reading, expected):
        raise DomainError(
            "OPENING_TEXT_UNVERIFIED",
            "当前输出画面的第一帧未核实指定文字。",
            409,
            {"expected_text": expected, "observation": reading},
        )
    return reading
