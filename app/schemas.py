"""Engineering validation layered over the unmodified architecture catalogue.

The source contains descriptive types, not a complete SQL/JSON schema. Generated
schemas explicitly mark inferred broad types; business validators below cover the
operations implemented here. Unspecified provider payloads stay in extensions.
"""

import copy
import json
import math
import re
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from .catalog import ENTITIES, FEATURES, ROOT
from .errors import DomainError
from .contract_types import typed_field

NULLABLE = lambda kind: {"type": [kind, "null"]}
COMMON = {
    "name": {"type": "string", "minLength": 1, "maxLength": 240},
    "title": {"type": "string", "maxLength": 10000},
    "description": {"type": "string", "maxLength": 20000},
    "status": {"type": "string", "maxLength": 100},
    "feature_id": {"type": "string", "enum": list(FEATURES)},
    "route_id": {"type": ["string", "null"]},
    "parameters": {"type": "object"},
    "extensions": {"type": "object"},
}
STRICT = {
    "Project": {
        "name": COMMON["name"],
        "goals": {"type": ["string", "array", "object"]},
        "language": {"type": "string", "minLength": 1},
        "market": {"type": "string", "minLength": 1},
        "content_mode": {"enum": ["voiceover", "music", "mixed", "product", "custom"]},
        "duration": {"type": "number", "exclusiveMinimum": 0, "maximum": 86400},
        "width": {"type": "integer", "minimum": 64, "maximum": 8192},
        "height": {"type": "integer", "minimum": 64, "maximum": 8192},
        "budget": {"type": ["number", "object"], "minimum": 0},
    },
    "Product": {
        "SKU": {"type": "string"},
        "quantity": {"type": ["number", "null"], "minimum": 0},
        "price": {
            "anyOf": [
                {"type": "number", "minimum": 0},
                {"type": "string", "pattern": r"^(0|[1-9][0-9]*)(\.[0-9]+)?$"},
                {"type": "null"},
            ]
        },
        "currency": {"type": ["string", "null"], "pattern": "^[A-Z]{3}$"},
        "evidence": {"type": ["array", "object", "string"]},
        "validity": {"type": ["object", "string", "null"]},
    },
    "Metric": {
        "value_or_null": {"type": ["number", "null"]},
        "metric_name": {"type": "string", "minLength": 1},
        "missing_reason": {"type": ["string", "null"]},
        "observed_at": {"type": "string"},
        "unit": {"type": "string"},
        "window": {"type": ["string", "object"]},
    },
    "Script": {
        "text": {"type": "string", "maxLength": 500000},
        "language": {"type": "string"},
        "claims": {"type": "array"},
        "product_bindings": {"type": ["array", "object"]},
    },
    "Workflow": {
        "DAG": {"type": "object"},
        "nodes": {"type": "array"},
        "edges": {"type": "array"},
        "max_steps": {"type": "integer", "minimum": 1, "maximum": 10000},
        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "ModelProfile": {
        "provider": {"type": "string", "minLength": 1},
        "model_id": {"type": "string", "minLength": 1},
        "model": {"type": "string"},
        "credential_ref": {
            "type": ["string", "null"],
            "pattern": r"^(env:[A-Z][A-Z0-9_]{0,127}|secret:[A-Za-z0-9_./-]{1,200})$",
        },
        "endpoint_ref": {"type": ["string", "null"]},
        "sampling_parameters": {"type": "object"},
        "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 3600000},
        "context_limit": {"type": "integer", "minimum": 1},
        "health_status": {
            "enum": [
                "unprobed",
                "healthy",
                "degraded",
                "unavailable",
                "disabled",
                "revoked",
                "retired",
            ]
        },
    },
    "AgentDeployment": {
        "role_bindings": {"type": "array"},
        "model_profile_refs": {"type": "array"},
        "max_rounds": {"type": "integer", "minimum": 1, "maximum": 100},
        "max_delegation_depth": {"type": "integer", "minimum": 0, "maximum": 10},
        "concurrency": {"type": "integer", "minimum": 1, "maximum": 32},
        "token_budget": {"type": "integer", "minimum": 1},
        "cost_budget": {"type": "number", "minimum": 0},
    },
    "TaskContract": {
        "goal": {"type": "string"},
        "success_criteria": {"type": "array"},
        "input_bindings": {"type": ["object", "array"]},
        "required_outputs": {"type": "array"},
        "optional_outputs": {"type": "array"},
        "budget_caps": {"type": "object"},
        "locks": {"type": ["array", "object"]},
        "scope": {"type": ["object", "string"]},
    },
    "Cost": {
        "estimated": {"type": ["number", "null"], "minimum": 0},
        "reserved": {"type": ["number", "null"], "minimum": 0},
        "actual": {"type": ["number", "null"], "minimum": 0},
        "calculated_charge": {"type": ["number", "null"], "minimum": 0},
        "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
        "limits": {"type": ["number", "object"], "minimum": 0},
        "reservation_kind": {"type": "string"},
        "side_effect_status": {"type": "string"},
        "budget_scope": {"type": "string"},
        "unpriced_cost_refs": {"type": "array", "items": {"type": "string"}},
        "pricing_snapshot": {"type": "object"},
    },
    "View": {
        "layout": {"type": ["array", "object"]},
        "operation_scope": {"type": "object"},
        "saved_filters": {"type": ["array", "object"]},
        "selections": {"type": ["array", "object"]},
    },
    "Timeline": {
        "tracks": {"type": "array"},
        "clip_uses": {"type": "array"},
        "sequences": {"type": "array"},
        "duration": {"type": "number", "minimum": 0, "maximum": 86400},
        "timebase_num": {"type": "integer", "minimum": 1},
        "timebase_den": {"type": "integer", "minimum": 1},
        "locks": {"type": ["array", "object"]},
        "keyframe_curves": {"type": "array"},
        "parameter_bindings": {"type": "array"},
    },
    "Approval": {
        "kind": {
            "enum": [
                "review_finding",
                "content_check",
                "rights_check",
                "execution_authorization",
                "manual_receipt",
            ]
        },
        "status": {"enum": ["proposed", "approved", "rejected", "expired", "revoked"]},
        "subject_kind": {"type": "string", "enum": list(ENTITIES)},
        "subject_version": {"type": "integer", "minimum": 1},
    },
    "ImportEntry": {
        "relative_path": {"type": "string"},
        "size_bytes": {"type": "integer", "minimum": 0},
        "transferred_bytes": {"type": "integer", "minimum": 0},
        "status": {"type": "string"},
    },
    "ImportBatch": {"entry_count": {"type": "integer", "minimum": 0}, "status": {"type": "string"}},
    "BlobLocation": {
        "size_bytes": {"type": "integer", "minimum": 0},
        "object_key": {"type": "string", "minLength": 1},
    },
    "Run": {
        "state": {"type": "string"},
        "attempt_id": {"type": ["string", "integer"]},
        "fencing_token": {"type": "integer", "minimum": 0},
    },
    "AgentRole": {
        "label": {"type": "string"},
        "role_kind": {"enum": ["agent", "rule", "human", "hybrid"]},
        "allowed_actions": {"type": "array"},
        "denied_actions": {"type": "array"},
    },
}


def field_name(raw):
    return raw.split(":", 1)[0].replace("[]", "").strip()


def build_schema(kind, entity):
    props = copy.deepcopy(COMMON)
    for raw in entity["fields"]:
        name = field_name(raw)
        # Unknown descriptive fields accept explicit JSON types; do not invent scalar precision.
        desc = entity.get("fieldSchema", {}).get(
            name, "Architecture field; domain semantics remain in source contract."
        )
        props[name] = {
            **typed_field(kind, name, desc),
            "description": desc if isinstance(desc, str) else json.dumps(desc, ensure_ascii=False),
            "x-source-field": raw,
        }
        if ":" in raw and "/" in raw.split(":", 1)[1] and not any(c in raw for c in ("[", "]")):
            options = raw.split(":", 1)[1].split("/")
            if all(re.fullmatch(r"[a-z_]+", s) for s in options):
                props[name] = {
                    "enum": options + [None],
                    "x-source-field": raw,
                    "x-validation-level": "source-enum",
                }
    props.update(copy.deepcopy(STRICT.get(kind, {})))
    required = {
        "Project": ["name"],
        "ModelProfile": ["provider", "model_id"],
        "Script": ["text"],
        "Metric": ["metric_name", "value_or_null"],
    }.get(kind, [])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://ai-ship.local/contracts/{kind}.schema.json",
        "title": entity["label"],
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": True,
        "x-source-entity": kind,
        "x-boundary": "Unspecified field semantics are not claimed to be fully validated. Required fields depend on operation and selected route.",
    }


SCHEMAS = {kind: build_schema(kind, e) for kind, e in ENTITIES.items()}
VALIDATORS = {
    kind: Draft202012Validator(schema, format_checker=FormatChecker())
    for kind, schema in SCHEMAS.items()
}

SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "client_secret",
    "authorization",
    "bearer_token",
    "private_key",
}


def validate_json(value, path="$", depth=0):
    if depth > 32:
        raise DomainError("JSON_DEPTH", "数据层级过深。")
    if isinstance(value, float) and not math.isfinite(value):
        raise DomainError("NONFINITE_NUMBER", "数字必须是有限值。", details={"path": path})
    if isinstance(value, dict):
        if len(value) > 2000:
            raise DomainError("JSON_LIMIT", "对象字段过多。")
        for k, v in value.items():
            if not isinstance(k, str):
                raise DomainError("INVALID_KEY", "对象键必须为文本。")
            if k.lower() in SECRET_KEYS and v not in (None, ""):
                raise DomainError(
                    "SECRET_IN_RECORD",
                    "凭据只能通过服务端 env:/secret: 引用，不得写入业务数据。",
                    details={"path": f"{path}.{k}"},
                )
            validate_json(v, f"{path}.{k}", depth + 1)
    elif isinstance(value, list):
        if len(value) > 20000:
            raise DomainError("JSON_LIMIT", "数组项目过多。")
        for i, v in enumerate(value):
            validate_json(v, f"{path}[{i}]", depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise DomainError("INVALID_JSON", "包含不可保存的值。")


def validate_record(kind, data):
    if kind not in SCHEMAS:
        raise DomainError("UNKNOWN_ENTITY", f"未定义的数据类型：{kind}")
    if not isinstance(data, dict):
        raise DomainError("INVALID_RECORD", "记录数据必须为对象。")
    validate_json(data)
    if len(json.dumps(data, ensure_ascii=False)) > 3_000_000:
        raise DomainError("RECORD_TOO_LARGE", "记录超过 3 MB；媒体请走对象存储。", 413)
    errors = sorted(VALIDATORS[kind].iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise DomainError(
            "SCHEMA_VALIDATION",
            "字段不符合数据合同。",
            details=[
                {"path": ".".join(map(str, e.path)), "message": e.message} for e in errors[:30]
            ],
        )
    if (
        kind == "Metric"
        and "value_or_null" in data
        and data["value_or_null"] is None
        and not data.get("missing_reason")
    ):
        raise DomainError("MISSING_REASON", "指标缺失必须说明原因；缺失不能冒充零。")
    if kind == "Metric" and data.get("value_or_null") is not None and data.get("missing_reason"):
        raise DomainError("METRIC_CONTRADICTION", "有值指标不能同时声明缺失。")
    if kind == "ModelProfile":
        for key in ("temperature", "top_p"):
            val = data.get("sampling_parameters", {}).get(key)
            if val is not None and (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not 0 <= val <= (2 if key == "temperature" else 1)
            ):
                raise DomainError(
                    "MODEL_PARAMETER", f"{key} 超出通用参数范围；供应商仍需能力探测。"
                )
    if kind == "Timeline":
        validate_timeline(data)
    if kind == "AgentDeployment":
        roles = [
            r.get("role_ref", r.get("role_id"))
            for r in data.get("role_bindings", [])
            if isinstance(r, dict)
        ]
        if len(roles) != len(set(roles)):
            raise DomainError("DUPLICATE_ROLE", "同一部署不能重复绑定角色。")
    if kind == "Product" and data.get("price") is not None and not data.get("currency"):
        raise DomainError("MISSING_CURRENCY", "价格必须声明币种。")
    if kind == "VoiceMap":
        status = data.get("timing_status")
        if status in ("aligned", "verified") and not (
            data.get("audio_sample_range") or data.get("time_range")
        ):
            raise DomainError("UNTIMED_ANCHOR", "已对齐语义锚必须有真实音频区间。")
    if kind == "Approval" and data.get("status") == "approved":
        if not all(
            data.get(k) for k in ("subject_id", "subject_version", "subject_hash", "principal")
        ):
            raise DomainError(
                "UNBOUND_APPROVAL", "已批准记录必须绑定对象、精确版本、hash 与批准人。"
            )


def _interval(value, label):
    if isinstance(value, list) and len(value) == 2:
        start, end = value
    elif isinstance(value, dict):
        start = value.get("start", value.get("in", value.get("start_ticks")))
        end = value.get("end", value.get("out", value.get("end_ticks")))
    else:
        raise DomainError("INVALID_INTERVAL", f"{label} 必须是半开区间。")
    if (
        any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in (start, end)
        )
        or start < 0
        or end <= start
    ):
        raise DomainError("INVALID_INTERVAL", f"{label} 必须满足 0 ≤ start < end。")
    return start, end


def validate_timeline(data):
    tracks = data.get("tracks", [])
    if any(not isinstance(t, dict) for t in tracks):
        raise DomainError("INVALID_TRACK", "轨道必须为对象。")
    ids = [t.get("track_id", t.get("id")) for t in tracks]
    if len(ids) != len(set(ids)) or (tracks and any(not x for x in ids)):
        raise DomainError("DUPLICATE_TRACK", "轨道必须有唯一 ID。")
    seq = data.get("sequences", [])
    seq_ids = [s.get("sequence_id", s.get("id")) for s in seq if isinstance(s, dict)]
    if len(seq) != len(seq_ids) or len(seq_ids) != len(set(seq_ids)):
        raise DomainError("DUPLICATE_SEQUENCE", "序列必须有唯一 ID。")
    if seq:
        for t in tracks:
            if t.get("sequence_ref") not in seq_ids:
                raise DomainError("TRACK_SEQUENCE", "轨道必须属于已有序列。")
        for s in seq:
            if not set(s.get("track_refs", [])) <= set(ids):
                raise DomainError("MISSING_TRACK", "序列引用了不存在的轨道。")
        if data.get("primary_sequence_id") and data["primary_sequence_id"] not in seq_ids:
            raise DomainError("MISSING_SEQUENCE", "主序列不存在。")
    clips = data.get("clip_uses", [])
    clip_ids = []
    for c in clips:
        if not isinstance(c, dict):
            raise DomainError("INVALID_CLIP", "片段必须为对象。")
        cid = c.get("clip_use_id", c.get("id"))
        if not cid or cid in clip_ids:
            raise DomainError("DUPLICATE_CLIP", "每次片段使用必须有唯一 ID。")
        clip_ids.append(cid)
        if c.get("track_ref") and c["track_ref"] not in ids:
            raise DomainError("MISSING_TRACK", "片段轨道不存在。")
        for key in ("source_range", "output_range"):
            if key in c:
                _interval(c[key], key)
        if "start" in c and (not isinstance(c["start"], (int, float)) or c["start"] < 0):
            raise DomainError("INVALID_CLIP", "起点不得小于零。")
        if "duration" in c and (not isinstance(c["duration"], (int, float)) or c["duration"] <= 0):
            raise DomainError("INVALID_CLIP", "片段时长必须大于零。")
    for t in tracks:
        if "clip_refs" in t and not set(t["clip_refs"]) <= set(clip_ids):
            raise DomainError("MISSING_CLIP", "轨道引用了不存在的片段。")
    seen = set()
    for key in data.get("keyframe_curves", []):
        if not isinstance(key, dict):
            raise DomainError("INVALID_KEYFRAME", "关键帧必须为对象。")
        domain = key.get("clock_domain")
        anchor = key.get("anchor_mode")
        if (
            domain
            and anchor
            and {
                "sequence_absolute": "fixed_sequence",
                "clip_local": "follow_clip",
                "source_pts": "follow_source",
            }.get(domain)
            != anchor
        ):
            raise DomainError("KEYFRAME_CLOCK", "关键帧时钟与锚定方式不一致。")
        time = key.get("time", {})
        sig = (
            key.get("parameter_path"),
            key.get("clock_owner_ref"),
            json.dumps(time, sort_keys=True),
        )
        if sig in seen:
            raise DomainError("KEYFRAME_CONFLICT", "同一曲线同一时刻存在冲突关键帧。")
        seen.add(sig)


def write_schemas():
    out = ROOT / "contracts" / "entities"
    out.mkdir(exist_ok=True)
    for kind, schema in SCHEMAS.items():
        (out / f"{kind}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        )


if __name__ == "__main__":
    write_schemas()
