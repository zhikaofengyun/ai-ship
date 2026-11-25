"""Fit a director turn to its real provider budget while keeping editorial constraints intact."""

from copy import deepcopy
import json

from .errors import DomainError
from .production import digest
from .providers import input_upper_bound, wire_messages as provider_messages

POLICY_VERSION = "grounded-edit-context-v1"


def edit_messages(prompt, payload):
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def turn_bound(messages, schema):
    return input_upper_bound(provider_messages(messages, True), schema)


def fit_turn(prompt, payload, schema, limit, reserved_output):
    """Count the same serialized inputs as the provider. Only omit recoverable observations."""
    full_hash = digest(payload)
    messages = edit_messages(prompt, payload)
    initial = turn_bound(messages, schema)
    receipt = {
        "policy": POLICY_VERSION,
        "full_context_hash": full_hash,
        "full_input_upper_bound": initial,
        "reserved_output": reserved_output,
        "context_limit": limit,
        "omissions": [],
    }
    remaining = limit - reserved_output
    if initial > remaining:
        payload = deepcopy(payload)
        sources = payload.get("sources", [])
        # Never remove a cut, a brief, a review, a source identity, or measured facts for citation.
        # First discard per-word OCR boxes while retaining words, order, time and confidence status.
        changes = []
        for source in sources:
            ocr = source.get("ocr")
            if isinstance(ocr, dict) and ocr.get("words"):
                words = ocr["words"]
                reduced = [{k: w[k] for k in ("text", "confidence") if k in w} for w in words]
                if reduced != words:
                    ocr["words"] = reduced
                    changes.append(source["asset_id"])
        if changes:
            receipt["omissions"].append({"kind": "ocr_geometry", "assets": changes})
        messages = edit_messages(prompt, payload)
        if turn_bound(messages, schema) > remaining:
            changes = []
            for source in sources:
                frames = source.get("sampled_frames", [])
                if len(frames) > 2:
                    changes.append(
                        {"asset": source["asset_id"], "times": [f["at"] for f in frames[1:-1]]}
                    )
                    source["sampled_frames"] = [frames[0], frames[-1]]
            if changes:
                receipt["omissions"].append({"kind": "interior_frame_samples", "sources": changes})
            messages = edit_messages(prompt, payload)
    bound = turn_bound(messages, schema)
    receipt.update(sent_context_hash=digest(payload), sent_input_upper_bound=bound)
    if bound > remaining:
        raise DomainError(
            "CONTEXT_BUDGET",
            "保留制作目标、原片、审核依据和输出预算后仍超出模型窗口，请选择更大窗口或缩小本次选材。",
            422,
            receipt,
        )
    return messages, receipt


def revision_signature(plan):
    """Compare executable choices and outstanding objections, independent of generated titles."""
    return digest(
        {
            "cuts": [[s["asset_id"], s["source_in"], s["source_out"]] for s in plan["shots"]],
            "missing": plan.get("missing", []),
            "blocker": plan["review_blocker"],
            "reviews": [
                {
                    k: r.get(k)
                    for k in (
                        "role",
                        "decision",
                        "evidence_id",
                        "observed_value",
                        "reason",
                        "evidence_problem",
                    )
                }
                for r in plan.get("reviews", [])
            ],
        }
    )
