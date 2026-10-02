"""Bounded Eastmoney minute observations, with explicit market identity.

Contract: AKShare stock_zh_a_hist_min_em / index_zh_a_hist_min_em, period=1.
The source's volume unit is lots (手), amount is CNY. Source zero OHLC other
than close is missing, as documented for older days. Never infer availability
from the bar timestamp or feed this display-only data into strategy execution.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

from trade_app.market.symbols import normalize_a_share_symbol, normalize_market_symbol
from trade_app.platform.types import TradeError

URL = 'https://push2his.eastmoney.com/api/qt/stock/trends2/get'
VERSION = 'eastmoney-minute-identity-v1'
MAX_BYTES = 512 * 1024
MAX_ROWS = 1600
SHANGHAI = ZoneInfo('Asia/Shanghai')
INDEX_SYMBOLS = ('sh000001', 'sh000016', 'sh000300', 'sh000688', 'sh000905',
                 'sh000852', 'sz399001', 'sz399005', 'sz399006', 'sz399007')
_slots = threading.BoundedSemaphore(2)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def identity(raw_symbol: str) -> dict:
    exchange, code = normalize_market_symbol(raw_symbol)
    canonical = exchange + code
    explicit = bool(re.match(r'^(sh|sz)', raw_symbol.strip().lower()) or
                    re.search(r'\.(sh|sz)$', raw_symbol.strip().lower()))
    if canonical in INDEX_SYMBOLS and explicit:
        kind = 'index'
    else:
        try:
            normalize_a_share_symbol(raw_symbol)
        except TradeError as exc:
            raise TradeError('INTRADAY_UNSUPPORTED_SYMBOL', '在线分时仅支持沪深 A 股及能力列表中带交易所的指数') from exc
        if exchange not in ('sh', 'sz'):
            raise TradeError('INTRADAY_UNSUPPORTED_SYMBOL', '当前在线分时适配器未验证该交易所，请使用本地原始分时')
        kind = 'stock'
    return {'symbol': canonical, 'exchange': exchange, 'code': code,
            'instrument_type': kind, 'secid': ('1.' if exchange == 'sh' else '0.') + code}


def request_config(raw_symbol: str, raw_date: str, *, observed_at: datetime | None = None) -> dict:
    instrument = identity(raw_symbol)
    observed = observed_at or now_utc()
    if observed.tzinfo is None:
        raise TradeError('INVALID_INTRADAY_TIME', '分时观察时间必须带时区')
    try:
        day = date.fromisoformat(raw_date)
        if day.isoformat() != raw_date:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise TradeError('INVALID_DATE', '分时日期须为 YYYY-MM-DD') from exc
    if day > observed.astimezone(SHANGHAI).date():
        raise TradeError('INTRADAY_FUTURE_DATE', '不能查询尚未发生的分时日期')
    return {**instrument, 'date': day.isoformat(), 'as_of_at': observed.astimezone(timezone.utc).isoformat()}


def fetch_bytes(secid: str) -> bytes:
    """Single fixed host; bounded streaming, no redirects or market fallback."""
    if not _slots.acquire(blocking=False):
        raise TradeError('INTRADAY_BUSY', '已有两个分时请求正在联网，请稍后重试', 409)
    try:
        deadline = time.monotonic() + 15
        params = {'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
                  'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58', 'ndays': '5',
                  'iscr': '0', 'secid': secid, 'ut': '7eea3edcaed734bea9cbfc24409ed989'}
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False,
                          headers={'User-Agent': 'TradeRebuild/1.0'}) as client:
            with client.stream('GET', URL, params=params) as response:
                if response.status_code != 200:
                    raise TradeError('INTRADAY_PROVIDER_UNAVAILABLE', '分时来源暂不可用；本地缓存保持不变', 502)
                contents = bytearray()
                for chunk in response.iter_bytes():
                    contents.extend(chunk)
                    if len(contents) > MAX_BYTES:
                        raise TradeError('INTRADAY_RESPONSE_TOO_LARGE', '分时来源响应超过 512 KiB', 502)
                    if time.monotonic() > deadline:
                        raise TradeError('INTRADAY_TIMEOUT', '分时请求超过时间预算，请稍后重试', 504)
                return bytes(contents)
    except httpx.TimeoutException as exc:
        raise TradeError('INTRADAY_TIMEOUT', '分时来源响应超时；本地缓存保持不变', 504) from exc
    except (httpx.HTTPError, OSError) as exc:
        raise TradeError('INTRADAY_PROVIDER_UNAVAILABLE', '分时来源连接失败；本地缓存保持不变', 502) from exc
    finally:
        _slots.release()


def _number(raw: str, *, price=False, required=False) -> str | None:
    if raw.strip() in ('', '-', '--', 'null'):
        if required:
            raise ValueError('Missing close')
        return None
    if len(raw) > 40:
        raise ValueError('Numeric length')
    value = Decimal(raw)
    if not value.is_finite() or value < 0 or value > Decimal('1e18'):
        raise ValueError('Invalid numeric')
    if price and value == 0:
        if required:
            raise ValueError('Zero close')
        return None
    if value.as_tuple().exponent < -8:
        raise ValueError('Numeric precision')
    return format(value, 'f')


def parse_response(contents: bytes, config: dict) -> dict:
    if len(contents) > MAX_BYTES:
        raise TradeError('INTRADAY_RESPONSE_TOO_LARGE', '分时来源响应超过 512 KiB', 502)
    try:
        payload = json.loads(contents)
        if not isinstance(payload, dict) or payload.get('rc', 0) != 0:
            raise ValueError('Source status')
        source = payload.get('data')
        if source is None:
            raise TradeError('INTRADAY_SOURCE_EMPTY', '来源未返回该证券分时；可能暂不可用或不受支持', 404)
        if not isinstance(source, dict):
            raise ValueError('Source type')
        if str(source.get('code')) != config['code'] or type(source.get('market')) is not int or source['market'] != int(config['secid'][0]):
            raise TradeError('INTRADAY_IDENTITY_MISMATCH', '分时来源返回的证券或交易所与请求不一致', 502)
        rows = source.get('trends')
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise ValueError('Rows type or count')
        points, seen, flags = [], set(), set()
        excluded_future = 0
        cutoff = datetime.fromisoformat(config['as_of_at'])
        for raw in rows:
            if not isinstance(raw, str) or len(raw) > 512:
                raise ValueError('Row type or size')
            fields = raw.split(',')
            if len(fields) != 8 or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', fields[0]):
                raise ValueError('Row shape')
            at = datetime.strptime(fields[0], '%Y-%m-%d %H:%M').replace(tzinfo=SHANGHAI)
            if at.date().isoformat() != config['date']:
                continue
            if fields[0] in seen:
                raise ValueError('Duplicate minute')
            seen.add(fields[0])
            # A current minute can still change while the HTTP response is in flight.
            # Conservatively wait until its whole timestamp minute has ended.
            if at + timedelta(minutes=1) > cutoff:
                excluded_future += 1
                continue
            opened, closed, high, low = (_number(fields[i], price=True, required=i == 2) for i in (1, 2, 3, 4))
            volume, amount, average = _number(fields[5]), _number(fields[6]), _number(fields[7], price=True)
            if high is not None and low is not None and Decimal(high) < Decimal(low):
                raise ValueError('High low')
            for value in (opened, closed):
                if value is not None and ((high is not None and Decimal(value) > Decimal(high)) or
                                          (low is not None and Decimal(value) < Decimal(low))):
                    raise ValueError('OHLC bounds')
            point = {'time': at.strftime('%H:%M'), 'event_at': at.isoformat(), 'open': opened,
                     'high': high, 'low': low, 'close': closed, 'volume': volume, 'amount': amount, 'average': average}
            if any(point[key] is None for key in ('open', 'high', 'low', 'volume', 'amount')):
                flags.add('source_fields_missing')
            points.append(point)
        if not points:
            raise TradeError('INTRADAY_DATE_NOT_FOUND', '来源没有该日期在观察时点之前的分时；不会替换日期或生成近似记录', 404)
        if len(points) > 500:
            raise ValueError('Daily row count')
        points.sort(key=lambda point: point['time'])
        if excluded_future:
            flags.add('future_minutes_excluded')
        return {'symbol': config['symbol'], 'instrument_type': config['instrument_type'], 'date': config['date'],
                'provider': 'eastmoney_online', 'quality': 'source_one_minute', 'adjustment': 'none',
                'availability_quality': 'historical_availability_unknown', 'as_of_at': config['as_of_at'],
                'timezone': 'Asia/Shanghai', 'minute_policy': 'completed_timestamp_minute_only',
                'volume_unit': 'lots', 'amount_unit': 'CNY',
                'price_unit': 'index_points' if config['instrument_type'] == 'index' else 'CNY',
                'quality_flags': sorted(flags), 'excluded_future_count': excluded_future,
                'source': {'adapter_version': VERSION, 'upstream': URL, 'secid': config['secid'],
                           'sha256': hashlib.sha256(contents).hexdigest(), 'bytes': len(contents),
                           'name': str(source.get('name') or '')[:80], 'requested_recent_trading_days': 5,
                           'retrieved_at': now_utc().isoformat()}, 'points': points}
    except TradeError:
        raise
    except (ValueError, TypeError, InvalidOperation, UnicodeError, RecursionError, OverflowError) as exc:
        raise TradeError('INTRADAY_SOURCE_FORMAT', '分时来源格式或数值无效；未保存为有效缓存', 502) from exc


def prepare(raw_symbol: str, raw_date: str) -> dict:
    config = request_config(raw_symbol, raw_date)
    return parse_response(fetch_bytes(config['secid']), config)
