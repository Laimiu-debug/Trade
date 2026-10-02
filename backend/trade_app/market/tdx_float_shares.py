"""Read TDX base.dbf float shares with source identity; never modify TDX files."""
from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone
from pathlib import Path

from trade_app.market.tdx import normalize_tdx_symbol
from trade_app.platform.types import TradeError


_HEADER = struct.Struct('<BBBBIHH20x')
_FIELD_SIZE = 32
_MAX_BYTES = 50 * 1024 * 1024


def _fields(raw: bytes, header_len: int) -> dict[str, tuple[int, int]]:
    fields = {}
    offset, position = _HEADER.size, 1
    while offset + _FIELD_SIZE <= header_len and raw[offset] != 0x0D:
        descriptor = raw[offset:offset + _FIELD_SIZE]
        name = descriptor[:11].split(b'\x00', 1)[0].decode('ascii', 'ignore').strip()
        length = descriptor[16]
        if name:
            fields[name] = (position, length)
        position += length
        offset += _FIELD_SIZE
    return fields


def _ascii(raw: bytes) -> str:
    return raw.decode('ascii', 'ignore').strip('\x00').strip()


def read_tdx_float_shares(tdx_root: Path | None, requested: list[str]) -> dict:
    if tdx_root is None:
        raise TradeError('TDX_ROOT_NOT_CONFIGURED', '请在系统设置 → 行情来源选择通达信目录或扫描本机', 409)
    wanted = {''.join(normalize_tdx_symbol(symbol)): symbol for symbol in requested}
    base = tdx_root.parent if tdx_root.name.lower() == 'vipdoc' else tdx_root
    path = next((item for item in (base / 'T0002' / 'hq_cache' / 'base.dbf', base / 'base.dbf')
                 if item.is_file()), None)
    if path is None:
        raise TradeError('TDX_FLOAT_SHARES_NOT_FOUND', '通达信目录中没有 base.dbf', 404)
    before = path.stat()
    if before.st_size < _HEADER.size or before.st_size > _MAX_BYTES:
        raise TradeError('TDX_FLOAT_SHARES_INVALID', '通达信 base.dbf 长度无效或超过 50 MB')
    raw = path.read_bytes()
    after = path.stat()
    if len(raw) != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise TradeError('TDX_SOURCE_CHANGED', '通达信股本文件在读取期间变化，请重试', 409)
    _, _, _, _, count, header_len, record_len = _HEADER.unpack_from(raw)
    if count <= 0 or header_len <= _HEADER.size or record_len <= 1 or header_len > len(raw):
        raise TradeError('TDX_FLOAT_SHARES_INVALID', '通达信 base.dbf 表头无效')
    fields = _fields(raw, header_len)
    if not {'SC', 'GPDM', 'LTAG'} <= fields.keys():
        raise TradeError('TDX_FLOAT_SHARES_FIELDS_MISSING', '通达信股本文件缺少 SC / GPDM / LTAG 字段')
    if any(position + length > record_len for position, length in fields.values()):
        raise TradeError('TDX_FLOAT_SHARES_INVALID', '通达信股本字段超出记录长度')
    result = {}
    market_codes = {'0': 'sz', '1': 'sh', '2': 'bj'}
    for index in range(count):
        start = header_len + index * record_len
        record = raw[start:start + record_len]
        if len(record) != record_len:
            break
        if record[0] == 0x2A:
            continue
        def field(name: str) -> str:
            position, length = fields[name]
            return _ascii(record[position:position + length])
        market = market_codes.get(field('SC'))
        code = field('GPDM')
        if market is None or len(code) != 6 or not code.isdigit():
            continue
        symbol = market + code
        if symbol not in wanted:
            continue
        try:
            shares = float(field('LTAG')) * 10_000
        except ValueError:
            continue
        if not 0 < shares < float('inf'):
            continue
        result[symbol] = shares
    return {'values': result,
            'requested_values': {raw: result[canonical] for canonical, raw in wanted.items()
                                 if canonical in result},
            'missing': sorted(set(wanted) - result.keys()),
            'source': {'filename': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                       'bytes': len(raw),
                       'file_mtime_utc': datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat(),
                       'as_of_quality': 'unknown'}}
