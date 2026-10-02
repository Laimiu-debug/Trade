"""Point-in-time valuation of simulated positions using explicitly selected snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy.orm import Session

from trade_app.market.domain import eligible_bars
from trade_app.market.service import get_dataset
from trade_app.market.symbols import market_symbol_key
from trade_app.platform.types import TradeError, money_minor, money_text, price_units
from trade_app.trading.simulation import portfolio, sim_account


def value_sim_portfolio(session: Session, data_dir: Path, account_id: str,
                        dataset_ids: list[str], decision_at: str, strict: bool) -> dict:
    _account, wallet = sim_account(session, account_id)
    try:
        decision = datetime.fromisoformat(decision_at.replace('Z', '+00:00'))
        if decision.tzinfo is None:
            raise ValueError(decision_at)
        decision_at = decision.astimezone(timezone.utc).isoformat()
    except ValueError as exc:
        raise TradeError('INVALID_DECISION_TIME', '估值决策时间需要时区') from exc
    if len(dataset_ids) > 100 or len(dataset_ids) != len(set(dataset_ids)):
        raise TradeError('INVALID_VALUATION_DATASETS', '估值行情样本重复或数量过多')
    snapshots: dict[str, dict] = {}
    for dataset_id in dataset_ids:
        if len(dataset_id) != 64 or any(c not in '0123456789abcdef' for c in dataset_id):
            raise TradeError('INVALID_DATASET_ID', '估值行情样本 ID 无效')
        dataset = get_dataset(session, data_dir, dataset_id)
        key = market_symbol_key(dataset['symbol'])
        if key in snapshots:
            raise TradeError('DUPLICATE_SYMBOL_DATASET', '同一代码不能选择多个估值样本')
        snapshots[key] = dataset
    holdings = portfolio(session, account_id)['positions']
    positions = []
    flags: set[str] = set()
    known_value = 0
    known_cost = 0
    for holding in holdings:
        symbol = holding['symbol']
        dataset = snapshots.get(market_symbol_key(symbol))
        item = {'symbol': symbol, 'quantity': holding['quantity'],
                'cost_basis': holding['cost_basis'], 'unrealized_pnl': None,
                'dataset_id': dataset['id'] if dataset else None,
                'quote_date': None, 'close': None, 'market_value': None,
                'quality_flags': []}
        if dataset is None:
            item['quality_flags'].append('dataset_missing')
        else:
            bars, quality = eligible_bars(dataset['bars'], decision_at, strict)
            item['quality_flags'].extend(quality)
            bar = next((row for row in bars if row['event_date'] == wallet.as_of_date), None)
            if bar is None:
                item['quality_flags'].append('quote_unavailable_at_decision')
            else:
                units = price_units(bar['close'])
                value = int((Decimal(units) * holding['quantity'] / 100).quantize(
                    Decimal('1'), rounding=ROUND_HALF_UP))
                item['quote_date'] = bar['event_date']
                item['close'] = bar['close']
                item['market_value'] = money_text(value)
                cost = money_minor(holding['cost_basis'], '持仓成本', allow_zero=True)
                item['unrealized_pnl'] = money_text(value - cost)
                known_value += value
                known_cost += cost
        flags.update(item['quality_flags'])
        positions.append(item)
    complete = all(item['market_value'] is not None for item in positions)
    return {'account_id': account_id, 'as_of_date': wallet.as_of_date,
            'decision_at': decision_at, 'strict': strict,
            'cash': money_text(wallet.cash_minor),
            'known_position_value': money_text(known_value),
            'known_position_cost': money_text(known_cost),
            'known_unrealized_pnl': money_text(known_value - known_cost),
            'total_assets': money_text(wallet.cash_minor + known_value) if complete else None,
            'valuation_quality': 'complete' if complete and not flags else 'qualified' if complete else 'incomplete',
            'quality_flags': sorted(flags), 'positions': positions}
