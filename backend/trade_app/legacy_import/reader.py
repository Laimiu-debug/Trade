"""Read uploaded bytes only. Never instantiate either original application's store."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal

from trade_app.platform.types import TradeError

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 5000
REDACTED = '[REDACTED: legacy credential]'


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('JSON 包含重复字段')
        result[key] = value
    return result


def parse_json(text: str):
    return json.loads(text, parse_float=lambda value: str(Decimal(value)),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数字')),
                      object_pairs_hook=_pairs)


def _sensitive(key: str) -> bool:
    key = re.sub('[^a-z0-9]', '', key.lower())
    return any(part in key for part in ('apikey', 'accesstoken', 'refreshtoken', 'password', 'secret', 'authorization', 'credential')) or key in {'token', 'bearer'}


def redact(value, path: str = '', removed: list[str] | None = None, depth: int = 0):
    removed = removed if removed is not None else []
    if depth > 40:
        raise ValueError('资料嵌套超过 40 层')
    if isinstance(value, dict):
        setting_secret = _sensitive(str(value.get('key', ''))) or _sensitive(str(value.get('name', '')))
        result = {}
        for key, child in value.items():
            sub = f'{path}/{key}'
            if _sensitive(key) or (setting_secret and key in {'value', 'content'}):
                if child not in (None, '', REDACTED):
                    removed.append(sub)
                result[key] = REDACTED if child not in (None, '') else child
            else:
                result[key] = redact(child, sub, removed, depth + 1)
        return result
    if isinstance(value, list):
        return [redact(child, f'{path}/{index}', removed, depth + 1) for index, child in enumerate(value)]
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            nested = parse_json(value)
        except (ValueError, RecursionError):
            if re.search(r'(?i)(api[_-]?key|access[_-]?token|password|secret|authorization)', value):
                removed.append(path + '/$unparsed-credential')
                return REDACTED
            return value
        return canonical(redact(nested, path + '/$json', removed, depth + 1))
    if isinstance(value, float):
        if not Decimal(str(value)).is_finite():
            raise ValueError('SQLite 包含非有限数字')
        return str(value)
    if isinstance(value, str) and re.search(r'(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*=|\bbearer\s+[a-z0-9_.-]{8,}|https?://[^\s/:]+:[^\s/@]+@', value):
        removed.append(path + '/$embedded-credential')
        return REDACTED
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError('不支持的二进制字段；请保留原文件并使用原软件导出 JSON')


def _sqlite(raw: bytes) -> tuple[str, dict]:
    with closing(sqlite3.connect(':memory:')) as db:
        db.deserialize(raw)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1
            return int(ticks > 10000)

        db.set_progress_handler(progress, 1000)
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite 完整性检查失败；请退出旧软件并导出一致性备份')
        tables = {row[0]: row[1] for row in db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if len(tables) > 60 or any(not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]*', name) or 'VIRTUAL TABLE' in (sql or '').upper() for name, sql in tables.items()):
            raise ValueError('SQLite 包含不支持的表定义')
        if {'capital_flows', 'snapshots', 'trades', 'daily_reviews'} <= set(tables):
            source = 'laimiu_sqlite'
        elif 'wyckoff_daily_events' in tables:
            source = 'final_event_sqlite'
        else:
            raise ValueError('不是支持的 LaimiuTrade 账本或 final-trade 事件仓 SQLite')
        result, total = {}, 0
        for name in sorted(tables):
            cursor = db.execute(f'SELECT * FROM "{name}" LIMIT {MAX_ROWS + 1}')
            columns = [item[0] for item in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor]
            total += len(rows)
            if total > MAX_ROWS:
                raise ValueError(f'本批最多 {MAX_ROWS} 条，请先在旧软件分批导出')
            result[name] = rows
        return source, result


def read_upload(body: dict) -> dict:
    name = str(body.get('filename', '')).strip()
    if not name or len(name) > 180 or any(char in name for char in '/\\\r\n\x00'):
        raise TradeError('LEGACY_INVALID_FILENAME', '仅接受文件名，不接受服务器或目录路径')
    encoded = body.get('content_base64', '')
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES * 4 // 3 + 4):
        raise TradeError('LEGACY_FILE_TOO_LARGE', '单个资料文件最多 16 MiB')
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError('文件为空或超过 16 MiB')
        if raw.startswith(b'SQLite format 3\x00'):
            source, payload = _sqlite(raw)
        else:
            payload = parse_json(raw.decode('utf-8-sig'))
            if not isinstance(payload, dict):
                raise ValueError('资料根节点必须为对象')
            if 'exported_at' in payload and any(key in payload for key in ('capital_flows', 'trades', 'snapshots')):
                source = 'laimiu_json'
            elif 'schema_version' in payload and {'account', 'orders', 'fills', 'lots'} <= payload.keys():
                source = 'final_sim_json'
            elif 'schema_version' in payload and any(key in payload for key in ('annotations', 'screener_runs', 'event_judgment', 'signal_etf_backtests')):
                source = 'final_app_json'
            else:
                raise ValueError('未识别旧软件资料格式，请选择原始备份 / app_state / sim_state 文件')
        removed: list[str] = []
        logical = redact(payload, removed=removed)
        # Raw SQLite pages and raw JSON bytes are intentionally never persisted.
        return {'source': source, 'filename': name, 'source_sha256': hashlib.sha256(raw).hexdigest(),
                'source_bytes': len(raw), 'logical_sha256': digest(logical),
                'redacted_paths': removed, 'payload': logical}
    except (ValueError, TypeError, UnicodeError, RecursionError, sqlite3.Error) as exc:
        raise TradeError('LEGACY_INVALID_FILE', str(exc) if isinstance(exc, ValueError) else '文件编码或 SQLite 格式无效') from exc
