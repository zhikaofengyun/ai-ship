"""Shared monetary admission rules for commands and provider adapters.

One cost fact contributes one amount. A measured charge outranks an estimate;
unknown monetary liabilities are returned as None, never invented as zero.
Numeric legacy project budgets are USD. Structured budgets state a currency.
"""

from decimal import Decimal, InvalidOperation
import math
import re

from .errors import DomainError


def money_amount(value, field="amount") -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise DomainError("INVALID_BUDGET", f"{field} 必须是非负有限金额。")
    try:
        amount = Decimal(str(value))
        valid = amount.is_finite() and amount >= 0 and math.isfinite(float(amount))
    except (InvalidOperation, ValueError, OverflowError):
        valid = False
    if not valid:
        raise DomainError("INVALID_BUDGET", f"{field} 必须是非负有限金额。")
    return amount


def project_cap(project_data: dict, currency: str) -> Decimal | None:
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise DomainError("INVALID_CURRENCY", "费用需要明确的三位大写币种代码。")
    budget = project_data.get("budget")
    if budget is None:
        return None
    if isinstance(budget, dict):
        budget_currency = budget.get("currency")
        if not isinstance(budget_currency, str) or not re.fullmatch(r"[A-Z]{3}", budget_currency):
            raise DomainError("BUDGET_CONFIGURATION", "结构化项目预算必须声明币种。", 409)
        values = [budget[key] for key in ("max_cost", "amount") if budget.get(key) is not None]
        if not values:
            raise DomainError(
                "BUDGET_CONFIGURATION", "项目预算必须声明 max_cost 或 amount 上限。", 409
            )
        caps = [money_amount(value, "project.budget") for value in values]
        if len(set(caps)) != 1:
            raise DomainError("BUDGET_CONFIGURATION", "项目预算的金额字段相互矛盾。", 409)
        cap = caps[0]
    else:
        budget_currency = "USD"
        cap = money_amount(budget, "project.budget")
    if budget_currency != currency:
        raise DomainError(
            "BUDGET_CURRENCY_MISMATCH",
            "费用币种与项目预算不同；没有获准的汇率快照时不能混算。",
            409,
            {"budget_currency": budget_currency, "requested_currency": currency},
        )
    return cap


def is_unpriced_local_compute(data):
    return (
        data.get("category") in {"local_render", "local_generation"}
        and data.get("pricing_status") == "unconfigured"
    )


def committed_amount(data: dict) -> Decimal | None:
    # A historical erroneous release must never erase an already measured bill.
    for field in ("actual", "calculated_charge"):
        if data.get(field) is not None:
            return money_amount(data[field], "Cost." + field)
    status = data.get("status")
    reserved = (
        money_amount(data["reserved"], "Cost.reserved")
        if data.get("reserved") is not None
        else None
    )
    if status in {"released", "cancelled"}:
        # Legacy erroneous external releases can still carry a positive hold.
        return reserved if reserved is not None and reserved > 0 else Decimal(0)
    if status in {"usage_recorded", "settled", "billed"}:
        return None
    estimated = (
        money_amount(data["estimated"], "Cost.estimated")
        if data.get("estimated") is not None
        else None
    )
    bounds = [value for value in (reserved, estimated) if value is not None]
    return max(bounds) if bounds else None
