"""Explicit supervised in-sample fitting of the legacy Weike custom predicate.

This profile is never dispatched by the executable strategy runtime. Target
dates affect scoring only, and cannot alter a historical signal predicate.
"""
from datetime import date
import math

from trade_app.market.domain import eligible_bars
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError
from trade_app.research.force_rhythm_domain import (calculate_force_rhythm_signal, detect_rhythm_wave_pattern,
    detect_retail_sell_signal, detect_main_peak_sell_signal)
from trade_app.research.ths_volume_domain import calculate_ths_main_retail_signal
from trade_app.research.trend_king_domain import CandlePoint

VERSION = 'legacy-weike-custom-target-fit-v1'
DEFAULTS = {'min_cycle_count': 3, 'max_cycle_cv': .55, 'max_amplitude_cv': .85, 'min_autocorr': .05,
    'min_wave_swing_ratio': .12, 'trough_percentile_max': .40, 'peak_percentile_min': .65,
    'peak_reject_percentile_min': .72, 'flip_trough_percentile_min': .30, 'flip_trough_percentile_max': .55,
    'deep_trough_percentile_max': .08, 'retail_block_min_trough_percentile': .25,
    'retail_high_percentile_min': .60, 'block_buy_on_retail_sell': True, 'buy_trigger_mode': 'combined'}


def normalize_params(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise TradeError('LAB_FIT_UNUSED_PARAMETER', '人工目标拟合仅接受旧自定义谓词实际使用的参数')
    value = {**DEFAULTS, **raw}
    for key, item in value.items():
        if key == 'buy_trigger_mode':
            if item not in ('combined', 'flip_only', 'turn_only'): raise TradeError('LAB_FIT_PARAMETER', '触发模式无效')
        elif key == 'block_buy_on_retail_sell':
            if type(item) is not bool: raise TradeError('LAB_FIT_PARAMETER', '散户过滤须为布尔值')
        elif key == 'min_cycle_count':
            if type(item) is not int or not 2 <= item <= 20: raise TradeError('LAB_FIT_PARAMETER', '周期数量须为2至20整数')
        else:
            if type(item) not in (int, float) or not math.isfinite(item) or not (-1 if key == 'min_autocorr' else 0) <= item <= (10 if key in ('max_cycle_cv', 'max_amplitude_cv', 'min_wave_swing_ratio') else 1):
                raise TradeError('LAB_FIT_PARAMETER', f'{key} 超出有限数值范围')
    if value['flip_trough_percentile_min'] > value['flip_trough_percentile_max']:
        raise TradeError('LAB_FIT_PARAMETER', '翻转分位区间上下界无效')
    return value


def normalize_fit(raw, datasets):
    if raw is None: return None
    if not isinstance(raw, dict) or set(raw) - {'profile', 'symbol', 'date_from', 'date_to', 'target_dates', 'variants'}:
        raise TradeError('LAB_FIT_INPUT', '目标拟合字段无效')
    if raw.get('profile', VERSION) != VERSION: raise TradeError('LAB_FIT_INPUT', '不支持的目标拟合口径')
    symbol = ''.join(normalize_a_share_symbol(raw.get('symbol', '')))
    dataset = next((row for row in datasets if row['symbol'] == symbol), None)
    if dataset is None: raise TradeError('LAB_FIT_INPUT', '目标证券必须属于冻结行情样本')
    targets = raw.get('target_dates')
    start, end = raw.get('date_from'), raw.get('date_to')
    try:
        if not isinstance(targets, list) or not 1 <= len(targets) <= 100 or len(set(targets)) != len(targets): raise ValueError()
        if any(type(day) is not str or date.fromisoformat(day).isoformat() != day for day in [start, end, *targets]): raise ValueError()
    except (ValueError, TypeError): raise TradeError('LAB_FIT_INPUT', '拟合起止与1至100个不重复目标日期必须有效') from None
    dates = [row['event_date'] for row in dataset['bars'] if start <= row['event_date'] <= end]
    if not 1 <= len(dates) <= 366 or any(day not in dates for day in targets):
        raise TradeError('LAB_FIT_INPUT', '目标日须存在于拟合区间内，区间最多366个观察日')
    variants = raw.get('variants', [{}])
    if not isinstance(variants, list) or not 1 <= len(variants) <= 32:
        raise TradeError('LAB_FIT_LIMIT', '目标拟合须显式提供1至32组参数，不静默截断旧超大网格')
    return {'profile': VERSION, 'symbol': symbol, 'date_from': start, 'date_to': end,
        'target_dates': sorted(targets), 'variants': [normalize_params(row) for row in variants]}


def evaluate_custom(indicator, ths, params):
    cfg = normalize_params(params)
    pattern = detect_rhythm_wave_pattern(indicator, cfg)
    retail, peak = detect_retail_sell_signal(indicator, cfg), detect_main_peak_sell_signal(indicator, cfg)
    trough = float(indicator.get('trough_percentile', .5))
    state, previous = str(indicator.get('main_force_state') or 'flat'), str(ths.get('prev_main_force_state') or 'flat')
    flip = previous == 'falling' and state == 'rising'
    deep = trough <= cfg['deep_trough_percentile_max'] and state in ('falling', 'flat')
    band = flip and cfg['flip_trough_percentile_min'] <= trough <= cfg['flip_trough_percentile_max']
    turn = bool(indicator.get('trough_turn'))
    mode = cfg['buy_trigger_mode']
    triggered = band if mode == 'flip_only' else turn if mode == 'turn_only' else band or deep or turn and trough <= cfg['trough_percentile_max']
    checks = {'pattern': bool(pattern['rhythm_pattern_formed']), 'below_peak_reject': trough < cfg['peak_reject_percentile_min'],
        'triggered': bool(triggered), 'retail_allowed': not (cfg['block_buy_on_retail_sell'] and retail['retail_sell_signal'] and trough > cfg['retail_block_min_trough_percentile']),
        'peak_allowed': not peak['main_peak_sell_signal'] or deep}
    return {'signal': all(checks.values()), 'checks': checks, 'reasons': [key for key, passed in checks.items() if not passed],
        'flip_band': band, 'deep_trough': deep, 'classic_turn': turn, 'trough_percentile': trough,
        'pattern_checks': pattern, 'retail_checks': retail, 'peak_checks': peak}


def collect_snapshots(context, symbol, start, end, label_dates=()):
    dataset = next(row for row in context['datasets'] if row['symbol'] == symbol)
    targets = set(label_dates)
    snapshots, excluded = [], []
    for day in [row['event_date'] for row in dataset['bars'] if start <= row['event_date'] <= end]:
        prior = [row for row in dataset['bars'] if row['event_date'] <= day]
        at = day + 'T15:59:59.999999+00:00'
        bars, flags = eligible_bars(prior, at, context['config']['execution_strict'])
        if not context['config']['execution_strict'] and any(row.get('available_at') is None for row in bars):
            flags.append('historical_availability_assumed_at_local_close')
        if len(bars) < 40 or bars[-1]['event_date'] != day or context['config']['execution_strict'] and len(bars) != len(prior):
            excluded.append({'date': day, 'target': day in targets, 'reason': 'no_complete_fresh_history', 'quality_flags': flags})
            continue
        candles = [CandlePoint(time=row['event_date'], open=float(row['open']), high=float(row['high']), low=float(row['low']),
            close=float(row['close']), volume=row['volume'], amount=float(row.get('amount') or 0)) for row in bars]
        ths, indicator = calculate_ths_main_retail_signal(candles), calculate_force_rhythm_signal(candles)
        snapshots.append({'date': day, 'purple_flip': bool(ths.get('purple_to_yellow')), 'indicator': indicator,
            'ths': ths, 'quality_flags': flags, 'known_at': max(row['available_at'] for row in bars) if all(row.get('available_at') for row in bars) else None})
    return snapshots, excluded


def fit_targets(context, request):
    targets = set(request['target_dates'])
    snapshots, excluded = collect_snapshots(context, request['symbol'], request['date_from'], request['date_to'], targets)
    missing = sorted(targets - {row['date'] for row in snapshots})
    results = []
    for ordinal, params in enumerate(request['variants']):
        evaluations = [{'date': row['date'], **evaluate_custom(row['indicator'], row['ths'], params)} for row in snapshots]
        signals = {row['date'] for row in evaluations if row['signal']}
        purple = {row['date'] for row in snapshots if row['purple_flip']}
        hits, false_purple, extra = signals & targets, signals & purple - targets, signals - targets - purple
        score = len(signals) + 2 * len(false_purple) + len(extra)
        results.append({'ordinal': ordinal, 'params': params, 'signal_dates': sorted(signals), 'hit_dates': sorted(hits),
            'missed_target_dates': sorted(targets - hits), 'extra_purple_dates': sorted(false_purple), 'extra_other_dates': sorted(extra),
            'complete_target_match': hits == targets and not missing, 'legacy_exact_fit_score': score,
            'score_terms': {'all_signals': len(signals), 'purple_extra_times_two': 2 * len(false_purple), 'other_extra': len(extra)},
            'target_recall': len(hits) / len(targets), 'signal_precision': len(hits) / len(signals) if signals else None,
            'daily_evaluations': evaluations})
    exact = [row for row in results if row['complete_target_match']]
    ranking = sorted(exact, key=lambda row: (row['legacy_exact_fit_score'], row['ordinal'])) if exact else sorted(results, key=lambda row: (-len(row['hit_dates']), row['ordinal']))
    return {'version': VERSION, 'scope': 'supervised_in_sample_target_fit_only', 'symbol': request['symbol'],
        'target_dates': sorted(targets), 'date_from': request['date_from'], 'date_to': request['date_to'],
        'excluded': excluded, 'unobservable_target_dates': missing, 'snapshots': snapshots, 'results': results,
        'ranking': [row['ordinal'] for row in ranking], 'selected_variant': None if missing else ranking[0]['ordinal'],
        'ranking_rule': 'exact: min(all_signals+2*extra_purple+extra_other), stable ordinal; no exact: max target hits, stable ordinal',
        'limitations': ['人工指定目标日期后的样本内拟合，不是样本外验收或交易推荐',
            '旧自定义谓词与生产节奏波触发不同；不自动采用参数、不写回策略、不影响历史可执行信号',
            '目标日期只影响评分；无当时可得证据的目标不能得出可比较最优参数',
            '紫转黄额外命中按旧脚本加罚，原因可追溯；完整目标匹配优先，部分匹配并列保留原参数组次序']}
