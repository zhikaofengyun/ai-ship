"""Read bounded public trend feeds into the same evidence chain as creative review."""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import os
import re
from typing import Literal
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree as ET

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import Field, model_validator

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import Body, digest, record, ref, save, timestamp
from .store import get_store

router = APIRouter(prefix="/production/trends", tags=["trend feeds"])
GOOGLE_RSS = "https://trends.google.com/trending/rss"
YOUTUBE_CHART = "https://www.googleapis.com/youtube/v3/videos"
HT = "{https://trends.google.com/trending/rss}"
MAX_FEED_BYTES = 1_500_000


class FeedSync(Body):
    project_id: str
    provider: Literal["google_trends", "youtube_most_popular", "peertube_trending"]
    region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    instance: Literal["peertube.tv", "framatube.org", "tilvids.com"] | None = None
    limit: int = Field(default=10, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def correct_scope(self):
        if self.provider == "peertube_trending":
            if self.region is not None or not self.instance:
                raise ValueError("PeerTube 榜单按实例读取，须选择站点并留空地区。")
        elif self.region is None or self.instance is not None:
            raise ValueError("地区榜单须选择地区，不能指定实例。")
        return self


def fetch_feed(body):
    headers = {}
    if body.provider == "google_trends":
        url, params = GOOGLE_RSS, {"geo": body.region}
    elif body.provider == "peertube_trending":
        url, params = f"https://{body.instance}/api/v1/videos", {
            "sort": "-trending",
            "count": body.limit,
            "nsfw": "false",
        }
    else:
        key = os.getenv("AI_SHIP_YOUTUBE_DATA_KEY", "").strip()
        if not key:
            raise DomainError("TREND_CREDENTIAL_REQUIRED", "尚未配置 YouTube Data API 密钥。", 503)
        headers["X-Goog-Api-Key"] = key
        url, params = YOUTUBE_CHART, {
            "chart": "mostPopular",
            "regionCode": body.region,
            "part": "snippet,statistics",
            "maxResults": body.limit,
        }
    try:
        with httpx.stream(
            "GET", url, params=params, headers=headers, follow_redirects=False, timeout=12
        ) as response:
            if response.status_code != 200:
                raise DomainError(
                    "TREND_FEED_UNAVAILABLE",
                    "趋势供应商暂未返回可用数据。",
                    503,
                    {"upstream_status": response.status_code},
                )
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=32768):
                data.extend(chunk)
                if len(data) > MAX_FEED_BYTES:
                    raise DomainError("TREND_FEED_TOO_LARGE", "来源响应超过大小限制。", 502)
    except httpx.HTTPError:
        raise DomainError("TREND_FEED_UNAVAILABLE", "无法连接趋势供应商。", 503) from None
    return str(httpx.URL(url, params=params)), bytes(data)


def safe_link(value):
    try:
        url = urlsplit(value or "")
        if (
            url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
        ):
            return value[:3000]
    except ValueError:
        pass
    return None


def rss_rows(raw, body):
    # Both encodings and entities are rejected before parsing this UTF-8-only RSS feed.
    text = raw.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper() or "\x00" in text:
        raise ValueError("unsupported XML")
    channel = ET.fromstring(text).find("channel")
    if channel is None:
        raise ValueError("missing RSS channel")
    for rank, item in enumerate(channel.findall("item")[: body.limit], 1):
        title = (item.findtext("title") or "").strip()[:300]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if not published.tzinfo or not title:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            yield None
            continue
        news = []
        for entry in item.findall(HT + "news_item")[:8]:
            link = safe_link(entry.findtext(HT + "news_item_url"))
            if link:
                news.append(
                    {
                        "url": link,
                        "publisher": (entry.findtext(HT + "news_item_source") or "")[:200],
                    }
                )
        yield {
            "title": title,
            "url": "https://trends.google.com/trends/explore?"
            + urlencode({"geo": body.region, "q": title}),
            "published_at": published.astimezone(timezone.utc).isoformat(),
            "feed_position": rank,
            "search_traffic_label": (item.findtext(HT + "approx_traffic") or "")[:80],
            "related_articles": news,
            "metric_scope": "google_search_trend",
        }


def youtube_rows(raw, body):
    items = json.loads(raw).get("items")
    if not isinstance(items, list):
        raise ValueError("missing chart")
    for rank, item in enumerate(items[: body.limit], 1):
        if not isinstance(item, dict):
            yield None
            continue
        video = item.get("id")
        snippet = item.get("snippet")
        stats = item.get("statistics", {})
        if (
            not isinstance(video, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video)
            or not isinstance(snippet, dict)
            or not isinstance(stats, dict)
        ):
            yield None
            continue
        title = snippet.get("title")
        try:
            published = timestamp(snippet.get("publishedAt", "")).isoformat()
        except DomainError:
            yield None
            continue
        if not isinstance(title, str) or not title.strip():
            yield None
            continue
        yield {
            "title": title.strip()[:300],
            "url": "https://www.youtube.com/watch?v=" + video,
            "published_at": published,
            "chart_position": rank,
            "video_totals": {
                k: int(v)
                for k, v in stats.items()
                if k in {"viewCount", "likeCount", "commentCount"}
                and isinstance(v, str)
                and re.fullmatch(r"[0-9]{1,25}", v)
            },
            "metric_scope": "youtube_video_lifetime_totals_in_regional_chart",
        }


def peertube_rows(raw, body):
    doc = json.loads(raw)
    items = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ValueError("Missing PeerTube video collection")
    for rank, item in enumerate(items[: body.limit], 1):
        if not isinstance(item, dict) or item.get("nsfw") is not False:
            yield None
            continue
        title, video = item.get("name"), item.get("uuid")
        url = safe_link(item.get("url")) if isinstance(item.get("url"), str) else None
        account = item.get("account") or {}
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(video, str)
            or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", video)
            or not url
            or not isinstance(account, dict)
            or urlsplit(url).hostname != account.get("host")
            or not urlsplit(url).path.rstrip("/").endswith("/" + video)
        ):
            yield None
            continue
        try:
            published = timestamp(item.get("publishedAt")).isoformat()
        except (DomainError, TypeError, ValueError):
            yield None
            continue
        yield {
            "title": title.strip()[:300],
            "url": url,
            "published_at": published,
            "chart_position": rank,
            "observing_instance": body.instance,
            "origin_host": urlsplit(url).hostname,
            "origin_video_uuid": video.lower(),
            "federated_video_identity": urlsplit(url).hostname + "/" + video.lower(),
            "video_totals": {
                k: item[k]
                for k in ("views", "likes", "dislikes", "comments")
                if type(item.get(k)) is int and 0 <= item[k] <= 10**15
            },
            "metric_scope": "instance_trending_rank_with_provider_reported_counts",
        }


@router.post("/sync")
def sync(body: FeedSync, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)
    record(actor, body.project_id, "Project")
    url, raw = fetch_feed(body)
    try:
        parser = {
            "google_trends": rss_rows,
            "youtube_most_popular": youtube_rows,
            "peertube_trending": peertube_rows,
        }[body.provider]
        rows = list(parser(raw, body))
    except (ET.ParseError, ValueError, AttributeError, TypeError, RecursionError):
        raise DomainError("TREND_FEED_FORMAT", "来源响应格式有误，未写入观察。", 502) from None
    fetched = datetime.now(timezone.utc)
    fingerprint = sha256(raw).hexdigest()
    result = []
    skipped = 0
    store = get_store()

    def persist(row):
        evidence = {
            **row,
            "feed_url": url,
            "feed_sha256": fingerprint,
            "fetched_at": fetched.isoformat(),
        }
        source = save(
            actor,
            "Source",
            body.project_id,
            {
                "canonical_url": row["url"],
                "origin_kind": body.provider,
                "provenance": evidence,
                "grant_scope": [],
                "license_snapshot": {"status": "observation_only"},
            },
        )
        trend = save(
            actor,
            "Trend",
            body.project_id,
            {
                "title": row["title"],
                "platform": body.provider,
                "region": body.region,
                **({"instance": body.instance} if body.instance else {}),
                "observed_at": fetched.isoformat(),
                "expires_at": (fetched + timedelta(hours=24)).isoformat(),
                "raw_ref": source["id"],
                "observations": row,
                "status": "evidence_recorded",
                "verification": "single_provider_observation",
            },
            [ref(source)],
        )
        return {
            "id": trend["id"],
            "version": trend["version"],
            "title": row["title"],
            "source_id": source["id"],
            "observations": row,
        }

    with store.transaction():
        for row in rows:
            if row is None:
                skipped += 1
                continue
            # Same observation in an hour is one snapshot, regardless of other feed items.
            identity = [
                body.project_id,
                body.provider,
                body.region,
                fetched.strftime("%Y-%m-%dT%H"),
                row,
            ]
            key = "trend-observation:" + digest(identity)
            result.append(
                store.idempotent(actor.tenant_id, key, key, identity, lambda: persist(row))
            )
    return ok(
        {
            "provider": body.provider,
            "region": body.region,
            **({"instance": body.instance} if body.instance else {}),
            "fetched_at": fetched.isoformat(),
            "feed_sha256": fingerprint,
            "items": result,
            "skipped": skipped,
        },
        request,
    )
