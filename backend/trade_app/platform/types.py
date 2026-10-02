from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from uuid import uuid4


class TradeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def new_id() -> str:
    return uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decimal_value(raw: str | int, field: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise TradeError("INVALID_DECIMAL", f"{field} 必须是十进制数") from exc
    if not value.is_finite():
        raise TradeError("INVALID_DECIMAL", f"{field} 必须是有限数")
    return value


def money_minor(raw: str | int, field: str, *, allow_zero: bool = False) -> int:
    value = decimal_value(raw, field)
    if value < 0 or (value == 0 and not allow_zero):
        raise TradeError("INVALID_AMOUNT", f"{field} 必须大于零" if not allow_zero else f"{field} 不能小于零")
    cents = value * 100
    if cents != cents.to_integral_value():
        raise TradeError("INVALID_AMOUNT_PRECISION", f"{field} 最多保留两位小数")
    if cents > 9_223_372_036_854_775_807:
        raise TradeError("AMOUNT_TOO_LARGE", f"{field} 超出可存储范围")
    return int(cents)


def price_units(raw: str | int) -> int:
    value = decimal_value(raw, "价格")
    if value <= 0:
        raise TradeError("INVALID_PRICE", "价格必须大于零")
    scaled = value * 10_000
    if scaled != scaled.to_integral_value():
        raise TradeError("INVALID_PRICE_PRECISION", "价格最多保留四位小数")
    if scaled > 9_223_372_036_854_775_807:
        raise TradeError("PRICE_TOO_LARGE", "价格超出可存储范围")
    return int(scaled)


def decimal_text(value: Decimal, places: int = 8) -> str:
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + places + 4)
        return format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def money_text(minor: int | None) -> str | None:
    return format(Decimal(minor) / 100, ".2f") if minor is not None else None


def price_text(units: int) -> str:
    return format(Decimal(units) / 10_000, ".4f")
