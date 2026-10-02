"""Read-only period summaries over the last complete account projection."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from trade_app.analytics.service import projection_status
from trade_app.platform.types import TradeError, decimal_text


def _period(day: str, kind: str) -> tuple[str, str, str]:
    parsed = date.fromisoformat(day)
    if kind == 'daily':
        return day, day, day
    if kind == 'weekly':
        year, week, _ = parsed.isocalendar()
        start = parsed - timedelta(days=parsed.weekday())
        return f'{year:04d}-W{week:02d}', start.isoformat(), (start + timedelta(days=6)).isoformat()
    if kind == 'monthly':
        start = parsed.replace(day=1)
        next_month = date(parsed.year + 1, 1, 1) if parsed.month == 12 else date(
            parsed.year, parsed.month + 1, 1)
        return f'{parsed.year:04d}-{parsed.month:02d}', start.isoformat(), (next_month - timedelta(days=1)).isoformat()
    raise TradeError('INVALID_PERIOD_KIND', '统计周期无效')


def _round_metrics(rounds: list[dict]) -> dict:
    closed = [row for row in rounds if row.get('status') == 'closed' and row.get('pnl') is not None]
    wins = [Decimal(row['pnl']) for row in closed if Decimal(row['pnl']) > 0]
    losses = [-Decimal(row['pnl']) for row in closed if Decimal(row['pnl']) < 0]
    pnl = sum((Decimal(row['pnl']) for row in closed), Decimal(0))
    avg_win = sum(wins, Decimal(0)) / len(wins) if wins else None
    avg_loss = sum(losses, Decimal(0)) / len(losses) if losses else None
    return {'closed_rounds': len(closed), 'winning_rounds': len(wins),
            'losing_rounds': len(losses), 'closed_pnl': decimal_text(pnl, 2),
            'win_rate_pct': decimal_text(Decimal(len(wins)) * 100 / len(closed), 2) if closed else None,
            'payoff_ratio': decimal_text(avg_win / avg_loss, 4) if avg_win is not None and avg_loss else None,
            'profit_factor': decimal_text(sum(wins, Decimal(0)) / sum(losses, Decimal(0)), 4)
            if losses else None,
            'round_ids': [row['id'] for row in closed]}


def performance(session: Session, account_id: str, kind: str, limit: int = 24) -> dict:
    if kind not in ('daily', 'weekly', 'monthly'):
        raise TradeError('INVALID_PERIOD_KIND', '统计周期无效')
    if limit < 1 or limit > 366:
        raise TradeError('INVALID_LIMIT', '统计数量必须在 1 至 366 之间')
    projection = projection_status(session, account_id)
    source = projection['result'] or {}
    nav = source.get('nav') or {}
    all_points = nav.get('points') or []
    confirmed = [row for row in all_points if row['quality'] == 'confirmed' and row['nav'] is not None]
    rounds = source.get('rounds') or []
    days = sorted({row['date'] for row in all_points} | {
        row['end_date'] for row in rounds if row.get('end_date')})
    buckets = {_period(day, kind) for day in days}
    results = []
    for key, start, end in sorted(buckets, key=lambda item: item[1], reverse=True)[:limit]:
        within = [row for row in confirmed if start <= row['date'] <= end]
        before = [row for row in confirmed if row['date'] < start]
        last = within[-1] if within else None
        baseline = before[-1] if before else None
        coverage_days = (date.fromisoformat(last['date']) - date.fromisoformat(baseline['date'])).days \
            if last and baseline else None
        if last and baseline and Decimal(baseline['nav']) > 0 and last['segment'] == baseline['segment']:
            return_pct = decimal_text((Decimal(last['nav']) / Decimal(baseline['nav']) - 1) * 100, 4)
            quality = 'confirmed' if coverage_days == len(within) else 'gap'
        else:
            return_pct = None
            quality = 'missing_snapshot' if not within else 'no_baseline'
        period_rounds = [row for row in rounds if row.get('end_date') and
                         start <= row['end_date'] <= end]
        period_points = [row for row in all_points if start <= row['date'] <= end]
        drawdowns = [Decimal(row['drawdown_pct']) for row in period_points
                     if row['drawdown_pct'] is not None and row['quality'] == 'confirmed']
        achievements = [row for row in nav.get('first_achievements', [])
                        if start <= row['first_lit_date'] <= end]
        results.append({'key': key, 'start_date': start, 'end_date': end,
                        'return_pct': return_pct, 'return_quality': quality,
                        'baseline_date': baseline['date'] if baseline else None,
                        'last_confirmed_date': last['date'] if last else None,
                        'confirmed_points': len(within), 'coverage_days': coverage_days,
                        'last_nav': last['nav'] if last else None,
                        'min_drawdown_pct': decimal_text(min(drawdowns), 4) if drawdowns else None,
                        'node_achievements': achievements,
                        'rounds': _round_metrics(period_rounds)})
    return {'account_id': account_id, 'kind': kind,
            'projection_status': projection['status'],
            'projection_version': projection['projection_version'],
            'calculation_version': projection['calculation_version'],
            'items': results,
            'method': '收益率按本周期末次已确认快照净值与周期开始前末次已确认快照比较；快照缺失或净值段变化时不计算。回合盈亏按结束日归属，节点按首次达成日归属。'}
