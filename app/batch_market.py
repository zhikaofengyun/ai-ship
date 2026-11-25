"""P03-R2. Evidence-bound market briefs and exact copy checks before local production."""

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from .api import ok
from .batch_observability import duration_of_timeline
from .auth import Actor, current_actor, require_approval
from .commercial_handoff import account_market, pinned, product_fact, source_evidence
from .errors import DomainError
from .production import Body, record, ref, save, timestamp
from .selection import ChosenVersion, Label
from .store import digest, get_store

router = APIRouter(prefix="/market-briefs", tags=["market localization"])


class MarketBrief(Body):
    project_id: str
    name: Label
    request_id: Label
    market: Label
    language: str = Field(pattern=r"^[a-zA-Z]{2,8}(?:-[A-Za-z0-9]{1,8})*$", max_length=40)
    account: ChosenVersion
    source: ChosenVersion
    original_script: ChosenVersion
    localized_script: ChosenVersion
    product: ChosenVersion | None = None
    audience_hypothesis: str = Field(min_length=1, max_length=800)
    prohibited_phrases: list[Label] = Field(default_factory=list, max_length=40)
    valid_until: str


def text_copy(s):
    return " ".join(unicodedata.normalize("NFKC", str(s or "")).split())


def numbers(s):
    return Counter(re.findall(r"[-+]?[0-9]+(?:[.,][0-9]+)*", text_copy(s)))


def facts(actor, body):
    pid = body.project_id
    if timestamp(body.valid_until) <= datetime.now(timezone.utc):
        raise DomainError("MARKET_BRIEF_EXPIRED", "这份市场简报的有效期已结束。", 409)
    project = record(actor, pid, "Project")
    if (
        text_copy(project["data"].get("market")).casefold() != body.market.casefold()
        or text_copy(project["data"].get("language")).casefold() != body.language.casefold()
    ):
        raise DomainError("MARKET_PROJECT_SCOPE", "项目当前市场或语言与简报不一致。", 409)
    account = pinned(actor, pid, body.account, "Account")
    if not account_market(account, body.market):
        raise DomainError("MARKET_ACCOUNT_SCOPE", "缺少目标账号在该市场的有效记录。", 409)
    src = source_evidence(actor, pid, body.source)
    orig = pinned(actor, pid, body.original_script, "Script")
    localized = pinned(actor, pid, body.localized_script, "Script")
    original = text_copy(orig["data"].get("text"))
    target = text_copy(localized["data"].get("text"))
    if (
        not original
        or not target
        or text_copy(localized["data"].get("language")).casefold() != body.language.casefold()
    ):
        raise DomainError(
            "MARKET_SCRIPT_LANGUAGE", "原文与本地化脚本均须有正文，且译文脚本语种须匹配。", 409
        )
    pins = [ref(project), ref(account), ref(src), ref(orig), ref(localized)]
    if numbers(original) != numbers(target):
        raise DomainError(
            "MARKET_NUMERIC_DRIFT",
            "源脚本与本地化脚本中的数字、符号或出现次数不同，需人工复核。",
            409,
        )
    if any(text_copy(term).casefold() in target.casefold() for term in body.prohibited_phrases):
        raise DomainError(
            "MARKET_PROHIBITED_EXPRESSION", "本地化脚本包含该市场简报禁止的表达。", 409
        )
    sku = None
    if body.product:
        fact, origin = product_fact(actor, pid, body.product, body.market)
        if not fact["market"]:
            raise DomainError("MARKET_PRODUCT_SCOPE", "商品证据未指定本次目标市场。", 409)
        sku = text_copy(fact["SKU"])
        pattern = r"(?<![A-Za-z0-9_-])" + re.escape(sku) + r"(?![A-Za-z0-9_-])"
        src_count = len(re.findall(pattern, original))
        if not src_count or src_count != len(re.findall(pattern, target)):
            raise DomainError("MARKET_SKU_DRIFT", "两个脚本须按原文保留已核商品 SKU。", 409)
        # Only check explicitly written ISO prices and quantities with supported unit labels.
        # Other commercial statements remain subject to review.
        for line in (original, target):
            money = re.findall(
                r"(?<![A-Za-z])" + re.escape(fact["currency"]) + r"\s*([0-9]+(?:[.,][0-9]+)*)", line
            )
            try:
                exact_prices = all(Decimal(v) == Decimal(fact["price"]) for v in money)
            except InvalidOperation:
                exact_prices = False
            if not exact_prices:
                raise DomainError(
                    "MARKET_PRICE_DRIFT", "明确书写的币种金额与当前商品凭据不一致。", 409
                )
            quantities = re.findall(r"(?i)\bpack of\s+([0-9]+)\b", line)
            quantities += re.findall(
                r"(?<![A-Za-z0-9.])([0-9]+)\s*(?:件|个|個|pieces\b|units\b|pcs\b)",
                line,
                flags=re.IGNORECASE,
            )
            if any(int(value) != fact["quantity"] for value in quantities):
                raise DomainError(
                    "MARKET_QUANTITY_DRIFT", "明确书写的件数与当前商品凭据不一致。", 409
                )
        pins.extend([fact["product"], ref(origin)])
    return (
        target,
        pins,
        {
            "market": body.market,
            "language": body.language,
            "account": ref(account),
            "source": ref(src),
            "source_script": ref(orig),
            "localized_script": ref(localized),
            "numeric_literals": dict(numbers(target)),
            "product_sku": sku,
            "valid_until": body.valid_until,
        },
    )


@router.post("")
def create(body: MarketBrief, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        _, pins, checks = facts(actor, body)
        result = save(
            actor,
            "Brief",
            body.project_id,
            {
                "name": body.name,
                "status": "approved",
                "feature_id": "P03",
                "route_id": "P03-R2",
                "market_rules": body.model_dump(exclude={"request_id"}),
                "market_checks": checks,
                "audience_hypothesis": body.audience_hypothesis,
                "audience_basis": "operator_hypothesis",
                "source_claims_verified": "numeric_and_sku_preservation_only",
                "reviewer": actor.actor_id,
            },
            pins,
            "保存有地区有效期的规则简报与原文证据",
        )
        approval = save(
            actor,
            "Approval",
            body.project_id,
            {
                "kind": "review_finding",
                "status": "approved",
                "principal": actor.actor_id,
                "subject_kind": "Brief",
                "subject_id": result["id"],
                "subject_version": result["version"],
                "subject_hash": result["hash"],
                "operation": "batch_market_rules",
                "account_id": body.account.id,
                "expires_at": body.valid_until,
            },
            [ref(result), *pins],
            "确认本地化规则的精确版本和市场范围",
        )
        return {"brief": result, "approval": approval}

    key = "market-brief:" + digest([body.project_id, body.request_id])
    return ok(
        get_store().idempotent(
            actor.tenant_id, key, key, {"actor": actor.actor_id, **body.model_dump()}, apply
        ),
        request,
    )


@router.get("")
def list_briefs(
    project_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    actor: Actor = Depends(current_actor),
):
    record(actor, project_id, "Project")
    with get_store().read_snapshot() as db:
        ids = db.execute(
            """SELECT r.id FROM records r JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
                WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Brief' AND json_extract(v.data_json,'$.route_id')='P03-R2' AND json_type(v.data_json,'$.market_rules')='object'
                ORDER BY r.created_at DESC LIMIT ?""",
            (actor.tenant_id, project_id, limit),
        )
        return ok(
            {"briefs": [record(actor, row[0], "Brief", project=project_id) for row in ids]}, request
        )


def check(actor, recipe, timeline=None):
    brief = pinned(actor, recipe.project_id, recipe.market_brief, "Brief")
    d = brief["data"]
    if d.get("route_id") != "P03-R2" or d.get("status") != "approved" or not d.get("market_rules"):
        raise DomainError("MARKET_BRIEF_REQUIRED", "请选择已经核对的市场规则简报。", 409)
    with get_store().connection() as db:
        accepted = db.execute(
            """SELECT r.id FROM reference_index x JOIN records r ON r.tenant_id=x.tenant_id AND r.id=x.source_id AND r.latest_version=x.source_version
           JOIN revisions v ON v.tenant_id=r.tenant_id AND v.record_id=r.id AND v.version=r.latest_version
           WHERE x.tenant_id=? AND r.project_id=? AND x.target_id=? AND x.target_version=? AND r.kind='Approval'
           AND json_extract(v.data_json,'$.subject_hash')=? AND json_extract(v.data_json,'$.operation')='batch_market_rules'
           AND json_extract(v.data_json,'$.status')='approved' AND julianday(json_extract(v.data_json,'$.expires_at'))>julianday('now') LIMIT 1""",
            (actor.tenant_id, recipe.project_id, brief["id"], brief["version"], brief["hash"]),
        ).fetchone()
    if not accepted:
        raise DomainError(
            "MARKET_APPROVAL_REQUIRED", "简报缺少未过期且绑定精确版本的市场审阅。", 409
        )
    rules = MarketBrief.model_validate({**d["market_rules"], "request_id": "live-check"})
    if rules.project_id != recipe.project_id:
        raise DomainError("PROJECT_MISMATCH", "简报规则不属于当前项目。", 409)
    target, _, report = facts(actor, rules)
    if timeline is None:
        if not recipe.template:
            raise DomainError(
                "MARKET_CAPTIONS_REQUIRED",
                "此规则简报需要明确的本地化字幕，请使用含字幕的时间线样片。",
                409,
            )
        timeline = pinned(actor, recipe.project_id, recipe.template, "Timeline")
    captions = [cue for cue in timeline["data"].get("captions", []) if text_copy(cue.get("text"))]
    localized = [
        cue
        for cue in captions
        if str(cue.get("language", "")).casefold() == rules.language.casefold()
    ]
    ordered = sorted(localized, key=lambda cue: cue.get("start", 0))
    if not ordered or any(
        not isinstance(c.get("start"), (int, float))
        or not isinstance(c.get("end"), (int, float))
        or c["end"] <= c["start"]
        for c in ordered
    ):
        raise DomainError("MARKET_CAPTIONS_REQUIRED", "需有声明目标语种、起止时间有效的字幕。", 409)
    duration = duration_of_timeline(timeline["data"])
    if duration is None or any(c["start"] < 0 or c["end"] > duration + 1e-8 for c in ordered):
        raise DomainError("MARKET_CAPTION_RANGE", "本地化字幕须落在有据可核的成片时间范围内。", 409)
    copy = text_copy(" ".join(text_copy(c["text"]) for c in ordered))
    if copy != target:
        raise DomainError("MARKET_CAPTION_DRIFT", "实际时间线的本地化字幕与已核脚本不一致。", 409)
    all_text = text_copy(" ".join(text_copy(c["text"]) for c in captions)).casefold()
    if any(text_copy(term).casefold() in all_text for term in rules.prohibited_phrases):
        raise DomainError("MARKET_PROHIBITED_EXPRESSION", "时间线字幕包含该市场禁止表达。", 409)
    return brief, {
        **report,
        "brief": ref(brief),
        "approval_id": accepted["id"],
        "caption_count": len(ordered),
        "checked_text_sha256": digest(copy),
        "scope": "timeline_caption_text_and_declared_locale",
    }
