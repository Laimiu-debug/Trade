"""Read real TDX one-minute bars without inventing missing prices or turnover."""
from __future__ import annotations

import hashlib
import struct
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from trade_app.market.tdx import MAX_SOURCE_BYTES, normalize_tdx_symbol
from trade_app.platform.types import TradeError


LC1_RECORD = struct.Struct('<HHfffffII')


def read_tdx_intraday(tdx_root: Path | None, raw_symbol: str, raw_day: str) -> dict:
    market, code = normalize_tdx_symbol(raw_symbol)
    if tdx_root is None:
        raise TradeError('TDX_ROOT_NOT_CONFIGURED', '请在系统设置 → 行情来源选择通达信目录或扫描本机', 409)
    try:
        target = date.fromisoformat(raw_day)
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '分时日期须为 YYYY-MM-DD') from exc
    root = tdx_root
    if root.name.lower() != 'vipdoc' and (root / 'vipdoc').is_dir():
        root = root / 'vipdoc'
    folder = root / market / 'minline'
    path = next((item for item in (folder / f'{market}{code}.lc1', folder / f'{code}.lc1')
                 if item.is_file()), None)
    if path is None:
        raise TradeError('TDX_INTRADAY_NOT_FOUND', f'通达信目录中没有 {market}{code} 分时文件', 404)
    stat = path.stat()
    if stat.st_size < LC1_RECORD.size or stat.st_size > MAX_SOURCE_BYTES or stat.st_size % LC1_RECORD.size:
        raise TradeError('INVALID_TDX_INTRADAY', '通达信分时文件长度无效或超过 100 MB')
    contents = path.read_bytes()
    after = path.stat()
    if len(contents) != stat.st_size or after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
        raise TradeError('TDX_SOURCE_CHANGED', '通达信文件在读取期间变化，请重试', 409)
    points: list[dict] = []
    for offset in range(0, len(contents), LC1_RECORD.size):
        raw_date, raw_time, opened, high, low, closed, amount, volume, _ = LC1_RECORD.unpack_from(contents, offset)
        year = (raw_date >> 11) + 2004
        month = (raw_date & 0x07FF) // 100
        day = (raw_date & 0x07FF) % 100
        if (year, month, day) != (target.year, target.month, target.day):
            continue
        try:
            date(year, month, day)
        except ValueError as exc:
            raise TradeError('INVALID_TDX_INTRADAY', '分时文件日期无效') from exc
        if raw_time >= 1440 or min(opened, high, low, closed) <= 0 or volume < 0:
            raise TradeError('INVALID_TDX_INTRADAY', '分时文件价格、时间或成交量无效')
        prices = [Decimal(str(value)) for value in (opened, high, low, closed, amount)]
        if not all(value.is_finite() for value in prices) or prices[4] < 0:
            raise TradeError('INVALID_TDX_INTRADAY', '分时文件包含无效浮点值')
        points.append({'time': f'{raw_time // 60:02d}:{raw_time % 60:02d}',
                       'open': format(prices[0], '.4f'), 'high': format(prices[1], '.4f'),
                       'low': format(prices[2], '.4f'), 'close': format(prices[3], '.4f'),
                       'amount': format(prices[4], '.2f'), 'volume': volume})
        if len(points) > 500:
            raise TradeError('INVALID_TDX_INTRADAY', '同一天分时记录超过 500 条')
    if not points:
        raise TradeError('TDX_INTRADAY_DATE_NOT_FOUND', f'没有 {raw_day} 的真实分时记录', 404)
    points.sort(key=lambda point: point['time'])
    if len({point['time'] for point in points}) != len(points):
        raise TradeError('INVALID_TDX_INTRADAY', '同一分钟存在重复分时记录')
    return {'symbol': market + code, 'date': target.isoformat(), 'provider': 'tdx_local',
            'timezone': 'Asia/Shanghai', 'volume_unit': 'source_native', 'amount_unit': 'CNY',
            'price_unit': 'index_points' if (market == 'sh' and code.startswith('000')) or (market == 'sz' and code.startswith('399')) else 'CNY',
            'quality': 'original_one_minute', 'availability_quality': 'historical_availability_unknown',
            'source': {'format': 'tdx_lc1_v1', 'filename': path.name,
                       'sha256': hashlib.sha256(contents).hexdigest(), 'bytes': stat.st_size,
                       'file_mtime_utc': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()},
            'points': points}
