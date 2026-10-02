"""Temporal portfolio validation with isolated train and test input prefixes."""
from copy import deepcopy

from trade_app.platform.types import TradeError
from trade_app.research.portfolio_domain import digest
from trade_app.research.portfolio_experiment_domain import candidate_context, score_points
from trade_app.research.walk_forward_domain import build_folds

VERSION = 'portfolio-anchored-train-only-next-open-v1'


def build_plan(source, sampling, body):
    plan = build_folds([{'event_date': value} for value in source['calendar']],
        initial_train_bars=body.get('initial_train_bars', 60), test_bars=body.get('test_bars', 20),
        gap_bars=body.get('gap_bars', 1), warmup_bars=0, max_folds=body.get('max_folds', 4),
        min_train_trades=body.get('min_train_cycles', 1), candidate_count=sampling['actual_points'])
    for item in source['datasets']:
        if len([bar for bar in item['bars'] if bar['event_date'] <= plan['folds'][0]['train_end']]) < 32:
            raise TradeError('PORTFOLIO_WF_TRAINING_HISTORY', '每个固定样本在首折训练末日须有至少32根真实日线')
    plan.update(protocol_version=VERSION, selection_rule=VERSION, min_train_cycles=plan.pop('min_train_trades'))
    plan['notes'] += ['训练候选只接收训练末日及以前的日线；测试只接收测试末日及以前的日线',
        '预热前缀只用于信号，交易与权益从该阶段第一日开始；不存在把全区间最优参数回填为样本外']
    return plan


def temporal_context(source, fold, phase, axis_values):
    if phase not in ('train', 'test'):
        raise TradeError('INVALID_PORTFOLIO_WF_PHASE', '仅支持训练或测试阶段')
    context = candidate_context(source, axis_values)
    start, end = fold[phase + '_start'], fold[phase + '_end']
    datasets = []
    for item in context['datasets']:
        bars = [bar for bar in item['bars'] if bar['event_date'] <= end]
        datasets.append({**item, 'bars': bars, 'bars_sha256': digest(bars)})
    context['datasets'] = datasets
    context['all_calendar'] = sorted({bar['event_date'] for item in datasets for bar in item['bars']})
    context['calendar'] = [day for day in source['calendar'] if start <= day <= end]
    return context


def select_training(points, axes, min_cycles):
    eligible = [point for point in points if point.get('metrics') and point['metrics']['trade_count'] >= min_cycles]
    scored = score_points(eligible, axes)
    selected = scored['points'][0] if scored['points'] else None
    return {'selection_version': VERSION, 'min_train_cycles': min_cycles,
        'training_evidence_sha256': digest(sorted(points, key=lambda point: point['point_sha256'])),
        'selected_candidate_sha256': selected['point_sha256'] if selected else None,
        'selected_task_id': selected['id'] if selected else None,
        'axis_values': selected['axis_values'] if selected else None,
        'reason': None if selected else 'NO_ELIGIBLE_TRAINING_CANDIDATE',
        'ranking': [{'candidate_sha256': point['point_sha256'], 'task_id': point['id'],
                     'point_score': point['point_score'], 'plateau_score': point['plateau_score'], 'metrics': point['metrics']}
                    for point in scored['points']],
        'rejected': [{'task_id': point['id'], 'reason': 'TRAIN_CYCLES_BELOW_MINIMUM'} for point in points if point not in eligible]}


def summarize(folds):
    scale = peak = 1.0
    drawdown, curve, successful, result = 0.0, [], [], []
    for fold in sorted(folds, key=lambda item: item['index']):
        test = fold.get('test_result')
        result.append({key: value for key, value in fold.items() if key != 'test_result'})
        if test is None:
            continue
        initial = float(test['initial_capital'])
        for point in test['equity']:
            value = scale * float(point['total_assets']) / initial
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
            curve.append({'date': point['date'], 'normalized_assets': value, 'fold_index': fold['index']})
        scale *= float(test['ending_assets']) / initial
        successful.append(test)
    return {'protocol_version': VERSION, 'folds': result, 'completed_test_folds': len(successful), 'planned_folds': len(folds),
        'oos_normalized_curve': curve, 'oos_compounded_return': scale - 1 if successful else None,
        'oos_max_drawdown': drawdown if successful else None, 'oos_complete_cycles': sum(item['trade_count'] for item in successful),
        'positive_test_fold_rate': sum(float(item['total_return']) > 0 for item in successful) / len(successful) if successful else None,
        'quality_flags': sorted(set(flag for item in successful for flag in item['quality_flags'])),
        'limitations': ['固定研究样本，历史市场成分与幸存者偏差未经核验',
            '各折测试重置现金和仓位；按各折收益比例拼接，不能解释为连续持仓的组合实盘',
            '末日不虚构卖出，开放持仓计入该折末日权益，跨折不转移',
            '仅本折训练完整周期数达到门槛的候选参与本折选择；风险/收益阈值用于样本内相对评分',
            '候选范围在创建时冻结，但系统无法证明人工设计候选时未看过历史测试段',
            '沿用真实组合引擎的T+1、次开盘和日线顺序未知假设；未实现分钟成交']}
