"""Immutable sync behavior shared by online daily-bar adapters."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.domain import normalize_bars
from trade_app.market.models import MarketDataset
from trade_app.market.service import get_dataset, store_dataset
from trade_app.market.symbols import storage_market_symbol
from trade_app.market.tdx import normalize_tdx_symbol
from trade_app.platform.types import TradeError


def prepare_online_sync(session: Session, data_dir: Path, body: dict, *, provider: str,
                        fetcher: Callable[[str, date, date], list[dict]],
                        parser: Callable[[str, list[dict], date, date], list[dict]],
                        source: dict) -> dict:
    market, code = normalize_tdx_symbol(body['symbol'])
    stored_symbol = storage_market_symbol(market, code)
    start = date.fromisoformat(body['start_date'])
    end = date.fromisoformat(body['end_date'])
    if start > end or (end - start).days > 365 * 15:
        raise TradeError('INVALID_SYNC_RANGE', '同步起止日期无效或范围超过 15 年')
    previous_row = session.scalar(select(MarketDataset).where(
        MarketDataset.provider == provider, MarketDataset.symbol == stored_symbol,
        MarketDataset.adjustment == 'none').order_by(
        MarketDataset.created_at.desc(), MarketDataset.id.desc()).limit(1))
    previous = get_dataset(session, data_dir, previous_row.id) if previous_row else None
    fetch_start = start
    if body['mode'] == 'incremental' and previous:
        overlap_start = date.fromisoformat(previous['last_date']) - timedelta(days=14)
        fetch_start = max(start, overlap_start)
    if fetch_start > end:
        raise TradeError('INVALID_SYNC_RANGE', '增量截止日早于已有样本的重取区间')
    raw_rows = fetcher(f'{market}{code}', fetch_start, end)
    rows = parser(code, raw_rows, fetch_start, end)
    if not rows:
        if previous and body['mode'] == 'incremental':
            return {'existing_id': previous_row.id, 'bars': None, 'code': stored_symbol,
                    'provider': provider, 'fetched_count': 0,
                    'fetch_start': fetch_start.isoformat()}
        raise TradeError('MARKET_PROVIDER_EMPTY', '该行情来源在此日期范围未返回日线', 404)
    merged = {row['event_date']: row for row in previous['bars']} if previous and body['mode'] == 'incremental' else {}
    merged.update({row['event_date']: row for row in rows})
    bars = normalize_bars([merged[key] for key in sorted(merged)[-2000:]])
    content_hash = hashlib.sha256(json.dumps(bars, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    return {'existing_id': previous_row.id if previous_row else None, 'bars': bars,
            'code': stored_symbol, 'provider': provider,
            'source': {**source, 'canonical_rows_sha256': content_hash,
                       'historical_available_at': 'unknown'},
            'fetched_count': len(rows), 'fetch_start': fetch_start.isoformat()}


def save_online_sync(session: Session, data_dir: Path, prepared: dict) -> dict:
    if prepared['bars'] is None:
        existing_id = prepared['existing_id']
        if existing_id is None:
            raise TradeError('MARKET_PROVIDER_EMPTY', '没有可保存的行情', 404)
        previous = get_dataset(session, data_dir, existing_id)
        return {'dataset': {key: value for key, value in previous.items() if key != 'bars'},
                'fetched_count': 0, 'changed': False,
                'fetch_start': prepared['fetch_start']}
    dataset = store_dataset(session, data_dir, prepared['code'], prepared['bars'],
                            prepared['provider'], 'none', prepared['source'])
    return {'dataset': dataset, 'fetched_count': prepared['fetched_count'],
            'changed': dataset['id'] != prepared['existing_id'],
            'fetch_start': prepared['fetch_start']}
