"""Frozen signal basket observations; no portfolio order or account mutations."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from trade_app.market.domain import eligible_bars
from trade_app.platform.types import TradeError, decimal_value, money_minor, money_text, price_units
from trade_app.trading.domain import DEFAULT_FEE_CONFIG, FeeRule, calculate_fees


CALCULATION_VERSION = 'signal-basket-observed-open-close-v1'
DEFAULT_CONFIG = {'holding_bars': 5, 'holding_mode': 'legacy_signal_anchor',
                  'quantity': 100, 'fees': DEFAULT_FEE_CONFIG}


def validate_day(raw: object) -> str:
    try:
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw:
            raise ValueError(raw)
        return parsed.isoformat()
    except (TypeError, ValueError) as exc:
        raise TradeError('INVALID_BASKET_DATE', '日期须为 YYYY-MM-DD') from exc


def decision_date(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError(raw)
        return parsed.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    except (AttributeError, TypeError, ValueError) as exc:
        raise TradeError('INVALID_BASKET_SIGNAL_TIME', '来源信号决策时间缺少有效时区') from exc


def close_cutoff(day: str) -> str:
    return datetime.combine(date.fromisoformat(validate_day(day)), time(23, 59, 59),
                            tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc).isoformat()


def normalize_config(raw: dict | None = None, previous: dict | None = None) -> dict:
    raw = raw or {}
    base = previous or DEFAULT_CONFIG
    if set(raw) - set(DEFAULT_CONFIG):
        raise TradeError('INVALID_BASKET_CONFIG', '包含不支持的篮子计算参数')
    result = {key: raw.get(key, base[key]) for key in DEFAULT_CONFIG}
    for key, minimum, maximum in (('holding_bars', 1, 60), ('quantity', 100, 1000000)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise TradeError('INVALID_BASKET_CONFIG', f'{key} 须为 {minimum} 至 {maximum} 的整数')
    if result['quantity'] % 100:
        raise TradeError('INVALID_BASKET_CONFIG', '每只成分股的估算股数须为 100 的整数倍')
    if result['holding_mode'] not in ('legacy_signal_anchor', 'complete_overnights'):
        raise TradeError('INVALID_BASKET_CONFIG', '持有期口径无效')
    fees = result['fees']
    if not isinstance(fees, dict) or set(fees) != set(DEFAULT_FEE_CONFIG):
        raise TradeError('INVALID_BASKET_FEES', '费用配置字段不完整')
    normalized = {}
    for key in ('commission_rate', 'sell_stamp_rate', 'transfer_rate'):
        value = decimal_value(fees[key], key)
        if not Decimal(0) <= value <= Decimal('0.01'):
            raise TradeError('INVALID_BASKET_FEES', f'{key} 须为 0 至 1%')
        normalized[key] = format(value, 'f')
    normalized['minimum_commission'] = money_text(money_minor(fees['minimum_commission'], '最低佣金', allow_zero=True))
    result['fees'] = normalized
    return result


def _ratio(value: Decimal) -> float:
    return round(float(value), 6)


def _price(bar: dict, field: str) -> Decimal:
    return Decimal(price_units(bar[field])) / Decimal(10000)


def _entry_case(bars: list[dict], visible_by_day: dict, anchor: str, delay: int, config: dict) -> dict:
    following = [bar for bar in bars if bar['event_date'] > anchor]
    entry_index = delay - 1
    target_index = (config['holding_bars'] - 1 if config['holding_mode'] == 'legacy_signal_anchor'
                    else entry_index + config['holding_bars'])
    entry = following[entry_index] if entry_index < len(following) else None
    target = following[target_index] if target_index < len(following) else None
    result = {'status': 'pending_entry', 'entry_date': entry['event_date'] if entry else None,
              'entry_price': None, 'target_date': target['event_date'] if target else None,
              'exit_price': None, 'raw_return': None, 'after_cost_return': None,
              'mark_to_market_return': None, 'buy_fees': None, 'sell_fees': None,
              'quantity': config['quantity'], 'mark_series': []}
    if entry is None or entry['event_date'] not in visible_by_day:
        return result
    opening = _price(entry, 'open')
    result['entry_price'] = format(opening, '.4f')
    marks = [bar for bar in visible_by_day.values() if bar['event_date'] >= entry['event_date']]
    result['mark_series'] = [{'date': bar['event_date'], 'raw_return': _ratio(_price(bar, 'close') / opening - 1)}
                             for bar in marks]
    if marks:
        result['mark_to_market_return'] = result['mark_series'][-1]['raw_return']
    if target_index < entry_index:
        result['status'] = 'target_before_entry'
        return result
    if target is None or target['event_date'] not in visible_by_day:
        result['status'] = 'pending_exit'
        return result
    closing = _price(target, 'close')
    result['exit_price'] = format(closing, '.4f')
    result['raw_return'] = _ratio(closing / opening - 1)
    if target_index == entry_index:
        result['status'] = 'untradeable_same_day'
        return result
    rule = FeeRule(**{key: Decimal(value) for key, value in config['fees'].items()})
    purchase = calculate_fees(opening, config['quantity'], 'buy', rule).total
    sale = calculate_fees(closing, config['quantity'], 'sell', rule).total
    initial = opening * config['quantity'] + purchase
    proceeds = closing * config['quantity'] - sale
    result.update(status='completed', buy_fees=format(purchase, '.2f'), sell_fees=format(sale, '.2f'),
                  after_cost_return=_ratio(proceeds / initial - 1))
    return result


def evaluate_constituent(member: dict, bars: list[dict], *, as_of_date: str,
                         strict: bool, config: dict, dataset_id: str) -> dict:
    visible, flags = eligible_bars(bars, close_cutoff(as_of_date), strict)
    visible_by_day = {bar['event_date']: bar for bar in visible}
    current = visible[-1] if visible else None
    # Future observed sessions would reveal later halts/trading dates even when
    # their prices are hidden. Keep late/unavailable past rows in this calendar
    # so a missing entry row never shifts T+1 onto a later session.
    observed_calendar = [bar for bar in bars if bar['event_date'] <= as_of_date]
    return {'symbol': member['symbol'], 'run_ids': list(member['run_ids']),
            'source_date': member['source_date'], 'decision_date': member['decision_date'],
            'anchor_date': member['anchor_date'], 'dataset_id': dataset_id,
            'current_date': current['event_date'] if current else None,
            'current_price': format(_price(current, 'close'), '.4f') if current else None,
            'quality_flags': sorted(set(flags + member.get('quality_flags', []))),
            't1': _entry_case(observed_calendar, visible_by_day, member['anchor_date'], 1, config),
            't2': _entry_case(observed_calendar, visible_by_day, member['anchor_date'], 2, config)}


def summarize_constituents(rows: list[dict]) -> dict:
    summary = {}
    for mode in ('t1', 't2'):
        cases = [row[mode] for row in rows]
        raw = [row['raw_return'] for row in cases if row['raw_return'] is not None]
        net = [row['after_cost_return'] for row in cases if row['after_cost_return'] is not None]
        marks = [row['mark_to_market_return'] for row in cases if row['mark_to_market_return'] is not None]
        summary[mode] = {
            'total_constituents': len(rows), 'completed_count': len(net), 'raw_count': len(raw),
            'pending_count': sum(row['status'].startswith('pending_') for row in cases),
            'untradeable_count': sum(row['status'] in ('untradeable_same_day', 'target_before_entry') for row in cases),
            'raw_return': round(sum(raw) / len(raw), 6) if raw else None,
            'after_cost_return': round(sum(net) / len(net), 6) if net else None,
            'stock_win_rate': round(sum(value > 0 for value in raw) / len(raw), 6) if raw else None,
            'after_cost_win_rate': round(sum(value > 0 for value in net) / len(net), 6) if net else None,
            'mark_to_market_return': round(sum(marks) / len(marks), 6) if marks else None,
            'mark_to_market_count': len(marks),
        }
    by_date = {}
    for mode in ('t1', 't2'):
        values = {}
        for row in rows:
            for point in row[mode]['mark_series']:
                values.setdefault(point['date'], []).append(point['raw_return'])
        for day, returns in values.items():
            by_date.setdefault(day, {'date': day})[mode] = {
                'raw_return': round(sum(returns) / len(returns), 6), 'priced_count': len(returns)}
    return {'summary': summary, 'curve': [by_date[day] for day in sorted(by_date)]}
