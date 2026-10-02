"""Read-only estimate from booked cash flows, trades and selected market snapshots."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.domain import eligible_bars
from trade_app.market.service import get_dataset
from trade_app.market.symbols import market_symbol_key
from trade_app.platform.types import TradeError, money_text, price_units
from trade_app.trading.models import AssetSnapshot, CashFlow, Trade
from trade_app.trading.service import account_or_error, snapshot_data


def _trade_gross_cents(trade: Trade) -> int:
    return int((Decimal(trade.price_units) * trade.quantity / 100).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))


def estimate_real_assets(session: Session, data_dir: Path, account_id: str, day: str,
                         decision_at: str, dataset_ids: list[str], strict: bool) -> dict:
    account_or_error(session, account_id, real=True)
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError(day)
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '估值日期需要 YYYY-MM-DD') from exc
    eligible_bars([], decision_at, strict)
    if len(dataset_ids) > 100 or len(dataset_ids) != len(set(dataset_ids)):
        raise TradeError('INVALID_VALUATION_DATASETS', '行情样本重复或数量过多')
    snapshots: dict[str, dict] = {}
    for dataset_id in dataset_ids:
        if len(dataset_id) != 64 or any(c not in '0123456789abcdef' for c in dataset_id):
            raise TradeError('INVALID_DATASET_ID', '行情样本 ID 无效')
        dataset = get_dataset(session, data_dir, dataset_id)
        key = market_symbol_key(dataset['symbol'])
        if key in snapshots:
            raise TradeError('DUPLICATE_SYMBOL_DATASET', '同一代码不能选择多个估值样本')
        snapshots[key] = dataset
    flows = session.scalars(select(CashFlow).where(
        CashFlow.account_id == account_id, CashFlow.voided_at.is_(None),
        CashFlow.flow_date <= day).order_by(CashFlow.flow_date, CashFlow.created_at)).all()
    trades = session.scalars(select(Trade).where(
        Trade.account_id == account_id, Trade.voided_at.is_(None),
        Trade.trade_date <= day).order_by(Trade.trade_date, Trade.sequence)).all()
    flags: set[str] = set()
    has_initial = any(flow.kind == 'initial' for flow in flows)
    cash_minor = 0
    for flow in flows:
        cash_minor += flow.amount_minor if flow.kind in ('initial', 'deposit') else -flow.amount_minor
    quantities: dict[str, int] = {}
    names: dict[str, str] = {}
    display_symbols: dict[str, str] = {}
    for trade in trades:
        key = market_symbol_key(trade.symbol)
        display_symbols.setdefault(key, trade.symbol)
        gross = _trade_gross_cents(trade)
        if trade.side == 'buy':
            cash_minor -= gross + trade.fee_minor
            quantities[key] = quantities.get(key, 0) + trade.quantity
        else:
            cash_minor += gross - trade.fee_minor
            quantities[key] = quantities.get(key, 0) - trade.quantity
            if quantities[key] < 0:
                flags.add('oversold_trade_history')
        names[key] = trade.name or names.get(key, '')
    if not has_initial:
        flags.add('initial_capital_missing')
    if cash_minor < 0:
        flags.add('negative_inferred_cash')
    positions = []
    known_value = 0
    for key, quantity in sorted(quantities.items(), key=lambda item: display_symbols[item[0]]):
        symbol = display_symbols[key]
        if quantity <= 0:
            continue
        dataset = snapshots.get(key)
        item = {'symbol': symbol, 'name': names.get(key, ''), 'quantity': quantity,
                'dataset_id': dataset['id'] if dataset else None, 'quote_date': None,
                'close': None, 'market_value': None, 'quality_flags': []}
        if dataset is None:
            item['quality_flags'].append('dataset_missing')
        else:
            bars, quality = eligible_bars(dataset['bars'], decision_at, strict)
            item['quality_flags'].extend(quality)
            quote = next((bar for bar in reversed(bars) if bar['event_date'] <= day), None)
            if quote is None:
                item['quality_flags'].append('quote_unavailable_at_decision')
            else:
                if quote['event_date'] < day:
                    item['quality_flags'].append('stale_quote')
                units = price_units(quote['close'])
                value = int((Decimal(units) * quantity / 100).quantize(
                    Decimal('1'), rounding=ROUND_HALF_UP))
                item.update(quote_date=quote['event_date'], close=quote['close'],
                            market_value=money_text(value))
                known_value += value
        flags.update(item['quality_flags'])
        positions.append(item)
    complete = has_initial and cash_minor >= 0 and 'oversold_trade_history' not in flags and all(
        item['market_value'] is not None for item in positions)
    manual = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.account_id == account_id, AssetSnapshot.snap_date == day))
    estimated_total = cash_minor + known_value if complete else None
    return {'account_id': account_id, 'as_of_date': day, 'decision_at': decision_at,
            'strict': strict, 'cash': money_text(cash_minor) if has_initial else None,
            'known_position_value': money_text(known_value),
            'total_assets': money_text(estimated_total) if estimated_total is not None else None,
            'valuation_quality': 'complete' if complete and not flags else 'qualified' if complete else 'incomplete',
            'quality_flags': sorted(flags), 'positions': positions,
            'manual_snapshot': snapshot_data(manual) if manual else None,
            'difference_from_manual': money_text(estimated_total - manual.total_assets_minor)
            if estimated_total is not None and manual else None}
