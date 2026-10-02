"""Immutable local market snapshots; metadata in SQLite, bar contents in separate files."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.domain import normalize_bars
from trade_app.market.models import MarketDataset
from trade_app.platform.types import TradeError, utc_now


def dataset_data(row: MarketDataset) -> dict:
    return {'id': row.id, 'symbol': row.symbol, 'provider': row.provider,
            'adjustment': row.adjustment, 'first_date': row.first_date,
            'last_date': row.last_date, 'bar_count': row.bar_count,
            'availability_quality': row.availability_quality,
            'created_at': row.created_at}


def import_dataset(session: Session, data_dir: Path, body: dict) -> dict:
    if body['adjustment'] != 'none':
        raise TradeError('ADJUSTMENT_VERSION_REQUIRED', '当前仅支持不复权样本；复权需要固定因子版本')
    symbol = body['symbol'].strip().upper()
    if not symbol:
        raise TradeError('INVALID_SYMBOL', '证券代码不能为空')
    bars = normalize_bars(body['bars'])
    return store_dataset(session, data_dir, symbol, bars, 'manual_import',
                         body['adjustment'])


def store_dataset(session: Session, data_dir: Path, symbol: str, bars: list[dict],
                  provider: str, adjustment: str, source: dict | None = None) -> dict:
    manifest = {'schema_version': 1, 'symbol': symbol, 'provider': provider,
                'adjustment': adjustment, 'bars': bars}
    if source is not None:
        manifest['source'] = source
    contents = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    dataset_id = hashlib.sha256(contents).hexdigest()
    market_dir = data_dir / 'market'
    market_dir.mkdir(parents=True, exist_ok=True)
    path = market_dir / f'{dataset_id}.json'
    if path.exists():
        if path.read_bytes() != contents:
            raise TradeError('MARKET_DATA_CORRUPT', '现有行情文件与内容哈希不一致', 409)
    else:
        fd, temporary = tempfile.mkstemp(prefix='.market-', suffix='.tmp', dir=market_dir)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(contents)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    row = session.get(MarketDataset, dataset_id)
    if row is None:
        quality = 'provided_availability' if all(bar['available_at'] for bar in bars) else 'historical_availability_unknown'
        row = MarketDataset(id=dataset_id, symbol=symbol, provider=provider,
                            adjustment=adjustment, first_date=bars[0]['event_date'],
                            last_date=bars[-1]['event_date'], bar_count=len(bars),
                            availability_quality=quality, created_at=utc_now())
        session.add(row)
        session.flush()
    return dataset_data(row)


def list_datasets(session: Session) -> list[dict]:
    return [dataset_data(row) for row in session.scalars(select(MarketDataset).order_by(
        MarketDataset.created_at.desc(), MarketDataset.id))]


def get_dataset(session: Session, data_dir: Path, dataset_id: str) -> dict:
    row = session.get(MarketDataset, dataset_id)
    if row is None:
        raise TradeError('DATASET_NOT_FOUND', '行情数据集不存在', 404)
    path = data_dir / 'market' / f'{dataset_id}.json'
    if not path.is_file():
        raise TradeError('MARKET_DATA_MISSING', '行情内容文件缺失', 409)
    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != dataset_id:
        raise TradeError('MARKET_DATA_CORRUPT', '行情内容校验失败', 409)
    manifest = json.loads(contents)
    return {**dataset_data(row), 'bars': manifest['bars'], 'source': manifest.get('source')}


def close_on_date(session: Session, data_dir: Path, dataset_id: str, raw_day: str) -> dict:
    try:
        requested = date.fromisoformat(raw_day).isoformat()
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '查询日期须为 YYYY-MM-DD') from exc
    dataset = get_dataset(session, data_dir, dataset_id)
    bar = next((item for item in dataset['bars'] if item['event_date'] == requested), None)
    if bar is None:
        raise TradeError('MARKET_CLOSE_NOT_FOUND', '所选样本在该日期无收盘价；不会使用相邻日期替代', 404)
    return {'dataset_id': dataset_id, 'symbol': dataset['symbol'],
            'provider': dataset['provider'], 'adjustment': dataset['adjustment'],
            'requested_date': requested, 'trading_date': bar['event_date'],
            'close': bar['close'], 'available_at': bar['available_at'],
            'availability_quality': dataset['availability_quality'],
            'source': dataset['source']}
