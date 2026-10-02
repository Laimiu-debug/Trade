"""Causal daily portfolio execution on an explicitly fixed research sample.

Matrix formulas follow the original raw S1-S9 matrix on complete calendar
windows. Execution deliberately fixes unstable ties and same-day cash reuse.
No historical-universe, aligned event-matrix or minute-tick parity is claimed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math

from trade_app.market.domain import eligible_bars
from trade_app.platform.types import TradeError
from trade_app.research.runtime import evaluate_strategy
from trade_app.research.portfolio_event_domain import evaluate_event_bars
from trade_app.research.portfolio_exit_rules import known_band_exit, effective_stop
from trade_app.trading.domain import FillState, adverse_execution_price, apply_fill, calculate_fees
from trade_app.trading.simulation import fee_rule

VERSION = 'fixed-sample-causal-portfolio-known-band-exits-v3'
MATRIX_VERSION = 'raw-s1-s9-calendar-windows-stable-symbol-ties-v1'
CHUNK_DAYS = 5
LIMITATIONS = [
    '固定研究样本及样本内滚动池；缺少历史市场成分、退市和复权核验，不代表无幸存者偏差的全市场研究',
    '原始 S1-S9、14 个传统运行器、aligned事件矩阵为独立路径；事件级确认及退出优先相对旧版本有明确修正',
    '买入只在后续开盘；延迟按标的可观察日线计数，不允许当日收盘信号在同一收盘买入',
    '日线高低价仅模拟预设止损止盈触达；不具备分钟顺序，双触发默认保守止损，跳空按更差开盘处理',
    '移动回撤减仓及收盘走弱清仓使用已知历史日线，在下一开盘执行，不代表实际分钟成交',
    '仅100股整手、T+1可卖库存；缺失或零量日线不成交，未模拟涨跌停封单、盘口容量及分红送股',
    '期末未卖出持仓按最后已知收盘价计值，不强制虚构平仓；费用和滑点按冻结配置',
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def cash(value):
    return str(Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def _mean(values):
    return sum(values) / len(values) if values and all(v is not None for v in values) else None


def matrix_features(bars, calendar):
    """One raw matrix column at the last *calendar* date, preserving gaps."""
    mapped = {bar['event_date']: bar for bar in bars}
    rows = [mapped.get(day) for day in calendar[-61:]]
    close = [float(row['close']) if row else None for row in rows]
    volume = [float(row['volume']) if row else None for row in rows]
    def mean(values, n, previous=False):
        source = values[:-1] if previous else values
        return _mean(source[-n:]) if len(source) >= n else None
    def bound(key, n, previous=False):
        source = rows[:-1] if previous else rows
        window = source[-n:]
        if len(window) < n or any(row is None for row in window):
            return None
        values = [float(row[key]) for row in window]
        return max(values) if key == 'high' else min(values)
    latest = close[-1] if close else None
    m10, m20, m60 = (mean(close, n) for n in (10, 20, 60))
    v10, v20, v60 = (mean(volume, n) for n in (10, 20, 60))
    h20, l20, h60, l60 = bound('high', 20), bound('low', 20), bound('high', 60), bound('low', 60)
    r20 = h20 - l20 if h20 is not None and l20 is not None else None
    r60 = h60 - l60 if h60 is not None and l60 is not None else None
    ret = latest / close[-41] - 1 if len(close) >= 41 and latest is not None and close[-41] else None
    prev10, prev20, low10 = mean(close, 10, True), bound('low', 20, True), bound('low', 10, True)
    high20 = bound('high', 20, True)
    s = {f'S{i}': False for i in range(1, 10)}
    if latest is not None:
        s.update(S1=bool(r60 and r20 is not None and r20 / r60 < .7),
                 S2=bool(v60 and v10 is not None and v10 / v60 < .6),
                 S4=bool(r20 is not None and m20 and r20 / latest < .18 and abs(latest - m20) / m20 < .06),
                 S5=bool(high20 is not None and v20 is not None and latest > high20 and volume[-1] > v20 * 1.2),
                 S6=bool(m10 and prev10 and low10 is not None and len(close) > 1 and close[-2] is not None
                         and latest > m10 and close[-2] <= prev10 and latest > low10),
                 S7=bool(m10 and m20 and m60 and m10 > m20 > m60),
                 S8=bool(prev20 is not None and latest < prev20 or m20 and latest < m20 * .97),
                 S9=bool(m10 and v20 is not None and ret is not None and latest < m10 and volume[-1] > v20 * 1.4 and ret < 0))
    return {'components': s, 'return_40d': ret, 'ma10': m10, 'source_date': calendar[-1] if calendar else None}


def matrix_signals(prefixes, calendar, top_n):
    rows = {symbol: matrix_features(bars, calendar) for symbol, bars in prefixes.items()}
    ranked = sorted((symbol for symbol, row in rows.items() if row['return_40d'] is not None),
                    key=lambda symbol: (-rows[symbol]['return_40d'], symbol))
    selected = set(ranked[:top_n])
    for symbol, row in rows.items():
        s = row['components']
        s['S3'] = symbol in selected
        pool_score = sum(s[key] for key in ('S1', 'S2', 'S3', 'S4'))
        row.update(in_pool=pool_score >= 2, buy=bool(pool_score >= 2 and (s['S5'] or s['S6'])),
                   sell=bool(s['S8'] or s['S9']), score=(pool_score + 2 * s['S5'] + 1.5 * s['S6'] + s['S7']) / 8.5 * 100,
                   reasons=[key for key in ('S8', 'S9') if s[key]])
    return rows


def initial_checkpoint(context):
    return {'version': context.get('version', VERSION), 'cursor': 0, 'cash': context['config']['initial_capital'], 'positions': {},
            'pending': {}, 'pool': [], 'pool_initialized': False, 'refresh_due': False, 'last_week': None,
            'marks': {}, 'peak_equity': context['config']['initial_capital'], 'max_drawdown': '0',
            'ending_assets': context['config']['initial_capital'], 'realized_pnl': '0',
            'buy_count': 0, 'sell_count': 0, 'wins': 0, 'total_fees': '0',
            'quality_flags': ['selection_membership_unverified', 'corporate_actions_unverified',
                              'trading_calendar_unverified', 'daily_ohlc_execution_assumption']}


def run_chunk(context, checkpoint=None, *, days=CHUNK_DAYS, signal_provider=None):
    """Return one deterministic checkpoint and append-only daily evidence shard."""
    state = deepcopy(checkpoint if checkpoint is not None else initial_checkpoint(context))
    config, calendar, datasets = context['config'], context['calendar'], context['datasets']
    if state['version'] != VERSION or not 0 <= state['cursor'] <= len(calendar) or not 1 <= days <= 2000:
        raise TradeError('INVALID_PORTFOLIO_CHECKPOINT', '组合检查点或计算跨度无效')
    prices = {item['symbol']: {bar['event_date']: bar for bar in item['bars']} for item in datasets}
    fees = fee_rule(config['fee_config'])
    slip = Decimal(config['fee_config']['slippage_rate'])
    quality = set(state['quality_flags'])
    output = {'trades': [], 'equity': [], 'pool_history': [], 'decisions': []}
    positions = state['positions']

    def fill(symbol, side, quantity, reference, day, reason, *, phase, evidence=None):
        position = positions.get(symbol)
        price = adverse_execution_price(Decimal(str(reference)), side, slip)
        charge = calculate_fees(price, quantity, side, fees)
        old = FillState(Decimal(state['cash']), position['quantity'] if position else 0,
                        position['quantity'] if position and position['entry_date'] < day else 0,
                        Decimal(position['cost']) if position else Decimal(0))
        after = apply_fill(old, side=side, price=price, quantity=quantity, fees=charge)
        state['cash'] = cash(after.cash)
        state['total_fees'] = cash(Decimal(state['total_fees']) + charge.total)
        pnl = None
        if side == 'buy':
            positions[symbol] = {'quantity': after.quantity, 'cost': cash(after.cost_basis), 'entry_date': day,
                                 'entry_price': str(price), 'held_bars': 0, 'peak': str(price), 'weak_days': 0,
                                 'last_observed_date': None}
            entry_box = (evidence or {}).get('reason_metrics', {}).get('entry_box_high')
            if entry_box is not None: positions[symbol]['entry_box_high'] = entry_box
            state['buy_count'] += 1
        else:
            removed = old.cost_basis - after.cost_basis
            pnl = (price * quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) - charge.total - removed
            state['realized_pnl'] = cash(Decimal(state['realized_pnl']) + pnl)
            state['sell_count'] += 1
            state['wins'] += int(pnl > 0)
            state['refresh_due'] = True
            if after.quantity:
                position.update(quantity=after.quantity, cost=cash(after.cost_basis), peak=str(price), weak_days=0)
            else:
                del positions[symbol]
        evidence = evidence or {}
        output['trades'].append({'date': day, 'symbol': symbol, 'side': side, 'quantity': quantity,
            'price': str(price), 'reference_price': str(reference), 'slippage_rate': str(slip),
            'fees': cash(charge.total), 'commission': cash(charge.commission), 'stamp': cash(charge.stamp),
            'transfer': cash(charge.transfer), 'realized_pnl': cash(pnl) if pnl is not None else None,
            'reason': reason, 'phase': phase, 'execution_at': day + 'T01:30:00+00:00' if phase == 'open' else None,
            'execution_window': [day + 'T01:30:00+00:00', day + 'T07:00:00+00:00'] if phase == 'intraday_ohlc' else None,
            **evidence})

    end = min(len(calendar), state['cursor'] + days)
    for index in range(state['cursor'], end):
        day = calendar[index]
        all_prior_dates = context['all_calendar']
        previous_dates = [value for value in all_prior_dates if value < day]
        decision_at = day + 'T01:29:59.999999+00:00'
        prefixes, freshness, knowledge = {}, {}, {}
        signals = {}
        for item in datasets:
            symbol = item['symbol']
            prior = [bar for bar in item['bars'] if bar['event_date'] < day]
            visible, flags = eligible_bars(prior, decision_at, config['execution_strict'])
            quality.update(flags)
            fresh = bool(previous_dates and visible and visible[-1]['event_date'] == previous_dates[-1])
            complete = len(visible) == len(prior)
            freshness[symbol] = fresh and (complete or not config['execution_strict'])
            if not freshness[symbol]:
                quality.add('unavailable_or_stale_prior_history')
            knowledge[symbol] = max(bar['available_at'] for bar in visible) if visible and all(bar.get('available_at') for bar in visible) else None
            if freshness[symbol]:
                prefixes[symbol] = visible
        if signal_provider is not None:
            # Private pure-function hook for isolated experiments. Persisted web
            # jobs never deserialize or accept executable providers.
            signals = signal_provider(prefixes, previous_dates)
            if not isinstance(signals, dict) or set(signals) - set(prefixes):
                raise TradeError('INVALID_PORTFOLIO_SIGNALS', '实验信号必须属于当时可用的证券前缀')
            for symbol, signal in signals.items():
                if (not isinstance(signal, dict) or any(type(signal.get(key)) is not bool for key in ('in_pool', 'buy', 'sell'))
                        or type(signal.get('score')) not in (int, float) or not math.isfinite(signal['score'])
                        or signal.get('source_date') != prefixes[symbol][-1]['event_date']
                        or not isinstance(signal.get('components'), dict) or not isinstance(signal.get('reasons'), list)):
                    raise TradeError('INVALID_PORTFOLIO_SIGNALS', '实验信号字段或来源日期无效')
                if signal.get('entry_box_high') is not None:
                    try:
                        box = Decimal(str(signal['entry_box_high']))
                        if isinstance(signal['entry_box_high'], bool) or not box.is_finite() or not 0 < box <= Decimal('1e12'): raise ValueError()
                    except (ValueError, ArithmeticError):
                        raise TradeError('INVALID_PORTFOLIO_SIGNALS', '入场箱顶必须为已知的正有限价格') from None
                quality.update(signal.get('quality_flags', []))
        elif context['mode'] == 'matrix_raw_s1_s9':
            signals = matrix_signals(prefixes, previous_dates, config['top_n'])
        elif context['mode'] == 'aligned_wyckoff_events':
            for symbol, bars in prefixes.items():
                signals[symbol] = evaluate_event_bars(bars, context['params'], event_profile=context['event_profile'])
                quality.update(signals[symbol].get('quality_flags', []))
        else:
            for symbol, bars in prefixes.items():
                result = evaluate_strategy(context['strategy_id'], symbol=symbol, bars=bars, params=context['params'],
                                           event_profile=context.get('event_profile'), quality=[])
                quality.update(result.get('quality_flags', []))
                evaluation = result.get('evaluation') or {}
                score = next((value for value in (evaluation.get('local_score'), evaluation.get('signal_score'), result.get('score'),
                              evaluation.get('candidate_score'), evaluation.get('entry_quality_score'), (result.get('candidate') or {}).get('ret40'))
                              if isinstance(value, (int, float, str)) and value is not None), 0)
                try:
                    score = float(score)
                except (ValueError, TypeError):
                    score = 0
                if not math.isfinite(score):
                    score = 0
                risks = evaluation.get('risk_events') or []
                is_risk = evaluation.get('primary_event') in risks and bool(risks)
                signals[symbol] = {'buy': bool(result.get('signal') and result.get('draft_eligible')),
                    'sell': is_risk or result.get('exit_signal') is True,
                    'in_pool': result.get('status') == 'computed', 'score': score,
                    'source_date': result.get('source_date'), 'reasons': evaluation.get('reasons', []),
                    'components': {'primary_event': evaluation.get('primary_event'), 'risk_events': risks},
                    'ma10': _mean([float(bar['close']) for bar in bars[-10:]]) if len(bars) >= 10 else None}
                if 'exit_signal' in result:
                    signals[symbol]['components'].update(exit_signal=result['exit_signal'],
                        exit_reason=evaluation.get('exit_reason'), entry_reason=evaluation.get('entry_reason'),
                        indicator=result.get('indicator'), strength_formula=evaluation.get('local_score_formula'))
        week = list(datetime.fromisoformat(day).isocalendar()[:2])
        refresh = (not state['pool_initialized'] or config['pool_roll'] == 'daily'
                   or config['pool_roll'] == 'weekly' and week != state['last_week']
                   or config['pool_roll'] == 'position' and state['refresh_due'])
        if refresh:
            state['pool'] = sorted(symbol for symbol, row in signals.items() if row['in_pool'])
            state['pool_initialized'], state['refresh_due'] = True, False
            state['last_week'] = week
            output['pool_history'].append({'date': day, 'decision_at': decision_at, 'source_date': previous_dates[-1] if previous_dates else None,
                'scope': 'fixed_research_sample', 'members': state['pool'], 'trigger': config['pool_roll'],
                'rows': [{'symbol': symbol, 'in_pool': symbol in state['pool'], 'score': signals.get(symbol, {}).get('score'),
                          'known_at': knowledge[symbol], 'reason': 'ELIGIBLE' if freshness[symbol] else 'UNAVAILABLE_PRIOR_BAR',
                          'components': signals.get(symbol, {}).get('components', {})} for symbol in sorted(prices)]})
        sold_today = set()
        # Phase 1: known opening exits. These proceeds may fund subsequent open buys.
        for symbol in sorted(list(positions)):
            position, current = positions[symbol], prices[symbol].get(day)
            if not current or current['volume'] <= 0:
                continue
            reason, ratio, details = None, Decimal(1), {}
            signal, visible = signals.get(symbol), prefixes.get(symbol)
            if position['held_bars'] >= config['max_holding_bars']:
                reason = 'MAX_HOLDING_NEXT_OPEN'
            if signal and signal['sell']:
                reason = ('MATRIX_S8_S9_NEXT_OPEN' if context['mode'] == 'matrix_raw_s1_s9' else
                          'ALIGNED_EVENTS_NEXT_OPEN' if context['mode'] == 'aligned_wyckoff_events' else
                          'CLASSIC_SIGNAL_NEXT_OPEN' if signal['components'].get('exit_signal') is True else 'RISK_EVENT_NEXT_OPEN')
                details = {'components': signal['components']}
            if visible and position['last_observed_date'] != visible[-1]['event_date'] and visible[-1]['event_date'] >= position['entry_date']:
                prior = visible[-1]
                close, low, high = (Decimal(prior[key]) for key in ('close', 'low', 'high'))
                peak = Decimal(position['peak'])
                threshold = peak * (1 - Decimal(config['trailing_stop_pct']))
                if config['intraday_trailing'] and Decimal(config['trailing_stop_pct']) > 0 and low <= threshold and close < threshold and reason is None:
                    reason, ratio = 'OBSERVED_TRAILING_NEXT_OPEN', Decimal(config['trailing_reduce_ratio'])
                ma10 = signal.get('ma10') if signal else None
                position['weak_days'] = position['weak_days'] + 1 if ma10 and close < Decimal(str(ma10)) else 0
                if config['daily_weak_clear'] and position['weak_days'] >= config['weak_confirm_days']:
                    reason, ratio = 'KNOWN_CLOSE_WEAK_NEXT_OPEN', Decimal(1)
                ma20 = _mean([float(bar['close']) for bar in visible[-20:]]) if len(visible) >= 20 else None
                band_reason, band_details = known_band_exit(position, prior, ma10, ma20, config)
                if band_reason and reason is None:
                    reason, ratio = band_reason, Decimal(1)
                if band_details: details['band_exit'] = band_details
                position.update(peak=str(max(peak, high)), last_observed_date=prior['event_date'])
                details.update(observed_date=prior['event_date'], prior_peak=str(peak), observed_close=str(close))
            # A pre-existing barrier crossed by the opening auction fills there.
            # Later daily highs/lows cannot retroactively change that fill.
            opening, entry = Decimal(current['open']), Decimal(position['entry_price'])
            stop, stop_kind = effective_stop(position, config)
            if stop is not None and opening <= stop:
                reason, ratio = 'OPEN_GAP_' + stop_kind, Decimal(1)
                details.update(effective_stop=str(stop), stop_kind=stop_kind)
            elif Decimal(config['take_profit_pct']) > 0 and opening >= entry * (1 + Decimal(config['take_profit_pct'])):
                reason, ratio = 'OPEN_GAP_TAKE_PROFIT', Decimal(1)
            if reason and position['entry_date'] < day:
                qty = position['quantity'] if ratio == 1 else min(position['quantity'], max(100, int(position['quantity'] * ratio / 100) * 100))
                fill(symbol, 'sell', qty, current['open'], day, reason, phase='open',
                     evidence={'decision_at': day + 'T01:30:00+00:00' if reason.startswith('OPEN_GAP_') else decision_at,
                               'known_at': knowledge[symbol], 'reason_metrics': {**details,
                               **({'opening_quote_time_assumption': day + 'T01:30:00+00:00'} if reason.startswith('OPEN_GAP_') else {})}})
                sold_today.add(symbol)
        # Phase 2: create and age entry intents, always after the source close.
        for symbol in sorted(signals):
            signal = signals[symbol]
            if signal['buy'] and symbol in state['pool'] and symbol not in positions and symbol not in sold_today and symbol not in state['pending']:
                state['pending'][symbol] = {'remaining_bars': config['entry_delay_bars'] - 1, 'source_date': signal['source_date'],
                    'created_at': decision_at, 'known_at': knowledge[symbol], 'score': signal['score'], 'signal_sha256': digest(signal),
                    'components': signal['components']}
                if signal.get('entry_box_high') is not None:
                    state['pending'][symbol]['entry_box_high'] = str(signal['entry_box_high'])
        ready = []
        for symbol in sorted(list(state['pending'])):
            intent, current = state['pending'][symbol], prices[symbol].get(day)
            signal = signals.get(symbol)
            invalid = symbol in positions or symbol not in state['pool'] or config['invalidate_pending'] and signal and signal['sell']
            if invalid:
                del state['pending'][symbol]
                output['decisions'].append({'date': day, 'symbol': symbol, 'status': 'cancelled_intent', 'reason': 'POOL_OR_RISK_INVALIDATION'})
            elif current and current['volume'] > 0 and freshness[symbol]:
                if intent['remaining_bars'] <= 0:
                    ready.append(symbol)
                else:
                    intent['remaining_bars'] -= 1
        ready.sort(key=lambda symbol: (-state['pending'][symbol]['score'], symbol))
        if config['entry_top_k']:
            for symbol in ready[config['entry_top_k']:]:
                output['decisions'].append({'date': day, 'symbol': symbol, 'status': 'skipped', 'reason': 'DAILY_TOP_K'})
                del state['pending'][symbol]
            ready = ready[:config['entry_top_k']]
        for symbol in ready:
            intent = state['pending'].pop(symbol)
            if len(positions) >= config['max_positions']:
                output['decisions'].append({'date': day, 'symbol': symbol, 'status': 'skipped', 'reason': 'MAX_POSITIONS'})
                continue
            current = prices[symbol][day]
            if (config.get('structural_box_stop') or config.get('ma20_box_break')) and not intent.get('entry_box_high'):
                output['decisions'].append({'date': day, 'symbol': symbol, 'status': 'skipped', 'reason': 'ENTRY_BOX_REFERENCE_MISSING'})
                continue
            assets = Decimal(state['cash']) + sum(Decimal(prices[sym][day]['open'] if day in prices[sym]
                and prices[sym][day]['volume'] > 0 else state['marks'].get(sym, pos['entry_price'])) * pos['quantity'] for sym, pos in positions.items())
            available = max(Decimal(0), Decimal(state['cash']) - Decimal(config['fee_config']['cash_buffer']))
            budget = min(available, assets * Decimal(config['position_pct']))
            execution = adverse_execution_price(Decimal(current['open']), 'buy', slip)
            qty = max(0, int(budget / execution / 100) * 100)
            while qty and execution * qty + calculate_fees(execution, qty, 'buy', fees).total > budget:
                qty -= 100
            if qty:
                fill(symbol, 'buy', qty, current['open'], day, 'PRIOR_KNOWN_SIGNAL_NEXT_OPEN', phase='open',
                     evidence={'decision_at': decision_at, 'known_at': knowledge[symbol], 'signal_date': intent['source_date'],
                               'signal_sha256': intent['signal_sha256'], 'reason_metrics': intent})
            else:
                output['decisions'].append({'date': day, 'symbol': symbol, 'status': 'skipped', 'reason': 'INSUFFICIENT_CASH'})
        # Phase 3: preset daily barriers. Cash becomes usable only AFTER all open entries.
        for symbol in sorted(list(positions)):
            position, current = positions[symbol], prices[symbol].get(day)
            if not current or current['volume'] <= 0:
                continue
            if position['entry_date'] < day:
                entry, opening, low, high = (Decimal(position['entry_price']), Decimal(current['open']), Decimal(current['low']), Decimal(current['high']))
                stop, stop_kind = effective_stop(position, config)
                take = entry * (1 + Decimal(config['take_profit_pct']))
                sl = stop is not None and low <= stop
                tp = Decimal(config['take_profit_pct']) > 0 and high >= take
                choose_stop = sl and (not tp or config['ambiguity_policy'] == 'conservative')
                if sl or tp:
                    reference = min(opening, stop) if choose_stop else max(opening, take)
                    fill(symbol, 'sell', position['quantity'], str(reference), day, 'DAILY_' + stop_kind if choose_stop else 'DAILY_TAKE_PROFIT',
                         phase='intraday_ohlc', evidence={'decision_at': None, 'known_at': None,
                         'reason_metrics': {'stop': str(stop) if stop is not None else None, 'take': str(take), 'both_touched': bool(sl and tp),
                                            'ambiguity_policy': config['ambiguity_policy'], 'pre_set_after_entry': position['entry_date'],
                                            'band_observed_date': position.get('band_observed_date'),
                                            'entry_box_high': position.get('entry_box_high'), 'stop_kind': stop_kind}})
                    quality.add('intraday_sequence_unknown')
                    continue
            position['held_bars'] += 1
        # End-of-day reporting uses only known marks. Never invent a terminal sale.
        # Dates and sessions are Shanghai-local. UTC midnight would admit
        # prices received up to 07:59 the following local calendar day.
        mark_cutoff = day + 'T15:59:59.999999+00:00'
        for item in datasets:
            rows, flags = eligible_bars([bar for bar in item['bars'] if bar['event_date'] <= day], mark_cutoff, config['execution_strict'])
            quality.update(flags)
            if rows:
                state['marks'][item['symbol']] = rows[-1]['close']
        assets = Decimal(state['cash']) + sum(Decimal(state['marks'].get(symbol, position['entry_price'])) * position['quantity'] for symbol, position in positions.items())
        peak = max(Decimal(state['peak_equity']), assets)
        state.update(peak_equity=cash(peak), ending_assets=cash(assets),
                     max_drawdown=str(max(Decimal(state['max_drawdown']), (peak - assets) / peak)))
        output['equity'].append({'date': day, 'total_assets': cash(assets), 'cash': state['cash'],
                                 'position_count': len(positions), 'positions': {symbol: {'quantity': position['quantity'],
                                 'mark': state['marks'].get(symbol, position['entry_price'])} for symbol, position in sorted(positions.items())}})
    state['cursor'], state['quality_flags'] = end, sorted(quality)
    return {'checkpoint': state, **output, 'done': end == len(calendar)}


def summary(context, checkpoint):
    initial = Decimal(context['config']['initial_capital'])
    return {'version': VERSION, 'mode': context['mode'], 'universe_scope': 'fixed_research_sample',
            'strategy_id': context['strategy_id'], 'initial_capital': str(initial),
            'ending_assets': checkpoint['ending_assets'], 'total_return': str(Decimal(checkpoint['ending_assets']) / initial - 1),
            'max_drawdown': checkpoint['max_drawdown'], 'buy_count': checkpoint['buy_count'], 'sell_count': checkpoint['sell_count'],
            'realized_pnl': checkpoint['realized_pnl'], 'total_fees': checkpoint['total_fees'],
            'open_positions': checkpoint['positions'], 'quality_flags': checkpoint['quality_flags'],
            'win_rate_per_sell_leg': checkpoint['wins'] / checkpoint['sell_count'] if checkpoint['sell_count'] else None,
            'completed_days': checkpoint['cursor'], 'total_days': len(context['calendar']), 'limitations': LIMITATIONS}
