"""Deterministic portfolio sampling and completed-position-cycle metrics.

Execution remains in portfolio_domain. These scores describe the same fixed
sample; they are not untouched out-of-sample estimates or minute executions.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math

from trade_app.platform.types import TradeError
from trade_app.research.plateau_domain import robustness, sample_axes
from trade_app.research.plateau_service import supported_schema as single_schema
from trade_app.research.portfolio_event_domain import PARAM_SCHEMA, normalize_event_params
from trade_app.research.portfolio_service import normalize_config
from trade_app.research.service import normalize_strategy_params, strategy_catalog

VERSION = 'portfolio-grid-lhs-complete-cycles-v2'
CONFIG_SCHEMA = {
    'position_pct': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1, 'title': '单股资金比例'},
    'max_positions': {'type': 'integer', 'minimum': 1, 'maximum': 64, 'title': '最大持仓数'},
    'max_holding_bars': {'type': 'integer', 'minimum': 1, 'maximum': 240, 'title': '最长持有日线数'},
    'entry_top_k': {'type': 'integer', 'minimum': 0, 'maximum': 64, 'title': '每日入场前K名（0不限制）'},
    'entry_delay_bars': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'title': '入场延迟日线数'},
    'pool_roll': {'type': 'string', 'enum': ['static', 'daily', 'weekly', 'position'], 'title': '池刷新周期'},
    'stop_loss_pct': {'type': 'number', 'minimum': 0, 'maximum': .5, 'title': '止损比例'},
    'take_profit_pct': {'type': 'number', 'minimum': 0, 'maximum': 1.5, 'title': '止盈比例'},
    'trailing_stop_pct': {'type': 'number', 'minimum': 0, 'maximum': .5, 'title': '回撤退出比例'},
    'intraday_trailing': {'type': 'boolean', 'title': '启用已观察回撤退出'},
    'trailing_reduce_ratio': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1, 'title': '已观察回撤减仓比例'},
    'daily_weak_clear': {'type': 'boolean', 'title': '启用弱势清仓'},
    'weak_confirm_days': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'title': '弱势确认日数'},
    'invalidate_pending': {'type': 'boolean', 'title': '风险出现时取消等待入场'},
    'slippage_rate': {'type': 'number', 'minimum': 0, 'maximum': .05, 'title': '单边不利滑点'},
    'trail_activate': {'type': 'number', 'minimum': 0, 'maximum': 2, 'title': '已知峰值涨幅激活MA10退出（0关闭）'},
    'time_stop_days': {'type': 'integer', 'minimum': 0, 'maximum': 240, 'title': '入场后时间止损观察日数（0关闭）'},
    'time_stop_min_gain': {'type': 'number', 'minimum': 0, 'maximum': 2, 'title': '时间止损最低已知峰值涨幅'},
    'breakeven_trigger': {'type': 'number', 'minimum': 0, 'maximum': 2, 'title': '已知峰值涨幅激活保本障碍（0关闭）'},
}


def supported_schema(context):
    result = {'config.' + key: value for key, value in CONFIG_SCHEMA.items()}
    if context['mode'] == 'matrix_raw_s1_s9':
        result['config.top_n'] = {'type': 'integer', 'minimum': 1, 'maximum': 2000, 'title': 'S3收益排序前N'}
    elif context['mode'] == 'aligned_wyckoff_events':
        result.update({'params.' + key: spec for key, spec in PARAM_SCHEMA.items()
                       if spec.get('type') in ('integer', 'number', 'boolean') or spec.get('enum')})
    else:
        result.update({key: spec for key, spec in single_schema(context['strategy_id']).items() if key.startswith('params.')})
    return result


def candidate_context(source, axis_values):
    # Bars and calendar are immutable and can be shared while validating the
    # plan. Copy only mutable parameter maps, not 60,000 bars per candidate.
    context = {**source, 'config': deepcopy(source['config']), 'params': deepcopy(source['params'])}
    schema = supported_schema(source)
    if not axis_values or set(axis_values) - set(schema):
        raise TradeError('UNSUPPORTED_PORTFOLIO_AXIS', '参数维度不属于当前组合执行路径')
    for path, raw in axis_values.items():
        group, key = path.split('.', 1)
        spec = schema[path]
        value = int(raw) if spec['type'] == 'integer' else str(raw).lower() == 'true' if spec['type'] == 'boolean' else raw
        if key == 'slippage_rate' and group == 'config':
            context['config']['fee_config'][key] = value
        else:
            context[group][key] = value
    context['config'] = normalize_config(context['config'])
    if context['mode'] == 'aligned_wyckoff_events':
        context['params'] = normalize_event_params(context['params'])
    elif context['mode'] == 'traditional_runtime14':
        allowed = next(item['signal_params'] for item in strategy_catalog() if item['id'] == context['strategy_id'])
        context['params'] = normalize_strategy_params(context['strategy_id'], {key: value for key, value in context['params'].items() if key in allowed})
    return context


def sample_plan(source, body):
    plan = sample_axes(body.get('sampling_mode', 'grid'), body.get('axes'), supported_schema(source),
                       seed=body.get('seed', 0), sample_points=body.get('sample_points', 24), max_points=body.get('max_points', 120))
    # Validate every joint combination (including strategy constraints) before
    # accepting the immutable plan. No invalid point is silently omitted.
    contexts = [candidate_context(source, values) for values in plan['samples']]
    if any(key in plan['axes'] for key in ('config.trailing_reduce_ratio', 'config.intraday_trailing')) and not any(
            value['config']['intraday_trailing'] and Decimal(value['config']['trailing_stop_pct']) > 0 for value in contexts):
        raise TradeError('UNUSED_PORTFOLIO_AXIS', '所有候选均未启用回撤退出；请先启用回撤退出或同时采样正的回撤比例')
    if 'config.weak_confirm_days' in plan['axes'] and not any(value['config']['daily_weak_clear'] for value in contexts):
        raise TradeError('UNUSED_PORTFOLIO_AXIS', '所有候选均关闭弱势清仓，确认天数不会应用')
    return plan


def portfolio_metrics(result):
    """Aggregate partial exits until the original position is completely flat."""
    active, profits, ratios = {}, [], []
    buys = 0
    for trade in result['trades']:
        symbol, quantity = trade['symbol'], trade['quantity']
        if trade['side'] == 'buy':
            if symbol in active:
                raise TradeError('PORTFOLIO_CYCLE_INVALID', '组合引擎未支持持仓内加仓，结果批次不一致')
            gross = (Decimal(trade['price']) * quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            active[symbol] = {'quantity': quantity, 'cost': gross + Decimal(trade['fees']), 'pnl': Decimal(0)}
            buys += 1
        else:
            cycle = active.get(symbol)
            if cycle is None or quantity > cycle['quantity']:
                raise TradeError('PORTFOLIO_CYCLE_INVALID', '卖出数量超过可匹配买入批次')
            cycle['quantity'] -= quantity
            cycle['pnl'] += Decimal(trade['realized_pnl'])
            if cycle['quantity'] == 0:
                profits.append(float(cycle['pnl']))
                ratios.append(float(cycle['pnl'] / cycle['cost']))
                del active[symbol]
    gain, loss = sum(max(0, value) for value in profits), -sum(min(0, value) for value in profits)
    curve = result['equity']
    days = max(1, (date.fromisoformat(curve[-1]['date']) - date.fromisoformat(curve[0]['date'])).days) if curve else 1
    monthly, previous, returns = {}, float(result['initial_capital']), []
    for row in curve:
        monthly[row['date'][:7]] = float(row['total_assets'])
    for value in monthly.values():
        returns.append(value / previous - 1 if previous else 0)
        previous = value
    rejected = sum(row['reason'] in ('DAILY_TOP_K', 'MAX_POSITIONS', 'INSUFFICIENT_CASH') for row in result['decisions'])
    attempts = buys + rejected
    return {'total_return': float(result['total_return']), 'max_drawdown': abs(float(result['max_drawdown'])),
        'trade_count': len(profits), 'win_rate': sum(value > 0 for value in profits) / len(profits) if profits else None,
        'profit_factor': gain / loss if loss else None, 'profit_factor_unbounded': bool(gain and not loss),
        'avg_net_trade_return': sum(ratios) / len(ratios) if ratios else None,
        'annual_trades': len(profits) / max(days / 365.25, 1 / 365.25),
        'eligible_signals': attempts, 'executed_entries': buys, 'entry_fill_rate': buys / attempts if attempts else 0,
        'monthly_return_std': math.sqrt(sum((value - sum(returns) / len(returns)) ** 2 for value in returns) / len(returns)) if returns else 0,
        'open_cycles': len(active), 'partial_realized_pnl': str(sum((row['pnl'] for row in active.values()), Decimal(0))),
        'quality_flags': result['quality_flags'], 'metric_version': VERSION}


def score_points(points, axes):
    result = robustness(points, axes)
    result['scoring_version'] = VERSION
    result['notes'] = [note for note in result['notes'] if '仅单股' not in note]
    result['notes'] += ['固定研究样本内描述性比较，不是历史全市场或样本外绩效',
        '单笔统计按证券完整买入到清仓周期汇总全部减仓腿；未清仓周期不计入胜率/盈亏因子，权益仍含持仓',
        '入场成交率=实际开盘买入数/已到期可执行意图数；分母包含TOP_K、持仓上限、资金不足拦截，不含未到期或池/风险失效意图',
        '减仓比例只作用于已观察回撤后的下一开盘；没有分钟成交或同日收盘回填']
    return result
