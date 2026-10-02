"""Descriptive chart-score validation on actual complete causal position cycles."""
from decimal import Decimal, ROUND_HALF_UP
import math

from trade_app.platform.types import TradeError
from trade_app.research.portfolio_analysis_domain import complete_cycles

VERSION = 'causal-chart-score-buckets-v1'
EDGES = [(0, 62), (62, 70), (70, 75), (75, 82), (82, 101)]


def summarize(cycles):
    values = [Decimal(row['pnl_net']) for row in cycles]
    gains = sum((value for value in values if value > 0), Decimal(0))
    losses = -sum((value for value in values if value < 0), Decimal(0))
    return {'n': len(cycles), 'win_rate': sum(value > 0 for value in values)/len(values) if values else None,
        'avg_pnl': str(sum(values)/len(values)) if values else None,
        'pf': str(gains/losses) if losses else None, 'pf_status': 'computed' if losses else 'no_losses' if values else 'no_closed_cycles',
        'tp_rate': sum(row['hit_tp'] for row in cycles)/len(cycles) if cycles else None,
        'hit_30pct': sum(value >= Decimal('.28') for value in values),
        'hit_25pct': sum(value >= Decimal('.25') for value in values),
        'avg_hold': sum(row['holding_days'] for row in cycles)/len(cycles) if cycles else None}


def monotonic_check(rows, key):
    selected, excluded = [], []
    for row in rows:
        reason = 'fewer_than_20_cycles' if row['n'] < 20 else 'metric_undefined' if row[key] is None else None
        if reason: excluded.append({'score_range': row['score_range'], 'reason': reason})
        else: selected.append(row)
    valid = len(selected) >= 2
    return {'status': 'evaluated' if valid else 'insufficient_comparable_buckets',
        'nondecreasing': all(Decimal(str(a[key])) <= Decimal(str(b[key])) for a,b in zip(selected,selected[1:])) if valid else None,
        'minimum_cycles_per_bucket': 20, 'compared_buckets': [row['score_range'] for row in selected], 'excluded_buckets': excluded}


def complete_evidence(context, records):
    trades = [row for record in records for row in record['result']['trades']]
    completed, incomplete, _ = complete_cycles(trades)
    dates = {row['symbol']: [bar['event_date'] for bar in row['bars']] for row in context['datasets']}
    rows = []
    for cycle in completed:
        entry = trades[cycle['entry_trade_index']]
        # Match the shared cash ledger's cent-rounded gross, including all fees.
        cost = (Decimal(entry['price']) * entry['quantity']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) + Decimal(entry['fees'])
        exits = [trades[index] for index in cycle['exit_trade_indices']]
        rows.append({**cycle, 'entry_cost': str(cost), 'pnl_net': str(Decimal(cycle['net_pnl'])/cost),
                     'position_return': str(Decimal(cycle['net_pnl'])/cost),
                     'holding_days': dates[cycle['symbol']].index(cycle['exit_date'])-dates[cycle['symbol']].index(cycle['entry_date']),
                     'hit_tp': any(row['reason'] in ('DAILY_TAKE_PROFIT','OPEN_GAP_TAKE_PROFIT') for row in exits),
                     'exit_reasons': [row['reason'] for row in exits]})
    return trades, rows, incomplete


def build_trade_summary(context, records, *, window=20, target=.28):
    _, cycles, incomplete = complete_evidence(context, records)
    threshold = Decimal(str(target))
    for cycle in cycles:
        cycle['target_reached'] = Decimal(cycle['pnl_net']) >= threshold
        cycle['fast_target_reached'] = cycle['target_reached'] and cycle['holding_days'] <= window
    return {'version': VERSION, 'scope': 'complete_position_cycles', 'target_return': target,
            'fast_window_observed_bars': window, 'summary': summarize(cycles),
            'target_reached_count': sum(row['target_reached'] for row in cycles),
            'fast_target_reached_count': sum(row['fast_target_reached'] for row in cycles),
            'completed_cycles': cycles, 'open_cycles': incomplete,
            'notes': ['计数按完整清仓周期，包含全部部分卖出与费用；开放仓位不作为目标成功或失败样本',
                      'target默认为0.28，继承旧fast_30实际28%门槛；快目标天数为冻结标的日线日期间隔，不是自然日',
                      '完整周期回报是独立描述，不将重叠证券收益复利相乘；实际资金收益另见组合summary']}


def build_score_validation(context, records):
    if context['strategy_id'] != 'chart_volume_swing_v1':
        raise TradeError('LAB_CHART_REQUIRED', '图形分桶验证只适用于chart_volume_swing_v1，不混合不同策略评分')
    trades, completed, incomplete = complete_evidence(context, records)
    annotated, missing = [], []
    for cycle in completed:
        entry = trades[cycle['entry_trade_index']]; metrics = entry.get('reason_metrics', {})
        components = metrics.get('components', {}); evaluation = components.get('evaluation', {})
        indicator = components.get('indicator', {})
        score = evaluation.get('signal_score', metrics.get('score'))
        if type(score) not in (int,float) or not math.isfinite(score) or not 0 <= score <= 100:
            missing.append({**cycle, 'reason': 'entry_score_missing_or_outside_chart_range'}); continue
        annotated.append({**cycle, 'signal_date': entry.get('signal_date'), 'signal_score': score,
            'event_grade': evaluation.get('event_grade'), 'confirm_type': indicator.get('confirm_type'),
            'score_breakdown': evaluation.get('score_breakdown', {})})
    buckets = [{'score_range':f'[{low},{high})', **summarize([row for row in annotated if low <= row['signal_score'] < high])} for low, high in EDGES]
    grades = {grade:summarize([row for row in annotated if row['event_grade']==grade]) for grade in ('C','B','A')}
    return {'version': VERSION, 'scope': 'in_sample_complete_causal_cycles', 'summary': summarize(annotated),
        'score_buckets': buckets, 'grade_buckets': grades,
        'validation': {key+'_monotonic': monotonic_check(buckets,key) for key in ('win_rate','pf','avg_pnl')},
        'completed_cycles': annotated, 'open_cycles': incomplete, 'missing_score_cycles': missing,
        'unknown_grade_count': sum(row['event_grade'] not in grades for row in annotated),
        'top30_by_score': sorted(annotated,key=lambda row:(-row['signal_score'],row['entry_trade_index']))[:30],
        'trades_hit_30pct': [row for row in annotated if Decimal(row['pnl_net']) >= Decimal('.28')],
        'notes': ['分桶边界与原validate_chart_volume_scoring一致；收益来自新共享资金引擎的真实完整持仓周期，不用未来15日最高分去重',
            '部分卖出归并到完整清仓；开放周期和缺少入场评分的周期单列，不伪造期末卖出或补评分',
            'PF以完整周期净收益比例的正负和计算，非资金账户收益；无亏损时为null，不能用旧99替代无穷',
            '单调检查每桶至少20个完整周期且至少2个可比较桶；不足为未知，不返回通过；这是样本内描述']}
