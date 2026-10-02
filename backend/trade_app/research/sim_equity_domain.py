"""Linear cash/quantity replay with explicitly selected point-in-time quotes.

No live provider, order execution or mutable account access. Calendar month end
is a reporting boundary and never presented as a verified exchange session.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from trade_app.platform.types import TradeError, decimal_text, money_text, price_units

VERSION = 'sim-frozen-equity-v1'


def market_value_minor(quantity: int, close: str) -> int:
    return int((Decimal(quantity) * price_units(close) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def replay_ledger(initial_minor: int, fills: list[dict]) -> tuple[int, dict[str, int]]:
    cash, quantities = initial_minor, defaultdict(int)
    for fill in fills:
        fees = fill['commission_minor'] + fill['stamp_minor'] + fill['transfer_minor']
        if fill['quantity'] <= 0 or fill['gross_minor'] <= 0 or fees < 0:
            raise TradeError('SIM_EQUITY_LEDGER_INVALID', '成交金额、数量或费用无效')
        key = fill['symbol_key']
        if fill['side'] == 'buy':
            cash -= fill['gross_minor'] + fees
            quantities[key] += fill['quantity']
        elif fill['side'] == 'sell':
            cash += fill['gross_minor'] - fees
            quantities[key] -= fill['quantity']
        else:
            raise TradeError('SIM_EQUITY_LEDGER_INVALID', '成交方向无效')
        if cash < 0 or quantities[key] < 0:
            raise TradeError('SIM_EQUITY_LEDGER_INVALID', '成交重放出现负现金或超卖，不能生成资产报告')
    return cash, {key: value for key, value in quantities.items() if value}


def quote_schedule(datasets: list[dict], strict: bool, through: str) -> list[dict]:
    """Each bar is processed once, then sorted by when its close became visible."""
    buckets = defaultdict(list)
    for dataset in datasets:
        for bar in dataset['bars']:
            event_date = bar['event_date']
            known = bar.get('available_at')
            if known is None:
                if strict:
                    continue
                available_date = event_date
            else:
                moment = datetime.fromisoformat(known.replace('Z', '+00:00'))
                if moment.tzinfo is None:
                    raise TradeError('SIM_EQUITY_AVAILABLE_AT', '行情可得时间缺少时区')
                available_date = max(event_date, moment.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat())
            if available_date <= through:
                buckets[available_date].append({'symbol_key': dataset['symbol_key'], 'dataset_id': dataset['id'],
                    'event_date': event_date, 'visible_date': available_date, 'close': bar['close'],
                    'available_at': known, 'historical_availability_unknown': known is None})
    return [row for day in sorted(buckets) for row in buckets[day]]


def calculate_equity(frozen: dict, datasets: list[dict]) -> dict:
    start, end, initial_day = frozen['date_from'], frozen['date_to'], frozen['initial_date']
    initial = frozen['initial_minor']
    fills = frozen['fills']
    schedule = quote_schedule(datasets, frozen['strict'], end)
    baseline_day = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    baseline_is_initial = start == initial_day
    reasons = defaultdict(set)
    reasons[start].add('range_start')
    reasons[end].add('range_end')
    for fill in fills:
        if start <= fill['fill_date'] <= end: reasons[fill['fill_date']].add('fill')
    for row in schedule:
        if start <= row['event_date'] <= end: reasons[row['event_date']].add('observed_bar')
        if start <= row['visible_date'] <= end and row['visible_date'] != row['event_date']:
            reasons[row['visible_date']].add('quote_became_available')
    cursor = date.fromisoformat(start)
    while cursor <= date.fromisoformat(end):
        following = cursor + timedelta(days=1)
        if following.month != cursor.month: reasons[cursor.isoformat()].add('month_boundary')
        cursor = following
    days = sorted(reasons)
    process_days = ([baseline_day] if not baseline_is_initial else []) + days
    cash, fee_sum = initial, 0
    quantities, latest = defaultdict(int), {}
    fill_index = quote_index = 0
    points = []
    initial_base = {'date': initial_day, 'total_assets_minor': initial, 'total_assets': money_text(initial),
                    'quality': 'initial_capital_before_first_fill', 'positions': []}
    baseline = initial_base
    for day in process_days:
        while fill_index < len(fills) and fills[fill_index]['fill_date'] <= day:
            fill = fills[fill_index]
            fees = fill['commission_minor'] + fill['stamp_minor'] + fill['transfer_minor']
            cash += -fill['gross_minor'] - fees if fill['side'] == 'buy' else fill['gross_minor'] - fees
            quantities[fill['symbol_key']] += fill['quantity'] if fill['side'] == 'buy' else -fill['quantity']
            fee_sum += fees
            fill_index += 1
        while quote_index < len(schedule) and schedule[quote_index]['visible_date'] <= day:
            quote = schedule[quote_index]
            previous = latest.get(quote['symbol_key'])
            # Late arrival of an old bar must not replace a more recent close.
            if previous is None or quote['event_date'] >= previous['event_date']:
                latest[quote['symbol_key']] = quote
            quote_index += 1
        positions, known_value, missing, stale, unknown = [], 0, [], [], []
        for symbol, quantity in sorted(quantities.items()):
            if not quantity: continue
            quote = latest.get(symbol)
            value = market_value_minor(quantity, quote['close']) if quote else None
            if value is None: missing.append(symbol)
            else: known_value += value
            if quote and quote['event_date'] < day: stale.append(symbol)
            if quote and quote['historical_availability_unknown']: unknown.append(symbol)
            positions.append({'symbol': symbol, 'quantity': quantity,
                'dataset_id': quote['dataset_id'] if quote else None,
                'quote_date': quote['event_date'] if quote else None,
                'available_at': quote['available_at'] if quote else None,
                'close': quote['close'] if quote else None, 'market_value': money_text(value),
                'quality': 'missing' if not quote else 'stale' if quote['event_date'] < day else 'available_at_unknown' if quote['historical_availability_unknown'] else 'fresh'})
        total = cash + known_value if not missing else None
        point = {'date': day, 'cash': money_text(cash), 'known_position_value': money_text(known_value),
                 'total_assets': money_text(total), 'total_assets_minor': total,
                 'asset_index': decimal_text(Decimal(total) / initial, 8) if total is not None else None,
                 'cumulative_fees': money_text(fee_sum), 'positions': positions,
                 'missing_symbols': missing, 'stale_symbols': stale, 'unknown_availability_symbols': unknown,
                 'quality': 'missing_quotes' if missing else 'stale_quotes' if stale else 'availability_unknown' if unknown else 'complete',
                 'axis_reasons': sorted(reasons.get(day, {'opening_baseline'}))}
        if day == baseline_day and not baseline_is_initial:
            baseline = point
        else:
            points.append(point)
    peak = baseline['total_assets_minor']
    missing_path = peak is None
    observed_max_dd = Decimal(0)
    for point in points:
        total = point['total_assets_minor']
        if total is None:
            missing_path = True
            point.update(drawdown_pct=None, drawdown_quality='missing')
            continue
        peak = max(peak if peak is not None else total, total)
        drawdown = (Decimal(peak - total) / peak * 100) if peak else Decimal(0)
        observed_max_dd = max(observed_max_dd, drawdown)
        point.update(drawdown_pct=decimal_text(drawdown, 4), drawdown_quality='observed_points_only' if missing_path else 'complete')
    monthly, previous = [], baseline
    buckets = defaultdict(list)
    for point in points: buckets[point['date'][:7]].append(point)
    for month, group in sorted(buckets.items()):
        last = group[-1]
        denominator, numerator = previous['total_assets_minor'], last['total_assets_minor']
        change = (Decimal(numerator - denominator) / denominator * 100) if denominator and numerator is not None else None
        monthly.append({'month': month, 'start_date': previous['date'], 'end_date': last['date'],
            'denominator_assets': money_text(denominator), 'ending_assets': money_text(numerator),
            'return_pct': decimal_text(change, 4) if change is not None else None,
            'is_partial_month': (start[:7] == month and start[8:] != '01') or (date.fromisoformat(last['date']) + timedelta(days=1)).month == date.fromisoformat(last['date']).month,
            'quality': 'missing_endpoint' if change is None else 'stale_endpoint' if previous['quality'] == 'stale_quotes' or last['quality'] == 'stale_quotes' else 'availability_unknown' if previous['quality'] == 'availability_unknown' or last['quality'] == 'availability_unknown' else 'complete',
            'missing_observation_count': sum(row['total_assets_minor'] is None for row in group)})
        previous = last
    ending = points[-1]
    denominator = baseline['total_assets_minor']
    range_return = (Decimal(ending['total_assets_minor'] - denominator) / denominator * 100) if denominator and ending['total_assets_minor'] is not None else None
    return {'calculation_version': VERSION, 'date_from': start, 'date_to': end,
        'initial_date': initial_day, 'initial_capital': money_text(initial),
        'opening_baseline': baseline, 'points': points, 'monthly': monthly,
        'summary': {'ending_assets': ending['total_assets'], 'ending_cash': ending['cash'],
            'range_return_pct': decimal_text(range_return, 4) if range_return is not None else None,
            'range_denominator_assets': baseline['total_assets'], 'range_denominator_date': baseline['date'],
            'max_drawdown_pct': None if missing_path else decimal_text(observed_max_dd, 4),
            'observed_max_drawdown_pct': decimal_text(observed_max_dd, 4),
            'missing_observation_count': sum(point['total_assets'] is None for point in points),
            'stale_observation_count': sum(bool(point['stale_symbols']) for point in points)},
        'method': '实际模拟成交金额及当时费用重放现金和数量；只采用显式选择样本当日上海日终已可得收盘。资产指数=总资产/初始资金；模拟账户无外部出入金。曲线日期轴来自所选已可得行情、实际成交与报告边界，不补造缺失交易日；回撤分母为报告起点及随后已观察总资产峰值；缺价期间不宣称完整最大回撤。月份分母显示上个报告端点资产；范围边界不代表交易所开市日。'}
