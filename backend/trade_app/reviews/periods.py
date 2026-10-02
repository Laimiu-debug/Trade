"""Human weekly and monthly notes, with separately versioned derived context."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.analytics.service import projection_status
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.models import PeriodReview
from trade_app.trading.service import account_or_error
from trade_app.trading.models import Trade


FIELDS = {
    'weekly': {'core_goals', 'achievements', 'resource_analysis', 'market_rhythm',
               'next_week_strategy', 'key_insight', 'right_things', 'wrong_things',
               'market_review', 'next_strategy', 'tags'},
    'monthly': {'system_iteration', 'next_goal', 'summary', 'market_review', 'tags'},
}


def bounds(kind: str, key: str) -> tuple[str, str]:
    if kind not in FIELDS:
        raise TradeError('INVALID_PERIOD_KIND', '复盘周期无效')
    try:
        if kind == 'weekly':
            year_text, week_text = key.split('-W')
            year, week = int(year_text), int(week_text)
            start = date.fromisocalendar(year, week, 1)
            end = date.fromisocalendar(year, week, 7)
            if f'{year:04d}-W{week:02d}' != key:
                raise ValueError(key)
        else:
            year_text, month_text = key.split('-')
            year, month = int(year_text), int(month_text)
            start = date(year, month, 1)
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            end = date.fromordinal(next_month.toordinal() - 1)
            if f'{year:04d}-{month:02d}' != key:
                raise ValueError(key)
    except (ValueError, TypeError) as exc:
        raise TradeError('INVALID_PERIOD', '周使用 YYYY-Www，月使用 YYYY-MM') from exc
    return start.isoformat(), end.isoformat()


def period_data(row: PeriodReview | None, kind: str, key: str) -> dict:
    return {'id': row.id if row else None, 'kind': kind, 'period_key': key,
            'sections': json.loads(row.sections_json) if row else {},
            'revision': row.revision if row else 0,
            'updated_at': row.updated_at if row else None}


def list_periods(session: Session, account_id: str, kind: str) -> list[dict]:
    account_or_error(session, account_id, real=True)
    if kind not in FIELDS:
        raise TradeError('INVALID_PERIOD_KIND', '复盘周期无效')
    rows = session.scalars(select(PeriodReview).where(
        PeriodReview.account_id == account_id, PeriodReview.kind == kind)
        .order_by(PeriodReview.period_key.desc())).all()
    return [period_data(row, kind, row.period_key) for row in rows]


def get_period(session: Session, account_id: str, kind: str, key: str) -> dict:
    account_or_error(session, account_id, real=True)
    start, end = bounds(kind, key)
    row = session.scalar(select(PeriodReview).where(
        PeriodReview.account_id == account_id, PeriodReview.kind == kind,
        PeriodReview.period_key == key))
    result = period_data(row, kind, key)
    projection = projection_status(session, account_id)
    source = projection['result'] or {}
    trades = [trade for trade in source.get('rounds', []) if trade.get('status') == 'closed'
              and trade.get('end_date') and start <= trade['end_date'] <= end]
    from decimal import Decimal
    period_pnl = sum((Decimal(item['pnl']) for item in trades if item['pnl']), Decimal(0))
    nav_source = source.get('nav', {})
    nav_points = [point for point in nav_source.get('points', []) if start <= point['date'] <= end]
    confirmed = [point for point in nav_points if point['quality'] == 'confirmed' and point['nav'] is not None]
    baseline = [point for point in nav_source.get('points', [])
                if point['date'] < start and point['quality'] == 'confirmed' and point['nav'] is not None]
    last = confirmed[-1] if confirmed else None
    previous = baseline[-1] if baseline else None
    return_pct = (last and previous and last['segment'] == previous['segment'] and
                  Decimal(previous['nav']) > 0)
    wins = [item for item in trades if item['pnl'] and Decimal(item['pnl']) > 0]
    losses = [item for item in trades if item['pnl'] and Decimal(item['pnl']) < 0]
    period_trades = session.scalars(select(Trade).where(
        Trade.account_id == account_id, Trade.trade_date >= start, Trade.trade_date <= end,
        Trade.voided_at.is_(None)).order_by(Trade.trade_date, Trade.created_at, Trade.id)).all()
    drawdowns = [Decimal(point['drawdown_pct']) for point in confirmed if point['drawdown_pct'] is not None]
    events = [event for event in nav_source.get('node_events', []) if start <= event['date'] <= end]
    achievements = [event for event in nav_source.get('first_achievements', [])
                    if start <= event['first_lit_date'] <= end]
    result['derived'] = {'status': projection['status'], 'projection_version': projection['projection_version'],
                         'start_date': start, 'end_date': end, 'closed_rounds': len(trades),
                         'closed_pnl': format(period_pnl, '.2f'),
                         'last_nav': last['nav'] if last else None,
                         'return_pct': format((Decimal(last['nav']) / Decimal(previous['nav']) - 1) * 100, '.4f')
                         if return_pct else None,
                         'return_quality': 'confirmed' if return_pct else 'missing_snapshot_or_baseline',
                         'confirmed_snapshot_count': len(confirmed),
                         'min_drawdown_pct': format(min(drawdowns), '.4f') if drawdowns else None,
                         'trade_count': len(period_trades),
                         'trades': [{'id': item.id, 'date': item.trade_date,
                                     'symbol': item.symbol, 'side': item.side,
                                     'quantity': item.quantity} for item in period_trades],
                         'round_ids': [item['id'] for item in trades],
                         'rounds': [{'id': item['id'], 'symbol': item['symbol'],
                                     'name': item['name'], 'end_date': item['end_date'],
                                     'pnl': item['pnl'], 'trade_ids': item['trade_ids']}
                                    for item in trades],
                         'win_rate_pct': format(Decimal(len(wins)) * 100 / len(trades), '.2f') if trades else None,
                         'winning_rounds': len(wins), 'losing_rounds': len(losses),
                         'node_events': events, 'first_achievements': achievements}
    return result


def save_period(session: Session, account_id: str, kind: str, key: str, payload: dict) -> dict:
    account_or_error(session, account_id, real=True)
    bounds(kind, key)
    sections = payload['sections']
    if set(sections) - FIELDS[kind]:
        raise TradeError('UNKNOWN_REVIEW_FIELD', '复盘包含不支持的字段')
    if any(not isinstance(value, str) or len(value) > 50_000 for value in sections.values()):
        raise TradeError('INVALID_REVIEW_FIELD', '复盘内容格式或长度无效')
    row = session.scalar(select(PeriodReview).where(
        PeriodReview.account_id == account_id, PeriodReview.kind == kind,
        PeriodReview.period_key == key))
    before = period_data(row, kind, key) if row else None
    if row:
        if row.revision != payload['expected_revision']:
            raise TradeError('REVISION_CONFLICT', '周期复盘已在其他页面修改', 409)
        row.revision += 1
    else:
        if payload['expected_revision'] != 0:
            raise TradeError('REVISION_CONFLICT', '周期复盘不存在，请刷新后重试', 409)
        now = utc_now()
        row = PeriodReview(id=new_id(), account_id=account_id, kind=kind,
                           period_key=key, sections_json='{}', revision=1,
                           created_at=now, updated_at=now)
        session.add(row)
    row.sections_json = json.dumps(sections, ensure_ascii=False, sort_keys=True)
    row.updated_at = utc_now()
    session.flush()
    after = period_data(row, kind, key)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='period_review',
                           entity_id=row.id, operation='update' if before else 'create',
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(after, ensure_ascii=False), created_at=utc_now()))
    return get_period(session, account_id, kind, key)


def delete_period(session: Session, account_id: str, kind: str, key: str,
                  expected_revision: int) -> dict:
    account_or_error(session, account_id, real=True)
    bounds(kind, key)
    row = session.scalar(select(PeriodReview).where(
        PeriodReview.account_id == account_id, PeriodReview.kind == kind,
        PeriodReview.period_key == key))
    if row is None:
        raise TradeError('PERIOD_REVIEW_NOT_FOUND', '周期复盘不存在', 404)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '周期复盘版本已变化', 409)
    before = period_data(row, kind, key)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='period_review',
                           entity_id=row.id, operation='delete',
                           before_json=json.dumps(before, ensure_ascii=False),
                           after_json=None, created_at=utc_now()))
    session.delete(row)
    return {'deleted': True, 'kind': kind, 'period_key': key}
