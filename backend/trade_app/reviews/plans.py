"""Manual next-day plans compared with confirmed facts, without inferring market triggers."""
from __future__ import annotations

import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import money_text
from trade_app.platform.symbols import market_symbol_key
from trade_app.reviews.models import DailyReview
from trade_app.reviews.service import validate_day
from trade_app.trading.models import AssetSnapshot, SnapshotPosition, Trade
from trade_app.trading.service import account_or_error, trade_data


def plan_comparison(session: Session, account_id: str, day: str, *,
                    symbol_key: Callable[[str], str] = market_symbol_key) -> dict:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    candidates = session.scalars(select(DailyReview).where(
        DailyReview.account_id == account_id,
        DailyReview.review_date < day).order_by(DailyReview.review_date.desc())).all()
    plans = [row for row in candidates if row.next_target_date == day]
    trades = session.scalars(select(Trade).where(
        Trade.account_id == account_id, Trade.trade_date == day,
        Trade.voided_at.is_(None)).order_by(Trade.sequence, Trade.id)).all()
    trade_rows = [trade_data(row) for row in trades]
    snapshot = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.account_id == account_id, AssetSnapshot.snap_date == day))
    positions = session.scalars(select(SnapshotPosition).where(
        SnapshotPosition.snapshot_id == snapshot.id)).all() if snapshot else []
    quantities = {}
    for position in positions:
        key = symbol_key(position.symbol)
        quantities[key] = quantities.get(key, 0) + position.quantity
    actual_review = session.scalar(select(DailyReview).where(
        DailyReview.account_id == account_id, DailyReview.review_date == day))
    result_plans = []
    for row in plans:
        watchlist = json.loads(row.next_watchlist_json)
        rehearsal = json.loads(row.next_position_rehearsal_json)
        planned_keys = {symbol_key(item['code']) for item in rehearsal}
        compared = [{**item, 'explicit_target': True,
                     'actual_qty': quantities.get(symbol_key(item['code']), 0) if snapshot else None,
                     'quantity_delta': quantities.get(symbol_key(item['code']), 0) - item['qty'] if snapshot else None,
                     'quality': 'confirmed_snapshot' if snapshot else 'missing_snapshot'} for item in rehearsal]
        for position in positions:
            key = symbol_key(position.symbol)
            if key not in planned_keys:
                compared.append({'code': position.symbol, 'name': position.name, 'qty': None, 'note': '',
                    'explicit_target': False, 'actual_qty': quantities[key], 'quantity_delta': None,
                    'quality': 'not_in_plan'})
                planned_keys.add(key)
        result_plans.append({'written_on': row.review_date,
                             'target_date': row.next_target_date,
                             'forecast': row.next_market_forecast,
                             'position_plan': row.next_position_plan,
                             'risk_plan': row.next_risk_plan,
                             'watchlist': [{**item,
                                            'matching_trade_ids': [trade['id'] for trade in trade_rows
                                                                   if symbol_key(trade['symbol']) == symbol_key(item['code'])]}
                                           for item in watchlist],
                             'rehearsal': compared})
    return {'account_id': account_id, 'date': day,
            'plans': result_plans, 'actual_trades': trade_rows,
            'snapshot': {'id': snapshot.id, 'total_assets': money_text(snapshot.total_assets_minor),
                         'available_cash': money_text(snapshot.available_cash_minor)
                         if snapshot.available_cash_minor is not None else None,
                         'position_count': len(positions)} if snapshot else None,
            'actual_market_observation': actual_review.market_observation if actual_review else None,
            'method': '仅比较显式指定执行日的计划，按交易所和代码关联成交；触发条件不自动判定。持仓来自人工确认快照，缺快照不推断为零。未列入计划的实际持仓单独标示，不推断为计划清仓。'}
