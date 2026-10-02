"""Pure account rules shared by the live simulation and historical execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from trade_app.platform.types import TradeError


DEFAULT_FEE_CONFIG = {'commission_rate': '0.0003', 'minimum_commission': '5.00',
                      'sell_stamp_rate': '0.001', 'transfer_rate': '0.00001'}


@dataclass(frozen=True)
class FeeRule:
    commission_rate: Decimal
    minimum_commission: Decimal
    sell_stamp_rate: Decimal
    transfer_rate: Decimal


@dataclass(frozen=True)
class FeeBreakdown:
    commission: Decimal
    stamp: Decimal
    transfer: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.stamp + self.transfer


@dataclass(frozen=True)
class FillState:
    cash: Decimal
    quantity: int
    sellable_quantity: int
    cost_basis: Decimal


@dataclass(frozen=True)
class LotBalance:
    id: str
    acquired_date: str
    remaining_qty: int
    cost_minor: int


def consume_fifo(lots: list[LotBalance], quantity: int, fill_date: str) -> tuple[dict[str, tuple[int, int]], int]:
    """Consume oldest days, preserving the caller's execution order within a day.

    Lot IDs are random identifiers, never an ordering rule. Persistent callers
    supply acquisition date, creation time and ID as a stable final tie-break.
    """
    if quantity <= 0:
        raise TradeError('INVALID_QUANTITY', '成交数量必须大于零')
    remaining = quantity
    changes: dict[str, tuple[int, int]] = {}
    sold_cost = 0
    for lot in sorted(lots, key=lambda row: row.acquired_date):
        if not remaining:
            break
        if lot.acquired_date >= fill_date or lot.remaining_qty <= 0:
            continue
        taken = min(remaining, lot.remaining_qty)
        cost = lot.cost_minor if taken == lot.remaining_qty else int(
            (Decimal(lot.cost_minor) * taken / lot.remaining_qty).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        changes[lot.id] = (lot.remaining_qty - taken, lot.cost_minor - cost)
        sold_cost += cost
        remaining -= taken
    if remaining:
        raise TradeError('INSUFFICIENT_SELLABLE', '可卖数量不足')
    return changes, sold_cost


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_fees(price: Decimal, quantity: int, side: str, rule: FeeRule) -> FeeBreakdown:
    if price <= 0 or quantity <= 0 or side not in ("buy", "sell"):
        raise TradeError("INVALID_FILL", "成交价格、数量或方向无效")
    amount = price * quantity
    return FeeBreakdown(
        commission=_money(max(amount * rule.commission_rate, rule.minimum_commission)),
        stamp=_money(amount * rule.sell_stamp_rate if side == "sell" else Decimal(0)),
        transfer=_money(amount * rule.transfer_rate),
    )


def match_open_limit(*, side: str, limit_units: int, open_units: int) -> int | None:
    """Fill an eligible prior-day limit order at the next bar's opening price."""
    if limit_units <= 0 or open_units <= 0 or side not in ('buy', 'sell'):
        raise TradeError('INVALID_MATCH_INPUT', '开盘撮合输入无效')
    if side == 'buy' and open_units <= limit_units:
        return open_units
    if side == 'sell' and open_units >= limit_units:
        return open_units
    return None


def adverse_execution_price(reference: Decimal, side: str, rate: Decimal = Decimal(0)) -> Decimal:
    """Explicit proportional slippage, rounded to the shared four-decimal price unit."""
    if (not reference.is_finite() or reference <= 0 or side not in ('buy', 'sell')
            or not rate.is_finite() or not Decimal(0) <= rate <= Decimal('0.05')):
        raise TradeError('INVALID_SLIPPAGE', '成交参考价或滑点比例无效，滑点须在 0 至 5%')
    if rate == 0:
        return reference
    multiplier = 1 + rate if side == 'buy' else 1 - rate
    return (reference * multiplier).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def apply_fill(state: FillState, *, side: str, price: Decimal, quantity: int, fees: FeeBreakdown) -> FillState:
    """Executes one already-eligible fill; time and matching are supplied by callers."""
    if quantity <= 0 or price <= 0:
        raise TradeError("INVALID_FILL", "成交价格和数量必须大于零")
    gross = _money(price * quantity)
    if side == "buy":
        debit = gross + fees.total
        if debit > state.cash:
            raise TradeError("INSUFFICIENT_CASH", "可用现金不足")
        return FillState(
            cash=state.cash - debit,
            quantity=state.quantity + quantity,
            sellable_quantity=state.sellable_quantity,
            cost_basis=state.cost_basis + debit,
        )
    if side == "sell":
        if quantity > state.sellable_quantity or quantity > state.quantity:
            raise TradeError("INSUFFICIENT_SELLABLE", "可卖数量不足")
        removed_cost = _money(state.cost_basis * Decimal(quantity) / Decimal(state.quantity))
        return FillState(
            cash=state.cash + gross - fees.total,
            quantity=state.quantity - quantity,
            sellable_quantity=state.sellable_quantity - quantity,
            cost_basis=state.cost_basis - removed_cost,
        )
    raise TradeError("INVALID_SIDE", "成交方向无效")
