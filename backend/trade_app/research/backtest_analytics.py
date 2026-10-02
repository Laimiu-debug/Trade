"""Deterministic diagnostics of frozen single-symbol results; no trading advice or I/O."""
from __future__ import annotations

import math
import random
import statistics
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from trade_app.market.domain import eligible_bars


ANALYSIS_VERSION = 'single-symbol-diagnostics-v1'
ANNUAL_SESSIONS = 252


def _number(value):
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), 8)


def _mean(values):
    return statistics.fmean(values) if values else None


def _quantile(values, q):
    ordered = sorted(values)
    offset = (len(ordered) - 1) * q
    lo, hi = math.floor(offset), math.ceil(offset)
    return _number(ordered[lo] + (ordered[hi] - ordered[lo]) * (offset - lo))


def completed_trades(result: dict) -> list[dict]:
    """Pair actual fills; position return includes both sides' fees and slippage."""
    entry = None
    equity = {row['date']: row for row in result['equity']}
    out = []
    for fill in result['trades']:
        if fill['side'] == 'buy':
            if entry is not None:
                raise ValueError('Overlapping entries are not supported by single-symbol diagnostics')
            entry = fill
        elif entry is not None:
            if fill['quantity'] != entry['quantity']:
                raise ValueError('Partial exits require the portfolio diagnostics adapter')
            cost = Decimal(entry['price']) * entry['quantity'] + Decimal(entry['fees'])
            pnl = Decimal(fill['price']) * fill['quantity'] - Decimal(fill['fees']) - cost
            capital = Decimal(equity[entry['date']]['cash']) + cost
            out.append({'entry_date': entry['date'], 'exit_date': fill['date'],
                        'quantity': fill['quantity'], 'net_pnl': str(pnl.quantize(Decimal('0.01'))),
                        'position_return': _number(pnl / cost) if cost > 0 else None,
                        'account_return': _number(pnl / capital) if capital > 0 else None,
                        'holding_bars': fill.get('holding_bars')})
            entry = None
        else:
            raise ValueError('Exit without an entry in frozen result')
    return out


def _risk(result, pairs):
    equity = result['equity']
    values = [float(row['total_assets']) for row in equity]
    daily = [b / a - 1 for a, b in zip(values, values[1:]) if a > 0]
    mean = _mean(daily)
    std = statistics.pstdev(daily) if len(daily) >= 2 else None
    downside = math.sqrt(statistics.fmean(min(value, 0) ** 2 for value in daily)) if len(daily) >= 2 else None
    initial = float(result['initial_capital'])
    annual = (values[-1] / initial) ** (ANNUAL_SESSIONS / len(daily)) - 1 if daily and initial > 0 and values[-1] > 0 else None
    returns = [row['position_return'] for row in pairs if row['position_return'] is not None]
    gains = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_gain = sum(Decimal(row['net_pnl']) for row in pairs if Decimal(row['net_pnl']) > 0)
    gross_loss = -sum(Decimal(row['net_pnl']) for row in pairs if Decimal(row['net_pnl']) < 0)
    consecutive = longest = 0
    for value in returns:
        consecutive = consecutive + 1 if value < 0 else 0
        longest = max(longest, consecutive)
    peak = initial
    drawdowns = []
    for value in values:
        peak = max(peak, value)
        drawdowns.append((peak - value) / peak if peak > 0 else 0)
    max_dd = max(drawdowns, default=0)
    recovery = {'status': 'no_drawdown', 'calendar_days': 0, 'trading_sessions': 0, 'observed_calendar_days': 0}
    if max_dd > 1e-12:
        trough = drawdowns.index(max_dd)
        recovered = next((index for index in range(trough + 1, len(values)) if drawdowns[index] <= 1e-12), None)
        observed = (date.fromisoformat(equity[-1]['date']) - date.fromisoformat(equity[trough]['date'])).days
        recovery = {'status': 'recovered' if recovered is not None else 'unrecovered',
                    'trough_date': equity[trough]['date'],
                    'recovery_date': equity[recovered]['date'] if recovered is not None else None,
                    'calendar_days': (date.fromisoformat(equity[recovered]['date']) - date.fromisoformat(equity[trough]['date'])).days if recovered is not None else None,
                    'trading_sessions': recovered - trough if recovered is not None else None,
                    'observed_calendar_days': observed}
    reasons = {}
    if not std or std <= 1e-12: reasons['sharpe'] = '日收益样本不足或波动为零'
    if not downside or downside <= 1e-12: reasons['sortino'] = '日收益样本不足或没有下行波动'
    if max_dd <= 1e-12: reasons['calmar'] = '最大回撤为零，不能计算比值'
    if not gross_loss: reasons['profit_factor'] = '没有亏损交易，不能计算比值'
    if not pairs: reasons['trades'] = '没有已完成交易'
    return {'daily_sample_count': len(daily), 'completed_trade_count': len(pairs),
            'annualized_return': _number(annual),
            'sharpe': _number(mean / std * math.sqrt(ANNUAL_SESSIONS)) if std and std > 1e-12 else None,
            'sortino': _number(mean / downside * math.sqrt(ANNUAL_SESSIONS)) if downside and downside > 1e-12 else None,
            'calmar': _number(annual / max_dd) if annual is not None and max_dd > 1e-12 else None,
            'profit_factor': _number(gross_gain / gross_loss) if gross_loss else None,
            'expectancy': _number(_mean(returns)), 'average_win': _number(_mean(gains)),
            'average_loss': _number(_mean(losses)), 'average_net_pnl': _number(_mean([float(row['net_pnl']) for row in pairs])),
            'max_consecutive_losses': longest, 'recovery': recovery,
            'fill_rate': _number(len(pairs) / result['signal_count']) if result.get('signal_count') else None,
            'max_concurrent_positions': int(any(row['quantity'] > 0 for row in equity)),
            'unavailable_reasons': reasons}


def _monthly(result):
    rows = []
    previous = float(result['initial_capital'])
    for point in result['equity']:
        month = point['date'][:7]
        if not rows or rows[-1]['month'] != month:
            rows.append({'month': month, 'first_observed_date': point['date'], 'start_assets': previous,
                         'last_observed_date': point['date'], 'end_assets': float(point['total_assets']), 'sessions': 0})
        rows[-1]['last_observed_date'] = point['date']
        rows[-1]['end_assets'] = float(point['total_assets'])
        rows[-1]['sessions'] += 1
        previous = float(point['total_assets'])
    for row in rows:
        row['return'] = _number(row['end_assets'] / row['start_assets'] - 1) if row['start_assets'] > 0 else None
    returns = [row['return'] for row in rows if row['return'] is not None]
    return {'months': rows, 'monthly_return_std': _number(statistics.pstdev(returns)) if len(returns) >= 2 else None,
            'reason': None if len(returns) >= 2 else '至少需要两个有估值的月份',
            'scope': '按样本实际覆盖的月内日期计算；首尾月份可能不完整',
            'neighborhood': {'status': 'not_generated', 'reason': '参数邻域比较在收益平原中独立计算'}}


def _regimes(result, bars, pairs, strict):
    # Classification is based on the instrument's already available closes, never strategy P&L.
    by_day = {}
    for pair in pairs:
        day = pair['entry_date']
        opening = datetime.combine(date.fromisoformat(day), time(9, 30), ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
        history, _ = eligible_bars([bar for bar in bars if bar['event_date'] < day], (opening - timedelta(microseconds=1)).isoformat(), strict)
        if len(history) < 21:
            by_day[day] = 'unknown'
        else:
            change = float(history[-1]['close']) / float(history[-21]['close']) - 1
            by_day[day] = 'bull' if change >= .03 else 'bear' if change <= -.03 else 'range'
    buckets = []
    for name in ('bull', 'range', 'bear', 'unknown'):
        items = [pair for pair in pairs if by_day[pair['entry_date']] == name]
        values = [float(pair['net_pnl']) for pair in items]
        account = peak = 1.0
        max_dd = 0.0
        for pair in items:
            account *= 1 + pair['account_return']
            peak = max(peak, account)
            max_dd = max(max_dd, 1 - account / peak)
        buckets.append({'regime': name, 'trade_count': len(items),
                        'win_rate': _number(sum(value > 0 for value in values) / len(values)) if values else None,
                        'net_pnl': _number(sum(values)) if values else None,
                        'pnl_contribution': _number(sum(values) / float(result['initial_capital'])) if values else None,
                        'mean_position_return': _number(_mean([pair['position_return'] for pair in items])),
                        'closed_trade_sequence_drawdown': _number(max_dd) if items else None})
    return {'version': 'known-underlying-close-20sessions-3pct-v1', 'buckets': buckets,
            'scope': '入场前已可得的标的20交易日涨跌幅代理；非全市场牛熊判断。分组回撤只按组内平仓收益序列计算，未包含持仓中浮亏。'}


def _monte_carlo(pairs, seed, iterations):
    values = [row['account_return'] for row in pairs if row['account_return'] is not None]
    base = {'method': 'iid-bootstrap-account-trade-return-v1', 'seed': seed, 'requested_iterations': iterations,
            'sample_count': len(values), 'capital_loss_threshold': .20,
            'scope': '以含费用和实际仓位的整轮账户收益有放回抽样；假设交易独立，不保留市况顺序，不预测未来收益。'}
    if len(values) < 10:
        return {**base, 'status': 'insufficient_sample', 'reason': '至少需要10轮完整交易', 'iterations': 0,
                'total_return': None, 'max_drawdown': None, 'ruin_probability': None, 'capital_loss_20_probability': None}
    rng = random.Random(seed)
    returns, drawdowns, ruined = [], [], 0
    for _ in range(iterations):
        assets = peak = 1.0
        drawdown = 0.0
        for _ in values:
            assets = max(0.0, assets * (1 + rng.choice(values)))
            peak = max(peak, assets)
            drawdown = max(drawdown, 1 - assets / peak)
        returns.append(assets - 1)
        drawdowns.append(drawdown)
        ruined += int(assets <= 0)
    quantiles = lambda rows: {key: _quantile(rows, q) for key, q in (('p5', .05), ('p50', .5), ('p95', .95))}
    return {**base, 'status': 'generated', 'reason': None, 'iterations': iterations,
            'total_return': quantiles(returns), 'max_drawdown': quantiles(drawdowns),
            'ruin_probability': _number(ruined / iterations),
            'capital_loss_20_probability': _number(sum(value <= -.2 for value in returns) / iterations)}


def build_backtest_analysis(result: dict, bars: list[dict], *, seed: int = 20260926,
                            iterations: int = 400, strict: bool = True) -> dict:
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1 or type(iterations) is not int or not 100 <= iterations <= 2000:
        raise ValueError('Invalid bounded Monte Carlo options')
    pairs = completed_trades(result)
    return {'version': ANALYSIS_VERSION, 'status': 'generated', 'risk': _risk(result, pairs),
            'stability': _monthly(result), 'regimes': _regimes(result, bars, pairs, strict),
            'monte_carlo': _monte_carlo(pairs, seed, iterations), 'completed_trades': pairs,
            'walk_forward': {'status': 'not_generated', 'reason': '尚未执行按时间隔离的训练与测试，不将同一批样本拆分统计标为样本外验证'},
            'methodology': {'annual_sessions': ANNUAL_SESSIONS, 'risk_free_rate': 0,
                            'sharpe': '日收益总体标准差，252交易日年化，无风险收益设0',
                            'sortino': '全部日收益的负部平方均值开根，252交易日年化',
                            'calmar': '按实际样本日收益数年化的收益 / 最大回撤',
                            'expectancy': '完整交易净收益率算术平均，包含零收益交易',
                            'fill_rate': '完成买入交易数 / 可执行买入信号数，仅限当前单股路径',
                            'missing': '分母为0或样本不足返回null及原因，不用0代替'}}
