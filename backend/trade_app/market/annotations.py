"""Versioned manual stock annotations, isolated from generated analysis."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.annotation_models import StockAnnotation
from trade_app.market.tdx import normalize_tdx_symbol
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now


def _symbol(value: str) -> str:
    market, code = normalize_tdx_symbol(value)
    return market + code


def _data(row: StockAnnotation) -> dict:
    return {'symbol': row.symbol, 'start_date': row.start_date, 'stage': row.stage,
            'trend_class': row.trend_class, 'decision': row.decision, 'notes': row.notes,
            'updated_by': 'manual', 'revision': row.revision, 'updated_at': row.updated_at}


def get_annotation(session: Session, raw_symbol: str) -> dict | None:
    row = session.get(StockAnnotation, _symbol(raw_symbol))
    return _data(row) if row is not None else None


def list_annotations(session: Session) -> list[dict]:
    return [_data(row) for row in session.scalars(select(StockAnnotation).order_by(StockAnnotation.symbol))]


def save_annotation(session: Session, raw_symbol: str, body: dict) -> dict:
    symbol = _symbol(raw_symbol)
    try:
        start_date = date.fromisoformat(body['start_date']).isoformat()
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '人工启动日须为有效日期') from exc
    row = session.get(StockAnnotation, symbol)
    expected = body['expected_revision']
    if expected != (row.revision if row else 0):
        raise TradeError('REVISION_CONFLICT', '人工标注已变化，请重新读取后再保存', 409)
    before = _data(row) if row else None
    if row is None:
        row = StockAnnotation(symbol=symbol, start_date=start_date, stage=body['stage'],
                              trend_class=body['trend_class'], decision=body['decision'],
                              notes=body['notes'].strip(), revision=1, updated_at=utc_now())
        session.add(row)
    else:
        row.start_date = start_date
        row.stage = body['stage']
        row.trend_class = body['trend_class']
        row.decision = body['decision']
        row.notes = body['notes'].strip()
        row.revision += 1
        row.updated_at = utc_now()
    result = _data(row)
    session.add(AuditEvent(id=new_id(), account_id=None, entity_type='stock_annotation',
                           entity_id=symbol, operation='create' if before is None else 'update',
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(result, ensure_ascii=False), created_at=utc_now()))
    return result


def delete_annotation(session: Session, raw_symbol: str, expected_revision: int) -> dict:
    symbol = _symbol(raw_symbol)
    row = session.get(StockAnnotation, symbol)
    if row is None:
        raise TradeError('ANNOTATION_NOT_FOUND', '人工标注不存在', 404)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '人工标注已变化，请重新读取后再删除', 409)
    before = _data(row)
    session.delete(row)
    session.add(AuditEvent(id=new_id(), account_id=None, entity_type='stock_annotation',
                           entity_id=symbol, operation='delete',
                           before_json=json.dumps(before, ensure_ascii=False),
                           after_json=None, created_at=utc_now()))
    return {'symbol': symbol, 'deleted': True}
