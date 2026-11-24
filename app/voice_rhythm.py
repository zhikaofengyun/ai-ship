"""Resolve optional picture cuts inside verified speech gaps at the VoiceMap sample rate."""

from decimal import Decimal, ROUND_HALF_UP


def position(seconds, rate):
    return int((Decimal(str(seconds)) * rate).to_integral_value(rounding=ROUND_HALF_UP))


def geometry(slot, tag, rate):
    start, end = slot["sample_range"]
    data = tag["data"]
    lo, hi = (position(x, rate) for x in data["source_range"])
    observed = position(data["sample_time"], rate)
    source_start = max(lo, min(observed - (end - start) // 2, hi - (end - start)))
    return start, end, source_start, source_start + end - start, lo, hi


def solve(slots, tags, rate, markers, mode, tolerance, manual):
    windows = {s["anchor"]: geometry(s, tags[s["selected"]["id"]], rate) for s in slots}
    padding = {s["anchor"]: [0, 0] for s in slots}
    boundaries = []
    for left, right in zip(slots, slots[1:]):
        lk, rk = left["anchor"], right["anchor"]
        _, end, _, out, _, hi = windows[lk]
        start, _, inn, _, lo, _ = windows[rk]
        gap = start - end
        preferred = end + gap // 2
        min_cut = max(end, start - (0 if right["locked"] else inn - lo))
        max_cut = min(start, end + (0 if left["locked"] else hi - out))
        min_cut = max(min_cut, preferred - tolerance)
        max_cut = min(max_cut, preferred + tolerance)
        feasible = min_cut <= max_cut
        markers_here = [m for m in markers if min_cut <= m["sample"] <= max_cut]
        markers_here.sort(key=lambda m: (abs(m["sample"] - preferred), m["sample"], m["type"]))
        selected = None
        if mode == "manual":
            candidate = manual.get(lk)
            if candidate is not None and feasible and min_cut <= candidate <= max_cut:
                selected = candidate
            reason = "manual" if selected is not None else "manual_outside_handles_or_unspecified"
        elif mode == "beat":
            if feasible and markers_here:
                selected = markers_here[0]["sample"]
            reason = "verified_marker" if selected is not None else "no_eligible_marker"
        else:
            if feasible:
                selected = min(max_cut, max(min_cut, preferred))
            reason = "semantic_gap" if feasible else "insufficient_visual_handles"
        if selected is not None:
            padding[lk][1] = selected - end
            padding[rk][0] = start - selected
        boundaries.append(
            {
                "after": lk,
                "before": rk,
                "speech_gap_samples": [end, start],
                "feasible_samples": [min_cut, max_cut] if feasible else None,
                "preferred_sample": preferred,
                "cut_sample": selected,
                "offset_samples": selected - preferred if selected is not None else None,
                "resolution": reason,
                "eligible_markers": markers_here,
                "kept_original": selected is None,
            }
        )
    return {
        "rate": rate,
        "policy": mode,
        "tolerance_samples": tolerance,
        "padding_samples": padding,
        "boundaries": boundaries,
    }
