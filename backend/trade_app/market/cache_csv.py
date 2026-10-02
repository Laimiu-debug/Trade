"""Freeze existing AkShare/Baostock daily cache CSVs as market datasets."""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from trade_app.market.domain import normalize_bars
from trade_app.market.service import store_dataset
from trade_app.market.symbols import normalize_market_symbol, storage_market_symbol
from trade_app.market.tdx import MAX_SOURCE_BYTES, normalize_tdx_symbol
from trade_app.platform.types import TradeError


REQUIRED_COLUMNS = {'date', 'open', 'high', 'low', 'close', 'volume'}


def import_cache_csv(session: Session, data_dir: Path, provider: str,
                     raw_symbol: str, cache_roots: dict[str, Path | None]) -> dict:
    if provider not in {'akshare', 'baostock'}:
        raise TradeError('INVALID_MARKET_PROVIDER', '行情来源无效')
    market, code = normalize_tdx_symbol(raw_symbol)
    root = cache_roots.get(provider)
    if root is None:
        raise TradeError('CACHE_ROOT_NOT_CONFIGURED', '请先配置该行情来源的缓存目录', 409)
    path = next((candidate for candidate in (root / f'{market}{code}.csv', root / f'{code}.csv')
                 if candidate.is_file()), None)
    if path is None:
        raise TradeError('MARKET_CACHE_NOT_FOUND', f'没有找到 {market}{code} 的本地行情缓存', 404)
    before = path.stat()
    if before.st_size < 32 or before.st_size > MAX_SOURCE_BYTES:
        raise TradeError('INVALID_MARKET_CACHE', '行情缓存为空或超过 100 MB')
    contents = path.read_bytes()
    after = path.stat()
    if len(contents) != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise TradeError('MARKET_SOURCE_CHANGED', '行情缓存读取期间变化，请重试', 409)
    try:
        decoded = contents.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise TradeError('INVALID_MARKET_CACHE', '行情缓存必须是 UTF-8 CSV') from exc
    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames or not REQUIRED_COLUMNS <= set(reader.fieldnames):
        raise TradeError('INVALID_MARKET_CACHE', '行情缓存缺少日期、开高低收或成交量列')
    all_rows = list(reader)
    if len(all_rows) < 2:
        raise TradeError('INVALID_BAR_COUNT', '行情缓存至少需要 2 个交易日')
    rows = all_rows[-2000:]
    bars = []
    for row in rows:
        if (row.get('symbol') and row['symbol'].strip() != code
                and normalize_market_symbol(row['symbol']) != (market, code)):
            raise TradeError('MARKET_SYMBOL_MISMATCH', '行情缓存中的证券代码与请求不一致')
        try:
            volume = int(row['volume'])
        except (TypeError, ValueError) as exc:
            raise TradeError('INVALID_VOLUME', '成交量必须是非负整数') from exc
        bars.append({'event_date': row['date'], 'open': row['open'],
                     'high': row['high'], 'low': row['low'], 'close': row['close'],
                     'volume': volume, 'amount': row.get('amount') or None,
                     'available_at': None})
    normalized = normalize_bars(bars)
    source = {'format': 'legacy_daily_csv_v1', 'filename': path.name,
              'sha256': hashlib.sha256(contents).hexdigest(), 'bytes': len(contents),
              'source_row_count': len(all_rows), 'selected_row_count': len(rows),
              'file_mtime_utc': datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()}
    return store_dataset(session, data_dir, storage_market_symbol(market, code), normalized,
                         f'{provider}_cache', 'none', source)
