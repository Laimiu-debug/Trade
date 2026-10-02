"""Fetch AKShare daily bars before the short immutable-snapshot write transaction."""
from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from trade_app.market.online_common import prepare_online_sync, save_online_sync
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError


PROVIDER = 'akshare_online'
ADAPTER_VERSION = 'akshare-stock-zh-a-hist-none-v1'
_fetch_slots = threading.BoundedSemaphore(2)


def fetch_akshare_rows(code: str, start: date, end: date) -> list[dict]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise TradeError('MARKET_PROVIDER_UNAVAILABLE', '未安装 AKShare 行情依赖', 503) from exc
    try:
        with _fetch_slots:
            frame = ak.stock_zh_a_hist(symbol=code, period='daily',
                                       start_date=start.strftime('%Y%m%d'),
                                       end_date=end.strftime('%Y%m%d'),
                                       adjust='', timeout=15)
        return [] if frame is None else frame.to_dict('records')
    except Exception as exc:
        raise TradeError('MARKET_PROVIDER_FAILED', 'AKShare 行情请求失败，请稍后重试或改用本地来源', 503) from exc


def _integer_shares(hands: object) -> int:
    try:
        value = Decimal(str(hands)) * 100
    except (InvalidOperation, ValueError) as exc:
        raise TradeError('INVALID_PROVIDER_VOLUME', 'AKShare 成交量无效') from exc
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        raise TradeError('INVALID_PROVIDER_VOLUME', 'AKShare 成交量无法准确换算为股')
    return int(value)


def _parse_rows(code: str, rows: list[dict], start: date, end: date) -> list[dict]:
    result = []
    for raw in rows:
        try:
            day = date.fromisoformat(str(raw['日期'])[:10])
            actual_code = str(raw['股票代码']).strip().split('.')[0].zfill(6)
            if actual_code != code:
                raise TradeError('MARKET_SYMBOL_MISMATCH', 'AKShare 返回了其他证券的行情')
            if not start <= day <= end:
                raise TradeError('MARKET_PROVIDER_DATE_MISMATCH', 'AKShare 返回日期超出请求范围')
            result.append({'event_date': day.isoformat(), 'open': str(raw['开盘']),
                           'high': str(raw['最高']), 'low': str(raw['最低']),
                           'close': str(raw['收盘']),
                           'volume': _integer_shares(raw['成交量']),
                           'amount': str(raw['成交额']), 'available_at': None})
        except (KeyError, ValueError) as exc:
            raise TradeError('INVALID_PROVIDER_DATA', 'AKShare 行情缺少必需列或日期无效') from exc
    if len(result) != len({row['event_date'] for row in result}):
        raise TradeError('BAR_ORDER', 'AKShare 返回了重复日期')
    return sorted(result, key=lambda row: row['event_date'])


def prepare_akshare_sync(session: Session, data_dir: Path, body: dict,
                         fetcher: Callable[[str, date, date], list[dict]] | None = None) -> dict:
    try:
        normalize_a_share_symbol(body['symbol'])
    except TradeError as exc:
        raise TradeError('MARKET_PROVIDER_UNSUPPORTED', '当前 AKShare 日线适配器仅支持 A 股股票；指数请改用 BaoStock 或本地通达信') from exc
    def get_rows(symbol: str, start: date, end: date) -> list[dict]:
        # stock_zh_a_hist accepts a stock code, so sh000001 must never silently
        # fetch SZ000001 and be frozen with an index identity.
        return (fetcher or fetch_akshare_rows)(symbol[-6:], start, end)
    return prepare_online_sync(session, data_dir, body, provider=PROVIDER,
                               fetcher=get_rows, parser=_parse_rows,
                               source={'format': ADAPTER_VERSION,
                                       'upstream': 'AKShare stock_zh_a_hist / Eastmoney',
                                       'volume_unit': 'shares',
                                       'upstream_volume_unit': 'hands', 'amount_unit': 'CNY'})


def save_akshare_sync(session: Session, data_dir: Path, prepared: dict) -> dict:
    return save_online_sync(session, data_dir, prepared)
