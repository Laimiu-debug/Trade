"""Read TDX TNF names once before a full-market scan; never modify source files."""
from __future__ import annotations

import hashlib
from pathlib import Path

from trade_app.platform.types import TradeError

HEADER_SIZE = 50
RECORD_SIZE = 360
MAX_BYTES = 20_000_000
TNF_FILES = {'sh': 'shs.tnf', 'sz': 'szs.tnf', 'bj': 'bjs.tnf'}


def read_tdx_names(tdx_root: Path | None, symbols: list[str]) -> dict:
    if tdx_root is None:
        return {'names': {}, 'sources': []}
    root = tdx_root.parent if tdx_root.name.lower() == 'vipdoc' else tdx_root
    directory = root / 'T0002' / 'hq_cache'
    requested = set(symbols)
    names, sources = {}, []
    for market in sorted({symbol[:2] for symbol in symbols}):
        filename = TNF_FILES.get(market)
        if not filename:
            continue
        path = directory / filename
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                raise TradeError('TDX_TNF_TOO_LARGE', '通达信名称文件超过读取上限', 409)
            data = path.read_bytes()
        except OSError as exc:
            raise TradeError('TDX_TNF_READ_FAILED', '通达信名称文件读取失败', 409) from exc
        sources.append({'file': filename, 'sha256': hashlib.sha256(data).hexdigest(),
                        'bytes': len(data)})
        for offset in range(HEADER_SIZE, len(data) - RECORD_SIZE + 1, RECORD_SIZE):
            record = data[offset:offset + RECORD_SIZE]
            code = record[:6].decode('ascii', 'ignore').strip('\x00 ')
            symbol = market + code
            if len(code) != 6 or not code.isdigit() or symbol not in requested:
                continue
            name = record[31:47].split(b'\x00', 1)[0].decode('gbk', 'ignore').strip()
            if name:
                names[symbol] = name
    return {'names': names, 'sources': sources}
