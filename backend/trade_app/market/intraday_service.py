"""Immutable display-only minute snapshots; reads never fetch from the network."""
import hashlib
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.intraday_models import IntradaySnapshot
from trade_app.market.intraday_online import identity
from trade_app.platform.types import TradeError


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def save_snapshot(session: Session, prepared: dict) -> dict:
    payload = encode(prepared)
    identifier = hashlib.sha256(payload.encode()).hexdigest()
    if session.get(IntradaySnapshot, identifier) is None:
        session.add(IntradaySnapshot(id=identifier, symbol=prepared['symbol'], date=prepared['date'],
                                    as_of_at=prepared['as_of_at'], point_count=len(prepared['points']), payload_json=payload))
        session.flush()
    return {'id': identifier, **prepared}


def get_snapshot(session: Session, identifier: str) -> dict:
    row = session.get(IntradaySnapshot, identifier)
    if row is None:
        raise TradeError('INTRADAY_CACHE_NOT_FOUND', '分时缓存不存在', 404)
    if hashlib.sha256(row.payload_json.encode()).hexdigest() != row.id:
        raise TradeError('INTRADAY_CACHE_CORRUPTED', '分时缓存校验失败，请重新获取', 409)
    return {'id': row.id, **json.loads(row.payload_json)}


def list_snapshots(session: Session, raw_symbol: str, raw_date: str | None = None) -> list[dict]:
    symbol = identity(raw_symbol)['symbol']
    query = select(IntradaySnapshot.id, IntradaySnapshot.symbol, IntradaySnapshot.date,
                   IntradaySnapshot.as_of_at, IntradaySnapshot.point_count).where(IntradaySnapshot.symbol == symbol)
    if raw_date is not None:
        try:
            if date.fromisoformat(raw_date).isoformat() != raw_date:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise TradeError('INVALID_DATE', '分时日期须为 YYYY-MM-DD') from exc
        query = query.where(IntradaySnapshot.date == raw_date)
    return [dict(row) for row in session.execute(query.order_by(IntradaySnapshot.as_of_at.desc(), IntradaySnapshot.id).limit(100)).mappings()]


def delete_snapshot(session: Session, identifier: str) -> dict:
    row = session.get(IntradaySnapshot, identifier)
    if row is None:
        raise TradeError('INTRADAY_CACHE_NOT_FOUND', '分时缓存不存在', 404)
    session.delete(row)
    session.flush()
    return {'id': identifier, 'deleted': True}
