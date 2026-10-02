"""Frozen market execution adapter for the simulation's shared trading rules."""
from __future__ import annotations

from pathlib import Path
from decimal import Decimal
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.symbols import market_symbol_key
from trade_app.platform.types import TradeError, price_text, price_units
from trade_app.trading.domain import adverse_execution_price, match_open_limit
from trade_app.trading.sim_models import SimOrder
from trade_app.trading.simulation import _order, fill_order, order_data, portfolio, settle, sim_account, validate_day


def match_order_at_open(session: Session, data_dir: Path, account_id: str,
                        order_id: str, body: dict) -> dict:
    """Replay a frozen bar's open after the order's submit day."""
    _account, wallet, row = _order(session, account_id, order_id)
    if row.revision != body['expected_revision']:
        raise TradeError('REVISION_CONFLICT', '委托版本已变化', 409)
    if row.status != 'pending':
        raise TradeError('ORDER_FINAL', '委托已结束，不能重复撮合', 409)
    if row.submit_date >= wallet.as_of_date:
        raise TradeError('SAME_DAY_OPEN_LOOKAHEAD', '仅能用提交日之后的开盘价撮合', 409)
    dataset = get_dataset(session, data_dir, body['dataset_id'])
    if market_symbol_key(dataset['symbol']) != market_symbol_key(row.symbol):
        raise TradeError('MARKET_SYMBOL_MISMATCH', '行情代码与委托代码不一致', 409)
    bar = next((item for item in dataset['bars'] if item['event_date'] == wallet.as_of_date), None)
    if bar is None:
        return {'status': 'no_bar', 'order': order_data(row), 'fill': None,
                'dataset_id': dataset['id'], 'execution_date': wallet.as_of_date}
    slippage = Decimal(json.loads(row.config_json).get('slippage_rate', '0'))
    adjusted = adverse_execution_price(Decimal(bar['open']), row.side, slippage)
    opening = match_open_limit(side=row.side, limit_units=row.limit_price_units,
                               open_units=price_units(str(adjusted)))
    if opening is None:
        return {'status': 'limit_not_met', 'order': order_data(row), 'fill': None,
                'dataset_id': dataset['id'], 'execution_date': wallet.as_of_date,
                'reference_price': bar['open'], 'slippage_rate': str(slippage)}
    result = fill_order(session, account_id, order_id,
                        {'expected_revision': row.revision, 'fill_date': wallet.as_of_date,
                         'fill_price': price_text(opening)},
                        price_source=f"market_open:{dataset['id']}" + (f':slippage={slippage}' if slippage else ''))
    return {'status': 'filled', **result, 'dataset_id': dataset['id'],
            'execution_date': wallet.as_of_date, 'reference_price': bar['open'],
            'slippage_rate': str(slippage)}


def advance_market_day(session: Session, data_dir: Path, account_id: str,
                       body: dict) -> dict:
    """Advance one chosen date and attempt every pending order at its frozen open."""
    _account, wallet = sim_account(session, account_id, mutable=True)
    if wallet.revision != body['expected_wallet_revision']:
        raise TradeError('REVISION_CONFLICT', '模拟账户版本已变化', 409)
    to_date = validate_day(body['to_date'])
    if to_date <= wallet.as_of_date:
        raise TradeError('CLOCK_NOT_ADVANCED', '撮合日期必须晚于当前模拟日期')
    pending = list(session.scalars(select(SimOrder).where(
        SimOrder.account_id == account_id, SimOrder.status == 'pending'
    ).order_by(SimOrder.created_at, SimOrder.id)))
    if len(pending) > 500:
        raise TradeError('TOO_MANY_PENDING_ORDERS', '单次最多撮合 500 笔待成交委托', 409)
    datasets = {}
    for symbol, dataset_id in body['datasets'].items():
        key = market_symbol_key(symbol)
        if key in datasets:
            raise TradeError('DUPLICATE_SYMBOL_DATASET', '同一证券别名不能重复选择撮合样本')
        datasets[key] = dataset_id
    for symbol in {row.symbol for row in pending}:
        dataset_id = datasets.get(market_symbol_key(symbol))
        if not dataset_id:
            raise TradeError('DATASET_REQUIRED', f'{symbol} 缺少冻结行情样本')
        if len(dataset_id) != 64 or any(char not in '0123456789abcdef' for char in dataset_id):
            raise TradeError('INVALID_DATASET_ID', f'{symbol} 行情样本 ID 无效')
        if market_symbol_key(get_dataset(session, data_dir, dataset_id)['symbol']) != market_symbol_key(symbol):
            raise TradeError('MARKET_SYMBOL_MISMATCH', f'{symbol} 行情样本代码不匹配', 409)
    settle(session, account_id, to_date)
    outcomes = []
    for row in pending:
        outcomes.append({'order_id': row.id, **match_order_at_open(
            session, data_dir, account_id, row.id,
            {'expected_revision': row.revision, 'dataset_id': datasets[market_symbol_key(row.symbol)]})})
    return {'account_id': account_id, 'as_of_date': to_date,
            'outcomes': outcomes, 'portfolio': portfolio(session, account_id)}
