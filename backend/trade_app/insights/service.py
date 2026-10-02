from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.insights.models import DailyInspirationSelection, InspirationCard
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now


def _data(row: InspirationCard) -> dict:
    return {'id': row.id, 'content': row.content, 'tags': json.loads(row.tags_json),
            'revision': row.revision, 'created_at': row.created_at}


def list_cards(session: Session) -> list[dict]:
    return [_data(row) for row in session.scalars(select(InspirationCard).where(
        InspirationCard.deleted_at.is_(None)).order_by(InspirationCard.created_at.desc(),
                                                     InspirationCard.id.desc()))]


def _tags(raw: list[str]) -> list[str]:
    tags = list(dict.fromkeys(value.strip() for value in raw if value.strip()))
    if len(tags) > 20 or any(len(tag) > 40 for tag in tags):
        raise TradeError('INVALID_CARD_TAGS', '标签最多 20 个，每项不超过 40 字')
    return tags


def create_card(session: Session, payload: dict) -> dict:
    content = payload['content'].strip()
    if not content:
        raise TradeError('EMPTY_CARD', '灵感内容不能为空')
    now = utc_now()
    row = InspirationCard(id=new_id(), content=content,
                          tags_json=json.dumps(_tags(payload.get('tags', [])), ensure_ascii=False),
                          revision=1, created_at=now, updated_at=now, deleted_at=None)
    session.add(row)
    result = _data(row)
    session.add(AuditEvent(id=new_id(), account_id=None, entity_type='inspiration_card',
                           entity_id=row.id, operation='create', before_json=None,
                           after_json=json.dumps(result, ensure_ascii=False), created_at=now))
    return result


def delete_card(session: Session, card_id: str, expected_revision: int) -> dict:
    row = session.get(InspirationCard, card_id)
    if row is None or row.deleted_at is not None:
        raise TradeError('CARD_NOT_FOUND', '灵感卡片不存在', 404)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '灵感卡片已变化，请刷新后重试', 409)
    before = _data(row)
    row.deleted_at = utc_now()
    row.updated_at = row.deleted_at
    row.revision += 1
    session.add(AuditEvent(id=new_id(), account_id=None, entity_type='inspiration_card',
                           entity_id=row.id, operation='delete',
                           before_json=json.dumps(before, ensure_ascii=False), after_json=None,
                           created_at=utc_now()))
    return {'id': row.id, 'deleted': True}


def daily_old_card(session: Session, day: str) -> dict | None:
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError(day)
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '日期需要 YYYY-MM-DD') from exc
    selection = session.get(DailyInspirationSelection, day)
    if selection is not None:
        selected = session.get(InspirationCard, selection.card_id)
        return _data(selected) if selected and selected.deleted_at is None else None
    eligible = session.scalars(select(InspirationCard).where(
        InspirationCard.deleted_at.is_(None), InspirationCard.created_at < day
    ).order_by(InspirationCard.id)).all()
    if not eligible:
        return None
    index = int(hashlib.sha256(day.encode('ascii')).hexdigest(), 16) % len(eligible)
    selected = eligible[index]
    session.add(DailyInspirationSelection(review_date=day, card_id=selected.id,
                                          created_at=utc_now()))
    return _data(selected)
