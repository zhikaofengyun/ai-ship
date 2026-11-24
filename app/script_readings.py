"""V02-R3. Human English localization with a product-pinned, checked price reading."""

from collections import Counter
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .api import ok
from .auth import Actor, current_actor, require_approval
from .errors import DomainError
from .production import ref, save
from .selection import ChosenVersion, Label, Note, SelectionBody, live, on_request

router = APIRouter(prefix="/production/narration", tags=["narration"])
SMALL = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()
CURRENCIES = {
    "USD": ("dollar", "dollars", "cent", "cents"),
    "EUR": ("euro", "euros", "cent", "cents"),
    "GBP": ("pound", "pounds", "penny", "pence"),
    "CNY": ("yuan", "yuan", "fen", "fen"),
}
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")


# ISO 4217 codes identify conflicting denominations; synthesis is restricted to CURRENCIES.
ISO_CODES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL
BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF
DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HRK HTG
HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP
LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN
NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG
SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS
UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD
XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWL
""".split())
PRICE_UNITS = {
    "USD": r"USD|US dollars?|美元",
    "EUR": r"EUR|euros?|€|欧元",
    "GBP": r"GBP|pounds? sterling|£|英镑",
    "CNY": r"CNY|RMB|yuan|人民币",
}
SEPARATOR = r"(?:[-−–—~～至到]|\bto\b)"


def check_price_context(raw, token, template, currency):
    before, after = raw[: token.start()], raw[token.end() :]
    if re.search(SEPARATOR + r"\s*$", before, re.I) or re.match(r"\s*" + SEPARATOR, after, re.I):
        raise DomainError("READING_PRICE_AMBIGUOUS", "负数和价格区间需要单独核对。")
    if re.search(r"[0-9][,.]$", before) or re.match(r"[,.][0-9]", after):
        raise DomainError("READING_PRICE_AMBIGUOUS", "金额必须是完整数字。")
    declared = {c for c in re.findall(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", raw) if c in ISO_CODES}
    units = PRICE_UNITS.get(currency)
    if not units or declared - {currency}:
        raise DomainError("READING_CURRENCY_CONFLICT", "原稿含有不同或暂不支持的币种。", 409)
    # $ and ¥ alone can refer to multiple currencies. Require a nearby explicit
    # denomination before using the selected product's currency for speech.
    explicit = re.search(
        r"(?<![A-Za-z])(?:" + units + r")\s*[$¥￥]?\s*$", before, re.I
    ) or re.match(r"\s*(?:" + units + r")(?![A-Za-z])", after, re.I)
    allowed_symbols = {"USD": {"$"}, "EUR": {"€"}, "GBP": {"£"}, "CNY": {"¥", "￥"}}[currency]
    if not explicit or any(
        unicodedata.category(c) == "Sc" and c not in allowed_symbols for c in raw
    ):
        raise DomainError("READING_CURRENCY_CONFLICT", "请在原金额旁保留明确且一致的币种。", 409)
    target_codes = set(re.findall(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", template))
    target_units = (
        r"\b(?:dollars?|euros?|pounds?|cents?|pence|penny|yuan|yen|fen|USD|EUR|GBP|CNY|JPY|RMB)\b"
    )
    if (
        target_codes & ISO_CODES
        or any(unicodedata.category(c) == "Sc" for c in template)
        or re.search(target_units, template, re.I)
    ):
        raise DomainError(
            "READING_CURRENCY_CONFLICT", "占位符会生成金额与币种，译稿中不应再次指定货币。", 409
        )


def english_count(value):
    if value < 20:
        return SMALL[value]
    if value < 100:
        return TENS[value // 10] + ("-" + SMALL[value % 10] if value % 10 else "")
    step, unit = (100, "hundred") if value < 1000 else (1000, "thousand")
    return (
        english_count(value // step)
        + " "
        + unit
        + (" " + english_count(value % step) if value % step else "")
    )


def price_reading(amount, currency):
    if (
        currency not in CURRENCIES
        or not amount.is_finite()
        or not 0 <= amount < 1_000_000
        or amount * 100 != (amount * 100).to_integral()
    ):
        raise DomainError(
            "PRICE_READING_UNSUPPORTED",
            "当前英文读法支持列出的四种币种、六位以内整数和最多两位小数。",
        )
    major, minor = divmod(int(amount * 100), 100)
    a, b, c, d = CURRENCIES[currency]
    return (
        english_count(major)
        + " "
        + (a if major == 1 else b)
        + (" and " + english_count(minor) + " " + (c if minor == 1 else d) if minor else "")
    )


class Reading(SelectionBody):
    project_id: Label
    request_id: Label
    script: ChosenVersion
    product: ChosenVersion
    source_amount: str = Field(pattern=r"^[0-9]{1,6}(?:\.[0-9]{1,2})?$")
    source_currency: str = Field(pattern=r"^[A-Z]{3}$")
    target_template: Note
    review_note: Note


@router.post("/readings")
def save_reading(body: Reading, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def execute():
        origin = live(actor, body.script, "Script", body.project_id)
        product = live(actor, body.product, "Product", body.project_id)
        raw = origin["data"].get("text", "")
        pd = product["data"]
        if not isinstance(raw, str) or not (pd.get("evidence") or pd.get("evidence_source_id")):
            raise DomainError("READING_SOURCE_REQUIRED", "请提供原稿及带事实来源的商品版本。")
        # Source must contain the complete decimal token once. Pending currency
        # symbols, ranges and multi-price comparisons require a separate review.
        occurrences = [m for m in NUMBER.finditer(raw) if m.group() == body.source_amount]
        if len(occurrences) != 1 or body.target_template.count("{price}") != 1:
            raise DomainError(
                "READING_PRICE_AMBIGUOUS",
                "原金额需在原稿中唯一出现，英文译稿必须包含一个 {price} 占位符。",
            )
        try:
            amount = Decimal(str(pd.get("price")))
        except InvalidOperation:
            raise DomainError("READING_PRICE_UNKNOWN", "商品价格尚未核实。")
        if (
            not amount.is_finite()
            or amount != Decimal(body.source_amount)
            or pd.get("currency") != body.source_currency
        ):
            raise DomainError("READING_PRICE_CONFLICT", "原金额或币种与冻结商品事实冲突。", 409)
        token = occurrences[0]
        rest = raw[: token.start()] + raw[token.end() :]
        template = body.target_template
        # All other numeric literals must survive, including quantities and dates.
        if Counter(NUMBER.findall(rest)) != Counter(NUMBER.findall(template)):
            raise DomainError(
                "READING_NUMBERS_CHANGED", "除已绑定金额外，译稿必须保留原稿中其他数字。"
            )
        if any(c.isalpha() and not c.isascii() for c in template):
            raise DomainError("READING_LANGUAGE_UNSUPPORTED", "当前这条本地配音路线使用英文译稿。")
        check_price_context(raw, token, template, body.source_currency)
        spoken_price = price_reading(amount, body.source_currency)
        display = f"{body.source_currency} {body.source_amount}"
        spoken = template.replace("{price}", spoken_price)
        script = save(
            actor,
            "Script",
            body.project_id,
            {
                "name": "英文读法 · "
                + str(origin["data"].get("name") or pd.get("SKU") or "")[:160],
                "language": "en",
                "text": spoken,
                "display_text": template.replace("{price}", display),
                "source_script": ref(origin),
                "localization_method": "human_reviewed_price",
                "localization_product": ref(product),
                "reading": {
                    "source_span": [token.start(), token.end()],
                    "source_display": body.source_amount,
                    "currency": body.source_currency,
                    "spoken": spoken_price,
                    "amount": str(amount),
                },
                "review_note": body.review_note,
                "reviewer": actor.actor_id,
            },
            [ref(origin), ref(product)],
        )
        proof = save(
            actor,
            "Audit",
            body.project_id,
            {
                "name": "本地化读法复核",
                "action": "script.reading",
                "script_hash": script["hash"],
                "script_id": script["id"],
            },
            [ref(script), ref(origin), ref(product)],
        )
        return {"script": script, "audit": proof}

    return ok(on_request("script-reading", body, actor, execute), request)
