"""Evaluate final-trade's MatrixSignalPlugin against one frozen candidate pool.

The plugin uses screener metric proxies. It is not the vectorized matrix engine:
S1 uses retrace20 rather than ATR, S2 uses vol_slope20 rather than volume MA ratios,
and S3 differs between pool admission (return Top N) and ranking (positive return).
Preserve those historical formulas while making every approximation inspectable.
The caller enforces provenance, a shared as-of date, and a 40-session return window.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from trade_app.platform.types import TradeError, decimal_value
from trade_app.market.symbols import normalize_a_share_symbol


MATRIX_ID = 'matrix_signal_v1'
MATRIX_VERSION = 'legacy-matrix-plugin-frozen-pool-v1'
MATRIX_PARAM_KEYS = (
    'atr_ratio_max', 'vol_ratio_max', 'ret40_top_n', 'sideways_range_max',
    'near_ma20_max', 'min_pool_score', 'breakout_vol_ratio', 'max_pullback_days',
)
METRIC_KEYS = (
    'ret40', 'retrace20', 'vol_slope20', 'price_vs_ma20',
    'up_down_volume_ratio', 'pullback_days', 'ma10_above_ma20_days',
)
COMPONENT_RULES = {
    's1': 'retrace20 < atr_ratio_max（回撤代理，未计算 ATR）',
    's2': 'vol_slope20 < vol_ratio_max（量能斜率代理，未计算量均比）',
    's3': '在本次有效候选池内按 ret40 降序排入 ret40_top_n；同值保留输入顺序',
    's3_rank': '评分沿用旧插件 ret40 > 0，与入池 S3 独立',
    's4': 'retrace20 < sideways_range_max 且 price_vs_ma20 < near_ma20_max（保留有符号偏离值）',
    's5': 'ret40 > 0 且 up_down_volume_ratio >= breakout_vol_ratio（突破代理）',
    's6': 'abs(price_vs_ma20) < 0.06 且 pullback_days <= max_pullback_days（回踩代理）',
    's7': 'ma10_above_ma20_days >= 5（均线排列代理）',
}


@lru_cache(maxsize=1)
def _catalog_schema() -> dict:
    catalog = json.loads(Path(__file__).with_name('legacy_catalog.json').read_text(encoding='utf-8'))
    strategy = next(row for row in catalog['strategies'] if row['strategy_id'] == MATRIX_ID)
    return {key: strategy['params_schema'][key] for key in MATRIX_PARAM_KEYS}


def matrix_param_schema() -> dict:
    """Only expose parameters actually used by the pool plugin formulas."""
    return deepcopy(_catalog_schema())


def normalize_matrix_params(raw: dict[str, Any]) -> dict[str, str]:
    """Validate the historical catalog bounds, rejecting unused event/score knobs."""
    if set(raw) - set(MATRIX_PARAM_KEYS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '矩阵候选池包含未实现或未知参数')
    normalized = {}
    for key, spec in _catalog_schema().items():
        value = raw.get(key, spec['default'])
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
        normalized[key] = str(int(number)) if spec['type'] == 'integer' else format(number.normalize(), 'f')
    return normalized


def _validated_metrics(row: dict) -> dict[str, float | int]:
    values = {}
    for key in METRIC_KEYS:
        raw = row.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TradeError('INVALID_MATRIX_CANDIDATE', f'候选指标 {key} 缺失或无效') from exc
        if isinstance(raw, bool) or not math.isfinite(value):
            raise TradeError('INVALID_MATRIX_CANDIDATE', f'候选指标 {key} 须为有限数值')
        if key in ('pullback_days', 'ma10_above_ma20_days'):
            if value < 0 or not value.is_integer():
                raise TradeError('INVALID_MATRIX_CANDIDATE', f'候选指标 {key} 须为非负整数')
            values[key] = int(value)
        else:
            values[key] = value
    return values


def evaluate_matrix_pool(candidates: list[dict], params: dict[str, Any] | None = None) -> dict:
    """Return admission, triggers, proxy scores and stable signal ranks for all rows.

Rows remain in input order. ``ranking`` contains only admitted, triggered dataset
IDs ordered by score, with stable input-order ties. Invalid metrics and duplicate
equities fail before Top N is calculated so they cannot distort other rows' S3.
"""
    normalized = normalize_matrix_params(params or {})
    thresholds = {key: float(value) for key, value in normalized.items()}
    seen_datasets, seen_symbols = set(), set()
    metrics = []
    for row in candidates:
        dataset_id = row.get('dataset_id')
        symbol = row.get('symbol')
        if not isinstance(dataset_id, str) or not dataset_id:
            raise TradeError('INVALID_MATRIX_CANDIDATE', '候选必须包含冻结行情标识')
        try:
            canonical_symbol = normalize_a_share_symbol(symbol)[1]
        except TradeError as exc:
            raise TradeError('INVALID_MATRIX_CANDIDATE', '矩阵候选股票代码或交易所标记无效') from exc
        if dataset_id in seen_datasets:
            raise TradeError('DUPLICATE_DATASET', '同一冻结行情样本不能重复加入矩阵候选池')
        if canonical_symbol in seen_symbols:
            raise TradeError('DUPLICATE_SYMBOL', '同一证券只能选择一个冻结行情样本')
        seen_datasets.add(dataset_id)
        seen_symbols.add(canonical_symbol)
        metrics.append(_validated_metrics(row))

    by_return = sorted(range(len(candidates)), key=lambda index: metrics[index]['ret40'], reverse=True)
    return_ranks = {index: rank for rank, index in enumerate(by_return, start=1)}
    rows = []
    for index, (candidate, values) in enumerate(zip(candidates, metrics)):
        s1 = values['retrace20'] < thresholds['atr_ratio_max']
        s2 = values['vol_slope20'] < thresholds['vol_ratio_max']
        s3 = return_ranks[index] <= thresholds['ret40_top_n']
        s3_rank = values['ret40'] > 0
        s4 = values['retrace20'] < thresholds['sideways_range_max'] and values['price_vs_ma20'] < thresholds['near_ma20_max']
        s5 = values['ret40'] > 0 and values['up_down_volume_ratio'] >= thresholds['breakout_vol_ratio']
        s6 = abs(values['price_vs_ma20']) < 0.06 and values['pullback_days'] <= thresholds['max_pullback_days']
        s7 = values['ma10_above_ma20_days'] >= 5
        pool_score = sum((s1, s2, s3, s4))
        rank_pool_score = sum((s1, s2, s3_rank, s4))
        in_pool = pool_score >= thresholds['min_pool_score']
        signal = in_pool and (s5 or s6)
        raw_score = rank_pool_score + s5 * 2.0 + s6 * 1.5 + s7 * 1.0
        reasons = []
        if not in_pool:
            reasons.append('POOL_SCORE_BELOW_MIN')
        if not (s5 or s6):
            reasons.append('NO_ENTRY_TRIGGER')
        rows.append({
            'symbol': candidate['symbol'], 'dataset_id': candidate['dataset_id'],
            'as_of_date': candidate.get('as_of_date'), 'name': candidate.get('name', ''),
            'quality_flags': list(candidate.get('quality_flags', [])),
            'metrics': values, 'ret40_rank': return_ranks[index],
            'in_pool': in_pool, 'signal': signal, 'pool_score': pool_score,
            'rank_pool_score': rank_pool_score,
            'score': max(0.0, min(100.0, raw_score / 8.5 * 100.0)),
            'rank': None, 'reasons': reasons,
            'components': {'s1': s1, 's2': s2, 's3': s3, 's3_rank': s3_rank,
                           's4': s4, 's5': s5, 's6': s6, 's7': s7},
        })
    ranked = sorted((row for row in rows if row['signal']), key=lambda row: row['score'], reverse=True)
    for rank, row in enumerate(ranked, start=1):
        row['rank'] = rank
    return {
        'calculation_version': MATRIX_VERSION,
        'metric_label': 'legacy_matrix_plugin_proxy_not_vectorized_engine',
        'params': normalized, 'component_rules': dict(COMPONENT_RULES),
        'rows': rows, 'ranking': [row['dataset_id'] for row in ranked],
        'summary': {'input_count': len(rows), 'pool_count': sum(row['in_pool'] for row in rows),
                    'signal_count': len(ranked)},
    }
