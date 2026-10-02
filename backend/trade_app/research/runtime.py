"""Shared strategy evaluation on already eligible frozen bars, without storage.

Both research and backtests must supply the same normalized parameters and frozen
event profile. Availability selection belongs to the caller; this module never
reads future bars, mutable profiles, services, or market providers.
"""
from __future__ import annotations

import math
from dataclasses import asdict

from trade_app.platform.types import TradeError, decimal_value
from trade_app.research.domain import STRATEGY_ID, candidate_from_bars, relative_strength_signal
from trade_app.research.trend_king_domain import CandlePoint, calculate_trend_king_signal, evaluate_trend_king_signal
from trade_app.research.limit_up_arb_domain import calculate_limit_up_arb_signal, evaluate_limit_up_arb_signal
from trade_app.research.ths_volume_domain import calculate_ths_main_retail_signal
from trade_app.research.emotion_limit_up_domain import calculate_emotion_limit_up_signal, evaluate_emotion_limit_up_signal
from trade_app.research.wulong_domain import calculate_wulong_cluster_signal, evaluate_wulong_cluster_signal
from trade_app.research.force_rhythm_domain import calculate_force_rhythm_signal, evaluate_force_rhythm_signal
from trade_app.research.wulong_universe import UNIVERSE_PARAM_KEYS, evaluate_wulong_universe
from trade_app.research.wyckoff_domain import calculate_snapshot
from trade_app.research.wyckoff_strategy import WYCKOFF_STRATEGY_IDS, evaluate_wyckoff_strategy
from trade_app.research.classic_domain import CLASSIC_STRATEGY_IDS, evaluate_classic


TREND_KING_IDS = ('trend_king_v1', 'trend_king_limitup_v1', 'trend_king_rally_v1', 'trend_king_pullback_v1')
THS_TRIGGERS = {'ths_main_force_flip_v1': 'purple_to_yellow', 'ths_main_force_golden_cross_v1': 'golden_cross'}
SINGLE_SYMBOL_STRATEGIES = (STRATEGY_ID, *TREND_KING_IDS, 'limit_up_arb_v1', *THS_TRIGGERS,
                            'emotion_limit_up_v1', 'wulong_cluster_v1', 'ths_force_rhythm_v1',
                            *WYCKOFF_STRATEGY_IDS, *CLASSIC_STRATEGY_IDS)
RUNTIME_VERSION = 'shared-frozen-strategy-runtime-v1'


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def evaluate_strategy(strategy_id: str, *, symbol: str, bars: list[dict], params: dict,
                      event_profile: dict | None = None, quality: list[str] | None = None) -> dict:
    """Return the research result shape, excluding persistence/code-version fields."""
    if strategy_id not in SINGLE_SYMBOL_STRATEGIES:
        raise TradeError('STRATEGY_NOT_MIGRATED', '该策略没有可用于单股运行的实现', 409)
    quality = list(quality or [])
    if strategy_id in CLASSIC_STRATEGY_IDS:
        calculated = evaluate_classic(strategy_id, bars, params)
        enough = calculated['available']
        buy, sell = bool(calculated['signal']), bool(calculated['exit_signal'])
        evaluation = {key: value for key, value in calculated.items() if key != 'metrics'}
        evaluation.update(primary_event=calculated['entry_reason'] or calculated['exit_reason'] or 'NONE',
                          trigger_date=calculated['source_date'] if buy or sell else None,
                          events=[calculated['entry_reason']] if buy else [], risk_events=[])
        return {'status': 'computed' if enough else 'insufficient_data',
                'signal': buy if enough else None, 'exit_signal': sell if enough else None,
                'draft_eligible': enough and buy, 'candidate': {'symbol': symbol,
                    'date': calculated['source_date']} if bars else None,
                'source_date': calculated['source_date'], 'quality_flags': quality + ['classic_reference_variant'],
                'score': None, 'executable_date': None, 'indicator': calculated['metrics'],
                'evaluation': evaluation, 'observed_bars': len(bars), 'required_bars': calculated['required_bars'],
                'source_path': 'classic_confirmed_daily_prefix'}
    if strategy_id in WYCKOFF_STRATEGY_IDS:
        if not event_profile or not isinstance(event_profile.get('snapshot'), dict):
            raise TradeError('EVENT_PROFILE_REQUIRED', '维科夫运行必须传入已冻结事件模板')
        calculated = calculate_snapshot(bars, int(params['window_days']), profile=event_profile['snapshot'])
        snapshot = calculated['snapshot']
        evaluation = evaluate_wyckoff_strategy(strategy_id, snapshot, params, candidate=calculated['candidate'])
        enough = calculated['has_data']
        observation = bool(evaluation['signal']) and enough
        positive_event = evaluation['positive_primary_event']
        return {
            'status': 'computed' if enough else 'insufficient_data',
            'signal': observation if enough else None, 'draft_eligible': observation and positive_event,
            'draft_block_reason': None if observation and positive_event else
                '观察门槛未通过' if not observation else '主事件不是可用于买入草稿的正向事件',
            'candidate': {'symbol': symbol, 'date': calculated['source_date'],
                          **(calculated['candidate'] or {})} if bars else None,
            'source_date': calculated['source_date'],
            'quality_flags': quality + evaluation.get('quality_flags', []),
            'score': None, 'executable_date': None, 'indicator': _json_safe(snapshot),
            'evaluation': _json_safe(evaluation), 'event_age_days': calculated['event_age_days'],
            'event_profile': event_profile, 'observed_bars': calculated['observed_bars'],
            'required_bars': calculated['required_bars'], 'source_path': calculated['source_path'],
        }
    if strategy_id == STRATEGY_ID:
        candidate = candidate_from_bars(symbol, bars)
        signal = relative_strength_signal(candidate, params) if candidate else None
        return {'status': 'computed' if candidate else 'insufficient_data',
                'signal': signal, 'candidate': asdict(candidate) if candidate else None,
                'source_date': candidate.date if candidate else bars[-1]['event_date'] if bars else None,
                'quality_flags': quality, 'score': None, 'executable_date': None,
                'draft_eligible': bool(signal)}
    candles = [CandlePoint(time=bar['event_date'], open=float(bar['open']), high=float(bar['high']),
                           low=float(bar['low']), close=float(bar['close']), volume=bar['volume'],
                           amount=float(bar.get('amount') or 0)) for bar in bars]
    if strategy_id in TREND_KING_IDS:
        indicator = calculate_trend_king_signal(candles)
        evaluation = evaluate_trend_king_signal(indicator, params)
    elif strategy_id == 'limit_up_arb_v1':
        indicator = calculate_limit_up_arb_signal(candles, symbol=symbol)
        evaluation = evaluate_limit_up_arb_signal(indicator, params)
    elif strategy_id in THS_TRIGGERS:
        indicator = calculate_ths_main_retail_signal(candles)
        evaluation = {'signal': bool(indicator[THS_TRIGGERS[strategy_id]]),
                      'trigger_key': THS_TRIGGERS[strategy_id], 'signal_score': indicator['signal_score']}
    elif strategy_id == 'emotion_limit_up_v1':
        indicator = calculate_emotion_limit_up_signal(candles, lookback_days=int(params['lookback_days']))
        evaluation = evaluate_emotion_limit_up_signal(indicator, params)
    elif strategy_id == 'wulong_cluster_v1':
        indicator = calculate_wulong_cluster_signal(candles)
        evaluation = evaluate_wulong_cluster_signal(indicator, params)
    else:
        indicator = calculate_force_rhythm_signal(candles)
        evaluation = evaluate_force_rhythm_signal(indicator, params)
    enough = indicator.get('available', indicator.get('has_data', False))
    universe = (evaluate_wulong_universe(bars, {key: params[key] for key in UNIVERSE_PARAM_KEYS})
                if strategy_id == 'wulong_cluster_v1' else None)
    if universe is not None:
        if universe['status'] == 'insufficient_data':
            quality.append('candidate_universe_insufficient_data')
        elif not universe['passed']:
            quality.append('candidate_universe_rejected')
    elif strategy_id in TREND_KING_IDS:
        quality.append('sector_context_unavailable')
        if len(bars) < 250:
            quality.append('historical_elasticity_short_window')
    elif strategy_id == 'limit_up_arb_v1':
        quality.extend(('sector_context_unavailable', 'st_status_unknown'))
        if len(bars) < 5:
            quality.append('ma5_volume_unavailable')
            if decimal_value(params['min_volume_ratio_ma5'], 'min_volume_ratio_ma5') > 0:
                enough = False
    elif strategy_id == 'emotion_limit_up_v1':
        quality.append('sector_context_unavailable')
        if len(bars) < 60:
            quality.append('indicator_warmup_short')
    elif (strategy_id in THS_TRIGGERS and len(bars) < 30) or (strategy_id == 'ths_force_rhythm_v1' and len(bars) < 80):
        quality.append('indicator_warmup_short')
    result = {'status': 'computed' if enough else 'insufficient_data',
              'signal': bool(evaluation['signal']) if enough else None,
              'draft_eligible': bool(evaluation['signal']) and bool(enough) and (universe is None or universe['passed']),
              'candidate': {'symbol': symbol, 'date': bars[-1]['event_date']} if bars else None,
              'source_date': bars[-1]['event_date'] if bars else None, 'quality_flags': quality,
              'score': None, 'executable_date': None, 'indicator': _json_safe(indicator),
              'evaluation': _json_safe(evaluation)}
    if universe is not None:
        result['universe'] = universe
        result['candidate_universe_filter_run'] = universe['status'] != 'insufficient_data'
        result['shape_signal'] = result['signal']
        result['signal'] = result['signal'] and universe['passed'] if enough else None
    return result
