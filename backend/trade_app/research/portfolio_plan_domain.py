"""End-of-day conditional plans. Never inspect a subsequent bar or create fills."""
from copy import deepcopy
from decimal import Decimal
import math

from trade_app.market.domain import eligible_bars
from trade_app.research.portfolio_domain import matrix_signals, run_chunk
from trade_app.research.portfolio_event_domain import evaluate_event_bars
from trade_app.research.portfolio_exit_rules import known_band_exit, effective_stop
from trade_app.research.runtime import evaluate_strategy

VERSION = 'portfolio-asof-conditional-next-observation-v2'


def signals_at_close(context, as_of_date):
    cutoff = as_of_date + 'T15:59:59.999999+00:00'
    dates = [day for day in context['all_calendar'] if day <= as_of_date]
    prefixes, knowledge, flags = {}, {}, set()
    for item in context['datasets']:
        prior = [bar for bar in item['bars'] if bar['event_date'] <= as_of_date]
        visible, quality = eligible_bars(prior, cutoff, context['config']['execution_strict'])
        flags.update(quality)
        if (not visible or visible[-1]['event_date'] != as_of_date
                or context['config']['execution_strict'] and len(visible) != len(prior)):
            flags.add('unavailable_or_stale_prior_history')
            continue
        prefixes[item['symbol']] = visible
        knowledge[item['symbol']] = max(row['available_at'] for row in visible) if all(row.get('available_at') for row in visible) else None
    signals = {}
    if context['mode'] == 'matrix_raw_s1_s9':
        signals = matrix_signals(prefixes, dates, context['config']['top_n'])
    elif context['mode'] == 'aligned_wyckoff_events':
        signals = {symbol: evaluate_event_bars(bars, context['params'], event_profile=context['event_profile']) for symbol, bars in prefixes.items()}
    else:
        for symbol, bars in prefixes.items():
            result = evaluate_strategy(context['strategy_id'], symbol=symbol, bars=bars, params=context['params'],
                event_profile=context.get('event_profile'), quality=[])
            flags.update(result.get('quality_flags', []))
            evaluation = result.get('evaluation') or {}
            score = next((value for value in (evaluation.get('local_score'), evaluation.get('signal_score'), result.get('score'),
                evaluation.get('candidate_score'), evaluation.get('entry_quality_score'), (result.get('candidate') or {}).get('ret40'))
                if isinstance(value, (int, float, str)) and value is not None), 0)
            try: score = float(score)
            except (ValueError, TypeError): score = 0
            if not math.isfinite(score): score = 0
            risks = evaluation.get('risk_events') or []
            signals[symbol] = {'buy': bool(result.get('signal') and result.get('draft_eligible')),
                'sell': bool(risks and evaluation.get('primary_event') in risks) or result.get('exit_signal') is True,
                'in_pool': result.get('status') == 'computed',
                'score': score, 'source_date': result.get('source_date'), 'reasons': evaluation.get('reasons', []),
                'components': {'primary_event': evaluation.get('primary_event'), 'risk_events': risks},
                'ma10': sum(float(row['close']) for row in bars[-10:]) / 10 if len(bars) >= 10 else None}
            if 'exit_signal' in result:
                signals[symbol]['components'].update(exit_signal=result['exit_signal'],
                    exit_reason=evaluation.get('exit_reason'), entry_reason=evaluation.get('entry_reason'),
                    indicator=result.get('indicator'), strength_formula=evaluation.get('local_score_formula'))
    for signal in signals.values(): flags.update(signal.get('quality_flags', []))
    return signals, prefixes, knowledge, sorted(flags)


def build_plan(context, state, as_of_date):
    """state is an exact end-of-day frozen/replayed checkpoint on as_of_date."""
    signals, prefixes, knowledge, flags = signals_at_close(context, as_of_date)
    config, positions = context['config'], deepcopy(state['positions'])
    current_pool = set(state['pool'])
    refreshed_pool = {symbol for symbol, signal in signals.items() if signal['in_pool']}
    pool_rule = config['pool_roll']
    refresh = not state['pool_initialized'] or pool_rule == 'daily' or pool_rule == 'position' and state['refresh_due']
    # The next actual session date is unknown to this plan. A weekly refresh
    # must remain a condition instead of reading the next future dataset row.
    weekly_unknown = pool_rule == 'weekly' and state['pool_initialized']
    planned_pool = refreshed_pool if refresh else current_pool
    if weekly_unknown: planned_pool = current_pool | refreshed_pool
    rows, holdings = [], []
    for symbol, position in sorted(positions.items()):
        quantity, mark = position['quantity'], Decimal(state['marks'].get(symbol, position['entry_price']))
        cost = Decimal(position['cost'])
        holdings.append({'symbol': symbol, **position, 'mark': str(mark), 'market_value': str(mark * quantity),
            'unrealized_pnl': str(mark * quantity - cost), 'sellable_at_cutoff': quantity if position['entry_date'] < as_of_date else 0,
            'sellable_next_session_if_open': quantity})
        signal, visible = signals.get(symbol), prefixes.get(symbol)
        reason = 'MAX_HOLDING_NEXT_OPEN' if position['held_bars'] >= config['max_holding_bars'] else None
        ratio, details = Decimal(1), {}
        if signal and signal['sell']:
            reason = ('MATRIX_S8_S9_NEXT_OPEN' if context['mode'] == 'matrix_raw_s1_s9' else
                'ALIGNED_EVENTS_NEXT_OPEN' if context['mode'] == 'aligned_wyckoff_events' else
                'CLASSIC_SIGNAL_NEXT_OPEN' if signal['components'].get('exit_signal') is True else 'RISK_EVENT_NEXT_OPEN')
            details['components'] = signal['components']
        if visible and position['last_observed_date'] != visible[-1]['event_date'] and visible[-1]['event_date'] >= position['entry_date']:
            prior = visible[-1]
            close, low = Decimal(prior['close']), Decimal(prior['low'])
            threshold = Decimal(position['peak']) * (1 - Decimal(config['trailing_stop_pct']))
            if config['intraday_trailing'] and Decimal(config['trailing_stop_pct']) > 0 and low <= threshold and close < threshold and reason is None:
                reason, ratio = 'OBSERVED_TRAILING_NEXT_OPEN', Decimal(config['trailing_reduce_ratio'])
            ma10 = signal.get('ma10') if signal else None
            weak_days = position['weak_days'] + 1 if ma10 and close < Decimal(str(ma10)) else 0
            if config['daily_weak_clear'] and weak_days >= config['weak_confirm_days']:
                reason, ratio = 'KNOWN_CLOSE_WEAK_NEXT_OPEN', Decimal(1)
            ma20 = sum(float(row['close']) for row in visible[-20:]) / 20 if len(visible) >= 20 else None
            band_reason, band_details = known_band_exit(position, prior, ma10, ma20, config)
            if band_reason and reason is None:
                reason, ratio = band_reason, Decimal(1)
            if band_details: details['band_exit'] = band_details
            details.update(observed_date=prior['event_date'], close=str(close), prior_peak=position['peak'], weak_days=weak_days)
        if reason:
            qty = quantity if ratio == 1 else min(quantity, max(100, int(quantity * ratio / 100) * 100))
            rows.append({'symbol': symbol, 'side': 'sell', 'status': 'conditional_next_open', 'reason': reason,
                'quantity_if_executable': qty, 'price': None, 'known_at': knowledge.get(symbol), 'source_date': as_of_date,
                'remaining_observed_bars': 0, 'conditions': ['下一实际交易日有可成交开盘价与成交量', '满足T+1',
                    '隔夜新信息重新核验；开盘跳空止损/止盈可覆盖本计划'], 'components': details})
        stop, stop_kind = effective_stop(position, config)
        barriers = []
        if stop is not None: barriers.append((stop, 'PRESET_' + stop_kind))
        if Decimal(config['take_profit_pct']) > 0:
            barriers.append((Decimal(position['entry_price']) * (1 + Decimal(config['take_profit_pct'])), 'PRESET_TAKE_PROFIT'))
        for threshold, reason_code in barriers:
                rows.append({'symbol': symbol, 'side': 'sell', 'status': 'conditional_price_barrier', 'reason': reason_code,
                    'quantity_if_executable': quantity, 'reference_threshold': str(threshold),
                    'price': None, 'known_at': None, 'source_date': position['entry_date'], 'remaining_observed_bars': None,
                    'conditions': ['未来价格是否触达未知', '开盘跳空用实际开盘，日内双触发遵循冻结假设', '与其他退出计划互斥，不累加卖出数量'], 'components': {}})
    pending = deepcopy(state['pending'])
    for symbol, signal in signals.items():
        if signal['buy'] and symbol in planned_pool and symbol not in positions and symbol not in pending:
            pending[symbol] = {'remaining_bars': config['entry_delay_bars'] - 1, 'source_date': signal['source_date'],
                'known_at': knowledge[symbol], 'score': signal['score'], 'components': signal['components']}
    ranked = sorted(pending, key=lambda symbol: (-pending[symbol]['score'], symbol))
    for rank, symbol in enumerate(ranked, 1):
        intent, signal = pending[symbol], signals.get(symbol)
        invalid = symbol in positions or symbol not in planned_pool or config['invalidate_pending'] and signal and signal['sell']
        if invalid: status = 'invalidated_asof'
        elif symbol not in prefixes: status = 'waiting_available_history'
        elif intent['remaining_bars'] > 0: status = 'waiting_observed_bars'
        else: status = 'conditional_next_open'
        rows.append({'symbol': symbol, 'side': 'buy', 'status': status, 'reason': 'FROZEN_OR_CURRENT_SIGNAL_INTENT',
            'candidate_order': rank, 'score': intent['score'], 'quantity_if_executable': None, 'price': None,
            'known_at': intent.get('known_at'), 'source_date': intent['source_date'], 'remaining_observed_bars': intent['remaining_bars'],
            'conditions': ['开盘价与实际买入股数未知；按开盘资金、整手、费用和滑点重新计算',
                '先开盘退出再买入；盘中退出资金不能提前使用', '候选排序/前K限制只在实际到期可执行意图中应用',
                '隔夜新信息、池成员资格、已有持仓和可用资金重新核验'] +
                (['下次观察若跨新日历周则刷新池；否则沿用当前池，资格尚未确定'] if weekly_unknown else []),
            'components': intent.get('components', {})})
    return {'version': VERSION, 'status': 'generated', 'as_of_date': as_of_date,
        'decision_cutoff': as_of_date + 'T15:59:59.999999+00:00', 'target_session_date': None,
        'cash': state['cash'], 'total_assets': state['ending_assets'], 'holdings': holdings, 'plan_signals': rows,
        'open_signals': [row for row in rows if row['status'] == 'conditional_next_open'],
        'current_pool': sorted(current_pool), 'refresh_candidate_pool': sorted(refreshed_pool),
        'weekly_refresh_date_unknown': weekly_unknown, 'quality_flags': sorted(set(flags) | set(state['quality_flags'])),
        'scope': '按上海当日日终已可得信息生成的条件计划，不是成交或真实委托；不读取后一天日期、开盘、成交量或涨跌。下一观察时点重新校验资金、T+1、候选与新信息。',
        'risk_order': '已知退出→开盘候选→盘中风险；多个卖出情景互斥，不得相加；数量未知不以收盘价推算假成交'}


def build_frozen_plan(context, frozen):
    if frozen['status'] != 'ready': return frozen
    state = frozen['checkpoint']
    if frozen['replay_days']:
        # Calendar and rows are truncated before executing even one replay day.
        # This protects the boundary even if a future calculator changes.
        bounded = {**context, 'calendar': [day for day in context['calendar'] if day <= frozen['as_of_date']],
            'all_calendar': [day for day in context['all_calendar'] if day <= frozen['as_of_date']],
            'datasets': [{**item, 'bars': [row for row in item['bars'] if row['event_date'] <= frozen['as_of_date']]} for item in context['datasets']]}
        state = run_chunk(bounded, state, days=frozen['replay_days'])['checkpoint']
    return build_plan(context, state, frozen['as_of_date'])
