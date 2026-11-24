"""Evidence-bound topic matching, director proposals and structural repetition checks."""

import re
import unicodedata
from datetime import datetime, timezone
from itertools import combinations
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_write
from .errors import DomainError
from .production import current_dependency_problem, record, ref, revise, save, timestamp
from .selection import ChosenVersion, Label, Note, SelectionBody, live, on_request

router = APIRouter(prefix="/production/creative", tags=["creative"])
DIMENSIONS = ("hook", "narrative", "shot_families", "sound")


class CreativeRequest(SelectionBody):
    project_id: Label
    request_id: Label


class Observation(SelectionBody):
    source: ChosenVersion
    original_url: Note
    publisher_group: Label
    published_at: Label
    observed_at: Label
    stance: Literal["supports", "contradicts", "context"]
    note: Note


class TrendReview(CreativeRequest):
    trend: ChosenVersion
    evidence: list[Observation] = Field(min_length=1, max_length=32)


def origin_url(url):
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise DomainError("INVALID_SOURCE", "原始出处地址无法解析。")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise DomainError("INVALID_SOURCE", "原始出处须为可复核的网页 URL。")
    try:
        port = parsed.port
    except ValueError:
        raise DomainError("INVALID_SOURCE", "来源地址端口无效。")
    host = parsed.hostname.casefold().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    if port and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not (k.casefold().startswith("utm_") or k.casefold() in {"fbclid", "gclid"})
    )
    return urlunsplit((parsed.scheme, host, parsed.path or "/", urlencode(query), ""))


@router.post("/verify-trend")
def verify_trend(body: TrendReview, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        trend = record(actor, body.trend.id, "Trend", project=body.project_id)
        if trend["version"] != body.trend.version:
            raise DomainError("STALE_SELECTION", "热点已经修订，请重新查看。", 409)
        if trend["data"].get("status") in {"revoked", "deleted", "deleting", "restricted"} or trend[
            "data"
        ].get("revoked_at"):
            raise DomainError("DEPENDENCY_NOT_CURRENT", "已经撤回的热点不能通过新增核验恢复。", 409)
        seen_ids = set()
        now = datetime.now(timezone.utc)
        observations, source_refs, groups = [], [], []
        for evidence in body.evidence:
            source = record(actor, evidence.source.id, "Source", project=body.project_id)
            if source["version"] != evidence.source.version:
                raise DomainError("STALE_SELECTION", "来源已经修订，请重新查看。", 409)
            if source["id"] in seen_ids:
                raise DomainError("DUPLICATE_OBSERVATION", "同一来源记录不能重复录入。")
            seen_ids.add(source["id"])
            published = timestamp(evidence.published_at)
            observed = timestamp(evidence.observed_at)
            if published > observed or observed > now:
                raise DomainError(
                    "INVALID_WINDOW", "发布时间应不晚于观测时间，观测时间应不晚于当前时间。"
                )
            source_url = origin_url(source["data"].get("canonical_url") or "")
            original = origin_url(evidence.original_url)
            identifiers = {
                ("url", original),
                ("url", source_url),
                ("publisher", normalize(evidence.publisher_group)),
            }
            # A shared origin, observation URL or editorial group joins even transitive repost chains.
            matched = [g for g in groups if g & identifiers]
            for group in matched:
                identifiers.update(group)
                groups.remove(group)
            groups.append(identifiers)
            observations.append(
                {
                    **evidence.model_dump(),
                    "source_url": source_url,
                    "origin": original,
                    "currently_usable": current_dependency_problem(source) is None,
                }
            )
            source_refs.append(ref(source))
        supported_groups = {
            i
            for i, group in enumerate(groups)
            if any(
                ("url", item["origin"]) in group
                and item["stance"] == "supports"
                and item["currently_usable"]
                for item in observations
            )
        }
        conflict = any(
            item["stance"] == "contradicts" and item["currently_usable"] for item in observations
        )
        expired = trend["data"].get("status") == "expired" or bool(
            trend["data"].get("expires_at") and timestamp(trend["data"]["expires_at"]) <= now
        )
        verdict = (
            "expired"
            if expired
            else (
                "conflicting"
                if conflict
                else (
                    "corroborated_as_reviewed"
                    if len(supported_groups) >= 2
                    else "insufficient_independent_sources"
                )
            )
        )
        review = {
            "outcome": verdict,
            "observations": observations,
            "attributed_groups": len(groups),
            "supporting_groups": len(supported_groups),
            "reviewer": actor.actor_id,
            "reviewed_at": now.isoformat(),
            "basis": "human_attributed_origins",
        }
        old_refs = {
            r["id"]: r for r in trend["refs"] if r.get("id") == trend["data"].get("raw_ref")
        }
        old_refs.update({r["id"]: r for r in source_refs})
        changes = {"verification": verdict, "source_review": review}
        if expired:
            changes["status"] = "expired"
        return revise(
            actor, trend, changes, "人工核对原始出处、转载、冲突与时效", list(old_refs.values())
        )

    return ok(on_request("verify-trend", body, actor, apply), request)


class TopicMatch(CreativeRequest):
    project: ChosenVersion
    topic: Label
    audience: Label
    rationale: Note
    allow_hypothesis: bool = False
    trend: ChosenVersion | None = None
    product: ChosenVersion | None = None


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def terms(text):
    tokens = set()
    for word in re.findall(r"[\u3400-\u9fff]+|[^\W_]+", normalize(text)):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            tokens.update(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        else:
            tokens.add(word)
    return tokens


def current_refs(actor, project, inputs):
    """Check immediate frozen facts at the point of use, including source records."""
    records = {}
    for item in inputs:
        for dependency in item.get("refs", []):
            if item["kind"] == "Trend" and (review := item["data"].get("source_review")):
                # Preserve historical observations in the audit; only live evidence supports new briefs.
                if not any(
                    o["source"]["id"] == dependency["id"]
                    and o["currently_usable"]
                    and o["stance"] != "context"
                    for o in review["observations"]
                ):
                    continue
            r = record(actor, dependency["id"], project=project)
            if r["version"] != dependency["version"] or current_dependency_problem(r):
                raise DomainError(
                    "CREATIVE_FACT_CHANGED", "创意引用的来源或事实已修订或失效。", 409
                )
            records[r["id"]] = r
        records[item["id"]] = item
    return [ref(r) for r in records.values()]


@router.post("/match")
def match_topic(body: TopicMatch, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        if body.project.id != body.project_id:
            raise DomainError("PROJECT_MISMATCH", "项目版本与当前项目不符。", 409)
        project = live(actor, body.project, "Project", body.project_id)
        trend = live(actor, body.trend, "Trend", body.project_id) if body.trend else None
        product = live(actor, body.product, "Product", body.project_id) if body.product else None
        if product and not (
            product["data"].get("evidence") or product["data"].get("evidence_source_id")
        ):
            raise DomainError("PRODUCT_EVIDENCE_REQUIRED", "商品须有来源或人工事实证据。")
        verdict = trend["data"].get("verification") if trend else None
        restricted = verdict in {"conflicting", "insufficient_independent_sources"}
        if restricted and not body.allow_hypothesis:
            raise DomainError(
                "TREND_REVIEW_REQUIRED", "来源不足或互相冲突；请选常青主题，或明确按假设使用。", 409
            )
        inputs = [item for item in (project, trend, product) if item]
        refs = current_refs(actor, body.project_id, inputs)
        if product and (sid := product["data"].get("evidence_source_id")):
            source = record(actor, sid, "Source", project=body.project_id)
            if current_dependency_problem(source):
                raise DomainError("CREATIVE_FACT_CHANGED", "商品来源已经失效。", 409)
            if source["id"] not in {r["id"] for r in refs}:
                refs.append(ref(source))
        targets = {"audience": body.audience, "project_goal": project["data"].get("goals")}
        if product:
            targets["product"] = product["data"].get("name") or product["data"].get("SKU")
        if trend:
            targets["trend"] = trend["data"].get("title")
        available = {k: v for k, v in targets.items() if isinstance(v, str) and v.strip()}
        topic_tokens = terms(body.topic)
        overlaps = {k: sorted(topic_tokens & terms(v)) for k, v in available.items()}
        risks = []
        if trend and verdict != "corroborated_as_reviewed":
            risks.append("热点作为待证假设，未取得独立来源交叉支持")
        if trend:
            region = trend["data"].get("region")
            market = project["data"].get("market")
            if (
                region
                and market
                and region != "unspecified"
                and normalize(region) != normalize(market)
            ):
                risks.append("趋势观察地区与项目市场不同，需复核适用范围")
        fit = {
            "method": "lexical_field_coverage",
            "trend_verdict": verdict,
            "matched_terms": overlaps,
            "relevance": sum(bool(v) for v in overlaps.values()) / len(overlaps),
            "popularity": None,
            "trend_observations": trend["data"].get("observations", {}) if trend else None,
            "fact_basis": "evidence_bound" if product else "non_product",
            "risks": risks,
            "human_rationale": body.rationale,
        }
        return save(
            actor,
            "Brief",
            body.project_id,
            {
                "name": body.topic,
                "intent": body.topic,
                "audience": body.audience,
                "status": "candidate",
                "feature_id": "T03",
                "route_id": "T03-R2",
                "project_version": project["version"],
                "topic_mode": "trend" if trend else "evergreen",
                "claims": [],
                "match_evidence": fit,
            },
            refs,
            "按主题与受众匹配规则保存候选和证据",
        )

    return ok(on_request("match-topic", body, actor, apply), request)


Steps = Annotated[list[Label], Field(min_length=1, max_length=16)]


class Direction(SelectionBody):
    name: Label
    hook: Label
    narrative: Steps
    shot_families: Steps
    sound: Label
    script: Note
    hypothesis: Note
    feasibility: Note


class Directions(CreativeRequest):
    brief: ChosenVersion
    proposals: list[Direction] = Field(min_length=2, max_length=6)


def signature(value):
    if isinstance(value, list):
        return tuple(normalize(x) for x in value)
    return normalize(value)


@router.post("/directions")
def propose_directions(body: Directions, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        base = live(actor, body.brief, "Brief", body.project_id)
        refs = current_refs(actor, body.project_id, [base])
        proposals = [p.model_dump() for p in body.proposals]
        names = {normalize(p["name"]) for p in proposals}
        if len(names) != len(proposals):
            raise DomainError("DUPLICATE_DIRECTION", "请给不同路线使用不同名称。")
        for left, right in combinations(proposals, 2):
            # Reordering the same shots/steps does not establish a different concept.
            def content(p, key):
                value = signature(p[key])
                return frozenset(value) if isinstance(value, tuple) else value

            if sum(content(left, k) != content(right, k) for k in DIMENSIONS) < 2:
                raise DomainError("DIRECTIONS_TOO_SIMILAR", "候选至少须在两个结构维度有区别。")
        result = []
        for p in proposals:
            candidate = save(
                actor,
                "Brief",
                body.project_id,
                {
                    **{
                        k: base["data"][k]
                        for k in ("audience", "claims", "project_version")
                        if k in base["data"]
                    },
                    "name": p["name"],
                    "intent": p["script"],
                    "status": "candidate",
                    "feature_id": "T04",
                    "route_id": "T04-R3",
                    "direction": {k: p[k] for k in DIMENSIONS},
                    "hypothesis": p["hypothesis"],
                    "resource_assessment": p["feasibility"],
                },
                refs,
                "保存人工导演路线与实施条件",
            )
            script = save(
                actor,
                "Script",
                body.project_id,
                {
                    "name": p["name"],
                    "text": p["script"],
                    "status": "draft",
                    "claims": base["data"].get("claims") or [],
                    "feature_id": "T04",
                    "route_id": "T04-R3",
                },
                [ref(candidate), *refs],
                "保存候选脚本及其依据",
            )
            result.append({"brief": candidate, "script": script})
        return {"directions": result}

    return ok(on_request("propose-directions", body, actor, apply), request)


class Preflight(CreativeRequest):
    brief: ChosenVersion
    comparisons: list[ChosenVersion] = Field(default_factory=list, max_length=100)
    threshold: float = Field(default=0.7, gt=0, le=1)


def compare(left, right):
    a, b = left.get("direction") or {}, right.get("direction") or {}
    values, locations = {}, []
    for key in DIMENSIONS:
        x, y = a.get(key), b.get(key)
        if key in {"narrative", "shot_families"}:
            if not (
                isinstance(x, list)
                and isinstance(y, list)
                and x
                and y
                and all(isinstance(s, str) for s in x + y)
            ):
                continue
            matches = {normalize(value) for value in y}
            matched = [i for i, value in enumerate(x) if normalize(value) in matches]
            values[key] = len(matched) / len(x)
            locations += [{"dimension": key, "index": i, "value": x[i]} for i in matched]
        elif isinstance(x, str) and isinstance(y, str) and x and y:
            values[key] = int(normalize(x) == normalize(y))
    if isinstance(left.get("intent"), str) and isinstance(right.get("intent"), str):
        x, y = terms(left["intent"]), terms(right["intent"])
        if x and y:
            values["wording"] = len(x & y) / len(x | y)
    return values, locations


@router.post("/preflight")
def creative_preflight(body: Preflight, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        selected = live(actor, body.brief, "Brief", body.project_id)
        ids = [p.id for p in body.comparisons]
        if selected["id"] in ids or len(ids) != len(set(ids)):
            raise DomainError("DUPLICATE_COMPARISON", "请使用其他不同的候选作为参照。")
        history = [live(actor, p, "Brief", body.project_id) for p in body.comparisons]
        results = []
        for item in history:
            scores, hits = compare(selected["data"], item["data"])
            flagged = [k for k, v in scores.items() if v >= body.threshold]
            results.append(
                {
                    "brief": ref(item),
                    "scores": scores,
                    "repeated_locations": hits,
                    "review_dimensions": flagged,
                    "missing_dimensions": [k for k in DIMENSIONS if k not in scores],
                }
            )
        sufficient = bool(results) and all(not r["missing_dimensions"] for r in results)
        return save(
            actor,
            "Insight",
            body.project_id,
            {
                "name": "候选创意结构重复检查",
                "feature_id": "T05",
                "route_id": "T05-R2",
                "scope": {
                    "selected": ref(selected),
                    "comparison_count": len(history),
                    "threshold": body.threshold,
                },
                "evidence": results,
                "coverage": "selected_structures" if sufficient else "insufficient_history",
                "status": (
                    "needs_review" if any(r["review_dimensions"] for r in results) else "recorded"
                ),
                "uncertainty": "仅比较选定版本中的导演结构与文字。相同商品事实或品牌惯例由审阅者判读。",
            },
            [ref(selected), *[ref(item) for item in history]],
            "保存逐维重复证据及命中位置",
        )

    return ok(on_request("creative-preflight", body, actor, apply), request)
