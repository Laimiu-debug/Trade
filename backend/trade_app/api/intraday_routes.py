"""Explicit fetch outside SQLite writes; same request key never refetches."""
import hashlib
import json
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.market import intraday_online as online, intraday_service as service
from trade_app.platform.models import IdempotencyKey
from trade_app.platform.types import TradeError

router = APIRouter(prefix='/api/v1/market/intraday', tags=['market-intraday'])
_lock = threading.Lock()
_active: set[str] = set()


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(min_length=6, max_length=16)
    date: str = Field(min_length=10, max_length=10)


@router.get('/capabilities')
def capabilities():
    return {'data': {'provider': 'eastmoney_online', 'adapter_version': online.VERSION,
                     'stock_exchanges': ['sh', 'sz'], 'index_symbols': list(online.INDEX_SYMBOLS),
                     'recent_trading_days_requested': 5, 'timezone': 'Asia/Shanghai',
                     'volume_unit': 'lots', 'amount_unit': 'CNY', 'adjustment': 'none',
                     'availability': 'historical_availability_unknown', 'display_only': True}}


@router.get('/snapshots')
def history(symbol: str, date: str | None = None, session: Session = Depends(read_session)):
    return {'data': service.list_snapshots(session, symbol, date)}


@router.get('/snapshots/{identifier}')
def detail(identifier: str, session: Session = Depends(read_session)):
    return {'data': service.get_snapshot(session, identifier)}


@router.delete('/snapshots/{identifier}')
def delete(request: Request, identifier: str):
    return _write(request, f'market/intraday/snapshots/{identifier}/delete', {},
                  lambda session: service.delete_snapshot(session, identifier))


@router.post('/fetch')
def fetch(request: Request, payload: FetchRequest):
    body = payload.model_dump(mode='json')
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key')
    scope = 'market/intraday/fetch'
    request_hash = hashlib.sha256(service.encode(body).encode()).hexdigest()
    with _lock:
        if key in _active:
            raise TradeError('INTRADAY_REQUEST_RUNNING', '该分时请求正在执行，请等待或稍后读取缓存', 409)
        _active.add(key)
    try:
        with request.app.state.db_factory() as session:
            existing = session.get(IdempotencyKey, (scope, key))
            if existing:
                if existing.request_hash != request_hash:
                    raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
                return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
        prepared = online.prepare(body['symbol'], body['date'])
        return _write(request, scope, body, lambda session: service.save_snapshot(session, prepared))
    finally:
        with _lock:
            _active.discard(key)
