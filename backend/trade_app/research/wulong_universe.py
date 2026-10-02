"""Wulong candidate gates for the legacy single-stock K-line candidate path.

The caller must supply sorted, already eligible frozen bars. This adapter never
reads dataset metadata or decides when a bar was available. Its metric semantics
match ``InMemoryStore._build_row_from_candles``, including the 19-index MA count
and the all-history up/down-volume ratio. The TDX pool computes some same-named
metrics differently and is deliberately a separate calculation path.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trade_app.platform.types import TradeError, decimal_value


CALCULATION_VERSION = 'legacy-store-wulong-universe-v1'
SOURCE_PATH = 'store._build_row_from_candles'
MINIMUM_BARS = 30
# Registry defaults are authoritative; the plugin's fallback defaults differ.
DEFAULT_UNIVERSE_PARAMS = {
    'min_ret40': '0.06',
    'max_retrace20': '0.18',
    'min_up_down_volume_ratio': '1.08',
    'min_vol_slope20': '0',
    'min_ma10_above_ma20_days': '4',
    'min_ma5_above_ma10_days': '3',
    'allow_upper_shadow_risk': False,
    'allow_blowoff_top': False,
}
UNIVERSE_PARAM_KEYS = tuple(DEFAULT_UNIVERSE_PARAMS)
_BOUNDS = {
    'min_ret40': ('0', '1.2'),
    'max_retrace20': ('0.01', '0.5'),
    'min_up_down_volume_ratio': ('0.8', '3'),
    'min_vol_slope20': ('-0.5', '0.5'),
    'min_ma10_above_ma20_days': ('0', '30'),
    'min_ma5_above_ma10_days': ('0', '30'),
}
_INTEGER_KEYS = frozenset(('min_ma10_above_ma20_days', 'min_ma5_above_ma10_days'))


def normalize_universe_params(raw: Mapping[str, Any] | None = None) -> dict:
    """Validate the eight universe parameters, parsing booleans explicitly."""
    raw = {} if raw is None else raw
    if set(raw) - set(UNIVERSE_PARAM_KEYS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '五龙聚首入池过滤包含不支持的参数')
    result = dict(DEFAULT_UNIVERSE_PARAMS)
    for key, value in raw.items():
        if key not in _BOUNDS:
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, str) and value.lower() in ('true', 'false'):
                result[key] = value.lower() == 'true'
            else:
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为 true 或 false')
            continue
        number = decimal_value(value, key)
        lower, upper = _BOUNDS[key]
        if number < decimal_value(lower, key) or number > decimal_value(upper, key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        if key in _INTEGER_KEYS and number != int(number):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为整数')
        result[key] = format(number, 'f')
    return result


def _mean(values: list[float] | list[int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def candidate_metrics_from_bars(bars: list[dict]) -> dict | None:
    """Return exactly the eight rounded candidate fields consumed by Wulong."""
    if len(bars) < MINIMUM_BARS:
        return None
    closes = [float(bar['close']) for bar in bars]
    highs = [float(bar['high']) for bar in bars]
    lows = [float(bar['low']) for bar in bars]
    opens = [float(bar['open']) for bar in bars]
    volumes = [max(0, int(bar['volume'])) for bar in bars]
    latest = closes[-1]
    look40 = min(40, len(closes) - 1)
    start_close = closes[len(closes) - look40]
    ret40 = (latest - start_close) / max(start_close, 0.01)
    high20 = max(highs[-20:])
    retrace20 = max(0.0, (high20 - latest) / max(high20, 0.01))
    avg_vol10 = _mean(volumes[-10:])
    avg_prev10 = _mean(volumes[-20:-10])
    vol_slope20 = (avg_vol10 - avg_prev10) / max(avg_prev10, 1.0)
    up_volumes, down_volumes = [], []
    for index in range(1, len(closes)):
        (up_volumes if closes[index] >= closes[index - 1] else down_volumes).append(volumes[index])
    ratio = _mean(up_volumes) / max(_mean(down_volumes), 1.0)

    ma10_days = ma5_days = 0
    # Retain the legacy exclusive lower range bound and >= comparisons.
    for index in range(len(closes) - 1, max(len(closes) - 20, 1), -1):
        ma10 = _mean(closes[max(0, index - 9):index + 1])
        ma20 = _mean(closes[max(0, index - 19):index + 1])
        ma5 = _mean(closes[max(0, index - 4):index + 1])
        ma10_days += int(ma10 >= ma20)
        ma5_days += int(ma5 >= ma10)

    blowoff = any(volumes[index] > max(avg_vol10, 1.0) * 2.5 and closes[index] <= opens[index]
                  for index in range(len(closes) - 20, len(closes)))
    upper_shadow = any(
        highs[index] - lows[index] > 0
        and (highs[index] - max(opens[index], closes[index])) / (highs[index] - lows[index]) > 0.5
        and closes[index] <= opens[index]
        for index in range(len(closes) - 5, len(closes)))
    return {
        'ret40': round(ret40, 4), 'retrace20': round(retrace20, 4),
        'up_down_volume_ratio': round(ratio, 4), 'vol_slope20': round(vol_slope20, 4),
        'ma10_above_ma20_days': ma10_days, 'ma5_above_ma10_days': ma5_days,
        'has_upper_shadow_risk': upper_shadow, 'has_blowoff_top': blowoff,
    }


def evaluate_wulong_candidate(metrics: Mapping[str, Any], params: Mapping[str, Any] | None = None) -> dict:
    """Apply exact plugin predicates to candidate metrics and explain each gate."""
    normalized = normalize_universe_params(params)
    definitions = (
        ('min_ret40', 'ret40', '>=', '40日涨幅未达下限'),
        ('max_retrace20', 'retrace20', '<=', '20日回撤超过上限'),
        ('min_up_down_volume_ratio', 'up_down_volume_ratio', '>=', '上涨量能比未达下限'),
        ('min_vol_slope20', 'vol_slope20', '>=', '20日量能斜率未达下限'),
        ('min_ma10_above_ma20_days', 'ma10_above_ma20_days', '>=', 'MA10站上MA20天数不足'),
        ('min_ma5_above_ma10_days', 'ma5_above_ma10_days', '>=', 'MA5站上MA10天数不足'),
        ('allow_upper_shadow_risk', 'has_upper_shadow_risk', 'risk_allowed', '存在未允许的上影线风险'),
        ('allow_blowoff_top', 'has_blowoff_top', 'risk_allowed', '存在未允许的高潮尖顶'),
    )
    checks = []
    for key, metric, operator, reason in definitions:
        actual, threshold = metrics[metric], normalized[key]
        if operator == 'risk_allowed':
            passed = threshold or not actual
        elif operator == '>=':
            passed = float(actual) >= float(threshold)
        else:
            passed = float(actual) <= float(threshold)
        checks.append({'parameter': key, 'metric': metric, 'operator': operator,
                       'actual': actual, 'threshold': threshold, 'passed': bool(passed),
                       'reason': None if passed else reason})
    failed = [check for check in checks if not check['passed']]
    return {'passed': not failed, 'status': 'rejected' if failed else 'passed',
            'metrics': dict(metrics), 'checks': checks,
            'failed_conditions': [check['parameter'] for check in failed],
            'reasons': [check['reason'] for check in failed], 'params': normalized,
            'calculation_version': CALCULATION_VERSION, 'source_path': SOURCE_PATH}


def evaluate_wulong_universe(bars: list[dict], params: Mapping[str, Any] | None = None) -> dict:
    """Evaluate a single frozen candidate; no cross-stock ranking is implied."""
    normalized = normalize_universe_params(params)
    metrics = candidate_metrics_from_bars(bars)
    if metrics is None:
        result = {'passed': False, 'status': 'insufficient_data', 'metrics': None,
                  'checks': [], 'failed_conditions': ['minimum_bars'],
                  'reasons': [f'候选指标至少需要 {MINIMUM_BARS} 根已可得日线'],
                  'params': normalized, 'calculation_version': CALCULATION_VERSION,
                  'source_path': SOURCE_PATH}
    else:
        result = evaluate_wulong_candidate(metrics, normalized)
    return {**result, 'required_bars': MINIMUM_BARS, 'observed_bars': len(bars)}
