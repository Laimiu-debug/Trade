from __future__ import annotations

import json
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.symbols import market_symbol_aliases, validated_market_symbol_key
from trade_app.platform.types import TradeError, money_minor, money_text, new_id, price_text, price_units, utc_now
from trade_app.trading.models import PendingTrade, Trade
from trade_app.trading.real_fees import resolve_trade_fee
from trade_app.trading.service import account_or_error, create_trade


def _fields(payload: dict) -> dict:
    day = str(payload['trade_date'])
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '交易日期无效') from exc
    symbol = str(payload['symbol']).strip().upper()
    if not symbol:
        raise TradeError('INVALID_SYMBOL', '证券代码不能为空')
    validated_market_symbol_key(symbol)
    quantity = payload['quantity']
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise TradeError('INVALID_QUANTITY', '数量必须为正整数')
    if payload['side'] not in ('buy', 'sell'):
        raise TradeError('INVALID_SIDE', '交易方向无效')
    return dict(trade_date=day, symbol=symbol, name=str(payload.get('name', '')).strip(),
                side=payload['side'], quantity=quantity, price_units=price_units(payload['price']),
                fee_minor=money_minor(payload.get('fee', '0'), '费用', allow_zero=True),
                note=str(payload.get('note', '')))


def _data(row: PendingTrade, duplicates: list[str] | None = None) -> dict:
    return dict(id=row.id, account_id=row.account_id, trade_date=row.trade_date, symbol=row.symbol,
                name=row.name, side=row.side, quantity=row.quantity, price=price_text(row.price_units),
                fee=money_text(row.fee_minor), calculated_fee=money_text(row.calculated_fee_minor),
                fee_source=row.fee_source, fee_rule_version=row.fee_rule_version,
                fee_breakdown=json.loads(row.fee_breakdown_json) if row.fee_breakdown_json else None,
                note=row.note, source=row.source,
                source_text=row.source_text, status=row.status, confirmed_trade_id=row.confirmed_trade_id,
                revision=row.revision, duplicate_trade_ids=duplicates or [], created_at=row.created_at)


def _duplicates(session: Session, row: PendingTrade) -> list[str]:
    return list(session.scalars(select(Trade.id).where(
        Trade.account_id == row.account_id, Trade.trade_date == row.trade_date,
        func.upper(func.trim(Trade.symbol)).in_(market_symbol_aliases(row.symbol)),
        Trade.side == row.side, Trade.quantity == row.quantity,
        Trade.price_units == row.price_units, Trade.voided_at.is_(None)).order_by(Trade.sequence)))


def _row(session: Session, account_id: str, pending_id: str) -> PendingTrade:
    account_or_error(session, account_id, real=True)
    row = session.get(PendingTrade, pending_id)
    if row is None or row.account_id != account_id:
        raise TradeError('PENDING_TRADE_NOT_FOUND', '待确认交易不存在', 404)
    return row


def _audit(session: Session, row: PendingTrade, operation: str, before: dict | None, after: dict | None) -> None:
    session.add(AuditEvent(id=new_id(), account_id=row.account_id, entity_type='pending_trade',
                           entity_id=row.id, operation=operation,
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(after, ensure_ascii=False) if after else None,
                           created_at=utc_now()))


def list_pending_trades(session: Session, account_id: str) -> list[dict]:
    account_or_error(session, account_id, real=True)
    rows = session.scalars(select(PendingTrade).where(
        PendingTrade.account_id == account_id, PendingTrade.status == 'pending'
    ).order_by(PendingTrade.created_at, PendingTrade.id)).all()
    return [_data(row, _duplicates(session, row)) for row in rows]


def create_pending_trade(session: Session, account_id: str, payload: dict) -> dict:
    account_or_error(session, account_id, real=True)
    fields = _fields(payload)
    fields.update(resolve_trade_fee(session, account_id, payload))
    now = utc_now()
    row = PendingTrade(id=new_id(), account_id=account_id, **fields, source='manual', source_text=None,
                       status='pending', confirmed_trade_id=None, revision=1,
                       created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    result = _data(row, _duplicates(session, row))
    _audit(session, row, 'create', None, result)
    return result


def update_pending_trade(session: Session, account_id: str, pending_id: str, payload: dict) -> dict:
    row = _row(session, account_id, pending_id)
    if row.status != 'pending':
        raise TradeError('PENDING_TRADE_CLOSED', '该待确认交易已处理', 409)
    if row.revision != payload['expected_revision']:
        raise TradeError('REVISION_CONFLICT', '待确认交易已被修改，请刷新后核对', 409)
    fields = _fields(payload)
    fields.update(resolve_trade_fee(session, account_id, payload))
    before = _data(row)
    for key, value in fields.items():
        setattr(row, key, value)
    row.revision += 1
    row.updated_at = utc_now()
    result = _data(row, _duplicates(session, row))
    _audit(session, row, 'update', before, result)
    return result


def discard_pending_trade(session: Session, account_id: str, pending_id: str,
                          expected_revision: int) -> dict:
    row = _row(session, account_id, pending_id)
    if row.status != 'pending':
        raise TradeError('PENDING_TRADE_CLOSED', '该待确认交易已处理', 409)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '待确认交易已被修改，请刷新后核对', 409)
    before = _data(row)
    row.status = 'discarded'
    row.revision += 1
    row.updated_at = utc_now()
    _audit(session, row, 'discard', before, _data(row))
    return {'id': row.id, 'discarded': True}


def confirm_pending_trade(session: Session, account_id: str, pending_id: str,
                          expected_revision: int, acknowledge_duplicate: bool) -> dict:
    row = _row(session, account_id, pending_id)
    if row.status == 'confirmed':
        return {'id': row.id, 'trade_id': row.confirmed_trade_id, 'confirmed': True}
    if row.status != 'pending':
        raise TradeError('PENDING_TRADE_CLOSED', '该待确认交易已处理', 409)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '待确认交易已被修改，请刷新后核对', 409)
    duplicates = _duplicates(session, row)
    if duplicates and not acknowledge_duplicate:
        raise TradeError('POSSIBLE_DUPLICATE', '存在相同日期、代码、方向、数量和价格的正式交易，请核对后确认', 409)
    before = _data(row, duplicates)
    trade = create_trade(session, account_id, dict(
        trade_date=row.trade_date, symbol=row.symbol, name=row.name, side=row.side,
        quantity=row.quantity, price=price_text(row.price_units), fee=money_text(row.fee_minor),
        note=row.note, fee_mode='snapshot', fee_snapshot={
            'fee_minor': row.fee_minor, 'calculated_fee_minor': row.calculated_fee_minor,
            'fee_source': row.fee_source, 'fee_rule_version': row.fee_rule_version,
            'fee_breakdown_json': row.fee_breakdown_json}))
    row.status = 'confirmed'
    row.confirmed_trade_id = trade['id']
    row.revision += 1
    row.updated_at = utc_now()
    _audit(session, row, 'confirm', before, _data(row))
    return {'id': row.id, 'trade_id': trade['id'], 'confirmed': True}


def confirm_pending_batch(session: Session, account_id: str, items: list[dict]) -> dict:
    account_or_error(session, account_id, real=True)
    if len(items) > 500:
        raise TradeError('BATCH_TOO_LARGE', '每次最多确认 500 条')
    if len({item['id'] for item in items}) != len(items):
        raise TradeError('DUPLICATE_BATCH_ID', '批量确认包含重复记录')
    results = []
    for item in items:
        try:
            with session.begin_nested():
                result = confirm_pending_trade(session, account_id, item['id'],
                                               item['expected_revision'], item.get('acknowledge_duplicate', False))
                session.flush()
            results.append({'id': item['id'], 'status': 'confirmed', 'trade_id': result['trade_id']})
        except TradeError as exc:
            results.append({'id': item['id'], 'status': 'failed', 'code': exc.code, 'message': exc.message})
    return {'results': results, 'confirmed_count': sum(row['status'] == 'confirmed' for row in results)}
