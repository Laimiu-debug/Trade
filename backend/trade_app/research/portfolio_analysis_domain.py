"""Pure diagnostics of a frozen portfolio, including partial position exits."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math
import random
import statistics

from trade_app.market.domain import eligible_bars

VERSION = 'portfolio-complete-cycles-daily-block-bootstrap-v2'
ANNUAL_SESSIONS = 252


def number(value):
    return round(float(value), 10) if value is not None and math.isfinite(float(value)) else None


def mean(values):
    return statistics.fmean(values) if values else None


def quantile(values, q):
    values = sorted(values)
    position = (len(values) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return number(values[lo] + (values[hi] - values[lo]) * (position - lo))


def complete_cycles(trades):
    active, completed, peak_positions = {}, [], 0
    for index, trade in enumerate(trades):
        symbol = trade['symbol']
        if trade['side'] == 'buy':
            if symbol in active:
                raise ValueError('Unexpected additional entry inside a portfolio position')
            # Match apply_fill: round the executed gross to cents before fees.
            gross = (Decimal(trade['price']) * trade['quantity']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            active[symbol] = {'symbol': symbol, 'entry_date': trade['date'], 'quantity': trade['quantity'],
                'remaining_quantity': trade['quantity'], 'entry_cost': str(gross + Decimal(trade['fees'])),
                'net_pnl': '0', 'exit_legs': 0, 'entry_trade_index': index, 'exit_trade_indices': []}
            peak_positions = max(peak_positions, len(active))
        else:
            item = active.get(symbol)
            if item is None or trade['quantity'] > item['remaining_quantity']:
                raise ValueError('Unmatched exit in portfolio evidence')
            item['remaining_quantity'] -= trade['quantity']
            item['net_pnl'] = str(Decimal(item['net_pnl']) + Decimal(trade['realized_pnl']))
            item['exit_legs'] += 1
            item['exit_trade_indices'].append(index)
            if not item['remaining_quantity']:
                item.update(exit_date=trade['date'], position_return=number(Decimal(item['net_pnl']) / Decimal(item['entry_cost'])))
                completed.append(item)
                del active[symbol]
    return completed, list(active.values()), peak_positions


def daily_returns(result):
    previous, values = float(result['initial_capital']), []
    for row in result['equity']:
        assets = float(row['total_assets'])
        if previous <= 0:
            raise ValueError('Nonpositive portfolio equity cannot produce relative return diagnostics')
        values.append(assets / previous - 1)
        previous = assets
    return values


def risk_metrics(result, cycles, daily, max_positions):
    values = [float(row['total_assets']) for row in result['equity']]
    initial = float(result['initial_capital'])
    annual = None
    if daily and values[-1] > 0 and initial > 0:
        log_return = math.log(values[-1] / initial) * ANNUAL_SESSIONS / len(daily)
        if log_return < 700:
            annual = math.expm1(log_return)
    std = statistics.pstdev(daily) if len(daily) >= 2 else None
    downside = math.sqrt(statistics.fmean(min(value, 0) ** 2 for value in daily)) if len(daily) >= 2 else None
    cycle_returns = [item['position_return'] for item in cycles]
    profits = [float(item['net_pnl']) for item in cycles]
    gross_gain, gross_loss = sum(max(0, value) for value in profits), -sum(min(0, value) for value in profits)
    longest = consecutive = 0
    for value in profits:
        consecutive = consecutive + 1 if value < 0 else 0
        longest = max(longest, consecutive)
    peak, max_dd, drawdowns = initial, 0.0, []
    for value in values:
        peak = max(peak, value)
        dd = (peak - value) / peak
        drawdowns.append(dd)
        max_dd = max(max_dd, dd)
    recovery = {'status': 'no_drawdown', 'calendar_days': 0, 'trading_sessions': 0, 'observed_calendar_days': 0}
    if max_dd > 1e-12:
        trough = drawdowns.index(max_dd)
        recovered = next((index for index in range(trough + 1, len(values)) if drawdowns[index] <= 1e-12), None)
        recovery = {'status': 'recovered' if recovered is not None else 'unrecovered',
            'trough_date': result['equity'][trough]['date'], 'recovery_date': result['equity'][recovered]['date'] if recovered is not None else None,
            'calendar_days': (date.fromisoformat(result['equity'][recovered]['date']) - date.fromisoformat(result['equity'][trough]['date'])).days if recovered is not None else None,
            'trading_sessions': recovered - trough if recovered is not None else None,
            'observed_calendar_days': (date.fromisoformat(result['equity'][-1]['date']) - date.fromisoformat(result['equity'][trough]['date'])).days}
    attempted = result['buy_count'] + sum(item['reason'] in ('DAILY_TOP_K', 'MAX_POSITIONS', 'INSUFFICIENT_CASH') for item in result['decisions'])
    reasons = {}
    if not std or std <= 1e-12: reasons['sharpe'] = '至少需要2个有效日收益且波动不为零'
    if not downside or downside <= 1e-12: reasons['sortino'] = '样本不足或没有下行波动'
    if max_dd <= 1e-12: reasons['calmar'] = '回撤为零，不生成比值'
    if not gross_loss: reasons['profit_factor'] = '没有已完成亏损周期，不生成比值'
    if not cycles: reasons['cycles'] = '没有完整清仓的持仓周期'
    if annual is None: reasons['annualized_return'] = '无法年化或超出有限数值范围'
    return {'daily_sample_count': len(daily), 'completed_trade_count': len(cycles),
        'annualized_return': number(annual), 'sharpe': number(mean(daily) / std * math.sqrt(ANNUAL_SESSIONS)) if std and std > 1e-12 else None,
        'sortino': number(mean(daily) / downside * math.sqrt(ANNUAL_SESSIONS)) if downside and downside > 1e-12 else None,
        'calmar': number(annual / max_dd) if annual is not None and max_dd > 1e-12 else None,
        'max_drawdown': number(max_dd), 'profit_factor': number(gross_gain / gross_loss) if gross_loss else None,
        'expectancy': number(mean(cycle_returns)), 'average_win': number(mean([value for value in cycle_returns if value > 0])),
        'average_loss': number(mean([value for value in cycle_returns if value < 0])), 'average_net_pnl': number(mean(profits)),
        'max_consecutive_losses': longest, 'recovery': recovery,
        'fill_rate': number(result['buy_count'] / attempted) if attempted else None,
        'eligible_entry_attempts': attempted, 'executed_entries': result['buy_count'],
        'max_concurrent_positions': max_positions, 'unavailable_reasons': reasons}


def stability(result, cycles):
    months, previous = [], float(result['initial_capital'])
    for row in result['equity']:
        month = row['date'][:7]
        if not months or months[-1]['month'] != month:
            months.append({'month': month, 'first_observed_date': row['date'], 'start_assets': previous, 'sessions': 0})
        months[-1].update(last_observed_date=row['date'], end_assets=float(row['total_assets']))
        months[-1]['sessions'] += 1
        previous = float(row['total_assets'])
    for row in months:
        row['return'] = number(row['end_assets'] / row['start_assets'] - 1)
    std = statistics.pstdev([row['return'] for row in months]) if len(months) >= 2 else None
    return {'months': months, 'monthly_return_std': number(std), 'reason': None if std is not None else '至少需要两个有估值的月份',
        'minimum_complete_cycles': 20, 'complete_cycle_count': len(cycles),
        'trade_count_penalty': number(max(0, 1 - len(cycles) / 20) * 100),
        'return_variance_penalty': number(min(1, std / .10) * 100) if std is not None else None,
        'stability_score': None, 'neighborhood_consistency': None,
        'neighborhood': {'status': 'not_generated', 'reason': '单次组合没有参数邻域证据，请运行组合收益平原；不以0冒充一致性'},
        'scope': '按实际覆盖日期统计月收益；首尾月份可能不完整。周期数量门槛20；月波动10%对应100分惩罚，不组合成无邻域证据的总分'}


def regimes(context, cycles, initial_capital):
    bars_by_symbol = {item['symbol']: item['bars'] for item in context['datasets']}
    annotated = []
    for cycle in cycles:
        prefix = [bar for bar in bars_by_symbol[cycle['symbol']] if bar['event_date'] < cycle['entry_date']]
        visible, flags = eligible_bars(prefix, cycle['entry_date'] + 'T01:29:59.999999+00:00', context['config']['execution_strict'])
        regime, change = 'unknown', None
        if len(visible) >= 21 and (len(visible) == len(prefix) or not context['config']['execution_strict']):
            change = float(visible[-1]['close']) / float(visible[-21]['close']) - 1
            regime = 'bull' if change >= .03 else 'bear' if change <= -.03 else 'range'
        annotated.append({**cycle, 'regime': regime, 'known_20bar_return': number(change), 'regime_quality_flags': flags})
    buckets = []
    for regime in ('bull', 'range', 'bear', 'unknown'):
        members = [row for row in annotated if row['regime'] == regime]
        equity = peak = float(initial_capital)
        max_dd = 0.0
        for item in members:
            equity += float(item['net_pnl'])
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
        total = sum(float(item['net_pnl']) for item in members)
        buckets.append({'regime': regime, 'trade_count': len(members),
            'win_rate': number(sum(float(item['net_pnl']) > 0 for item in members) / len(members)) if members else None,
            'net_pnl': number(total) if members else None, 'pnl_contribution': number(total / float(initial_capital)) if members else None,
            'mean_position_return': number(mean([row['position_return'] for row in members])),
            'closed_trade_sequence_drawdown': number(max_dd) if members else None})
    return {'version': 'entry-known-symbol-20bars-3pct-closed-pnl-v1', 'buckets': buckets,
        'scope': '入场前已可得的各证券20日涨跌幅代理，不是全市场牛熊。分组收益为已清仓净盈亏/组合初始资金；分组回撤是平仓盈亏累加序列，不复利、不含持仓浮亏，不是可投资分组策略。'}, annotated


def monte_carlo(daily, *, seed, iterations, block_size):
    result = {'method': 'moving-block-bootstrap-portfolio-daily-returns-v1', 'seed': seed, 'requested_iterations': iterations,
        'sample_count': len(daily), 'block_size': block_size, 'capital_loss_threshold': .2,
        'scope': '对已聚合全部证券与费用的组合日收益进行移动分块有放回抽样，保留块内时间顺序与同期持仓组合效应；块间独立，不保留跨块依赖，不预测未来。'}
    if len(daily) < max(30, block_size * 3):
        return {**result, 'status': 'insufficient_sample', 'reason': '至少需要30个日收益及3个完整分块', 'iterations': 0,
            'total_return': None, 'max_drawdown': None, 'ruin_probability': None, 'capital_loss_20_probability': None}
    rng = random.Random(seed)
    returns, drawdowns, ruined, overflow = [], [], 0, 0
    for _ in range(iterations):
        log_assets = log_peak = 0.0
        drawdown, used = 0.0, 0
        while used < len(daily):
            start = rng.randrange(len(daily) - block_size + 1)
            for value in daily[start:start + min(block_size, len(daily) - used)]:
                log_assets = log_assets + math.log1p(value) if value > -1 and log_assets != -math.inf else -math.inf
                log_peak = max(log_peak, log_assets)
                drawdown = max(drawdown, 1 - math.exp(log_assets - log_peak))
                used += 1
        if log_assets > 700:
            overflow += 1
        else:
            returns.append(math.expm1(log_assets))
        drawdowns.append(drawdown)
        ruined += int(log_assets == -math.inf)
    percentiles = lambda values: {key: quantile(values, q) for key, q in (('p5', .05), ('p50', .5), ('p95', .95))}
    return {**result, 'status': 'generated', 'reason': '部分极端抽样收益超出有限数值范围，整组收益分位数不生成' if overflow else None,
        'iterations': iterations, 'numerical_overflow_paths': overflow,
        'total_return': None if overflow else percentiles(returns), 'max_drawdown': percentiles(drawdowns),
        'ruin_probability': number(ruined / iterations), 'capital_loss_20_probability': number(sum(value <= -.2 for value in returns) / iterations)}


def build_analysis(context, result, *, seed=20260926, iterations=400, block_size=5):
    if (type(seed) is not int or not 0 <= seed <= 2**32 - 1 or type(iterations) is not int or not 100 <= iterations <= 2000
            or type(block_size) is not int or not 1 <= block_size <= 60):
        raise ValueError('Invalid bounded portfolio analysis options')
    cycles, incomplete, max_positions = complete_cycles(result['trades'])
    daily = daily_returns(result)
    groups, annotated = regimes(context, cycles, result['initial_capital'])
    return {'version': VERSION, 'status': 'generated', 'risk': risk_metrics(result, cycles, daily, max_positions),
        'stability': stability(result, cycles), 'regimes': groups,
        'monte_carlo': monte_carlo(daily, seed=seed, iterations=iterations, block_size=block_size),
        'completed_trades': annotated, 'open_cycles': incomplete, 'quality_flags': result['quality_flags'],
        'walk_forward': {'status': 'not_generated', 'reason': '组合Walk-forward在独立时间折任务中执行，本分析不把样本内描述改称样本外'},
        'methodology': {'annual_sessions': ANNUAL_SESSIONS, 'risk_free_rate': 0,
            'daily_returns': '含初始资金到首日权益的收益，之后按组合日终资产计算；缺估值沿用原冻结组合质量标记',
            'sharpe': '日收益总体标准差，252观察日年化；无风险利率0',
            'sortino': '全部日收益负部平方均值开根，252观察日年化',
            'calmar': '按观察日数量年化收益/最大回撤，极端年化溢出返回null',
            'trade_cycles': '逐证券将买入至完全清仓的所有部分卖出净盈亏汇总；末日未平仓不伪造成交',
            'fill_rate': '实际买入数/已到期可执行意图；包括TOP_K、持仓数、资金拦截，不含未到期和池/风险失效意图',
            'consecutive_losses': '按完整清仓发生顺序计算；同日按冻结成交稳定顺序',
            'missing': '样本不足、分母为零和未生成指标返回null并写明原因'}}
