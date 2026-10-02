"""Frozen legacy event profiles and deterministic, strict input normalization.

Valid inputs retain the old default rules, ordering, six-decimal rounding, and
score modes. Unlike the old store, invalid fields are rejected rather than
discarded, clamped, truncated, or coerced through Python truthiness. Weighted
profiles require at least one enabled dimension with a positive weight.
Persistence and the active-profile selection belong to the application service.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from trade_app.platform.types import TradeError


DEFAULT_PROFILE_ID = 'system_legacy_formula_v1'
PROFILE_VERSION = 'legacy-event-profiles-frozen-v1'
_PROFILE_FIELDS = {'profile_id', 'name', 'description', 'score_mode', 'is_system',
                   'updated_at', 'dimensions', 'rule_values'}
_DIMENSION_FIELDS = {'dimension_id', 'label', 'metric_key', 'weight', 'invert', 'enabled'}


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return json.loads(Path(__file__).with_name('event_profile_catalog.json').read_text(encoding='utf-8'))


def event_profile_catalog() -> dict:
    """Return detached profiles and the original metric/rule option schemas."""
    return deepcopy(_catalog())


def get_system_profile(profile_id: str = DEFAULT_PROFILE_ID) -> dict:
    for profile in _catalog()['profiles']:
        if profile['profile_id'] == profile_id:
            return deepcopy(profile)
    raise TradeError('EVENT_PROFILE_NOT_FOUND', '事件判别系统模板不存在', 404)


def _invalid(message: str) -> TradeError:
    return TradeError('INVALID_EVENT_PROFILE', message)


def _text(value: object, key: str, maximum: int, *, fallback: str = '') -> str:
    if not isinstance(value, str):
        raise _invalid(f'{key} 须为文本')
    normalized = value.strip() or fallback
    if len(normalized) > maximum:
        raise _invalid(f'{key} 最多 {maximum} 个字符')
    return normalized


def _number(value: object, key: str, minimum: float | int = 0,
            maximum: float | int = 10, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise _invalid(f'{key} 须为数字')
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise _invalid(f'{key} 须为数字') from exc
    if not parsed.is_finite() or not Decimal(str(minimum)) <= parsed <= Decimal(str(maximum)):
        raise _invalid(f'{key} 须在 {minimum} 至 {maximum} 之间')
    if integer:
        if parsed != parsed.to_integral_value():
            raise _invalid(f'{key} 须为整数')
        return int(parsed)
    return round(float(parsed), 6)


def _boolean(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid(f'{key} 须为布尔值 true 或 false')
    return value


def _dimensions(raw: object) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 24:
        raise _invalid('dimensions 须为不超过 24 项的列表')
    metrics = {item['metric_key'] for item in _catalog()['metric_options']}
    dimensions = []
    identifiers: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - _DIMENSION_FIELDS:
            raise _invalid('判别维度包含不支持的字段')
        metric = _text(item.get('metric_key', ''), 'metric_key', 64)
        if metric not in metrics:
            raise _invalid(f'不支持的判别维度指标：{metric}')
        identifier = _text(item.get('dimension_id', ''), 'dimension_id', 64,
                           fallback=f'dim_{index + 1}')
        if identifier in identifiers:
            raise _invalid('dimension_id 不可重复')
        identifiers.add(identifier)
        dimensions.append({'dimension_id': identifier,
                           'label': _text(item.get('label', ''), 'label', 64, fallback=metric),
                           'metric_key': metric,
                           'weight': _number(item.get('weight', 1.0), 'weight'),
                           'invert': _boolean(item.get('invert', False), 'invert'),
                           'enabled': _boolean(item.get('enabled', True), 'enabled')})
    return dimensions


def _rule_values(raw: object, fallback: object) -> list[dict]:
    options = _catalog()['rule_options']
    by_key = {item['rule_key']: item for item in options}
    resolved = {item['rule_key']: item['default_value'] for item in options}
    for values in (fallback, raw):
        if values is None:
            continue
        if not isinstance(values, list) or len(values) > 256:
            raise _invalid('rule_values 须为不超过 256 项的列表')
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, dict) or set(item) - {'rule_key', 'value'}:
                raise _invalid('判别规则包含不支持的字段')
            key = _text(item.get('rule_key', ''), 'rule_key', 96)
            if key not in by_key:
                raise _invalid(f'不支持的判别规则：{key}')
            if key in seen:
                raise _invalid(f'判别规则重复：{key}')
            seen.add(key)
            spec = by_key[key]
            value = item.get('value', spec['default_value'])
            resolved[key] = (_boolean(value, key) if spec['value_type'] == 'boolean' else
                             _number(value, key, spec['min_value'], spec['max_value'],
                                     integer=spec['value_type'] == 'integer'))
    # Normalize defaults as the old store does, including number vs integer types.
    result = []
    for spec in options:
        key = spec['rule_key']
        value = resolved[key]
        result.append({'rule_key': key,
                       'value': (_boolean(value, key) if spec['value_type'] == 'boolean' else
                                 _number(value, key, spec['min_value'], spec['max_value'],
                                         integer=spec['value_type'] == 'integer'))})
    return result


def normalize_profile(raw: dict, *, profile_id: str, updated_at: str,
                      fallback_name: str = '事件判别模板',
                      fallback_rule_values: list[dict] | None = None,
                      is_system: bool = False) -> dict:
    """Normalize a complete snapshot with partial rule overrides.

The service owns profile_id, updated_at and is_system. Matching metadata from a
saved snapshot is accepted; conflicting metadata must be removed by the caller
when making a new copy. Missing rules use defaults, then fallback_rule_values.
"""
    if not isinstance(raw, dict) or set(raw) - _PROFILE_FIELDS:
        raise _invalid('事件判别模板包含不支持的字段')
    identifier = _text(profile_id, 'profile_id', 64)
    if not identifier:
        raise _invalid('profile_id 不能为空')
    timestamp = _text(updated_at, 'updated_at', 64)
    try:
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError(timestamp)
    except ValueError as exc:
        raise _invalid('updated_at 须包含有效时间和时区') from exc
    system = _boolean(is_system, 'is_system')
    owned = {'profile_id': identifier, 'updated_at': timestamp, 'is_system': system}
    for key, expected in owned.items():
        if key in raw and (type(raw[key]) is not type(expected) or raw[key] != expected):
            raise _invalid(f'{key} 与服务端指定值不一致')
    mode = _text(raw.get('score_mode', 'dimension_weighted'), 'score_mode', 32).lower()
    if mode not in {'legacy_formula', 'dimension_weighted'}:
        raise _invalid('不支持的事件判别计分方式')
    dimensions = _dimensions(raw.get('dimensions', []))
    if mode == 'legacy_formula' and dimensions:
        raise _invalid('经典公式不接受额外维度，请选择维度加权模式')
    if mode == 'dimension_weighted' and not any(item['enabled'] and item['weight'] > 0 for item in dimensions):
        raise _invalid('维度加权模板至少需要一个启用且权重大于零的维度')
    name = _text(raw.get('name', ''), 'name', 64, fallback=fallback_name)
    if not name:
        raise _invalid('name 不能为空')
    return {'profile_id': identifier, 'name': name,
            'description': _text(raw.get('description', ''), 'description', 200),
            'score_mode': mode, 'is_system': system, 'updated_at': timestamp,
            'dimensions': dimensions, 'rule_values': _rule_values(raw.get('rule_values'), fallback_rule_values)}


def profile_sha256(profile: dict) -> str:
    """Digest normalized calculation fields, excluding display metadata and time."""
    payload = {key: profile[key] for key in ('profile_id', 'score_mode', 'dimensions', 'rule_values')}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
