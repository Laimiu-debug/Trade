"""Human summaries follow deterministic round IDs and retain association snapshots."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.analytics.service import projection_status
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.models import DailyReview, PeriodReview
from trade_app.reviews.periods import bounds
from trade_app.reviews.round_models import RoundNote
from trade_app.trading.service import account_or_error


def _current_round(session: Session, account_id: str, round_id: str) -> tuple[dict | None, dict]:
    projection = projection_status(session, account_id)
    row = next((item for item in (projection['result'] or {}).get('rounds', [])
                if item.get('id') == round_id), None)
    return row, projection


def _related(session: Session, account_id: str, round_row: dict | None) -> dict:
    if round_row is None:
        return {'daily': [], 'periods': []}
    start = round_row['start_date']
    end = round_row['end_date'] or '9999-12-31'
    daily = session.scalars(select(DailyReview).where(
        DailyReview.account_id == account_id,
        DailyReview.review_date >= start, DailyReview.review_date <= end
    ).order_by(DailyReview.review_date)).all()
    periods = session.scalars(select(PeriodReview).where(
        PeriodReview.account_id == account_id)).all()
    related_periods = []
    for period in periods:
        period_start, period_end = bounds(period.kind, period.period_key)
        if period_start <= end and period_end >= start:
            related_periods.append({'kind': period.kind, 'period_key': period.period_key,
                                    'sections': json.loads(period.sections_json),
                                    'revision': period.revision})
    return {'daily': [{'date': row.review_date, 'title': row.title,
                       'decision_review': row.decision_review,
                       'mistakes': row.mistakes, 'revision': row.revision}
                      for row in daily], 'periods': related_periods}


def _data(row: RoundNote | None, round_id: str, current: dict | None, projection: dict,
          related: dict) -> dict:
    linked = json.loads(row.trade_ids_json) if row else []
    current_ids = current['trade_ids'] if current else []
    return {'id': row.id if row else None, 'round_id': round_id,
            'summary': row.summary if row else '', 'revision': row.revision if row else 0,
            'linked_trade_ids': linked, 'current_trade_ids': current_ids,
            'association_changed': bool(row) and linked != current_ids,
            'round_exists': current is not None, 'round': current,
            'projection_status': projection['status'],
            'projection_version': projection['projection_version'],
            'related': related, 'updated_at': row.updated_at if row else None}


def get_round_note(session: Session, account_id: str, round_id: str) -> dict:
    account_or_error(session, account_id, real=True)
    row = session.scalar(select(RoundNote).where(
        RoundNote.account_id == account_id, RoundNote.round_id == round_id))
    current, projection = _current_round(session, account_id, round_id)
    if row is None and current is None:
        raise TradeError('ROUND_NOT_FOUND', '交易回合不存在', 404)
    return _data(row, round_id, current, projection, _related(session, account_id, current))


def list_round_notes(session: Session, account_id: str) -> list[dict]:
    account_or_error(session, account_id, real=True)
    projection = projection_status(session, account_id)
    current = {item['id']: item for item in (projection['result'] or {}).get('rounds', [])
               if item.get('id')}
    rows = session.scalars(select(RoundNote).where(RoundNote.account_id == account_id)
                           .order_by(RoundNote.updated_at.desc())).all()
    return [_data(row, row.round_id, current.get(row.round_id), projection,
                  {'daily': [], 'periods': []}) for row in rows]


def save_round_note(session: Session, account_id: str, round_id: str, payload: dict) -> dict:
    account_or_error(session, account_id, real=True)
    current, projection = _current_round(session, account_id, round_id)
    row = session.scalar(select(RoundNote).where(
        RoundNote.account_id == account_id, RoundNote.round_id == round_id))
    if current is None and row is None:
        raise TradeError('ROUND_NOT_FOUND', '交易回合不存在', 404)
    if row and row.revision != payload['expected_revision']:
        raise TradeError('REVISION_CONFLICT', '回合摘要已被修改，请刷新后核对', 409)
    if row is None and payload['expected_revision'] != 0:
        raise TradeError('REVISION_CONFLICT', '回合摘要尚未建立', 409)
    if projection['status'] != 'fresh':
        raise TradeError('PROJECTION_NOT_FRESH', '交易统计正在重算，请稍后保存回合摘要', 409)
    now = utc_now()
    before = _data(row, round_id, current, projection, {'daily': [], 'periods': []}) if row else None
    if row is None:
        row = RoundNote(id=new_id(), account_id=account_id, round_id=round_id,
                        summary='', trade_ids_json='[]', revision=1,
                        created_at=now, updated_at=now)
        session.add(row)
    else:
        row.revision += 1
    row.summary = payload['summary']
    row.trade_ids_json = json.dumps(current['trade_ids'] if current else json.loads(row.trade_ids_json))
    row.updated_at = now
    session.flush()
    result = _data(row, round_id, current, projection, _related(session, account_id, current))
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='round_note',
                           entity_id=row.id, operation='update' if before else 'create',
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(result, ensure_ascii=False), created_at=now))
    return result
