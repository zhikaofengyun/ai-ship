"""Acquire original Commons video bytes selected against a fixed author, license and digest."""

import json
from hashlib import sha1
from html.parser import HTMLParser
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictInt

from .api import ok
from .auth import Actor, current_actor, require_write, require_approval, actor_still_authorized
from .errors import DomainError
from .media import MAX_BYTES, import_saved_file
from .production import Body, digest, record
from .store import get_store, now

router = APIRouter(prefix="/production/source-media", tags=["media acquisition"])
API = "https://commons.wikimedia.org/w/api.php"
AGENT = {"User-Agent": "AI-Ship/1.0 (https://github.com/zhikaofengyun/ai-ship)"}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def metadata(info, key):
    value = info.get("extmetadata", {}).get(key, {}).get("value")
    if not isinstance(value, str):
        return ""
    parser = PlainText()
    parser.feed(value[:12000])
    return " ".join(" ".join(parser.parts).split())[:1200]


def approved_url(url, host):
    try:
        target = urlsplit(url)
        return target.scheme == "https" and target.netloc == host and target.path.startswith("/")
    except (ValueError, TypeError):
        return False


def lookup(params):
    query = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "prop": "imageinfo",
        "iiprop": "url|size|sha1|mime|extmetadata|timestamp",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|AttributionRequired",
        "uselang": "en",
        **params,
    }
    try:
        with httpx.stream(
            "GET", API, params=query, headers=AGENT, timeout=15, follow_redirects=False
        ) as response:
            response.raise_for_status()
            buf = bytearray()
            for chunk in response.iter_bytes(32768):
                buf.extend(chunk)
                if len(buf) > 2_000_000:
                    raise ValueError()

        data = json.loads(buf)
        if not isinstance(data, dict) or "error" in data:
            raise ValueError()
        pages = data.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            raise ValueError()
        result = []
        for page in sorted(pages, key=lambda p: p.get("index", 0)):
            if page.get("ns") != 6 or not page.get("imageinfo"):
                continue
            info = page["imageinfo"][0]
            if not (
                str(info.get("mime", "")).startswith("video/")
                and approved_url(info.get("url"), "upload.wikimedia.org")
                and approved_url(info.get("descriptionurl"), "commons.wikimedia.org")
                and re.fullmatch("[0-9a-f]{40}", str(info.get("sha1")))
                and type(info.get("size")) is int
                and 0 < info["size"] <= MAX_BYTES
            ):
                continue
            result.append(
                {
                    "page_id": page["pageid"],
                    "title": page["title"],
                    "mime": info["mime"],
                    "size_bytes": info["size"],
                    "sha1": info["sha1"],
                    "description_url": info["descriptionurl"],
                    "download_url": info["url"],
                    "license": metadata(info, "LicenseShortName"),
                    "license_url": metadata(info, "LicenseUrl"),
                    "author": metadata(info, "Artist"),
                    "credit": metadata(info, "Credit"),
                    "attribution_required": metadata(info, "AttributionRequired"),
                    "file_timestamp": info.get("timestamp"),
                }
            )
        return result
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise DomainError("COMMONS_UNAVAILABLE", "无法核对 Wikimedia Commons 来源。", 503) from None


class SourceSettings(Body):
    project_id: str
    expected_revision: StrictInt = Field(ge=0)
    enabled: bool


def settings(store, actor, project_id):
    with store.connection() as db:
        row = db.execute(
            "SELECT revision,enabled FROM source_media_settings WHERE tenant=? AND project=?",
            (actor.tenant_id, project_id),
        ).fetchone()
    return {
        "provider": "wikimedia_commons",
        "revision": row["revision"] if row else 0,
        "enabled": bool(row["enabled"]) if row else True,
        "max_bytes": MAX_BYTES,
    }


def check_source(store, actor, project_id):
    current = settings(store, actor, project_id)
    if not current["enabled"]:
        raise DomainError("SOURCE_MEDIA_DISABLED", "此项目已停用 Wikimedia Commons 来源。", 409)
    return current["revision"]


@router.get("/settings")
def read_settings(project_id: str, request: Request, actor: Actor = Depends(current_actor)):
    record(actor, project_id, "Project")
    return ok(settings(get_store(), actor, project_id), request)


@router.post("/settings")
def configure(body: SourceSettings, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)
    record(actor, body.project_id, "Project")
    store = get_store()
    with store.transaction(), store.connection() as db:
        prior = settings(store, actor, body.project_id)
        if prior["revision"] != body.expected_revision:
            raise DomainError("REVISION_CONFLICT", "来源配置已经变更，请重读。", 409)
        revision = prior["revision"] + 1
        db.execute(
            "INSERT INTO source_media_settings VALUES(?,?,?,?) ON CONFLICT(tenant,project) DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled",
            (actor.tenant_id, body.project_id, revision, body.enabled),
        )
        store.append_event(
            tenant_id=actor.tenant_id,
            project_id=body.project_id,
            actor_id=actor.actor_id,
            aggregate_id=body.project_id,
            aggregate_version=revision,
            event_type="source_media.configured",
            payload={
                "provider": "wikimedia_commons",
                "enabled": body.enabled,
                "source_revision": revision,
            },
        )
        return ok(settings(store, actor, body.project_id), request)


@router.get("/search")
def search(
    project_id: str,
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(8, ge=1, le=30),
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    check_source(get_store(), actor, project_id)
    rows = lookup(
        {
            "generator": "search",
            "gsrsearch": q.strip() + " filetype:video",
            "gsrnamespace": 6,
            "gsrlimit": limit,
        }
    )
    return ok(
        {"items": [{k: v for k, v in row.items() if k != "download_url"} for row in rows]}, request
    )


class Acquisition(Body):
    project_id: str
    page_id: StrictInt = Field(gt=0)
    expected_sha1: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_license: str = Field(min_length=1, max_length=1200)
    expected_author: str = Field(max_length=1200)


def download(row, path):
    checksum = sha1()
    size = 0
    try:
        with (
            httpx.stream(
                "GET", row["download_url"], headers=AGENT, timeout=30, follow_redirects=False
            ) as response,
            path.open("wb") as stream,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(65536):
                size += len(chunk)
                if size > row["size_bytes"]:
                    raise DomainError("COMMONS_BYTES_CHANGED", "下载字节超过指定版本。", 409)
                checksum.update(chunk)
                stream.write(chunk)
    except httpx.HTTPError:
        raise DomainError("COMMONS_DOWNLOAD_FAILED", "原件下载未完成，可以重试。", 503) from None
    if size != row["size_bytes"] or checksum.hexdigest() != row["sha1"]:
        raise DomainError("COMMONS_BYTES_CHANGED", "来源字节与所选版本的长度或摘要不同。", 409)


@router.post("/import")
def acquire(body: Acquisition, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    record(actor, body.project_id, "Project")
    revision = check_source(get_store(), actor, body.project_id)
    found = lookup({"pageids": body.page_id})
    row = next((r for r in found if r["page_id"] == body.page_id), None)
    if not row or (row["sha1"], row["license"], row["author"]) != (
        body.expected_sha1,
        body.expected_license,
        body.expected_author,
    ):
        raise DomainError(
            "COMMONS_SELECTION_CHANGED", "来源版本、许可或署名已变化，请重新查看。", 409
        )
    store = get_store()
    ident = [
        body.project_id,
        row["page_id"],
        row["sha1"],
        row["license"],
        row["license_url"],
        row["author"],
        row["credit"],
    ]
    key = "commons-import:" + digest(ident)
    # Avoid re-downloading the binary on an ordinary retry; the write transaction
    # below still decides the winner if multiple new requests arrive together.
    with store.connection() as db:
        prior = db.execute(
            "SELECT response_json FROM command_results WHERE tenant_id=? AND idempotency_key=?",
            (actor.tenant_id, key),
        ).fetchone()
    if prior:

        return ok({**json.loads(prior[0]), "idempotent_replay": True}, request)
    with TemporaryDirectory(prefix="ai-ship-commons-") as directory:
        name = Path(row["title"].removeprefix("File:")).name
        path = Path(directory) / "original"
        download(row, path)

        def persist():
            if check_source(store, actor, body.project_id) != revision:
                raise DomainError(
                    "SOURCE_CONFIG_CHANGED", "下载期间适配器配置已变更，请重新核对。", 409
                )
            if not actor_still_authorized(actor):
                raise DomainError("AUTHORIZATION_REVOKED", "素材入库权限已撤回。", 403)
            imported = import_saved_file(
                store,
                actor,
                body.project_id,
                name,
                path,
                "来源署名与许可已留档，具体用途需核实。",
                source_media={**row, "adapter_revision": revision, "retrieved_at": now()},
            )
            src = record(
                actor,
                imported["import_entry"]["data"]["source_id"],
                "Source",
                project=body.project_id,
            )
            return {
                **{k: imported[k] for k in ("asset", "version", "import_entry", "duplicate")},
                "source": src,
            }

        result = store.idempotent(actor.tenant_id, key, key, ident, persist)
    return ok(result, request)
