"""Frozen anchored temporal folds and train-only candidate selection."""
from __future__ import annotations

from trade_app.platform.types import TradeError
from trade_app.research.plateau_domain import digest, robustness

PROTOCOL_VERSION = 'anchored-train-select-then-test-warmup-v1'
SELECTION_RULE = 'training-two-pass-score-min-trades-v1'


def build_folds(bars: list[dict], *, initial_train_bars: int = 120, test_bars: int = 40,
                gap_bars: int = 1, warmup_bars: int = 0, max_folds: int = 4,
                min_train_trades: int = 3, candidate_count: int) -> dict:
    ranges = {'initial_train_bars': (initial_train_bars, 32, 1800), 'test_bars': (test_bars, 10, 1000),
              'gap_bars': (gap_bars, 0, 60), 'warmup_bars': (warmup_bars, 0, 500),
              'max_folds': (max_folds, 1, 12), 'min_train_trades': (min_train_trades, 0, 1000)}
    for key, (value, lower, upper) in ranges.items():
        if type(value) is not int or not lower <= value <= upper:
            raise TradeError('INVALID_WALK_FORWARD_PLAN', f'{key} 必须在 {lower} 至 {upper} 之间')
    train_end = warmup_bars + initial_train_bars - 1
    folds = []
    while len(folds) < max_folds:
        test_start, test_end = train_end + gap_bars + 1, train_end + gap_bars + test_bars
        if test_end >= len(bars):
            break
        folds.append({'index': len(folds), 'train_start_index': warmup_bars, 'train_end_index': train_end,
                      'test_start_index': test_start, 'test_end_index': test_end,
                      'train_start': bars[warmup_bars]['event_date'], 'train_end': bars[train_end]['event_date'],
                      'test_start': bars[test_start]['event_date'], 'test_end': bars[test_end]['event_date']})
        train_end = test_end
    if not folds:
        raise TradeError('INSUFFICIENT_WALK_FORWARD_DATA', '冻结样本不足以构成一个完整训练段、间隔和测试段')
    if len(folds) * (candidate_count + 1) > 400:
        raise TradeError('WALK_FORWARD_POINT_LIMIT', '训练候选与测试点合计最多 400 点，请减少候选或折数')
    return {'protocol_version': PROTOCOL_VERSION, 'selection_rule': SELECTION_RULE,
            **{key: value for key, (value, _, _) in ranges.items()}, 'folds': folds,
            'actual_folds': len(folds), 'evaluation_points': len(folds) * (candidate_count + 1),
            'unused_tail_bars': len(bars) - 1 - folds[-1]['test_end_index'],
            'notes': ['扩展训练：后续折可使用此前已过去的测试行情；本折测试结果不参与本折选参',
                      '完整测试段才建立折；不足一段或超过折数上限的尾部行情不参与汇总',
                      '候选范围在任务创建时冻结；人工设计候选时是否已看过历史后段，系统无法证明']}


def select_candidate(training_points: list[dict], axes: dict, min_train_trades: int) -> dict:
    """This function accepts only the current fold's training point summaries."""
    eligible = [point for point in training_points if point.get('metrics') is not None
                and point['metrics']['trade_count'] >= min_train_trades]
    scored = robustness(eligible, axes)
    selected = scored['points'][0] if scored['points'] else None
    evidence = {'selection_rule': SELECTION_RULE, 'min_train_trades': min_train_trades,
                'training_evidence_sha256': digest(sorted(training_points, key=lambda point: point['point_sha256'])),
                'selected_candidate_sha256': selected['point_sha256'] if selected else None,
                'selected_train_task_id': selected['id'] if selected else None,
                'ranking': [{'candidate_sha256': point['point_sha256'], 'train_task_id': point['id'],
                             'point_score': point['point_score'], 'plateau_score': point['plateau_score'],
                             'metrics': point['metrics']} for point in scored['points']],
                'rejected': [{'candidate_sha256': point['point_sha256'],
                              'reason': point.get('error') or '训练交易次数低于门槛'}
                             for point in training_points if point not in eligible],
                'reason': None if selected else 'NO_ELIGIBLE_TRAINING_CANDIDATE'}
    return evidence


def summarize_folds(folds: list[dict]) -> dict:
    """Stitch normalized OOS curves; folds reset cash and never carry positions."""
    scale, peak, max_drawdown = 1.0, 1.0, 0.0
    curve, successful, output = [], [], []
    for fold in sorted(folds, key=lambda item: item['index']):
        result = fold.get('test_result')
        row = {key: value for key, value in fold.items() if key != 'test_result'}
        if result is not None:
            initial = float(result['initial_capital'])
            for point in result['equity']:
                value = scale * float(point['total_assets']) / initial
                peak = max(peak, value)
                max_drawdown = max(max_drawdown, (peak - value) / peak)
                curve.append({'date': point['date'], 'normalized_assets': value, 'fold_index': fold['index']})
            scale *= float(result['ending_assets']) / initial
            successful.append(result)
        output.append(row)
    return {'protocol_version': PROTOCOL_VERSION, 'folds': output, 'completed_test_folds': len(successful),
            'planned_folds': len(folds), 'oos_normalized_curve': curve,
            'oos_compounded_return': scale - 1 if successful else None,
            'oos_max_drawdown': max_drawdown if successful else None,
            'oos_trade_count': sum(result['trade_count'] for result in successful),
            'positive_test_fold_rate': sum(float(result['total_return']) > 0 for result in successful) / len(successful) if successful else None,
            'quality_flags': sorted(set(flag for result in successful for flag in result.get('quality_flags', []))),
            'limitations': ['每折测试仅执行训练阶段选出的候选；测试数据不进入本折参数选择',
                            '每折测试重置初始资金与仓位，按收益比例拼接曲线；不等于连续持仓的组合回测',
                            '预热行情不产生交易或收益，首测试日仅使用开盘前已知信息',
                            '沿用单股末样本日开盘强平与收盘条件次开盘执行规则',
                            '这里只验证已冻结候选计划；人工选候选的事前独立性无法追溯证明']}
