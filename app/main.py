from contextlib import asynccontextmanager
from pathlib import Path
import importlib
import importlib.util
import logging
import os
import re
import sqlite3
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from .api import router as core_router
from .errors import DomainError
from .store import get_store

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("ai_ship")


@asynccontextmanager
async def lifespan(app):
    from .batches import batch_workers
    from .material_worker import material_worker

    with batch_workers(), material_worker():
        yield


app = FastAPI(
    title="AI Ship 控制面 API",
    version="0.1.0",
    description="按 AI_Video_Agent_Atlas schema 6.0 实现的本地控制面。外部能力均单独准入；不将目录覆盖报告为执行完成。",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1500)


@app.middleware("http")
async def trace_headers(request: Request, call_next):
    request.state.trace_id = uuid.uuid4().hex
    full_import = request.method == "POST" and request.url.path == "/api/v1/media/import"
    chunk_upload = (
        request.method == "PUT"
        and re.fullmatch(r"/api/v1/media/uploads/[A-Za-z0-9_-]+", request.url.path) is not None
    )
    limit = 4 * 1024 * 1024 if chunk_upload else 4_000_000
    length = request.headers.get("content-length")
    if length:
        try:
            size = int(length)
        except ValueError:
            size = -1
        if size < 0:
            return JSONResponse(
                {
                    "error": {
                        "code": "INVALID_LENGTH",
                        "message": "请求长度无效。",
                        "details": None,
                    },
                    "trace_id": request.state.trace_id,
                },
                status_code=400,
            )
        if not full_import and size > limit:
            return JSONResponse(
                {
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "message": f"请求超过当前接口 {limit} 字节限制。",
                        "details": None,
                    },
                    "trace_id": request.state.trace_id,
                },
                status_code=413,
            )
    if (
        not full_import
        and request.method in ("POST", "PUT", "PATCH")
        and request.url.path.startswith("/api/")
    ):
        # Limit actual received bytes as well as the declared Content-Length.
        # Small JSON and one 4 MiB block are buffered once by Starlette's cached
        # request; whole-file media uploads retain their endpoint's streaming cap.
        chunks = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                return JSONResponse(
                    {
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": f"实际请求超过 {limit} 字节限制。",
                            "details": None,
                        },
                        "trace_id": request.state.trace_id,
                    },
                    status_code=413,
                )
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = (
        "no-referrer"
        if request.url.path == "/api/v1/production/tiktok/login/callback"
        else "same-origin"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    return JSONResponse(
        {
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "trace_id": getattr(request.state, "trace_id", None),
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo raw supplied credentials/body in validation errors.
    details = [
        {"path": ".".join(map(str, e["loc"])), "message": e["msg"], "type": e["type"]}
        for e in exc.errors()
    ]
    return JSONResponse(
        {
            "error": {
                "code": "REQUEST_VALIDATION",
                "message": "请求不符合接口合同。",
                "details": details,
            },
            "trace_id": getattr(request.state, "trace_id", None),
        },
        status_code=422,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        {
            "error": {"code": "HTTP_ERROR", "message": str(exc.detail), "details": None},
            "trace_id": getattr(request.state, "trace_id", None),
        },
        status_code=exc.status_code,
    )


@app.exception_handler(sqlite3.OperationalError)
async def db_operational(request: Request, exc: sqlite3.OperationalError):
    logger.exception("Database operation failed trace=%s", getattr(request.state, "trace_id", None))
    return JSONResponse(
        {
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "存储暂时不可用；请保留请求 ID 后重试。",
                "details": None,
            },
            "trace_id": getattr(request.state, "trace_id", None),
        },
        status_code=503,
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    trace = getattr(request.state, "trace_id", None)
    logger.error("Unexpected server failure type=%s trace=%s", type(exc).__name__, trace)
    return JSONResponse(
        {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务处理失败，当前操作未被报告为成功。请保留请求 ID 以便核对。",
                "details": None,
            },
            "trace_id": trace,
        },
        status_code=500,
    )


app.include_router(core_router, prefix="/api/v1")
for module_name in (
    "media",
    "directory_import",
    "render_recovery",
    "captions",
    "selection",
    "asset_lifecycle",
    "asset_families",
    "creative",
    "trend_feeds",
    "tiktok_metrics",
    "tiktok_login",
    "tiktok_inbox",
    "source_changes",
    "source_media",
    "rhythm",
    "providers",
    "production",
    "account_quota",
    "commercial_handoff",
    "publication_lifecycle",
    "score_policies",
    "comment_feedback",
    "post_reviews",
    "auto_edits",
    "batch_publications",
    "batches",
    "generation",
    "execution",
    "harness_qualification",
    "evidence_replay",
    "context_pack",
    "timeline_exchange",
    "asset_index",
    "voice_studio",
    "speech",
    "narration",
    "script_readings",
    "variants",
    "soundtrack",
):
    if importlib.util.find_spec("app." + module_name):
        module = importlib.import_module("app." + module_name)
        app.include_router(module.router, prefix="/api/v1")

if (ROOT / "web").exists():
    app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")

from .openapi_contract import install_openapi

install_openapi(app)
