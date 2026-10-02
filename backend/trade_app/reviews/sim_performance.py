"""Realized simulation fill analysis; prices for open lots are deliberately excluded."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date
from decimal import Decimal
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, decimal_text, money_text
from trade_app.platform.symbols import market_symbol_key
from trade_app.trading.sim_models import SimFill, SimOrder
from trade_app.trading.simulation import sim_account


def sim_performance(session: Session, account_id: str, date_basis: str = 'sell', date_from=None, date_to=None) -> dict:
    if date_basis not in ('sell', 'buy'):
        raise TradeError('INVALID_DATE_BASIS', '归属日期须为买入日或卖出日')
    for day in (date_from, date_to):
        if day is not None:
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise ValueError(day)
            except (ValueError, TypeError) as exc:
                raise TradeError('INVALID_PERFORMANCE_RANGE', '日期使用YYYY-MM-DD') from exc
    if date_from and date_to and date_from > date_to:
        raise TradeError('INVALID_PERFORMANCE_RANGE', '开始日期不能晚于结束日期')
    within = lambda day: (date_from is None or day >= date_from) and (date_to is None or day <= date_to)
    filtered = date_from is not None or date_to is not None
    _account, wallet = sim_account(session, account_id)
    fills = session.execute(select(SimFill, SimOrder).join(
        SimOrder, SimFill.order_id == SimOrder.id).where(
        SimFill.account_id == account_id).order_by(
        SimFill.fill_date, SimFill.created_at, SimFill.id)).all()
    lots: dict[str, deque[list]] = defaultdict(deque)
    closed: list[dict] = []
    buy_count = 0
    for fill, order in fills:
        key = market_symbol_key(order.symbol)
        if order.side == 'buy':
            buy_count += int(within(fill.fill_date))
            lots[key].append([fill.id, fill.fill_date, order.quantity])
            continue
        remaining = order.quantity
        allocations = []
        while remaining and lots[key]:
            lot = lots[key][0]
            take = min(remaining, lot[2])
            allocations.append({'buy_fill_id': lot[0], 'buy_date': lot[1], 'quantity': take})
            lot[2] -= take
            remaining -= take
            if lot[2] == 0:
                lots[key].popleft()
        persisted = json.loads(fill.allocations_json) if fill.allocations_json is not None else None
        allocation_quality = 'persisted' if persisted is not None else 'legacy_inferred_dates'
        if persisted is not None:
            allocations = persisted
        closed.append({'fill_id': fill.id, 'order_id': order.id,
                       'symbol': order.symbol, 'sell_date': fill.fill_date,
                       'quantity': order.quantity,
                       'sell_gross': money_text(fill.gross_minor),
                       'fees': money_text(fill.commission_minor + fill.stamp_minor + fill.transfer_minor),
                       'realized_pnl': money_text(fill.realized_pnl_minor),
                       'buy_allocations': allocations, 'allocation_quality': allocation_quality,
                       'quality': 'complete' if remaining == 0 else 'unmatched_buy'})
    excluded = []
    if filtered:
        selected = []
        for row in closed:
            if date_basis == 'sell':
                if within(row['sell_date']): selected.append(row)
                continue
            allocations = row['buy_allocations']
            if row['allocation_quality'] == 'persisted':
                parts = [item for item in allocations if within(item['buy_date'])]
                if not parts: continue
                selected.append({**row, 'buy_allocations': parts, 'original_quantity': row['quantity'],
                    'allocation_subset': len(parts) != len(allocations), 'quantity': sum(item['quantity'] for item in parts),
                    'realized_pnl': decimal_text(sum((Decimal(item['realized_pnl']) for item in parts), Decimal(0)), 2),
                    'fees': decimal_text(sum((Decimal(item['sell_fees']) for item in parts), Decimal(0)), 2),
                    'sell_gross': decimal_text(sum((Decimal(item['realized_pnl']) + Decimal(item['cost_basis']) + Decimal(item['sell_fees']) for item in parts), Decimal(0)), 2)})
            elif row['quality'] == 'complete' and len({item['buy_date'] for item in allocations}) == 1:
                if within(allocations[0]['buy_date']): selected.append(row)
            else:
                excluded.append(row['fill_id'])
        closed = selected
    pnl_values = [Decimal(row['realized_pnl']) for row in closed if row['realized_pnl'] is not None]
    wins = [value for value in pnl_values if value > 0]
    losses = [-value for value in pnl_values if value < 0]
    monthly: dict[str, dict] = {}
    cumulative = Decimal(0)
    curve = []
    streak_wins = streak_losses = max_wins = max_losses = 0
    for row in closed:
        value = Decimal(row['realized_pnl'] or '0')
        month = row['sell_date'][:7]
        bucket = monthly.setdefault(month, {'month': month, 'sell_count': 0,
                                             'realized_pnl': Decimal(0), 'fill_ids': []})
        bucket['sell_count'] += 1
        bucket['realized_pnl'] += value
        bucket['fill_ids'].append(row['fill_id'])
        cumulative += value
        curve.append({'date': row['sell_date'], 'fill_id': row['fill_id'],
                      'cumulative_realized_pnl': decimal_text(cumulative, 2)})
        streak_wins = streak_wins + 1 if value > 0 else 0
        streak_losses = streak_losses + 1 if value < 0 else 0
        max_wins = max(max_wins, streak_wins)
        max_losses = max(max_losses, streak_losses)
    best = max(closed, key=lambda row: Decimal(row['realized_pnl'] or '0')) if closed else None
    worst = min(closed, key=lambda row: Decimal(row['realized_pnl'] or '0')) if closed else None
    buy_monthly, unallocated = {}, list(excluded)
    for row in closed:
        allocations = row['buy_allocations']
        if row['allocation_quality'] == 'persisted':
            parts = [(item['buy_date'], Decimal(item['realized_pnl'])) for item in allocations]
        elif row['quality'] == 'complete' and len({item['buy_date'] for item in allocations}) == 1:
            # The entire sale belongs to one purchase date even when the old
            # database did not retain the split among same-day lots.
            parts = [(allocations[0]['buy_date'], Decimal(row['realized_pnl']))]
        else:
            unallocated.append(row['fill_id'])
            continue
        for day, value in parts:
            bucket = buy_monthly.setdefault(day[:7], {'month': day[:7], 'realized_pnl': Decimal(0), 'fill_ids': set(), 'allocation_count': 0})
            bucket['realized_pnl'] += value
            bucket['fill_ids'].add(row['fill_id'])
            bucket['allocation_count'] += 1
    buy_rows = [{'month': row['month'], 'sell_count': len(row['fill_ids']), 'allocation_count': row['allocation_count'],
                 'realized_pnl': decimal_text(row['realized_pnl'], 2), 'fill_ids': sorted(row['fill_ids'])}
                for row in sorted(buy_monthly.values(), key=lambda item: item['month'], reverse=True)]
    sell_rows = [{'month': row['month'], 'sell_count': row['sell_count'],
                  'realized_pnl': decimal_text(row['realized_pnl'], 2), 'fill_ids': row['fill_ids']}
                 for row in sorted(monthly.values(), key=lambda item: item['month'], reverse=True)]
    return {'account_id': account_id, 'as_of_date': wallet.as_of_date,
            'date_from': date_from, 'date_to': date_to,
            'date_basis': date_basis, 'buy_attribution_unavailable_fill_ids': unallocated,
            'frozen': bool(wallet.frozen), 'buy_fill_count': buy_count,
            'sell_fill_count': len(closed),
            'realized_pnl': decimal_text(sum(pnl_values, Decimal(0)), 2),
            'win_rate_pct': decimal_text(Decimal(len(wins)) * 100 / len(closed), 2) if closed else None,
            'profit_factor': decimal_text(sum(wins, Decimal(0)) / sum(losses, Decimal(0)), 4) if losses else None,
            'max_consecutive_wins': max_wins,
            'max_consecutive_losses': max_losses,
            'best_fill_id': best['fill_id'] if best else None,
            'worst_fill_id': worst['fill_id'] if worst else None,
            'monthly': buy_rows if date_basis == 'buy' else sell_rows,
            'realized_curve': curve, 'closed_fills': closed,
            'method': ('月份按买入批次日期回看已经卖出的盈亏，使用成交时保存的成本与卖出费用分摊。'
                       '这是事后归属，买入当时尚未知晓后续盈亏；一笔卖出可归属多个买入月份。'
                       '旧记录跨多个买入日且没有精确分摊时，列为无法归属，不按数量猜测。'
                       if date_basis == 'buy' else '月份按卖出成交日归属已实现盈亏。') +
                      ('买入日期筛选使用精确批次分摊，仅统计所选批次对应的数量、费用和盈亏；同一卖出合并计数。缺分摊的跨买入日旧成交单列排除。'
                       if filtered and date_basis == 'buy' else '胜率和最佳/最差均按完整卖出成交计数；') +
                      '买入笔数按实际买入日期范围计数。未平仓不计入。累计已实现曲线保持卖出日期轴，不是账户净值。'}
