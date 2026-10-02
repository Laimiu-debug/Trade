"""Independent chart/hybrid laboratory, evaluated on already visible prefixes.

This is deliberately separate from the application's production strategy catalog.
Ranking is within the supplied sample; no historical market membership is implied.
"""
from dataclasses import asdict
import math

from trade_app.platform.types import TradeError
from trade_app.research.chart_volume_swing_domain import (ChartVolumeSwingParams,
    calculate_chart_volume_swing_signal, evaluate_chart_volume_swing_signal)
from trade_app.research.hybrid_band_domain import calculate_hybrid_band_signal, evaluate_hybrid_band_signal
from trade_app.research.trend_king_domain import CandlePoint
from trade_app.research.runtime import evaluate_strategy

VERSION = 'independent-chart-hybrid-lab-v2'
STRATEGIES = ('chart_volume_swing_v1', 'hybrid_band_v1')
RUNTIME_STRATEGIES = ('limit_up_arb_v1', 'ths_main_force_flip_v1',
                      'ths_main_force_golden_cross_v1', 'ths_force_rhythm_v1')
FILTER_DEFAULTS = {'min_amount20': '0', 'ret40_min': '-1', 'ret40_max': '100',
    'ret40_top_n': 500, 'daily_top': 3, 'rank_bonus': False,
    'confirm_type': 'any', 'grade': 'any', 'min_volume_price_ratio': '0',
    'require_ths': False, 'require_rhythm': False, 'sources': 'any'}
LIMITATIONS = ['fixed_sample_membership_unverified', 'no_hindsight_best_signal_deduplication',
    'corporate_actions_unverified', 'sample_dates_are_not_verified_exchange_calendar']


def normalize_params(strategy, raw):
    if strategy not in STRATEGIES or not isinstance(raw, dict):
        raise TradeError('INVALID_LAB_STRATEGY', '实验室仅支持独立图形量价和混合波段策略')
    defaults = asdict(ChartVolumeSwingParams())
    if strategy == 'hybrid_band_v1':
        defaults.update(min_hybrid_score=82.0, ret40_rank_bonus_100=8.0,
                        ret40_rank_bonus_200=5.0, ret40_rank_bonus_500=2.0)
    if set(raw) - set(defaults):
        raise TradeError('UNKNOWN_LAB_PARAMETER', '存在未实现的实验参数')
    result = {**defaults, **raw}
    windows = result['box_windows']
    if not isinstance(windows, (list, tuple)) or not 1 <= len(windows) <= 5 or any(
            type(value) is not int or not 10 <= value <= 240 for value in windows) or len(set(windows)) != len(windows):
        raise TradeError('INVALID_LAB_PARAMETER', '箱体窗口须为1至5个不同的10至240整数')
    result['box_windows'] = list(windows)
    integer_bounds = {'vol_lookback': (10, 240), 'confirm_hold_days': (1, 4),
        'max_breakout_age_days': (5, 120), 'retest_recent_days': (2, 60), 'min_days_after_breakout': (1, 60)}
    unit_values = {'box_range_min', 'box_range_max', 'min_close_strength', 'max_breakout_wick_ratio', 'retest_band'}
    for key, value in result.items():
        if key == 'box_windows':
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise TradeError('INVALID_LAB_PARAMETER', f'{key} 需要有限数值') from exc
        maximum = 1 if key in unit_values else 200 if 'score' in key else 100 if 'bonus' in key else 240
        if isinstance(value, bool) or not math.isfinite(numeric) or not 0 <= numeric <= maximum:
            raise TradeError('INVALID_LAB_PARAMETER', f'{key} 超出支持范围')
        if key in integer_bounds:
            lo, hi = integer_bounds[key]
            if type(value) is not int or not lo <= value <= hi:
                raise TradeError('INVALID_LAB_PARAMETER', f'{key} 须为 {lo} 至 {hi} 的整数')
            result[key] = value
        else:
            result[key] = numeric
    if not result['box_range_min'] < result['box_range_max'] or not 0 < result['hold_ratio'] <= 1:
        raise TradeError('INVALID_LAB_PARAMETER', '箱体上下界或站稳比例无效')
    return result


def normalize_filters(raw):
    if not isinstance(raw, dict) or set(raw) - set(FILTER_DEFAULTS):
        raise TradeError('UNKNOWN_LAB_FILTER', '存在未实现的实验筛选条件')
    value = {**FILTER_DEFAULTS, **raw}
    for key, lo, hi in [('min_amount20', 0, 1e15), ('ret40_min', -1, 100),
                        ('ret40_max', -1, 100), ('min_volume_price_ratio', 0, 100)]:
        try:
            number = float(value[key])
        except (ValueError, TypeError) as exc:
            raise TradeError('INVALID_LAB_FILTER', f'{key} 需要数值') from exc
        if isinstance(value[key], bool) or not math.isfinite(number) or not lo <= number <= hi:
            raise TradeError('INVALID_LAB_FILTER', f'{key} 超出范围')
        value[key] = format(number, '.12g')
    for key in ('ret40_top_n', 'daily_top'):
        if type(value[key]) is not int or not 1 <= value[key] <= 2000:
            raise TradeError('INVALID_LAB_FILTER', f'{key} 须为1至2000整数')
    if type(value['rank_bonus']) is not bool or value['grade'] not in ('any', 'A', 'B', 'C') or value['confirm_type'] not in (
            'any', 'pullback_retest', 'hold_above', 'trend_ma10_pullback'):
        raise TradeError('INVALID_LAB_FILTER', '排名加分、等级或确认类型无效')
    if float(value['ret40_min']) > float(value['ret40_max']):
        raise TradeError('INVALID_LAB_FILTER', '40日涨幅下界不能大于上界')
    if any(type(value[key]) is not bool for key in ('require_ths', 'require_rhythm')) or value['sources'] not in ('any', 'pullback', 'trend_only', 'chart_only'):
        raise TradeError('INVALID_LAB_FILTER', '混合信号过滤必须使用已实现的布尔值和来源枚举')
    return value


def signal_rows(prefixes, strategy, params, filters):
    returns = {symbol: float(bars[-1]['close']) / float(bars[-41]['close']) - 1
               for symbol, bars in prefixes.items() if len(bars) >= 41}
    # Stable identity tie-break; future signal scores never change an earlier rank.
    ranked = sorted(returns, key=lambda symbol: (-returns[symbol], symbol))
    ranks = {symbol: at + 1 for at, symbol in enumerate(ranked)}
    rows = {}
    for symbol, bars in sorted(prefixes.items()):
        candles = [CandlePoint(time=bar['event_date'], open=float(bar['open']), high=float(bar['high']),
            low=float(bar['low']), close=float(bar['close']), volume=int(bar['volume']),
            amount=float(bar['amount']) if bar.get('amount') is not None else 0) for bar in bars]
        runtime = None
        if strategy in RUNTIME_STRATEGIES:
            runtime = evaluate_strategy(strategy, symbol=symbol, bars=bars, params=params)
            indicator, evaluation = runtime.get('indicator', {}), runtime.get('evaluation', {})
        elif strategy == 'hybrid_band_v1':
            indicator = calculate_hybrid_band_signal(candles, params,
                ret40_rank=ranks.get(symbol) if filters['rank_bonus'] else None)
            evaluation = evaluate_hybrid_band_signal(indicator, params)
        else:
            indicator = calculate_chart_volume_swing_signal(candles, params)
            evaluation = evaluate_chart_volume_swing_signal(indicator, params)
        chart = indicator.get('chart', indicator)
        score = float(evaluation.get('signal_score') or 0)
        amount_known = len(bars) >= 20 and all(bar.get('amount') is not None for bar in bars[-20:])
        amount = sum(float(bar['amount']) for bar in bars[-20:]) / 20 if amount_known else None
        ret = returns.get(symbol)
        reasons = []
        if not (runtime.get('status') == 'computed' if runtime else indicator.get('has_data')):
            reasons.append('insufficient_history')
        if ret is None or not float(filters['ret40_min']) <= ret <= float(filters['ret40_max']):
            reasons.append('return_40d_outside_range')
        if ranks.get(symbol, 2001) > filters['ret40_top_n']:
            reasons.append('outside_sample_trend_pool')
        if float(filters['min_amount20']) > 0 and (amount is None or amount < float(filters['min_amount20'])):
            reasons.append('amount20_missing' if amount is None else 'amount20_below_threshold')
        if filters['confirm_type'] != 'any' and indicator.get('confirm_type') != filters['confirm_type']:
            reasons.append('confirmation_type_mismatch')
        if filters['grade'] != 'any' and evaluation.get('event_grade') != filters['grade']:
            reasons.append('grade_mismatch')
        volume_ratio = chart.get('up_down_vol_ratio')
        if float(filters['min_volume_price_ratio']) > 0 and (volume_ratio is None or float(volume_ratio) < float(filters['min_volume_price_ratio'])):
            reasons.append('volume_price_ratio_below_threshold')
        if filters['require_ths'] and not (indicator.get('purple_to_yellow') or indicator.get('golden_cross')):
            reasons.append('hybrid_ths_required')
        if filters['require_rhythm'] and not indicator.get('trough_turn'):
            reasons.append('hybrid_rhythm_required')
        if filters['sources'] == 'pullback' and indicator.get('confirm_type') not in ('pullback_retest', 'trend_ma10_pullback'):
            reasons.append('hybrid_pullback_source_required')
        if filters['sources'] == 'trend_only' and not indicator.get('trend_ma10_pullback'):
            reasons.append('hybrid_trend_source_required')
        if filters['sources'] == 'chart_only' and not chart.get('signal'):
            reasons.append('chart_signal_required')
        in_pool = not reasons
        rows[symbol] = {'source_date': bars[-1]['event_date'] if bars else None,
            'in_pool': in_pool, 'buy': bool(in_pool and (runtime.get('draft_eligible') if runtime else evaluation.get('signal'))), 'sell': False,
            'score': score, 'reasons': reasons, 'ma10': sum(float(bar['close']) for bar in bars[-10:]) / 10 if len(bars) >= 10 else None,
            'components': {'return_40d': ret, 'ret40_rank': ranks.get(symbol), 'amount20': amount,
                'rank_scope': 'fixed_research_sample', 'rank_bonus_enabled': filters['rank_bonus'],
                'indicator': indicator, 'evaluation': evaluation},
            'quality_flags': (['amount20_unknown'] if not amount_known else []) + (runtime.get('quality_flags', []) if runtime else [])}
        box = indicator.get('box_high') if strategy in STRATEGIES else None
        if box is not None and math.isfinite(float(box)) and float(box) > 0:
            rows[symbol]['entry_box_high'] = str(box)
    selected = sorted((symbol for symbol, row in rows.items() if row['buy']), key=lambda symbol: (-rows[symbol]['score'], symbol))
    for symbol in selected[filters['daily_top']:]:
        rows[symbol]['buy'] = False
        rows[symbol]['reasons'].append('outside_daily_top')
    return rows


def band_statistics(equity, initial_capital, *, window=20, target=.28):
    """Non-overlapping sample-date bands; incomplete final bands remain explicit."""
    if type(window) is not int or not 2 <= window <= 240 or not math.isfinite(target) or not 0 <= target <= 10:
        raise TradeError('INVALID_LAB_TARGET', '波段窗口或目标无效')
    bands, before = [], float(initial_capital)
    for offset in range(0, len(equity), window):
        chunk = equity[offset:offset + window]
        after = float(chunk[-1]['total_assets'])
        result = after / before - 1 if before > 0 else None
        bands.append({'start_date': chunk[0]['date'], 'end_date': chunk[-1]['date'],
            'sample_days': len(chunk), 'complete': len(chunk) == window,
            'starting_assets': format(before, '.2f'), 'ending_assets': format(after, '.2f'),
            'return': result, 'target_reached': result >= target if result is not None else None})
        before = after
    full = [row for row in bands if row['complete'] and row['return'] is not None]
    return {'window_sample_days': window, 'target': target, 'bands': bands, 'complete_bands': len(full),
        'positive_band_rate': sum(row['return'] > 0 for row in full) / len(full) if full else None,
        'target_reached_count': sum(row['target_reached'] for row in full)}
