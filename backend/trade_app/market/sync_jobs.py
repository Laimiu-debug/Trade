"""Durable, bounded batch ingestion of immutable daily-bar snapshots."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from trade_app.market.akshare_online import prepare_akshare_sync, save_akshare_sync
from trade_app.market.baostock_online import prepare_baostock_sync, save_baostock_sync
from trade_app.market.models import MarketSyncJob
from trade_app.market.tdx import normalize_tdx_symbol
from trade_app.platform.types import TradeError, utc_now


_PROVIDERS = {
    'baostock': (prepare_baostock_sync, save_baostock_sync),
    'akshare': (prepare_akshare_sync, save_akshare_sync),
}
_FALLBACK_CODES = {
    'MARKET_PROVIDER_TIMEOUT', 'MARKET_PROVIDER_FAILED', 'MARKET_PROVIDER_UNAVAILABLE',
    'MARKET_PROVIDER_UNSUPPORTED', 'MARKET_PROVIDER_EMPTY', 'MARKET_PROVIDER_TOO_LARGE',
    'INVALID_PROVIDER_DATA', 'INVALID_PROVIDER_VOLUME',
}


def _view(row: MarketSyncJob) -> dict:
    request = json.loads(row.request_json)
    results = json.loads(row.results_json)
    return {'id': row.id, 'state': 'cancelling' if row.state == 'running' and row.cancel_requested else row.state,
            'provider': request['provider'], 'mode': request['mode'],
            'start_date': request['start_date'], 'end_date': request['end_date'],
            'symbols': request['symbols'], 'total': len(request['symbols']),
            'completed': len(results), 'results': results,
            'created_at': row.created_at, 'updated_at': row.updated_at}


def create_sync_job(session: Session, body: dict) -> dict:
    symbols = []
    for raw in body['symbols']:
        market, code = normalize_tdx_symbol(raw)
        canonical = f'{market}{code}'
        if canonical not in symbols:
            symbols.append(canonical)
    if not symbols:
        raise TradeError('EMPTY_SYNC_JOB', '请填写至少一个证券代码')
    from datetime import date
    start, end = date.fromisoformat(body['start_date']), date.fromisoformat(body['end_date'])
    if start > end or (end - start).days > 365 * 15:
        raise TradeError('INVALID_SYNC_RANGE', '同步起止日期无效或范围超过 15 年')
    active = session.scalar(select(func.count()).select_from(MarketSyncJob).where(
        MarketSyncJob.state.in_(('queued', 'running')))) or 0
    if active >= 10:
        raise TradeError('MARKET_SYNC_QUEUE_FULL', '行情同步队列已满', 429)
    request = {**body, 'symbols': symbols}
    now = utc_now()
    row = MarketSyncJob(id=uuid.uuid4().hex, request_json=json.dumps(request, ensure_ascii=False),
                        state='queued', next_index=0, results_json='[]', cancel_requested=0,
                        created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _view(row)


def list_sync_jobs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(MarketSyncJob).order_by(
        MarketSyncJob.created_at.desc(), MarketSyncJob.id.desc()).limit(50))]


def get_sync_job(session: Session, job_id: str) -> dict:
    row = session.get(MarketSyncJob, job_id)
    if row is None:
        raise TradeError('MARKET_SYNC_NOT_FOUND', '行情同步任务不存在', 404)
    return _view(row)


def cancel_sync_job(session: Session, job_id: str) -> dict:
    row = session.get(MarketSyncJob, job_id)
    if row is None:
        raise TradeError('MARKET_SYNC_NOT_FOUND', '行情同步任务不存在', 404)
    if row.state == 'queued':
        row.state = 'cancelled'
    elif row.state == 'running':
        row.cancel_requested = 1
    elif row.state != 'cancelled':
        raise TradeError('MARKET_SYNC_FINAL', '已结束的行情同步任务不能取消', 409)
    row.updated_at = utc_now()
    session.flush()
    return _view(row)


def retry_failed_symbols(session: Session, job_id: str) -> dict:
    row = session.get(MarketSyncJob, job_id)
    if row is None:
        raise TradeError('MARKET_SYNC_NOT_FOUND', '行情同步任务不存在', 404)
    if row.state != 'partial_failed':
        raise TradeError('MARKET_SYNC_NOT_RETRYABLE', '该任务没有可重试的失败证券', 409)
    failed = [item['symbol'] for item in json.loads(row.results_json) if item['state'] == 'failed']
    if not failed:
        raise TradeError('MARKET_SYNC_NOT_RETRYABLE', '该任务没有可重试的失败证券', 409)
    request = json.loads(row.request_json)
    return create_sync_job(session, {**request, 'symbols': failed})


def recover_sync_jobs(factory: sessionmaker) -> None:
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(MarketSyncJob).where(MarketSyncJob.state == 'running')):
            row.state = 'cancelled' if row.cancel_requested else 'queued'
            row.cancel_requested = 0
            row.updated_at = utc_now()


def _sync_one(factory: sessionmaker, data_dir: Path, body: dict) -> dict:
    priority = body.get('provider_order', ['baostock', 'akshare']) if body['provider'] == 'auto' else [body['provider']]
    errors = []
    for index, provider in enumerate(priority):
        prepare, save = _PROVIDERS[provider]
        try:
            with factory() as session:
                prepared = prepare(session, data_dir, body)
        except TradeError as exc:
            errors.append({'provider': provider, 'code': exc.code})
            if index == len(priority) - 1 or exc.code not in _FALLBACK_CODES:
                break
            continue
        except Exception as exc:
            errors.append({'provider': provider, 'code': 'MARKET_PROVIDER_FAILED',
                           'detail': type(exc).__name__})
            if index == len(priority) - 1:
                break
            continue
        try:
            with factory.begin() as session:
                saved = save(session, data_dir, prepared)
        except TradeError as exc:
            return {'symbol': body['symbol'], 'state': 'failed',
                    'errors': [*errors, {'provider': provider, 'code': exc.code}]}
        except Exception as exc:
            return {'symbol': body['symbol'], 'state': 'failed',
                    'errors': [*errors, {'provider': provider, 'code': 'LOCAL_STORAGE_FAILED',
                                         'detail': type(exc).__name__}]}
        return {'symbol': body['symbol'], 'state': 'succeeded',
                'provider': provider, 'dataset_id': saved['dataset']['id'],
                'changed': saved['changed'], 'fetched_count': saved['fetched_count'],
                'fallback_errors': errors}
    return {'symbol': body['symbol'], 'state': 'failed', 'errors': errors}


def process_one_sync_symbol(factory: sessionmaker, data_dir: Path) -> bool:
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.scalar(select(MarketSyncJob).where(MarketSyncJob.state.in_(('queued', 'running')))
                             .order_by(MarketSyncJob.created_at, MarketSyncJob.id).limit(1))
        if row is None:
            return False
        if row.cancel_requested:
            row.state = 'cancelled'
            row.updated_at = utc_now()
            return True
        request = json.loads(row.request_json)
        index = row.next_index
        if index >= len(request['symbols']):
            row.state = 'succeeded' if all(item['state'] == 'succeeded' for item in json.loads(row.results_json)) else 'partial_failed'
            row.updated_at = utc_now()
            return True
        row.state = 'running'
        row.updated_at = utc_now()
        job_id = row.id
        symbol = request['symbols'][index]
    result = _sync_one(factory, data_dir, {**request, 'symbol': symbol})
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.get(MarketSyncJob, job_id)
        if row is None:
            return True
        results = json.loads(row.results_json)
        results.append(result)
        row.results_json = json.dumps(results, ensure_ascii=False)
        row.next_index = index + 1
        if row.cancel_requested:
            row.state = 'cancelled'
        elif row.next_index >= len(request['symbols']):
            row.state = 'succeeded' if all(item['state'] == 'succeeded' for item in results) else 'partial_failed'
        else:
            row.state = 'queued'
        row.updated_at = utc_now()
    return True
