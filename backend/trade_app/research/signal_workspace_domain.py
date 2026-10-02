"""Signal presentation on saved observations; no strategy recomputation or orders.

Legacy rank formulas and two-calendar-day expiry are retained. Confirmation is
dated by the first saved observation, never by the backdated pattern event.
Range statistics v2 fix the legacy out-of-range last-close and drawdown reads.
"""
from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo

from trade_app.platform.types import TradeError
from trade_app.research.wulong_universe import candidate_metrics_from_bars

VERSION = 'saved-signal-workspace-v3-full-context'
WYCKOFF = {'wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'}


def local_day(moment: str) -> str:
    parsed = datetime.fromisoformat(moment.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise TradeError('INVALID_SIGNAL_TIME', '信号观察时间必须包含时区')
    return parsed.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()


def _score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return max(0.0, min(100.0, float(value)))


def rank_score(strategy_id: str, result: dict, metrics: dict | None, *, params: dict | None = None, metric_source='store._build_row_from_candles') -> dict:
    indicator, evaluation = result.get('indicator') or {}, result.get('evaluation') or {}
    quality = _score(indicator.get('entry_quality_score'))
    if quality is None:
        quality = _score(evaluation.get('signal_score', indicator.get('signal_score')))
    if quality is None:
        quality = _score(evaluation.get('local_score'))
    health, event = _score(indicator.get('health_score')), _score(indicator.get('event_score'))
    formula = None
    score = None
    params = params or {}
    components = None
    if strategy_id == 'relative_strength_breakout_v1' and metrics is not None and health is not None and event is not None:
        components = {'health': health, 'event': event, 'strength': _score(metrics['ret40']/.40*100),
                      'volume': _score((metrics['up_down_volume_ratio']-1)/.8*100),
                      'structure': _score(100-abs(metrics['retrace20']-.12)/.20*100-max(0,metrics['pullback_volume_ratio']-.92)*100)}
        weights = {key:float(params.get('rank_weight_'+key,default)) for key,default in zip(components,(.25,.25,.30,.10,.10))}
        score = sum(components[key]*weights[key] for key in components)/max(.01,sum(weights.values()))
        formula = 'sum(health,event,strength,volume,structure * frozen_weights)/max(0.01,sum(weights))'
        components = {'scores':components,'weights':weights}
    elif strategy_id == 'matrix_signal_v1' and metrics is not None:
        s1 = metrics['retrace20'] < float(params.get('atr_ratio_max',.7))
        s2 = metrics['vol_slope20'] < float(params.get('vol_ratio_max',.6))
        s3 = metrics['ret40'] > 0
        s4 = metrics['retrace20'] < float(params.get('sideways_range_max',.18)) and metrics['price_vs_ma20'] < float(params.get('near_ma20_max',.06))
        s5 = metrics['ret40'] > 0 and metrics['up_down_volume_ratio'] >= float(params.get('breakout_vol_ratio',1.2))
        s6 = abs(metrics['price_vs_ma20']) < .06 and metrics['pullback_days'] <= float(params.get('max_pullback_days',3))
        s7 = metrics['ma10_above_ma20_days'] >= 5
        score,formula=(s1+s2+s3+s4+s5*2+s6*1.5+s7)/8.5*100,'(s1+s2+s3_positive_return+s4+s5*2+s6*1.5+s7)/8.5*100'
    elif strategy_id == 'b1_mtf_v1' and metrics is not None and quality is not None:
        score=quality*.70+_score(max(0,metrics['ret40'])/.30*100)*.15+_score((1-min(1,metrics['pullback_volume_ratio']))*30)*.15
        formula='quality*0.70+trend_bonus*0.15+contraction_bonus*0.15'
    elif strategy_id in ('wyckoff_trend_v1', 'wyckoff_trend_v2') and health is not None and event is not None:
        score, formula = health * .45 + event * .55, 'health_score*0.45+event_score*0.55'
    elif strategy_id in ('score_only_rank_v1', 'ths_main_force_flip_v1', 'ths_main_force_golden_cross_v1'):
        score, formula = quality, 'entry_quality_score'
    elif quality is not None and metrics is not None:
        trend = min(100.0, max(0.0, metrics['ret40']) / (.35 if strategy_id in ('emotion_limit_up_v1', 'ths_force_rhythm_v1') else .30) * 100)
        volume = min(100.0, max(0.0, (metrics['up_down_volume_ratio'] - 1) / .8 * 100))
        if strategy_id.startswith('trend_king'):
            score, formula = quality * .65 + trend * .20 + volume * .15, 'quality*0.65+trend_bonus*0.20+volume_bonus*0.15'
        elif strategy_id == 'limit_up_arb_v1':
            score, formula = quality * .85 + volume * .15, 'quality*0.85+volume_bonus*0.15'
        elif strategy_id == 'emotion_limit_up_v1':
            score, formula = quality * .70 + trend * .20 + volume * .10, 'quality*0.70+trend_bonus*0.20+volume_bonus*0.10'
        elif strategy_id in ('wulong_cluster_v1', 'ths_force_rhythm_v1'):
            score, formula = quality * .75 + trend * .15 + volume * .10, 'quality*0.75+trend_bonus*0.15+volume_bonus*0.10'
    return {'rank_score': _score(score), 'rank_formula': formula, 'entry_quality_score': quality,
            'health_score':health,'event_score':event,'rank_components':components,
            'rank_scope': 'same_strategy_same_decision_date_selected_pool',
            'rank_metric_source': metric_source, 'rank_metrics': metrics}


def signal_row(run: dict, decision_bars: list[dict], report_bars: list[dict],
               as_of_date: str, descriptor: dict, delay: int) -> dict:
    result = run['result']
    indicator, evaluation = result.get('indicator') or {}, result.get('evaluation') or {}
    decision_date = local_day(run['decision_at'])
    source = result.get('source_date')
    signal = result.get('signal') is True and source == decision_date
    has_context = bool(result.get('signal_context'))
    trigger = indicator.get('trigger_date') if run['strategy_id'] in WYCKOFF or has_context else source
    if not isinstance(trigger, str) or trigger > decision_date:
        trigger = None
    if trigger:
        try:
            date.fromisoformat(trigger)
        except ValueError:
            trigger = None
    primary = evaluation.get('primary_event') or indicator.get('signal') or evaluation.get('trigger_key') or run['strategy_id']
    confirmation = ((indicator.get('event_confirmation_map') or {}).get(primary) or indicator.get('confirmation_status', 'unconfirmed')) if run['strategy_id'] in WYCKOFF or has_context else 'confirmed_on_observation'
    confirmed = confirmation in ('confirmed', 'confirmed_on_observation')
    dates = [bar['event_date'] for bar in report_bars if bar['event_date'] <= as_of_date]
    age = len([day for day in dates if trigger < day]) if trigger in dates else None
    expire = (date.fromisoformat(trigger) + timedelta(days=2)).isoformat() if trigger else None
    days_to_expire = (date.fromisoformat(expire) - date.fromisoformat(as_of_date)).days if expire else None
    timeliness = ('unknown' if days_to_expire is None else 'expired' if days_to_expire < 0
                  else 'expiring' if days_to_expire <= 1 else 'active')
    effective_delay = 1 if run['strategy_id'] == 'limit_up_arb_v1' else delay
    anchor = max(source or decision_date, decision_date)
    later = [day for day in dates if day > anchor]
    executable = later[effective_delay - 1] if len(later) >= effective_delay and confirmed and signal else None
    rank = (result.get('ranking') or rank_score(run['strategy_id'],result,None,params=run.get('params'),
        metric_source=result['signal_context']['candidate_path'])) if has_context else rank_score(run['strategy_id'], result, candidate_metrics_from_bars(decision_bars))
    quality = rank['entry_quality_score']
    priority = 3 if (quality is not None and quality >= 82) or str(indicator.get('phase', '')).startswith(('吸筹D', '吸筹E')) else 2 if quality is not None and quality >= 68 else 1
    return {'run_id': run['id'], 'dataset_id': run['dataset_id'], 'strategy_id': run['strategy_id'],
            'strategy_version': run['strategy_version'], 'decision_at': run['decision_at'],
            'signal_context':result.get('signal_context'),'universe':result.get('universe'),
            'decision_date': decision_date, 'source_date': source, 'signal': signal,
            'trigger_date': trigger, 'signal_age_bars': age, 'age_basis': 'observed_trading_bars',
            'expire_date': expire, 'timeliness': timeliness, 'days_to_expire': days_to_expire,
            'primary_event': primary, 'confirmation_status': confirmation,
            'confirmation_observed_at': run['decision_at'] if confirmed and signal else None,
            'confirmation_date': decision_date if confirmed and signal else None,
            'confirmation_basis': 'saved_observation_upper_bound_not_backdated_event',
            'requested_entry_delay_bars': delay, 'effective_entry_delay_bars': effective_delay,
            'entry_policy': 'legacy_fixed_t1' if run['strategy_id'] == 'limit_up_arb_v1' else 'after_saved_confirmation',
            'executable_date': executable, 'execution_status': 'no_signal' if not signal else 'unconfirmed' if not confirmed else 'observed_next_open' if executable else 'pending_future_bar',
            'draft_eligible': signal and result.get('draft_eligible') is not False,
            'events': indicator.get('events') or [], 'risk_events': indicator.get('risk_events') or [],
            'event_dates': indicator.get('event_dates') or {}, 'event_chain': indicator.get('event_chain') or [],
            'phase': indicator.get('phase'), 'quality_flags': result.get('quality_flags') or [],
            'priority': priority, 'event_count': len(indicator.get('event_chain') or []) or len(indicator.get('events') or []) + len(indicator.get('risk_events') or []) or (1 if signal else 0),
            'capabilities': descriptor['capabilities'], 'code_sha256': result.get('code_sha256'), **rank}


def range_performance(bars: list[dict], date_from: str, date_to: str) -> dict:
    selected = [bar for bar in bars if date_from <= bar['event_date'] <= date_to]
    base = {'date_from': date_from, 'date_to': date_to, 'bar_count': len(selected),
            'basis': 'observed_close_to_close_unadjusted_costs_excluded', 'calculation_version': VERSION}
    if not selected:
        return {**base, 'status': 'missing', 'start_date': None, 'end_date': None,
                'start_close': None, 'end_close': None, 'return_pct': None, 'max_drawdown_pct': None}
    closes = [float(bar['close']) for bar in selected]
    if any(not math.isfinite(value) or value <= 0 for value in closes):
        raise TradeError('INVALID_SIGNAL_PRICE', '区间价格须为有限正数')
    peak, drawdown = closes[0], 0.0
    for close in closes:
        peak = max(peak, close)
        drawdown = max(drawdown, (peak - close) / peak)
    return {**base, 'status': 'single_bar' if len(selected) == 1 else 'observed',
            'start_date': selected[0]['event_date'], 'end_date': selected[-1]['event_date'],
            'start_close': selected[0]['close'], 'end_close': selected[-1]['close'],
            'return_pct': round((closes[-1] / closes[0] - 1) * 100, 2),
            'max_drawdown_pct': round(drawdown * 100, 2)}
