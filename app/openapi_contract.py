"""Executable OpenAPI projection with common envelopes and media wire contracts.

This documents the implemented service, not every proposed external Atlas adapter.
Domain admission (rights, revisions, byte probes) remains an execution-time check.
"""

from copy import deepcopy
import json
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from .schemas import SCHEMAS

ROOT = Path(__file__).resolve().parents[1]


def reference(name):
    return {"$ref": f"#/components/schemas/{name}"}


def envelope(data=None):
    return {
        "type": "object",
        "required": ["data", "trace_id"],
        "properties": {"data": data or {}, "trace_id": {"type": ["string", "null"]}},
        "additionalProperties": False,
    }


def install_openapi(app):
    def document():
        if app.openapi_schema:
            return app.openapi_schema
        from .execution import OPERATION_SCHEMAS, INPUT_SCHEMAS, HANDLERS
        from .media import MAX_BYTES, MAX_CHUNK_BYTES

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            openapi_version=app.openapi_version,
        )
        components = schema.setdefault("components", {})
        types = components.setdefault("schemas", {})
        components["securitySchemes"] = {
            "BearerIdentity": {
                "type": "http",
                "scheme": "bearer",
                "description": "服务端 AI_SHIP_API_KEYS 映射的身份；不接受客户端声明租户或角色。",
            },
            "BrowserSession": {
                "type": "apiKey",
                "in": "cookie",
                "name": "ai_ship_session",
                "description": "同源 HttpOnly、SameSite=Strict 短期会话；通过 Bearer 身份 POST /session 创建。",
            },
        }
        types["SuccessEnvelope"] = envelope()
        types["ErrorEnvelope"] = {
            "type": "object",
            "required": ["error", "trace_id"],
            "properties": {
                "error": {
                    "type": "object",
                    "required": ["code", "message", "details"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {},
                    },
                },
                "trace_id": {"type": ["string", "null"]},
            },
        }
        record = json.loads((ROOT / "contracts/record.schema.json").read_text())
        record.pop("$schema", None)
        for item in record.get("allOf", []):
            kind = item["if"]["properties"]["kind"]["const"]
            item["then"]["properties"]["data"] = reference("Entity_" + kind)
        types["FactVersion"] = record
        types["FactResponse"] = envelope(reference("FactVersion"))
        types["FactListResponse"] = envelope({"type": "array", "items": reference("FactVersion")})
        for kind, entity in SCHEMAS.items():
            types["Entity_" + kind] = {
                k: deepcopy(v) for k, v in entity.items() if k not in {"$schema", "$id"}
            }
        transform = deepcopy(OPERATION_SCHEMAS["transform"])
        transform["properties"].pop("asset_id")
        transform["properties"]["asset_version_id"] = {"type": "string", "minLength": 1}
        transform["required"] = ["project_id", "expected_revision"]
        types["MediaTransformInput"] = transform
        render = deepcopy(OPERATION_SCHEMAS["render"])
        render["required"] = ["project_id", "timeline_id", "expected_revision"]
        types["MediaRenderInput"] = render
        types["MediaUploadStart"] = {
            "type": "object",
            "required": ["project_id", "filename", "size_bytes"],
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "filename": {"type": "string", "minLength": 1},
                "size_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_BYTES},
                "content_hash": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
                "source_note": {"type": "string", "maxLength": 2000},
            },
        }
        types["AudioExtractInput"] = {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 1},
            },
            "description": "路径 asset_id 可以直接指定 AssetVersion 或 Audio 的ID；不接受一个未被执行器消费的额外版本选择字段。",
        }
        types["ShotDetectionInput"] = {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "minimum": 0.01, "maximum": 1, "default": 0.3}
            },
        }
        body_contracts = {
            "/api/v1/media/{asset_id}/transform": "MediaTransformInput",
            "/api/v1/media/render": "MediaRenderInput",
            "/api/v1/media/uploads": "MediaUploadStart",
            "/api/v1/media/{asset_id}/extract-audio": "AudioExtractInput",
            "/api/v1/media/{asset_id}/detect-shots": "ShotDetectionInput",
        }
        fact_routes = {
            ("/api/v1/projects", "post"),
            ("/api/v1/records", "post"),
            ("/api/v1/records/{record_id}", "get"),
            ("/api/v1/records/{record_id}", "patch"),
        }
        for route in (
            "trends",
            "usage/reserve",
            "usage/{usage_id}/release",
            "assets/score",
            "voice/align",
            "approvals",
            "publications",
            "experiments",
            "experiments/{experiment_id}/evaluate",
            "replay/prepare",
        ):
            fact_routes.add(("/api/v1/production/" + route, "post"))
        fact_routes.add(("/api/v1/projects/{project_id}/timeline-exchange/import", "post"))
        group_routes = {}
        fact = reference("FactVersion")
        for route, name, properties in (
            (
                "selection/brief",
                "BriefReviewResponse",
                {key: fact for key in ("brief", "approval", "decision")},
            ),
            (
                "selection/shot",
                "ShotSelectionResponse",
                {
                    "plan": fact,
                    "slot": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "locked": {"type": "boolean"},
                            "evidence_kind": {"type": "string"},
                            "shot_ref": {"type": "object"},
                            "selected_source": {"type": "object"},
                        },
                    },
                },
            ),
            ("music/markers", "MusicMarkerResponse", {"music": fact}),
            ("tags", "TagAndShotResponse", {"tag": fact, "shot": fact}),
            (
                "products/import",
                "ProductImportResponse",
                {
                    "records": {"type": "array", "items": fact},
                    "errors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["line", "reason"],
                            "properties": {
                                "line": {"type": "integer"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                    "imported": {"type": "integer"},
                    "rejected": {"type": "integer"},
                },
            ),
        ):
            data = {
                "type": "object",
                "properties": dict(properties),
                "required": list(properties),
                "additionalProperties": False,
            }
            if route.startswith("selection/") or route == "music/markers":
                data["properties"]["idempotent_replay"] = {"type": "boolean"}
            types[name] = envelope(data)
            group_routes[("/api/v1/production/" + route, "post")] = name
        optional_fact = {"anyOf": [fact, {"type": "null"}]}

        def project_data(name, fields, paths):
            types[name] = envelope(
                {
                    "type": "object",
                    "properties": fields,
                    "required": list(fields),
                    "additionalProperties": False,
                }
            )
            for path, verb in paths:
                group_routes[("/api/v1/production/" + path, verb)] = name

        project_data(
            "AutoEditResponse",
            {"session": fact, **{k: optional_fact for k in ("plan", "timeline", "artifact", "qc")}},
            [
                ("auto-edits", "post"),
                ("auto-edits/{run_id}", "get"),
                *[
                    ("auto-edits/{run_id}/" + action, "post")
                    for action in ("approve", "render", "replace-shot")
                ],
            ],
        )
        project_data(
            "AutoEditList", {"sessions": {"type": "array", "items": fact}}, [("auto-edits", "get")]
        )
        project_data(
            "GenerationResponse",
            {"job": fact, "asset": optional_fact},
            [
                ("generation", "post"),
                ("generation/{id}/refresh", "post"),
                ("generation/{id}/cancel", "post"),
                ("generation/{id}/hold", "post"),
            ],
        )
        project_data(
            "GenerationList", {"jobs": {"type": "array", "items": fact}}, [("generation", "get")]
        )
        summary_fields = {
            k: {"type": "string"} for k in ("id", "label", "model", "output_kind", "status")
        }
        summary_fields["requires_reference"] = {"type": "boolean"}
        project_data(
            "GenerationProfiles",
            {
                "profiles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": summary_fields,
                        "required": list(summary_fields),
                        "additionalProperties": False,
                    },
                }
            },
            [("generation/profiles", "get")],
        )
        list_routes = {
            ("/api/v1/projects", "get"),
            ("/api/v1/records", "get"),
            ("/api/v1/records/{record_id}/history", "get"),
            ("/api/v1/runs", "get"),
        }
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                operation["security"] = [{"BearerIdentity": []}, {"BrowserSession": []}]
                operation["x-local-mode"] = (
                    "Loopback-only development mode requires no token; Host and Origin are checked."
                )
                response_name = (
                    "FactResponse"
                    if (path, method) in fact_routes
                    else "FactListResponse" if (path, method) in list_routes else "SuccessEnvelope"
                )
                response_name = group_routes.get((path, method), response_name)
                responses = operation.setdefault("responses", {})
                responses["default"] = {
                    "description": "其余领域或服务端错误也使用统一错误信封。",
                    "content": {"application/json": {"schema": reference("ErrorEnvelope")}},
                }
                for code, response in responses.items():
                    declared = (
                        response.get("content", {}).get("application/json", {}).get("schema", {})
                    )
                    if code.startswith("2") and "$ref" not in declared:
                        response["content"] = {
                            "application/json": {"schema": reference(response_name)}
                        }
                for code, meaning in {
                    "401": "身份缺失或会话失效",
                    "403": "角色、租户、同源或授权拒绝",
                    "404": "对象或字节不存在",
                    "409": "版本冲突、锁或未核对的结果",
                    "413": "输入资源上限",
                    "422": "类型、语义或路线条件不满足",
                    "503": "能力或依赖暂不可用",
                }.items():
                    responses[code] = {
                        "description": meaning,
                        "content": {"application/json": {"schema": reference("ErrorEnvelope")}},
                    }
                if path in body_contracts and method == "post":
                    operation["requestBody"] = {
                        "required": True,
                        "content": {
                            "application/json": {"schema": reference(body_contracts[path])}
                        },
                    }
                if path.endswith("/file") or path.endswith("/package") or path.endswith("/frame"):
                    mime = (
                        "image/png"
                        if path.endswith("/frame")
                        else (
                            "application/zip"
                            if path.endswith("/package")
                            else "application/octet-stream"
                        )
                    )
                    responses["200"] = {
                        "description": "真实媒体字节；不包装 JSON",
                        "content": {mime: {"schema": {"type": "string", "format": "binary"}}},
                    }
                    if path.endswith("/file"):
                        responses["206"] = {
                            "description": "Range 分段媒体字节",
                            "content": {mime: {"schema": {"type": "string", "format": "binary"}}},
                        }
                        responses["416"] = {"description": "请求范围超出文件"}
                if path == "/api/v1/events/stream":
                    responses["200"] = {
                        "description": "SSE fact 事件；id=持久 seq；Last-Event-ID 重连，revoked 事件终止身份失效连接。",
                        "content": {"text/event-stream": {"schema": {"type": "string"}}},
                    }
                if path == "/api/v1/media/import" or (
                    path == "/api/v1/media/uploads/{entry_id}" and method == "put"
                ):
                    operation["requestBody"] = {
                        "required": True,
                        "content": {
                            "application/octet-stream": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    }
                    operation["x-max-body-bytes"] = (
                        MAX_BYTES if path.endswith("/import") else MAX_CHUNK_BYTES
                    )
                    if method == "put":
                        operation.setdefault("parameters", []).append(
                            {
                                "name": "X-Chunk-SHA256",
                                "in": "header",
                                "required": False,
                                "description": "本块小写 SHA-256；重传同offset同hash幂等，未知结果先GET已确认offset。",
                                "schema": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                            }
                        )
        schema["x-operation-contracts"] = OPERATION_SCHEMAS
        schema["x-workflow-input-bindings"] = INPUT_SCHEMAS
        schema["x-local-route-handlers"] = HANDLERS
        schema["x-contract-boundary"] = (
            "实现接口合同；业务草稿保存不等于执行成功，完整178功能验收状态见contracts/implementation.json。"
        )
        app.openapi_schema = schema
        return schema

    app.openapi = document
