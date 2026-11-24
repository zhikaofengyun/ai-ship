"""Compile explicit, same-track two-source overlaps. All clocks are output seconds."""

import math

from .errors import DomainError

KINDS = ("dissolve", "wipe_left", "wipe_right", "dip_black", "dip_white")
EPS = 0.00001


def transition_spec(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"from_id", "kind", "duration"}:
        raise DomainError("INVALID_TRANSITION", "转场需要前一片段、类型和交叠时长")
    duration = value["duration"]
    if (
        value["kind"] not in KINDS
        or not isinstance(value["from_id"], str)
        or not value["from_id"]
        or type(duration) not in (float, int)
        or not math.isfinite(duration)
        or not 0 < duration <= 30
    ):
        raise DomainError(
            "INVALID_TRANSITION", "转场类型、片段或时长无效，时长范围为大于零至 30 秒"
        )
    return dict(value)


def resolve_transitions(clips, fps):
    """Validate against the actual decoded source lengths before creating a render job."""
    resolved = []
    by_id = {clip["id"]: clip for clip in clips}
    per_lane = {}
    for clip in clips:
        per_lane.setdefault(clip["track"], []).append(clip)
    for lane in per_lane.values():
        lane.sort(key=lambda c: c["timeline_start"])
        for index, incoming in enumerate(lane):
            target = incoming.get("transition_in")
            if target is None:
                continue
            outgoing = by_id.get(target["from_id"])
            start, duration = incoming["timeline_start"], target["duration"]
            end = start + duration
            if (
                not index
                or lane[index - 1] is not outgoing
                or outgoing["timeline_start"] >= start - EPS
                or duration + EPS < 1 / fps
                or duration >= incoming["duration"] - EPS
                or abs(end - (outgoing["timeline_start"] + outgoing["duration"])) > EPS
            ):
                raise DomainError(
                    "TRANSITION_OVERLAP",
                    "转场需要同轨相邻镜头和相符的交叠区间，至少一帧且两侧保留独立画面",
                )
            if any(
                other is not outgoing
                and other is not incoming
                and other["timeline_start"] < end - EPS
                and other["timeline_start"] + other["duration"] > start + EPS
                for other in lane
            ):
                raise DomainError("TRANSITION_AMBIGUOUS", "转场区间与同轨第三个镜头重叠")
            item = {
                **target,
                "to_id": incoming["id"],
                "track": incoming["track"],
                "start": start,
                "end": end,
            }
            incoming["incoming_fx"] = item
            outgoing["outgoing_fx"] = item
            resolved.append(item)
    return resolved


def video_filters(clip):
    parts = []
    incoming = clip.get("incoming_fx")
    if incoming:
        kind = incoming["kind"]
        d = incoming["duration"]
        if kind == "dissolve":
            parts.append(f"fade=t=in:st=0:d={d:.9f}:alpha=1")
        else:
            if kind.startswith("wipe"):
                comparison = "lt(X/W" if kind == "wipe_right" else "gt(X/W"
                boundary = (
                    f"clip(T/{d:.9f},0,1)" if kind == "wipe_right" else f"1-clip(T/{d:.9f},0,1)"
                )
                weight = f"if(gte(T,{d:.9f}),1,{comparison},{boundary}))"
            else:
                weight = f"clip((T-{d / 2:.9f})/{d / 2:.9f},0,1)"
            parts.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({weight})'")
    outgoing = clip.get("outgoing_fx")
    if outgoing and outgoing["kind"].startswith("dip_"):
        d = outgoing["duration"]
        color = outgoing["kind"].split("_")[1]
        parts.append(f"fade=t=out:st={clip['duration']-d:.9f}:d={d / 2:.9f}:color={color}")
    return "".join("," + part for part in parts)


def audio_filters(clip):
    parts = []
    if fx := clip.get("incoming_fx"):
        parts.append(f"afade=t=in:st=0:d={fx['duration']:.9f}")
    if fx := clip.get("outgoing_fx"):
        parts.append(f"afade=t=out:st={clip['duration']-fx['duration']:.9f}:d={fx['duration']:.9f}")
    return "".join("," + part for part in parts)
