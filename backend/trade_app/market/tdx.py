"""Read TDX daily files into immutable snapshots without changing the source."""
from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from trade_app.market.domain import normalize_bars
from trade_app.market.service import store_dataset
from trade_app.market.symbols import normalize_market_symbol, storage_market_symbol
from trade_app.platform.types import TradeError


DAY_RECORD = struct.Struct('<IIIIIfII')
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def normalize_tdx_symbol(raw: str) -> tuple[str, str]:
    return normalize_market_symbol(raw)


def _source_path(tdx_root: Path | None, market: str, code: str) -> Path:
    if tdx_root is None:
        raise TradeError('TDX_ROOT_NOT_CONFIGURED', '请在系统设置 → 行情来源选择通达信目录或扫描本机', 409)
    root = tdx_root
    if root.name.lower() != 'vipdoc' and (root / 'vipdoc').is_dir():
        root = root / 'vipdoc'
    folder = root / market / 'lday'
    for name in (f'{market}{code}.day', f'{code}.day'):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    raise TradeError('TDX_DAY_NOT_FOUND', f'通达信目录中没有 {market}{code} 日线文件', 404)


def _price(value: int) -> str:
    return format(Decimal(value) / 100, '.4f')


def import_tdx_day(session: Session, data_dir: Path, raw_symbol: str,
                   tdx_root: Path | None, max_bars: int = 2000) -> dict:
    if not 251 <= max_bars <= 2000:
        raise TradeError('INVALID_BAR_LIMIT', '通达信冻结日线数量须在 251 至 2000 之间')
    market, code = normalize_tdx_symbol(raw_symbol)
    path = _source_path(tdx_root, market, code)
    stat = path.stat()
    if stat.st_size < DAY_RECORD.size * 2 or stat.st_size % DAY_RECORD.size or stat.st_size > MAX_SOURCE_BYTES:
        raise TradeError('INVALID_TDX_DAY', '通达信日线文件长度无效或超过 100 MB')
    contents = path.read_bytes()
    after = path.stat()
    if len(contents) != stat.st_size or after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
        raise TradeError('TDX_SOURCE_CHANGED', '通达信文件在读取期间变化，请重试', 409)
    digest = hashlib.sha256(contents).hexdigest()
    raw = contents[-DAY_RECORD.size * max_bars:]
    bars = []
    for offset in range(0, len(raw), DAY_RECORD.size):
        day, opened, high, low, closed, amount, volume, _ = DAY_RECORD.unpack_from(raw, offset)
        day_text = str(day)
        if len(day_text) != 8 or min(opened, high, low, closed) <= 0 or not (Decimal(str(amount)).is_finite() and amount >= 0):
            raise TradeError('INVALID_TDX_DAY', f'通达信日线文件第 {offset // DAY_RECORD.size + 1} 条记录无效')
        bars.append({'event_date': f'{day_text[:4]}-{day_text[4:6]}-{day_text[6:]}',
                     'open': _price(opened), 'high': _price(high),
                     'low': _price(low), 'close': _price(closed),
                     'volume': volume, 'amount': format(Decimal(str(amount)), 'f'),
                     'available_at': None})
    normalized = normalize_bars(bars)
    source = {'format': 'tdx_day_v1', 'filename': path.name,
              'sha256': digest, 'bytes': stat.st_size,
              'file_mtime_utc': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}
    if max_bars != 2000:
        source['last_n_bars'] = max_bars
    return store_dataset(session, data_dir, storage_market_symbol(market, code), normalized, 'tdx_local', 'none', source)
