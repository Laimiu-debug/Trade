"""Manual final scores are versioned separately from any future AI suggestion."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.symbols import market_symbol_key
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.score_models import ReviewScoreSheet
from trade_app.reviews.service import validate_day
from trade_app.trading.models import Trade
from trade_app.trading.service import account_or_error


DIMENSIONS = {'daily': ('position', 'drawdown', 'discipline', 'entry', 'exit', 'emotion'),
              'trade': ('timing', 'discipline', 'emotion'),
              't_group': ('timing', 'discipline', 'emotion')}


def _current_trades(session: Session, account_id: str, day: str,
                    ids: list[str]) -> list[Trade]:
    rows = {row.id: row for row in session.scalars(select(Trade).where(
        Trade.account_id == account_id, Trade.trade_date == day,
        Trade.voided_at.is_(None), Trade.id.in_(ids)))} if ids else {}
    if len(ids) != len(set(ids)) or len(rows) != len(ids):
        raise TradeError('INVALID_SCORE_TRADES', '评分关联的成交不存在、日期不匹配或重复')
    return [rows[item] for item in ids]


def _data(row: ReviewScoreSheet, current_ids: list[str]) -> dict:
    saved_ids = json.loads(row.trade_ids_json)
    return {'id': row.id, 'review_date': row.review_date, 'scope': row.scope,
            'subject_id': row.subject_id, 'trade_ids': saved_ids,
            'association_changed': saved_ids != current_ids,
            'scores': json.loads(row.scores_json), 'comment': row.comment,
            'revision': row.revision, 'updated_at': row.updated_at}


def list_scores(session: Session, account_id: str, day: str) -> list[dict]:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    current_ids = set(session.scalars(select(Trade.id).where(
        Trade.account_id == account_id, Trade.trade_date == day,
        Trade.voided_at.is_(None))))
    rows = session.scalars(select(ReviewScoreSheet).where(
        ReviewScoreSheet.account_id == account_id,
        ReviewScoreSheet.review_date == day).order_by(
        ReviewScoreSheet.scope, ReviewScoreSheet.created_at, ReviewScoreSheet.id))
    return [_data(row, [item for item in json.loads(row.trade_ids_json) if item in current_ids])
            for row in rows]


def save_scores(session: Session, account_id: str, day: str, body: dict) -> dict:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    scope = body['scope']
    ids = body['trade_ids']
    trades = _current_trades(session, account_id, day, ids)
    if scope == 'daily':
        if ids:
            raise TradeError('INVALID_SCORE_TRADES', '整日评分不绑定单笔成交')
        subject_id = day
    elif scope == 'trade':
        if len(ids) != 1:
            raise TradeError('INVALID_SCORE_TRADES', '逐笔评分须绑定一笔成交')
        subject_id = ids[0]
    else:
        if len(ids) < 2 or len({market_symbol_key(row.symbol) for row in trades}) != 1 or {row.side for row in trades} != {'buy', 'sell'}:
            raise TradeError('INVALID_T_GROUP', '做 T 分组须包含同日同股的买入和卖出')
        ids = sorted(ids)
        subject_id = 'group:' + hashlib.sha256('|'.join(ids).encode()).hexdigest()[:24]
    allowed = set(DIMENSIONS[scope])
    if set(body['scores']) - allowed:
        raise TradeError('INVALID_SCORE_DIMENSION', '评分维度不适用于当前范围')
    row = session.scalar(select(ReviewScoreSheet).where(
        ReviewScoreSheet.account_id == account_id,
        ReviewScoreSheet.review_date == day,
        ReviewScoreSheet.scope == scope,
        ReviewScoreSheet.subject_id == subject_id))
    if body['expected_revision'] != (row.revision if row else 0):
        raise TradeError('REVISION_CONFLICT', '评分已在其他页面更新', 409)
    before = _data(row, json.loads(row.trade_ids_json)) if row else None
    now = utc_now()
    if row is None:
        row = ReviewScoreSheet(id=new_id(), account_id=account_id, review_date=day,
                               scope=scope, subject_id=subject_id,
                               trade_ids_json='[]', scores_json='{}', comment='',
                               revision=1, created_at=now, updated_at=now)
        session.add(row)
    else:
        row.revision += 1
    existing = json.loads(row.scores_json)
    merged = {}
    for dimension, entry in body['scores'].items():
        prior = existing.get(dimension, {})
        merged[dimension] = {**{key: value for key, value in prior.items() if key.startswith('ai_')},
                             'ai': prior.get('ai'),
                             'final': entry['final'], 'comment': entry['comment'],
                             'final_source': 'manual' if entry['final'] is not None else None}
    row.trade_ids_json = json.dumps(ids)
    row.scores_json = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    row.comment = body['comment']
    row.updated_at = now
    after = _data(row, ids)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='review_score_sheet',
                           entity_id=row.id, operation='update' if before else 'create',
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(after, ensure_ascii=False), created_at=now))
    return after
