"""Deterministic single-symbol sensitivity sampling and descriptive stability.

This is a new, versioned analysis over the rebuilt daily next-open engine. It
does not reproduce the legacy multi-stock portfolio or intraday model. Scores
are calculated in two passes; every neighbour sees the final point scores.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN

from trade_app.platform.types import TradeError

SAMPLING_VERSION = 'explicit-grid-stratified-lhs-v1'
SCORING_VERSION = 'single-symbol-two-pass-neighbors-v1'
MAX_POINTS = 400
MAX_AXES = 8


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value) -> Decimal:
    if type(value) not in (str, int, float) or len(str(value)) > 64:
        raise TradeError('INVALID_PLATEAU_AXIS', '采样参数必须是有限数值')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal('1e12') or result.as_tuple().exponent < -12:
            raise ValueError()
        return result
    except Exception as exc:
        raise TradeError('INVALID_PLATEAU_AXIS', '采样数值超出支持精度或范围') from exc


def text_number(value: Decimal) -> str:
    return '0' if not value else format(value.normalize(), 'f')


def sample_axes(mode: str, axes: dict, schema: dict, *, seed: int | None,
                sample_points: int = 24, max_points: int = 120) -> dict:
    if mode not in ('grid', 'lhs') or not isinstance(axes, dict) or not 1 <= len(axes) <= MAX_AXES:
        raise TradeError('INVALID_PLATEAU_AXIS', f'请选择 grid / lhs 与 1 至 {MAX_AXES} 个有效维度')
    if type(max_points) is not int or not 1 <= max_points <= MAX_POINTS:
        raise TradeError('PLATEAU_POINT_LIMIT', f'单次实验最多 {MAX_POINTS} 个参数点')
    if type(sample_points) is not int or not 1 <= sample_points <= MAX_POINTS or mode == 'lhs' and sample_points > max_points:
        raise TradeError('PLATEAU_POINT_LIMIT', '采样点数必须在 1 与本次点数上限之间')
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
        raise TradeError('INVALID_PLATEAU_SEED', '随机种子须为 0 至 2147483647 的整数')
    seed = seed if seed is not None else random.SystemRandom().randrange(2147483648) if mode == 'lhs' else 0
    normalized = {}
    for key in sorted(axes):
        spec = schema.get(key)
        axis = axes[key]
        if not spec or spec.get('readonly') or not isinstance(axis, dict):
            raise TradeError('UNSUPPORTED_PLATEAU_AXIS', f'{key} 不是当前执行路径可调整的参数')
        numeric = spec.get('type') in ('number', 'integer')

        def value(raw):
            if not numeric:
                if spec.get('type') == 'boolean' and (type(raw) is bool or raw in ('true', 'false')):
                    return str(raw).lower()
                if str(raw) in [str(item) for item in spec.get('enum', [])]:
                    return str(raw)
                raise TradeError('INVALID_PLATEAU_AXIS', f'{key} 的离散取值不合法')
            result = number(raw)
            if spec['type'] == 'integer' and result != int(result):
                raise TradeError('INVALID_PLATEAU_AXIS', f'{key} 只接受整数')
            if 'minimum' in spec and result < number(spec['minimum']) or 'maximum' in spec and result > number(spec['maximum']):
                raise TradeError('INVALID_PLATEAU_AXIS', f'{key} 超出可执行范围')
            if spec.get('exclusiveMinimum') is not None and result <= number(spec['exclusiveMinimum']):
                raise TradeError('INVALID_PLATEAU_AXIS', f'{key} 必须大于 {spec["exclusiveMinimum"]}')
            return text_number(result)

        if mode == 'grid':
            if set(axis) != {'values'} or not isinstance(axis['values'], list) or not 1 <= len(axis['values']) <= 32:
                raise TradeError('INVALID_PLATEAU_AXIS', 'grid 每个维度须有 1 至 32 个 values')
            values = list(dict.fromkeys(value(item) for item in axis['values']))
            normalized[key] = {'values': sorted(values, key=Decimal) if numeric else sorted(values)}
        else:
            if not numeric or set(axis) - {'min', 'max', 'precision'} or not {'min', 'max'} <= set(axis):
                raise TradeError('INVALID_PLATEAU_AXIS', 'LHS 仅接受数值维度的 min / max / precision')
            low, high = value(axis['min']), value(axis['max'])
            precision = axis.get('precision', 0 if spec['type'] == 'integer' else 4)
            if type(precision) is not int or not 0 <= precision <= 6 or spec['type'] == 'integer' and precision != 0:
                raise TradeError('INVALID_PLATEAU_AXIS', '数值精度须为 0 至 6，整数维度精度为 0')
            if Decimal(low) > Decimal(high):
                raise TradeError('INVALID_PLATEAU_AXIS', 'LHS 下限不能大于上限')
            unit = Decimal(1).scaleb(-precision)
            if any(Decimal(item).quantize(unit) != Decimal(item) for item in (low, high)):
                raise TradeError('INVALID_PLATEAU_AXIS', '边界必须与采样精度对齐')
            normalized[key] = {'min': low, 'max': high, 'precision': precision}
    keys = list(normalized)
    if mode == 'grid':
        total = math.prod(len(item['values']) for item in normalized.values())
        if total > max_points:
            raise TradeError('PLATEAU_GRID_TOO_LARGE', f'完整网格有 {total} 点，超过本次上限 {max_points}；请缩小维度或改用 LHS')
        samples = [dict(zip(keys, values)) for values in itertools.product(*(normalized[key]['values'] for key in keys))]
        requested = total
    else:
        rng = random.Random(seed)
        columns = []
        for key in keys:
            bins = list(range(sample_points))
            rng.shuffle(bins)
            axis = normalized[key]
            low, high = Decimal(axis['min']), Decimal(axis['max'])
            unit = Decimal(1).scaleb(-axis['precision'])
            columns.append([text_number((low + (Decimal(bucket) + Decimal(str(rng.random()))) /
                                       sample_points * (high - low)).quantize(unit, rounding=ROUND_HALF_EVEN))
                            for bucket in bins])
        samples = [dict(zip(keys, values)) for values in zip(*columns)]
        samples = list({canonical(item): item for item in samples}.values())
        requested = sample_points
    return {'sampling_version': SAMPLING_VERSION, 'sampling_mode': mode, 'axes': normalized,
            'seed': seed, 'requested_points': requested, 'actual_points': len(samples), 'max_points': max_points,
            'deduplicated_points': requested - len(samples), 'samples': samples,
            'notes': ['LHS 连续分层后按精度舍入；重复点去重，不用额外随机点填充'] if mode == 'lhs' else []}


def point_metrics(result: dict) -> dict:
    """Net round trips and equity-only statistics; no portfolio fill-rate claim."""
    profits, ratios = [], []
    entry_cost = None
    buys = 0
    for trade in result['trades']:
        if trade['side'] == 'buy':
            buys += 1
            entry_cost = Decimal(trade['price']) * int(trade['quantity']) + Decimal(trade['fees'])
        elif trade['side'] == 'sell':
            pnl = Decimal(trade['realized_pnl'])
            profits.append(float(pnl))
            if entry_cost and entry_cost > 0:
                ratios.append(float(pnl / entry_cost))
            entry_cost = None
    gain = sum(max(0, item) for item in profits)
    loss = -sum(min(0, item) for item in profits)
    curve = result['equity']
    days = max(1, (date.fromisoformat(curve[-1]['date']) - date.fromisoformat(curve[0]['date'])).days) if curve else 1
    monthly = {}
    previous = float(result['initial_capital'])
    for item in curve:
        monthly[item['date'][:7]] = float(item['total_assets'])
    returns = []
    for last in monthly.values():
        returns.append(last / previous - 1 if previous else 0)
        previous = last
    return {'total_return': float(result['total_return']), 'max_drawdown': abs(float(result['max_drawdown'])),
            'trade_count': len(profits), 'win_rate': float(result['win_rate']) if result.get('win_rate') is not None else None,
            'profit_factor': gain / loss if loss else None, 'profit_factor_unbounded': bool(gain and not loss),
            'avg_net_trade_return': sum(ratios) / len(ratios) if ratios else None,
            'annual_trades': len(profits) / max(days / 365.25, 1 / 365.25),
            'eligible_signals': int(result['signal_count']), 'executed_entries': buys,
            'entry_fill_rate': buys / int(result['signal_count']) if result['signal_count'] else 0,
            'monthly_return_std': math.sqrt(sum((item - sum(returns) / len(returns)) ** 2 for item in returns) / len(returns)) if returns else 0,
            'quality_flags': result.get('quality_flags', [])}


def percentile(values: list[float]) -> list[float]:
    if len(values) <= 1:
        return [50.0] * len(values)
    return [100 * (sum(other < value for other in values) + (sum(other == value for other in values) - 1) / 2)
            / (len(values) - 1) for value in values]


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    offset = (len(values) - 1) * fraction
    low = int(offset)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (offset - low)


def robustness(points: list[dict], axes: dict) -> dict:
    valid = sorted([dict(point) for point in points if point.get('metrics') is not None], key=lambda row: row['point_sha256'])
    if not valid:
        return {'scoring_version': SCORING_VERSION, 'points': [], 'regions': [], 'correlations': [],
                'recommended_point_id': None, 'peak_point_id': None, 'notes': ['没有成功的参数点']}
    matrix = [[], [], [], [], []]
    for row in valid:
        metrics = row['metrics']
        pf = 5 if metrics['profit_factor_unbounded'] else min(5, metrics['profit_factor'] or 0)
        failures = []
        for failed, reason in ((metrics['total_return'] <= 0, '收益不为正'), (pf < 1.05, '盈亏因子低于1.05'),
                               (metrics['max_drawdown'] > .35, '回撤高于35%'), (metrics['entry_fill_rate'] < .1, '入场成交率低于10%'),
                               (metrics['annual_trades'] < 12, '年化交易次数少于12'),
                               ((metrics['avg_net_trade_return'] or 0) < .002, '平均单笔净收益低于0.2%')):
            if failed:
                failures.append(reason)
        row.update(hard_filter_failures=failures, passes_hard_filters=not failures)
        values = [metrics['total_return'] / max(.02, metrics['max_drawdown']), metrics['total_return'],
                  pf, metrics['annual_trades'], metrics['entry_fill_rate']]
        for column, value in zip(matrix, values):
            column.append(value)
    percentiles = [percentile(column) for column in matrix]
    # First pass is complete for all rows before any neighbour score is read.
    for index, row in enumerate(valid):
        row['point_score'] = round(sum(weight * values[index] for weight, values in
                                       zip((.30, .25, .15, .15, .15), percentiles)), 6)
    keys = sorted(axes)
    vectors = []
    for row in valid:
        vector = []
        for key in keys:
            axis = axes[key]
            try:
                values = [float(item['axis_values'][key]) for item in valid]
                span = max(values) - min(values)
                vector.append((float(row['axis_values'][key]) - min(values)) / span if span else 0)
            except ValueError:
                categories = sorted(axis['values'])
                vector.append(categories.index(row['axis_values'][key]) / max(1, len(categories) - 1))
        vectors.append(vector)
    neighbor_map, sensitivity = {}, []
    for index, row in enumerate(valid):
        neighbors = sorted([(math.dist(vectors[index], vector), other) for other, vector in enumerate(vectors)
                            if other != index], key=lambda item: (item[0], valid[item[1]]['point_sha256']))[:12]
        neighbor_map[row['id']] = [valid[other]['id'] for _, other in neighbors]
        scores = [valid[other]['point_score'] for _, other in neighbors]
        row.update(neighbor_count=len(neighbors), neighbor_ids=neighbor_map[row['id']],
                   neighbor_pass_rate=sum(valid[other]['passes_hard_filters'] for _, other in neighbors) / len(neighbors) if neighbors else None,
                   neighbor_median_score=quantile(scores, .5), neighbor_p25_score=quantile(scores, .25),
                   evidence_sufficient=len(neighbors) >= 3)
        sensitivity.append(sum(abs(valid[other]['point_score'] - row['point_score']) / max(.05, distance)
                               for distance, other in neighbors) / len(neighbors) if neighbors else 0)
    sensitivity_ranks = percentile(sensitivity)
    for index, row in enumerate(valid):
        row['sensitivity_penalty'] = round(sensitivity_ranks[index], 6) if row['evidence_sufficient'] else None
        row['local_score'] = round(.35 * row['neighbor_pass_rate'] * 100 + .30 * row['neighbor_median_score'] +
                                   .20 * row['neighbor_p25_score'] + .15 * (100 - sensitivity_ranks[index]), 6) if row['evidence_sufficient'] else None
        row['plateau_score'] = round((.35 * row['point_score'] + .65 * row['local_score']) *
                                     (1 if row['passes_hard_filters'] else .45), 6) if row['evidence_sufficient'] else None
    candidates = {row['id']: row for row in valid if row['passes_hard_filters'] and (row['local_score'] or 0) >= 55}
    adjacent = {key: set() for key in candidates}
    for key in candidates:
        for other in neighbor_map[key][:4]:
            if other in adjacent:
                adjacent[key].add(other)
                adjacent[other].add(key)
    regions, seen = [], set()
    for key in sorted(candidates):
        if key in seen:
            continue
        pending, members = [key], []
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            members.append(candidates[current])
            pending.extend(sorted(adjacent[current] - seen))
        vector_by_id = {row['id']: vectors[index] for index, row in enumerate(valid)}
        mean_distances = [sum(math.dist(vector_by_id[row['id']], vector_by_id[other['id']]) for other in members)
                          / max(1, len(members) - 1) for row in members]
        centrality = {row['id']: 100 - score for row, score in zip(members, percentile(mean_distances))}
        center = max(members, key=lambda row: (.7 * row['plateau_score'] + .3 * centrality[row['id']], row['point_sha256']))
        regions.append({'id': digest(sorted(row['id'] for row in members))[:16], 'point_ids': sorted(row['id'] for row in members),
                        'point_count': len(members), 'center_point_id': center['id'], 'center_margin_score': centrality[center['id']],
                        'median_plateau_score': quantile([row['plateau_score'] for row in members], .5),
                        'parameter_ranges': {axis: sorted(set(row['axis_values'][axis] for row in members)) for axis in keys}})
    regions.sort(key=lambda region: (-region['median_plateau_score'], -region['point_count'], region['id']))
    correlations = []
    for key in keys:
        try:
            xs = [float(row['axis_values'][key]) for row in valid]
        except ValueError:
            continue
        ys = [row['metrics']['total_return'] for row in valid]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        denom = math.sqrt(sum((value - mx) ** 2 for value in xs) * sum((value - my) ** 2 for value in ys))
        correlations.append({'parameter': key, 'total_return_pearson': sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else None,
                             'sample_count': len(xs)})
    valid.sort(key=lambda row: (-(row['plateau_score'] if row['plateau_score'] is not None else row['point_score']), row['point_sha256']))
    for rank, row in enumerate(valid, 1):
        row['rank'] = rank
    peak = max(valid, key=lambda row: (row['metrics']['total_return'], row['point_sha256']))
    return {'scoring_version': SCORING_VERSION, 'points': valid, 'regions': regions, 'correlations': correlations,
            'recommended_point_id': regions[0]['center_point_id'] if regions else None, 'peak_point_id': peak['id'],
            'notes': ['样本内相对百分位评分；未做样本外验证或 walk-forward',
                      '邻域采用标准化参数距离，等权、最多12邻居；至少3邻居才计算局部稳定性',
                      '入场成交率为实际买入次数 / 可买信号次数，仅单股；不等同旧组合撮合率',
                      '评分先计算全部点，再计算邻域，修正旧版结果依赖遍历顺序的问题']}
