"""Inspectable observation gates over a precomputed, point-in-time Wyckoff snapshot.

These are the legacy store's single-stock scan predicates. They do not select a
trend universe, rank a stock pool, or implement the vectorized matrix/backtest.
V2's matrix semantic/weight options were unused by that scan and are rejected.
An explicit zero health score is preserved, fixing the legacy ``value or quality``
fallback that could incorrectly let a zero-health snapshot pass the V2 gate.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from trade_app.platform.types import TradeError, decimal_value


WYCKOFF_STRATEGY_IDS = ('wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1')
WYCKOFF_GATE_VERSION = 'wyckoff-observation-gates-v1'
_GATE_KEYS = ('min_score', 'min_event_count', 'require_sequence', 'health_score_min',
              'event_score_min', 'event_grade_min', 'require_key_event_confirmation')
_KEY_EVENTS = {'SOS', 'LPS', 'Spring', 'JOC'}
_GRADE_RANK = {'C': 1, 'B': 2, 'A': 3}


@lru_cache(maxsize=3)
def _schema(strategy_id: str) -> dict:
    if strategy_id not in WYCKOFF_STRATEGY_IDS:
        raise TradeError('UNKNOWN_STRATEGY', '该策略不支持维科夫观察门槛')
    catalog = json.loads(Path(__file__).with_name('legacy_catalog.json').read_text(encoding='utf-8'))
    descriptor = next(row for row in catalog['strategies'] if row['strategy_id'] == strategy_id)
    # V1 has an empty strategy schema; these are get_signals' effective defaults.
    fallback = {
        'min_score': {'type': 'number', 'title': '入场质量分下限', 'minimum': 0, 'maximum': 100, 'default': 60},
        'min_event_count': {'type': 'integer', 'title': '最少事件数（含风险事件）', 'minimum': 0, 'maximum': 12, 'default': 1},
        'require_sequence': {'type': 'boolean', 'title': '要求事件序列', 'default': False},
        'health_score_min': {'type': 'number', 'title': '健康分下限', 'minimum': 0, 'maximum': 100, 'default': 0},
        'event_score_min': {'type': 'number', 'title': '事件分下限', 'minimum': 0, 'maximum': 100, 'default': 0},
        'event_grade_min': {'type': 'enum', 'title': '事件等级下限', 'options': ['A', 'B', 'C'], 'default': 'C'},
        'require_key_event_confirmation': {'type': 'boolean', 'title': '主要关键事件须确认', 'default': False},
    }
    result = {'window_days': {'type': 'integer', 'title': '事件窗口（交易日）',
                               'minimum': 20, 'maximum': 240, 'default': 60}}
    for key in _GATE_KEYS:
        spec = deepcopy(descriptor['params_schema'].get(key, fallback[key]))
        spec['default'] = descriptor['default_params'].get(key, spec['default'])
        result[key] = spec
    return result


def wyckoff_param_schema(strategy_id: str) -> dict:
    return deepcopy(_schema(strategy_id))


def normalize_wyckoff_params(strategy_id: str, raw: dict[str, Any]) -> dict[str, str | bool]:
    schema = _schema(strategy_id)
    if set(raw) - set(schema):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '包含当前观察门槛未实现的参数')
    result = {}
    for key, spec in schema.items():
        value = raw.get(key, spec['default'])
        if spec['type'] == 'boolean':
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, str) and value.strip().lower() in {'true', 'false'}:
                result[key] = value.strip().lower() == 'true'
            else:
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为 true 或 false')
        elif spec['type'] == 'enum':
            text = str(value).strip().upper()
            if text not in spec['options']:
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为 A、B 或 C')
            result[key] = text
        else:
            if isinstance(value, bool):
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为数值')
            try:
                number = decimal_value(value, key)
            except TradeError as exc:
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为有限数值') from exc
            if not decimal_value(spec['minimum'], key) <= number <= decimal_value(spec['maximum'], key):
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
            if spec['type'] == 'integer' and number != int(number):
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为整数')
            result[key] = str(int(number)) if spec['type'] == 'integer' else format(number.normalize(), 'f')
    return result


def _score(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TradeError('INVALID_WYCKOFF_SNAPSHOT', f'{field} 缺失或不是有效分数') from exc
    if isinstance(value, bool) or not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise TradeError('INVALID_WYCKOFF_SNAPSHOT', f'{field} 须为 0 至 100 的有限分数')
    return parsed


def _evidence(snapshot: dict) -> tuple[list, list, dict, list, dict]:
    events = list(snapshot['events']) if isinstance(snapshot.get('events'), list) else []
    risk_events = list(snapshot['risk_events']) if isinstance(snapshot.get('risk_events'), list) else []
    raw_dates = snapshot.get('event_dates')
    dates = {str(key).strip(): str(value).strip() for key, value in raw_dates.items()
             if str(key).strip() and str(value).strip()} if isinstance(raw_dates, dict) else {}
    chain = []
    raw_chain = snapshot.get('event_chain')
    if isinstance(raw_chain, list):
        for node in raw_chain:
            if not isinstance(node, dict):
                continue
            event = str(node.get('event', '')).strip()
            day = str(node.get('date', '')).strip()
            if event and day:
                chain.append({'event': event, 'date': day,
                              'category': str(node.get('category', '')).strip() or 'other'})
    if not chain and dates:
        risks = {str(event).strip() for event in risk_events}
        chain = [{'event': event, 'date': day,
                  'category': 'distributionRisk' if event in risks else 'accumulation'}
                 for event, day in sorted(dates.items(), key=lambda item: (item[1], item[0]))]
    raw_confirmation = snapshot.get('event_confirmation_map')
    confirmation = {str(key).strip(): str(value).strip().lower() for key, value in raw_confirmation.items()
                    if str(key).strip() and str(value).strip().lower() in {'confirmed', 'pending', 'failed'}} if isinstance(raw_confirmation, dict) else {}
    return events, risk_events, dates, chain, confirmation


def evaluate_wyckoff_strategy(strategy_id: str, snapshot: dict, params: dict,
                              candidate: dict | None = None) -> dict:
    """Evaluate seven gates, keeping event quality separate from candidate score.

``window_days`` is validated and returned for the snapshot-producing caller. The
snapshot must already be calculated from the specified frozen window. A passing
observation can still be a risk event, as in the original scan; this method makes
no buy-order eligibility decision. The caller checks trigger-date availability.
"""
    normalized = normalize_wyckoff_params(strategy_id, params)
    entry_quality = _score(snapshot.get('entry_quality_score'), 'entry_quality_score')
    health_raw = snapshot.get('health_score')
    health = _score(entry_quality if health_raw is None else health_raw, 'health_score')
    event_raw = snapshot.get('event_score', snapshot.get('event_strength_score', entry_quality))
    event_score = _score(0 if event_raw is None else event_raw, 'event_score')
    sequence = snapshot.get('sequence_ok')
    if not isinstance(sequence, bool):
        raise TradeError('INVALID_WYCKOFF_SNAPSHOT', 'sequence_ok 须为布尔值')
    events, risks, event_dates, chain, confirmation = _evidence(snapshot)
    event_count = len(chain) if chain else len(events) + len(risks)
    grade = str(snapshot.get('event_grade') or 'C').strip().upper()
    grade = grade if grade in _GRADE_RANK else 'C'
    primary = str(snapshot.get('signal', '')).strip()
    requires_confirmation = normalized['require_key_event_confirmation'] and primary in _KEY_EVENTS
    primary_confirmation = confirmation.get(primary, 'pending')
    checks = []

    def check(parameter: str, actual: Any, threshold: Any, passed: bool, reason: str) -> None:
        checks.append({'parameter': parameter, 'actual': actual, 'threshold': threshold,
                       'passed': passed, 'reason': None if passed else reason})

    check('min_event_count', event_count, int(normalized['min_event_count']),
          event_count >= int(normalized['min_event_count']), 'EVENT_COUNT_BELOW_MIN')
    check('require_sequence', sequence, normalized['require_sequence'],
          not normalized['require_sequence'] or sequence, 'EVENT_SEQUENCE_REQUIRED')
    check('min_score', entry_quality, float(normalized['min_score']),
          entry_quality >= float(normalized['min_score']), 'ENTRY_QUALITY_BELOW_MIN')
    check('health_score_min', health, float(normalized['health_score_min']),
          health >= float(normalized['health_score_min']), 'HEALTH_SCORE_BELOW_MIN')
    check('event_score_min', event_score, float(normalized['event_score_min']),
          event_score >= float(normalized['event_score_min']), 'EVENT_SCORE_BELOW_MIN')
    check('event_grade_min', grade, normalized['event_grade_min'],
          _GRADE_RANK[grade] >= _GRADE_RANK[normalized['event_grade_min']], 'EVENT_GRADE_BELOW_MIN')
    check('require_key_event_confirmation', primary_confirmation if primary in _KEY_EVENTS else 'not_applicable',
          normalized['require_key_event_confirmation'], not requires_confirmation or primary_confirmation == 'confirmed',
          'PRIMARY_KEY_EVENT_UNCONFIRMED')
    raw_trigger = str(snapshot.get('trigger_date') or '').strip()
    try:
        trigger = date.fromisoformat(raw_trigger).isoformat()
        valid_trigger = trigger == raw_trigger
        if not valid_trigger:
            trigger = None
    except ValueError:
        trigger, valid_trigger = None, False
    # The old scanner replaced invalid dates with today's date; never invent provenance.
    check('trigger_date', raw_trigger, 'YYYY-MM-DD', valid_trigger, 'INVALID_TRIGGER_DATE')
    reasons = [item['reason'] for item in checks if not item['passed']]
    local_score = entry_quality if strategy_id == 'score_only_rank_v1' else health * 0.45 + event_score * 0.55
    candidate_score = None
    if candidate is not None and candidate.get('score') is not None:
        candidate_score = _score(candidate['score'], 'candidate.score')
    quality = ['EXPLICIT_ZERO_HEALTH_PRESERVED'] if health == 0 and entry_quality > 0 else []
    return {
        'calculation_version': WYCKOFF_GATE_VERSION, 'params': normalized,
        'signal': not reasons, 'checks': checks, 'reasons': reasons,
        'entry_quality_score': entry_quality, 'health_score': health, 'event_score': event_score,
        'event_grade': grade, 'event_count': event_count, 'sequence_ok': sequence,
        'primary_event': primary, 'positive_primary_event': primary in _KEY_EVENTS,
        'primary_event_confirmation': primary_confirmation,
        'events': events, 'risk_events': risks, 'event_dates': event_dates,
        'event_chain': chain, 'event_confirmation_map': confirmation, 'trigger_date': trigger,
        'local_score': max(0.0, min(100.0, local_score)),
        'local_score_formula': 'entry_quality_score' if strategy_id == 'score_only_rank_v1' else '0.45 * health_score + 0.55 * event_score',
        'candidate_score': candidate_score,
        'candidate_quality_flags': list((candidate or {}).get('quality_flags', [])),
        'quality_flags': quality,
        'limitations': ['单股观察门槛；未执行趋势池筛选、跨股票排名或回测',
                        '风险事件也参与旧扫描的事件计数，观察通过不等于买入条件',
                        '矩阵语义版本及排名权重不属于本次旧扫描门槛'],
    }
