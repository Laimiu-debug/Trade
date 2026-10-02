"""Account-scoped human labels for immutable simulation fills."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, decimal_text, money_text, new_id, utc_now
from trade_app.reviews.tag_models import SimFillTagAssignment, SimReviewTag
from trade_app.trading.sim_models import SimFill, SimOrder
from trade_app.trading.simulation import sim_account


def _audit(session: Session, account_id: str, entity_type: str, entity_id: str,
           operation: str, before: dict | None, after: dict | None) -> None:
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type=entity_type,
                           entity_id=entity_id, operation=operation,
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(after, ensure_ascii=False) if after else None,
                           created_at=utc_now()))


def _tag_data(row: SimReviewTag) -> dict:
    return {'id': row.id, 'type': row.tag_type, 'name': row.name,
            'active': bool(row.active), 'revision': row.revision}


def list_tags(session: Session, account_id: str) -> list[dict]:
    sim_account(session, account_id)
    return [_tag_data(row) for row in session.scalars(select(SimReviewTag).where(
        SimReviewTag.account_id == account_id).order_by(
        SimReviewTag.tag_type, SimReviewTag.created_at, SimReviewTag.id))]


def create_tag(session: Session, account_id: str, body: dict) -> dict:
    sim_account(session, account_id, mutable=True)
    name = body['name'].strip()
    if not name:
        raise TradeError('INVALID_TAG_NAME', '标签名称不能为空')
    for row in session.scalars(select(SimReviewTag).where(
            SimReviewTag.account_id == account_id, SimReviewTag.tag_type == body['type'],
            SimReviewTag.active == 1)):
        if row.name.casefold() == name.casefold():
            raise TradeError('TAG_EXISTS', '同类标签名称已存在', 409)
    now = utc_now()
    row = SimReviewTag(id=new_id(), account_id=account_id, tag_type=body['type'],
                       name=name, active=1, revision=1, created_at=now, updated_at=now)
    session.add(row)
    result = _tag_data(row)
    _audit(session, account_id, 'sim_review_tag', row.id, 'create', None, result)
    return result


def delete_tag(session: Session, account_id: str, tag_id: str, expected_revision: int) -> dict:
    sim_account(session, account_id, mutable=True)
    row = session.get(SimReviewTag, tag_id)
    if row is None or row.account_id != account_id:
        raise TradeError('TAG_NOT_FOUND', '标签不存在', 404)
    if not row.active:
        raise TradeError('TAG_DELETED', '标签已删除', 409)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '标签版本已变化', 409)
    before = _tag_data(row)
    row.active = 0
    row.revision += 1
    row.updated_at = utc_now()
    result = _tag_data(row)
    _audit(session, account_id, 'sim_review_tag', row.id, 'delete', before, result)
    return result


def _fill(session: Session, account_id: str, fill_id: str) -> SimFill:
    fill = session.get(SimFill, fill_id)
    if fill is None or fill.account_id != account_id:
        raise TradeError('SIM_FILL_NOT_FOUND', '模拟成交不存在', 404)
    return fill


def _assignment_data(row: SimFillTagAssignment | None, fill_id: str) -> dict:
    return {'fill_id': fill_id, 'emotion_tag_id': row.emotion_tag_id if row else None,
            'reason_tag_ids': json.loads(row.reason_tag_ids_json) if row else [],
            'revision': row.revision if row else 0}


def list_assignments(session: Session, account_id: str) -> list[dict]:
    sim_account(session, account_id)
    return [_assignment_data(row, row.fill_id) for row in session.scalars(
        select(SimFillTagAssignment).where(SimFillTagAssignment.account_id == account_id))]


def save_assignment(session: Session, account_id: str, fill_id: str, body: dict) -> dict:
    sim_account(session, account_id, mutable=True)
    _fill(session, account_id, fill_id)
    row = session.get(SimFillTagAssignment, fill_id)
    if body['expected_revision'] != (row.revision if row else 0):
        raise TradeError('REVISION_CONFLICT', '成交标签版本已变化', 409)
    reason_ids = body['reason_tag_ids']
    if len(reason_ids) != len(set(reason_ids)):
        raise TradeError('DUPLICATE_TAG', '原因标签不能重复')
    all_ids = set(reason_ids)
    if body['emotion_tag_id']:
        all_ids.add(body['emotion_tag_id'])
    tags = {tag.id: tag for tag in session.scalars(select(SimReviewTag).where(
        SimReviewTag.id.in_(all_ids)))} if all_ids else {}
    if len(tags) != len(all_ids):
        raise TradeError('TAG_NOT_FOUND', '所选标签不存在', 404)
    for tag_id in all_ids:
        tag = tags[tag_id]
        expected_type = 'emotion' if tag_id == body['emotion_tag_id'] else 'reason'
        already_selected = bool(row) and (tag_id == row.emotion_tag_id or tag_id in json.loads(row.reason_tag_ids_json))
        if tag.account_id != account_id or (not tag.active and not already_selected) or tag.tag_type != expected_type:
            raise TradeError('INVALID_TAG_ASSIGNMENT', '标签类型、账户或状态不匹配')
    before = _assignment_data(row, fill_id) if row else None
    if row is None:
        row = SimFillTagAssignment(fill_id=fill_id, account_id=account_id,
                                   emotion_tag_id=None, reason_tag_ids_json='[]',
                                   revision=1, updated_at=utc_now())
        session.add(row)
    else:
        row.revision += 1
    row.emotion_tag_id = body['emotion_tag_id']
    row.reason_tag_ids_json = json.dumps(reason_ids)
    row.updated_at = utc_now()
    result = _assignment_data(row, fill_id)
    _audit(session, account_id, 'sim_fill_tags', fill_id,
           'update' if before else 'create', before, result)
    return result


def tag_stats(session: Session, account_id: str, date_from: str | None,
              date_to: str | None) -> dict:
    sim_account(session, account_id)
    for day in (date_from, date_to):
        if day:
            try:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError(day)
            except ValueError as exc:
                raise TradeError('INVALID_DATE', '日期必须使用 YYYY-MM-DD 格式') from exc
    if date_from and date_to and date_from > date_to:
        raise TradeError('INVALID_DATE_RANGE', '结束日期不能早于开始日期')
    tags = list(session.scalars(select(SimReviewTag).where(
        SimReviewTag.account_id == account_id)))
    stats = {tag.id: {'fill_count': 0, 'sell_count': 0, 'win_count': 0,
                      'gross_minor': 0, 'sell_gross_minor': 0,
                      'realized_pnl_minor': 0} for tag in tags}
    query = select(SimFill, SimOrder, SimFillTagAssignment).join(
        SimOrder, SimOrder.id == SimFill.order_id).join(
        SimFillTagAssignment, SimFillTagAssignment.fill_id == SimFill.id).where(
        SimFill.account_id == account_id, SimFillTagAssignment.account_id == account_id)
    if date_from:
        query = query.where(SimFill.fill_date >= date_from)
    if date_to:
        query = query.where(SimFill.fill_date <= date_to)
    for fill, order, assignment in session.execute(query):
        selected = ([assignment.emotion_tag_id] if assignment.emotion_tag_id else []) + json.loads(
            assignment.reason_tag_ids_json)
        for tag_id in selected:
            if tag_id not in stats:
                continue
            item = stats[tag_id]
            item['fill_count'] += 1
            item['gross_minor'] += fill.gross_minor
            if order.side == 'sell' and fill.realized_pnl_minor is not None:
                item['sell_count'] += 1
                item['win_count'] += int(fill.realized_pnl_minor > 0)
                item['sell_gross_minor'] += fill.gross_minor
                item['realized_pnl_minor'] += fill.realized_pnl_minor
    data = []
    for tag in tags:
        item = stats[tag.id]
        sells = item['sell_count']
        data.append({**_tag_data(tag), 'fill_count': item['fill_count'],
                     'sell_count': sells, 'win_count': item['win_count'],
                     'win_rate_pct': decimal_text(Decimal(item['win_count']) * 100 / sells, 2) if sells else None,
                     'gross': money_text(item['gross_minor']),
                     'realized_pnl': money_text(item['realized_pnl_minor']),
                     'realized_return_pct': decimal_text(Decimal(item['realized_pnl_minor']) * 100 /
                                                         item['sell_gross_minor'], 2) if item['sell_gross_minor'] else None})
    return {'data': data, 'date_from': date_from, 'date_to': date_to,
            'method': '每笔成交的总金额完整计入其每个标签；胜率仅以卖出成交计算，收益率=卖出已实现盈亏/卖出成交额。买入未实现盈亏不计入收益。'}
