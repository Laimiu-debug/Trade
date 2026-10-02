"""Event-based matrix adapter, explicitly separate from raw OHLCV S1-S9.

The legacy snapshot-wide confirmation gate remains selectable for comparison.
The default event gate cannot let a different confirmed event authorize an
unconfirmed entry. Both modes use only the caller's frozen, known prefix.
"""
from __future__ import annotations

from datetime import date
import math

from trade_app.platform.types import TradeError
from trade_app.research.wyckoff_domain import (WYCKOFF_ACC_EVENTS, WYCKOFF_EVENT_ORDER,
                                               calculate_snapshot)

VERSION = 'aligned-wyckoff-event-dates-confirmation-v1'
PARAM_SCHEMA = {
    'window_days': {'type': 'integer', 'minimum': 20, 'maximum': 240},
    'min_score': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'min_event_count': {'type': 'integer', 'minimum': 0, 'maximum': 12},
    'require_sequence': {'type': 'boolean'},
    'entry_events': {'type': 'events', 'options': list(WYCKOFF_ACC_EVENTS)},
    'exit_events': {'type': 'events', 'options': list(WYCKOFF_EVENT_ORDER)},
    'health_score_min': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'event_score_min': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'event_grade_min': {'type': 'enum', 'options': ['A', 'B', 'C']},
    'require_key_event_confirmation': {'type': 'boolean'},
    'confirmation_policy': {'type': 'enum', 'options': ['event', 'legacy_snapshot']},
}
DEFAULTS = {'window_days': 60, 'min_score': 55.0, 'min_event_count': 1, 'require_sequence': False,
            'entry_events': ['Spring', 'SOS', 'JOC', 'LPS'], 'exit_events': ['UTAD', 'SOW', 'LPSY'],
            'health_score_min': 0.0, 'event_score_min': 0.0, 'event_grade_min': 'C',
            'require_key_event_confirmation': False, 'confirmation_policy': 'event'}


def normalize_event_params(raw):
    if not isinstance(raw, dict) or set(raw) - set(PARAM_SCHEMA):
        raise TradeError('INVALID_EVENT_MATRIX_PARAM', '包含未实现的事件矩阵参数；排名权重不用于该模式')
    result = {}
    for key, spec in PARAM_SCHEMA.items():
        value = raw.get(key, DEFAULTS[key])
        kind = spec['type']
        if kind == 'boolean':
            if isinstance(value, str) and value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            if type(value) is not bool:
                raise TradeError('INVALID_EVENT_MATRIX_PARAM', f'{key} 必须是布尔值')
        elif kind in ('integer', 'number'):
            try:
                if isinstance(value, bool):
                    raise ValueError()
                number = float(value)
                if not math.isfinite(number) or not spec['minimum'] <= number <= spec['maximum'] or kind == 'integer' and not number.is_integer():
                    raise ValueError()
                value = int(number) if kind == 'integer' else number
            except (TypeError, ValueError, OverflowError) as exc:
                raise TradeError('INVALID_EVENT_MATRIX_PARAM', f'{key} 超出支持范围') from exc
        elif kind == 'enum':
            if value not in spec['options']:
                raise TradeError('INVALID_EVENT_MATRIX_PARAM', f'{key} 不是有效选项')
        elif kind == 'events':
            if (not isinstance(value, list) or len(value) > 12 or any(not isinstance(event, str) for event in value)
                    or len(set(value)) != len(value) or any(event not in spec['options'] for event in value)):
                raise TradeError('INVALID_EVENT_MATRIX_PARAM', f'{key} 必须为不重复的合法事件列表，最多12项')
            value = list(value)
        result[key] = value
    return result


def _number(raw, fallback=0.0):
    try:
        value = float(raw)
        return value if math.isfinite(value) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def _day(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def evaluate_event_snapshot(snapshot, params, *, source_date):
    """Legacy date/gate predicates with inspectable event-level confirmation."""
    params = normalize_event_params(params)
    if not isinstance(snapshot, dict) or not _day(source_date):
        raise TradeError('INVALID_EVENT_MATRIX_SNAPSHOT', '事件矩阵需要快照与有效观察日期')
    raw_dates = snapshot.get('event_dates') if isinstance(snapshot.get('event_dates'), dict) else {}
    event_dates = {event: day for event, raw in raw_dates.items() if event in WYCKOFF_EVENT_ORDER
                   and (day := _day(raw)) is not None and day <= source_date}
    quality = []
    if any(_day(raw) and raw > source_date for raw in raw_dates.values()):
        quality.append('FUTURE_EVENT_DATE_EXCLUDED')
    if any(not _day(raw) for raw in raw_dates.values()):
        quality.append('INVALID_EVENT_DATE_EXCLUDED')
    today_entry = [event for event in params['entry_events'] if event_dates.get(event) == source_date]
    today_exit = [event for event in params['exit_events'] if event_dates.get(event) == source_date]
    chain = snapshot.get('event_chain')
    count = sum(isinstance(item, dict) and bool(str(item.get('event', '')).strip()) for item in chain) if isinstance(chain, list) else 0
    if not count:
        count = len(snapshot['events']) if isinstance(snapshot.get('events'), list) else 0
        count += len(snapshot['risk_events']) if isinstance(snapshot.get('risk_events'), list) else 0
    entry_quality = _number(snapshot.get('entry_quality_score', 0))
    health = _number(snapshot.get('health_score', entry_quality))
    event_score = _number(snapshot.get('event_score', snapshot.get('event_strength_score', entry_quality)))
    grade = str(snapshot.get('event_grade') or 'C').upper()
    grade = grade if grade in ('A', 'B', 'C') else 'C'
    overall_confirmation = str(snapshot.get('confirmation_status') or 'unconfirmed').lower()
    if overall_confirmation not in ('confirmed', 'partial', 'unconfirmed', 'risk_blocked'):
        overall_confirmation = 'unconfirmed'
    confirmation_map = snapshot.get('event_confirmation_map') if isinstance(snapshot.get('event_confirmation_map'), dict) else {}
    confirmed_today = [event for event in today_entry if str(confirmation_map.get(event, '')).lower() == 'confirmed']
    accepted_entry = today_entry
    if params['require_key_event_confirmation']:
        accepted_entry = today_entry if params['confirmation_policy'] == 'legacy_snapshot' and overall_confirmation == 'confirmed' else confirmed_today if params['confirmation_policy'] == 'event' else []
    checks = []
    def check(key, actual, expected, passed, reason):
        checks.append({'parameter': key, 'actual': actual, 'expected': expected, 'passed': bool(passed), 'reason': None if passed else reason})
    check('entry_event_date', today_entry, source_date, bool(today_entry), 'NO_ENTRY_EVENT_ON_SOURCE_DATE')
    check('min_event_count', count, params['min_event_count'], count >= params['min_event_count'], 'EVENT_COUNT_BELOW_MINIMUM')
    check('require_sequence', bool(snapshot.get('sequence_ok')), params['require_sequence'], not params['require_sequence'] or bool(snapshot.get('sequence_ok')), 'EVENT_SEQUENCE_REQUIRED')
    check('min_score', entry_quality, params['min_score'], entry_quality >= params['min_score'], 'ENTRY_QUALITY_BELOW_MINIMUM')
    check('health_score_min', health, params['health_score_min'], health >= params['health_score_min'], 'HEALTH_BELOW_MINIMUM')
    check('event_score_min', event_score, params['event_score_min'], event_score >= params['event_score_min'], 'EVENT_SCORE_BELOW_MINIMUM')
    check('event_grade_min', grade, params['event_grade_min'], {'A': 3, 'B': 2, 'C': 1}[grade] >= {'A': 3, 'B': 2, 'C': 1}[params['event_grade_min']], 'EVENT_GRADE_BELOW_MINIMUM')
    check('require_key_event_confirmation', {event: confirmation_map.get(event, 'unknown') for event in today_entry},
          params['confirmation_policy'] if params['require_key_event_confirmation'] else 'disabled',
          not params['require_key_event_confirmation'] or bool(accepted_entry), 'ENTRY_EVENT_NOT_CONFIRMED')
    if params['confirmation_policy'] == 'legacy_snapshot':
        quality.append('SNAPSHOT_WIDE_CONFIRMATION_COMPARISON')
    if today_exit and today_entry:
        quality.append('EXIT_OVERRIDES_SIMULTANEOUS_ENTRY')
    gate = all(item['passed'] for item in checks)
    buy, sell = gate and not today_exit, bool(today_exit)
    components = {f'S{i}': False for i in range(1, 10)}
    components.update(S5=buy, S7=buy, S8=sell)
    return {'version': VERSION, 'source_date': source_date, 'buy': buy, 'sell': sell,
            'in_pool': gate, 'score': max(0.0, min(100.0, entry_quality)) if gate else 0.0,
            'components': components, 'reasons': [item['reason'] for item in checks if not item['passed']],
            'checks': checks, 'entry_events': today_entry, 'accepted_entry_events': accepted_entry,
            'exit_events': today_exit, 'event_dates': event_dates, 'event_confirmation_map': dict(confirmation_map),
            'confirmation_policy': params['confirmation_policy'], 'snapshot_confirmation_status': overall_confirmation,
            'event_count': count, 'entry_quality_score': entry_quality, 'health_score': health,
            'event_score': event_score, 'event_grade': grade, 'quality_flags': quality,
            'event_time_semantics': 'event_date is occurrence; observed snapshot is available only at caller decision time; no backdating'}


def evaluate_event_bars(bars, params, *, event_profile):
    params = normalize_event_params(params)
    if not event_profile or not isinstance(event_profile.get('snapshot'), dict):
        raise TradeError('EVENT_PROFILE_REQUIRED', '事件矩阵必须冻结事件模板')
    calculated = calculate_snapshot(bars, params['window_days'], profile=event_profile['snapshot'])
    if not calculated['source_date']:
        return {'source_date': None, 'buy': False, 'sell': False, 'in_pool': False, 'score': 0,
                'components': {f'S{i}': False for i in range(1, 10)}, 'reasons': ['INSUFFICIENT_DATA'],
                'quality_flags': [], 'ma10': None}
    result = evaluate_event_snapshot(calculated['snapshot'], params, source_date=calculated['source_date'])
    if not calculated['has_data']:
        result.update(buy=False, sell=False, in_pool=False, score=0)
        result['reasons'].append('INSUFFICIENT_DATA')
    # Legacy matrix in_pool is the entry gate, not a reusable daily universe.
    # The common fixed-sample pool keeps computable stocks, so a delayed entry
    # need not invent a second occurrence of yesterday's event to remain valid.
    result['event_gate_passed'] = result['in_pool']
    result['in_pool'] = bool(calculated['has_data'])
    result['components'].update(entry_events=result['entry_events'], exit_events=result['exit_events'],
        accepted_entry_events=result['accepted_entry_events'], event_gate_passed=result['event_gate_passed'],
        confirmation_policy=result['confirmation_policy'], event_confirmation_map=result['event_confirmation_map'],
        entry_quality_score=result['entry_quality_score'], health_score=result['health_score'],
        event_score=result['event_score'], event_grade=result['event_grade'],
        rejected_gates=result['reasons'], checks=result['checks'])
    result['ma10'] = sum(float(bar['close']) for bar in bars[-10:]) / 10 if len(bars) >= 10 else None
    return result
