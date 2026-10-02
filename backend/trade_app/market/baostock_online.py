"""BaoStock daily-bar adapter with process-isolated network access."""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from trade_app.market.online_common import prepare_online_sync, save_online_sync
from trade_app.platform.types import TradeError
from trade_app.platform.runtime import backend_root, child_environment, module_command


PROVIDER = 'baostock_online'
ADAPTER_VERSION = 'baostock-query-history-daily-none-v1'
MAX_OUTPUT_BYTES = 5_000_000


def fetch_baostock_rows(symbol: str, start: date, end: date, data_dir: Path) -> list[dict]:
    if symbol.startswith('bj'):
        raise TradeError('MARKET_PROVIDER_UNSUPPORTED', 'BaoStock 当前仅支持沪深证券代码')
    worker_symbol = f'{symbol[:2]}.{symbol[2:]}'
    environment = child_environment()
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    with tempfile.TemporaryDirectory(prefix='.baostock-', dir=data_dir) as temporary:
        output = Path(temporary) / 'bars.json'
        try:
            result = subprocess.run(module_command('trade_app.market.baostock_worker',
                worker_symbol, start.isoformat(), end.isoformat(), str(output),
            ), cwd=backend_root(), env=environment, capture_output=True, timeout=45, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise TradeError('MARKET_PROVIDER_TIMEOUT', 'BaoStock 行情请求超过 45 秒', 503) from exc
        except OSError as exc:
            raise TradeError('MARKET_PROVIDER_UNAVAILABLE', 'BaoStock 行情进程无法启动', 503) from exc
        if result.returncode != 0 or not output.is_file():
            raise TradeError('MARKET_PROVIDER_FAILED', 'BaoStock 登录或行情请求失败', 503)
        if output.stat().st_size > MAX_OUTPUT_BYTES:
            raise TradeError('MARKET_PROVIDER_TOO_LARGE', 'BaoStock 返回行情超过大小限制', 503)
        try:
            rows = json.loads(output.read_text(encoding='utf-8'))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TradeError('INVALID_PROVIDER_DATA', 'BaoStock 返回内容无效', 503) from exc
        if not isinstance(rows, list):
            raise TradeError('INVALID_PROVIDER_DATA', 'BaoStock 返回格式无效', 503)
        return rows


def _volume_shares(value: object) -> int:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradeError('INVALID_PROVIDER_VOLUME', 'BaoStock 成交量无效') from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise TradeError('INVALID_PROVIDER_VOLUME', 'BaoStock 成交量不是非负整数股')
    return int(number)


def _parse_rows(code: str, rows: list[dict], start: date, end: date) -> list[dict]:
    result = []
    for raw in rows:
        try:
            day = date.fromisoformat(str(raw['date'])[:10])
            if str(raw['code']).strip().split('.')[-1] != code:
                raise TradeError('MARKET_SYMBOL_MISMATCH', 'BaoStock 返回了其他证券的行情')
            if not start <= day <= end:
                raise TradeError('MARKET_PROVIDER_DATE_MISMATCH', 'BaoStock 返回日期超出请求范围')
            if str(raw.get('tradestatus', '1')).strip() == '0':
                continue
            if all(not str(raw.get(field, '')).strip() for field in ('open', 'high', 'low', 'close')):
                continue
            result.append({'event_date': day.isoformat(), 'open': str(raw['open']),
                           'high': str(raw['high']), 'low': str(raw['low']),
                           'close': str(raw['close']),
                           'volume': _volume_shares(raw['volume']),
                           'amount': str(raw['amount']), 'available_at': None})
        except (KeyError, ValueError) as exc:
            raise TradeError('INVALID_PROVIDER_DATA', 'BaoStock 行情缺少必需列或日期无效') from exc
    if len(result) != len({row['event_date'] for row in result}):
        raise TradeError('BAR_ORDER', 'BaoStock 返回了重复日期')
    return sorted(result, key=lambda row: row['event_date'])


def prepare_baostock_sync(session: Session, data_dir: Path, body: dict,
                          fetcher: Callable[[str, date, date], list[dict]] | None = None) -> dict:
    def get_rows(symbol: str, start: date, end: date) -> list[dict]:
        return (fetcher or (lambda stock, first, last: fetch_baostock_rows(stock, first, last, data_dir)))(
            symbol, start, end)
    return prepare_online_sync(session, data_dir, body, provider=PROVIDER,
                               fetcher=get_rows, parser=_parse_rows,
                               source={'format': ADAPTER_VERSION,
                                       'upstream': 'BaoStock query_history_k_data_plus',
                                       'volume_unit': 'shares',
                                       'upstream_volume_unit': 'shares', 'amount_unit': 'CNY',
                                       'suspension_handling': 'skip_no_trade_rows_without_price_fill'})


def save_baostock_sync(session: Session, data_dir: Path, prepared: dict) -> dict:
    return save_online_sync(session, data_dir, prepared)
