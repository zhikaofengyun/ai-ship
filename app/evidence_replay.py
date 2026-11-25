"""D08-R3 recorded observations; H05/H08 read-only prompt and receipt comparison."""

from contextlib import contextmanager

from fastapi import APIRouter, Depends, Query, Request

from .api import ok
from .auth import Actor, current_actor
from .errors import DomainError
from .media import check_current_media_rights
from .production import current_dependency_problem, digest, record, ref
from .providers import wire_payload
from .store import get_store

router = APIRouter(prefix="/harness", tags=["recorded evidence replay"])
KINDS = {"ToolCall", "PlatformEvent"}


@contextmanager
def snapshot():
    # The transaction can only read SQLite. No executor or provider is dispatched by these routes.
    with get_store().read_snapshot() as db:
        was_read_only = db.execute("PRAGMA query_only").fetchone()[0]
        db.execute("PRAGMA query_only=ON")
        try:
            yield db
        finally:
            if not was_read_only:
                db.execute("PRAGMA query_only=OFF")


@router.get("/replay-evidence")
def catalog(
    project_id: str,
    request: Request,
    q: str = Query("", max_length=180),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    actor: Actor = Depends(current_actor),
):
    with snapshot() as db:
        record(actor, project_id, "Project")
        rows = db.execute(
            """SELECT r.id,r.kind,r.latest_version AS version,
            coalesce(json_extract(v.data_json,'$.operation_kind'),json_extract(v.data_json,'$.event_type'),
                json_extract(v.data_json,'$.operation'),r.kind) AS operation,
            coalesce(json_extract(v.data_json,'$.outcome'),json_extract(v.data_json,'$.stage')) AS outcome
            FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
            WHERE r.tenant_id=? AND r.project_id=? AND r.kind IN ('ToolCall','PlatformEvent') AND instr(r.id,?)>0
            ORDER BY r.created_at DESC,r.id LIMIT ? OFFSET ?""",
            (actor.tenant_id, project_id, q, limit + 1, offset),
        ).fetchall()
    return ok(
        {
            "items": [dict(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        },
        request,
    )


def wire_evidence(data):
    saved = data.get("request_snapshot")
    if not isinstance(saved, dict) or not isinstance(saved.get("messages"), list):
        return None
    if any(
        not isinstance(m, dict) or not isinstance(m.get("content"), str) for m in saved["messages"]
    ):
        return None
    frozen = data.get("wire_snapshot")
    if isinstance(frozen, dict) and frozen.get("version") == 1:
        if not isinstance(frozen.get("options"), dict) or not isinstance(
            frozen.get("prefix"), list
        ):
            return None
        payload = {**frozen["options"], "messages": [*frozen["prefix"], *saved["messages"]]}
    elif frozen is None and all(k in saved for k in ("max_tokens", "temperature", "json_output")):
        # Legacy logical messages can include a transport-added JSON instruction. Verify before disclosure.
        payload = wire_payload(
            data.get("requested_provider_model"),
            saved["messages"],
            saved["max_tokens"],
            saved["temperature"],
            saved["json_output"],
        )
        if saved.get("response_schema"):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "edit_result",
                    "strict": True,
                    "schema": saved["response_schema"],
                },
            }
    else:
        return None
    return payload if digest(payload) == data.get("request_payload_hash") else None


def observation(actor, project, rid, version, expected_text, json_key):
    item = record(actor, rid, version=version, project=project)
    if item["kind"] not in KINDS:
        raise DomainError("REPLAY_RECORD_KIND", "请选择工具调用或平台回执的冻结版本。", 422)
    d = item["data"]
    output = {
        "pin": {**ref(item), "hash": item["hash"]},
        "status": "metadata_only",
        "issues": [],
        "operation": d.get("operation_kind")
        or d.get("event_type")
        or d.get("operation")
        or item["kind"],
        "outcome": d.get("outcome") or d.get("stage"),
        "messages": None,
        "response": None,
        "checks": {},
    }
    try:
        check_current_media_rights(get_store(), actor, item, purpose="inspect")
        for dep in item["refs"]:
            current = record(actor, dep["id"])
            if (
                current_dependency_problem(current)
                or current["data"].get("revocation_state") == "revoked"
            ):
                raise DomainError("REPLAY_EVIDENCE_WITHDRAWN", "关联证据已撤回。", 403)
            if current["kind"] == "Memory" and current["data"].get("feature_id") == "D04":
                pin = current["data"].get("source_record") or {}
                pref = record(actor, pin["id"])
                if (
                    pref["version"] != pin["version"]
                    or pref["data"].get("revocation_state") != "active"
                ):
                    raise DomainError("REPLAY_EVIDENCE_WITHDRAWN", "关联偏好已撤回或被更正。", 403)
    except DomainError as error:
        output.update(status="masked", issues=[error.code])
        return output
    output["dependencies"] = [
        {**ref(v), "hash": v["hash"]}
        for r in item["refs"]
        for v in [record(actor, r["id"], version=r["version"])]
    ]
    if item["kind"] == "PlatformEvent":
        output["status"] = "recorded_receipt"
        output["receipt"] = {
            k: d.get(k)
            for k in (
                "event_type",
                "source_kind",
                "provider",
                "event_time",
                "received_at",
                "publication_id",
                "signature_result",
                "dedupe_key",
            )
        }
        return output
    if output["operation"] != "model.chat":
        output["receipt"] = {
            k: d[k]
            for k in (
                "remote_id",
                "operation_kind",
                "stage",
                "outcome",
                "graph_hash",
                "profile_hash",
                "request_hash",
                "side_effect",
            )
            if k in d
        }
        output["issues"] = ["TOOL_METADATA_ONLY"]
        return output
    payload = wire_evidence(d)
    output["fingerprints"] = {
        "wire_request": d.get("request_payload_hash"),
        "provider_response": d.get("provider_response_sha256"),
        "provider_system": d.get("provider_system_fingerprint"),
        "model_profile": d.get("model_profile_ref"),
        "requested_model": d.get("requested_provider_model"),
        "reported_model": d.get("actual_provider_model"),
        "provider_identity_hash": (
            digest(d["provider_identity_snapshot"]) if d.get("provider_identity_snapshot") else None
        ),
    }
    output["receipt"] = {
        "provider_request_id": d.get("provider_request_id"),
        "usage": d.get("usage"),
        "error_code": d.get("error_code"),
    }
    if not payload:
        output.update(status="masked", issues=["WIRE_SNAPSHOT_UNVERIFIED"])
        return output
    output["transmission"] = (
        "not_sent"
        if d.get("outcome") == "not_sent"
        else (
            "provider_response_observed"
            if d.get("provider_request_id")
            or d.get("provider_response_sha256")
            or d.get("outcome") in {"succeeded", "rejected", "output_incomplete"}
            else "unconfirmed"
        )
    )
    output["messages"] = payload["messages"]
    output["fingerprints"]["messages"] = digest(payload["messages"])
    output["fingerprints"]["system_instructions"] = digest(
        [m for m in payload["messages"] if m.get("role") == "system"]
    )
    output["fingerprints"]["conversation"] = digest(
        [m for m in payload["messages"] if m.get("role") != "system"]
    )
    output["inference_parameters"] = {k: v for k, v in payload.items() if k != "messages"}
    response = d.get("response")
    if (
        d.get("outcome") != "succeeded"
        or not isinstance(response, dict)
        or response.get("finish_reason") not in {"stop", "eos_token"}
        or (d.get("normalized_response_hash") and digest(response) != d["normalized_response_hash"])
        or not isinstance(response.get("content"), str)
        or response.get("model_identity_status") != "provider_reported_matches_request"
    ):
        output.update(status="masked", issues=["COMPLETE_RESPONSE_UNAVAILABLE"])
    else:
        output.update(status="recorded_response", response=response)
        if expected_text:
            output["checks"]["contains_text"] = expected_text in response["content"]
        if json_key:
            output["checks"]["has_json_key"] = (
                isinstance(response.get("json"), dict) and json_key in response["json"]
            )
    return output


@router.get("/replay-evidence/{record_id}")
def replay(
    record_id: str,
    project_id: str,
    version: int = Query(ge=1),
    *,
    request: Request,
    compare_id: str | None = None,
    compare_version: int | None = Query(None, ge=1),
    expected_text: str = Query("", max_length=300),
    json_key: str = Query("", max_length=100),
    actor: Actor = Depends(current_actor),
):
    if bool(compare_id) != (compare_version is not None):
        raise DomainError("REPLAY_COMPARISON_PIN", "对照需要记录 ID 和确切版本。", 422)
    with snapshot():
        left = observation(actor, project_id, record_id, version, expected_text, json_key)
        right = (
            observation(actor, project_id, compare_id, compare_version, expected_text, json_key)
            if compare_id
            else None
        )
    comparison = None
    if right:
        if left["status"] == right["status"] == "recorded_response":
            comparison = {
                "status": "observed",
                "same_messages": left["messages"] == right["messages"],
                "same_system_instructions": left["fingerprints"]["system_instructions"]
                == right["fingerprints"]["system_instructions"],
                "same_parameters": left["inference_parameters"] == right["inference_parameters"],
                "same_model_profile": left["fingerprints"]["model_profile"]
                == right["fingerprints"]["model_profile"],
                "same_recorded_content": left["response"]["content"]
                == right["response"]["content"],
                "same_evidence_versions": [
                    p for p in left["dependencies"] if p["kind"] not in {"Cost", "ModelProfile"}
                ]
                == [p for p in right["dependencies"] if p["kind"] not in {"Cost", "ModelProfile"}],
            }
        else:
            comparison = {"status": "incomplete"}
    return ok(
        {
            "mode": "recorded_evidence",
            "live_calls": 0,
            "external_writes": 0,
            "left": left,
            "right": right,
            "comparison": comparison,
        },
        request,
    )
