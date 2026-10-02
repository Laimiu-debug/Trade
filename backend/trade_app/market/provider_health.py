"""Declared adapter capabilities and explicit, non-persisting source probes."""
import csv
from datetime import date
import importlib.util
from pathlib import Path
import tempfile
import threading
import time

from trade_app.market import akshare_online, baostock_online
from trade_app.market.domain import normalize_bars
from trade_app.market.symbols import normalize_market_symbol, normalize_a_share_symbol
from trade_app.market.tdx import DAY_RECORD
from trade_app.platform.types import TradeError, utc_now

_locks = {key: threading.Lock() for key in ('tdx', 'akshare_cache', 'baostock_cache', 'akshare', 'baostock')}


def capabilities(tdx_root, cache_roots):
    sources = [
        {'id': 'tdx', 'name': '本地通达信', 'network': False, 'root': tdx_root,
         'instruments': ['股票', '明确交易所的指数', '基金'], 'daily': True, 'intraday': '本地 LC1', 'dependency': None,
         'notes': '测试日线文件头尾记录可读性；完整导入仍会逐条校验。分时需要另有LC1文件。'},
        {'id': 'akshare_cache', 'name': 'AKShare 本地 CSV', 'network': False, 'root': cache_roots.get('akshare'),
         'instruments': ['CSV声明的证券'], 'daily': True, 'intraday': None, 'dependency': None, 'notes': 'CSV成交量单位须为股；测试只检查文件及必需列。'},
        {'id': 'baostock_cache', 'name': 'BaoStock 本地 CSV', 'network': False, 'root': cache_roots.get('baostock'),
         'instruments': ['CSV声明的证券'], 'daily': True, 'intraday': None, 'dependency': None, 'notes': '测试只检查文件及必需列；不会写入行情库。'},
        {'id': 'akshare', 'name': 'AKShare / 东方财富', 'network': True, 'root': None,
         'instruments': ['A股股票'], 'daily': True, 'intraday': None, 'dependency': 'akshare',
         'notes': '当前适配器 stock_zh_a_hist；不将同代码股票替代指数。测试会显式联网，历史可得时间未知。'},
        {'id': 'baostock', 'name': 'BaoStock', 'network': True, 'root': None,
         'instruments': ['沪深股票', '提供方支持的沪深指数'], 'daily': True, 'intraday': None, 'dependency': 'baostock',
         'notes': '不支持北交所；测试独立进程最长45秒，历史可得时间未知。'},
    ]
    for row in sources:
        root, dependency = row.pop('root'), row.pop('dependency')
        installed = dependency is None or importlib.util.find_spec(dependency) is not None
        row.update(configured=installed if row['network'] else bool(root and root.is_dir()),
            location=str(root) if root else None, adjustment=['none'],
            availability='historical_availability_unknown', health='not_tested')
    return {'sources': sources, 'method': '能力来自当前适配器；已配置不代表连通。打开此页不会联网或写入行情。'}


def _local_probe(provider, symbol, tdx_root, cache_roots):
    exchange, code = normalize_market_symbol(symbol)
    if provider == 'tdx':
        if tdx_root is None:
            raise TradeError('TDX_ROOT_NOT_CONFIGURED', '请先选择通达信目录，或点击扫描本机后选择发现的目录')
        root = tdx_root / 'vipdoc' if (tdx_root / 'vipdoc').is_dir() else tdx_root
        paths = [root / exchange / 'lday' / f'{exchange}{code}.day', root / exchange / 'lday' / f'{code}.day']
    else:
        root = cache_roots.get(provider.removesuffix('_cache'))
        if root is None:
            raise TradeError('CACHE_ROOT_NOT_CONFIGURED', '尚未配置该来源的本地CSV目录')
        paths = [root / f'{exchange}{code}.csv', root / f'{code}.csv']
    path = next((path for path in paths if path.is_file() and not path.is_symlink()), None)
    if path is None:
        if provider == 'tdx':
            raise TradeError('TDX_DAY_NOT_FOUND', f'目录已识别，但没有 {exchange}{code} 日线文件；请在通达信下载盘后日线，或更换测试证券代码')
        raise TradeError('MARKET_SAMPLE_NOT_FOUND', '配置目录中没有该证券的可读样本文件')
    size = path.stat().st_size
    if size == 0 or size > 100 * 1024 * 1024:
        raise TradeError('MARKET_SAMPLE_SIZE', '文件为空或超过100MiB')
    if provider == 'tdx':
        if size % DAY_RECORD.size:
            raise TradeError('INVALID_TDX_DAY', '日线文件长度不符合记录格式')
        with path.open('rb') as stream:
            first = DAY_RECORD.unpack(stream.read(DAY_RECORD.size))
            stream.seek(-DAY_RECORD.size, 2)
            last = DAY_RECORD.unpack(stream.read(DAY_RECORD.size))
        for row in (first, last):
            day = str(row[0])
            date.fromisoformat(day[:4] + '-' + day[4:6] + '-' + day[6:])
            if min(row[1:5]) <= 0 or row[2] < max(row[1], row[4]) or row[3] > min(row[1], row[4]):
                raise TradeError('INVALID_TDX_DAY', '日线首尾记录的OHLC关系无效')
        return {'status': 'readable', 'scope': 'first_last_record', 'sample_count': size // DAY_RECORD.size,
                'first_date_raw': first[0], 'last_date_raw': last[0], 'bytes': size,
                'first_date': f'{str(first[0])[:4]}-{str(first[0])[4:6]}-{str(first[0])[6:]}',
                'last_date': f'{str(last[0])[:4]}-{str(last[0])[4:6]}-{str(last[0])[6:]}'}
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        header = stream.readline(8193)
    if len(header) > 8192 or not {'date', 'open', 'high', 'low', 'close', 'volume'} <= set(next(csv.reader([header]))):
        raise TradeError('INVALID_MARKET_CACHE', 'CSV表头缺少必需列或超过限制')
    return {'status': 'readable', 'scope': 'header_only', 'bytes': size}


def probe(provider, body, *, tdx_root=None, cache_roots=None):
    if provider not in _locks:
        raise TradeError('UNKNOWN_PROVIDER', '未知行情来源')
    exchange, code = normalize_market_symbol(body['symbol'])
    start, end = date.fromisoformat(body['start_date']), date.fromisoformat(body['end_date'])
    if not 1 <= (end-start).days <= 31:
        raise TradeError('INVALID_PROBE_WINDOW', '测试窗口需为2至32个自然日')
    lock = _locks[provider]
    if not lock.acquire(blocking=False):
        raise TradeError('PROVIDER_PROBE_BUSY', '该来源正在测试，请等待当前请求完成', 409)
    started = time.monotonic()
    response = {'provider': provider, 'symbol': exchange + code, 'start_date': start.isoformat(),
                'end_date': end.isoformat(), 'checked_at': utc_now(), 'persisted': False}
    try:
        if provider in ('tdx', 'akshare_cache', 'baostock_cache'):
            result = _local_probe(provider, exchange + code, tdx_root, cache_roots or {})
        else:
            if provider == 'akshare':
                try:
                    normalize_a_share_symbol(exchange + code)
                except TradeError as exc:
                    raise TradeError('MARKET_PROVIDER_UNSUPPORTED', '该AKShare适配器仅支持股票日线') from exc
                rows = akshare_online.fetch_akshare_rows(code, start, end)
                bars = akshare_online._parse_rows(code, rows, start, end)
            else:
                with tempfile.TemporaryDirectory(prefix='trade-source-probe-') as folder:
                    rows = baostock_online.fetch_baostock_rows(exchange + code, start, end, Path(folder))
                bars = baostock_online._parse_rows(code, rows, start, end)
            if len(bars) < 2:
                result = {'status': 'empty' if not bars else 'insufficient_sample', 'scope': 'requested_daily_window',
                          'sample_count': len(bars), 'message': '没有足够样本验证完整日线；可能为休市、停牌、范围无数据或提供方不支持。'}
            else:
                bars = normalize_bars(bars)
                result = {'status': 'ok', 'scope': 'requested_daily_window', 'sample_count': len(bars),
                          'first_date': bars[0]['event_date'], 'last_date': bars[-1]['event_date'],
                          'availability': 'historical_availability_unknown'}
        return {**response, **result, 'elapsed_ms': round((time.monotonic()-started)*1000)}
    except TradeError as exc:
        return {**response, 'status': 'failed', 'error_code': exc.code, 'message': exc.message,
                'elapsed_ms': round((time.monotonic()-started)*1000)}
    except (OSError, ValueError, UnicodeError, StopIteration):
        return {**response, 'status': 'failed', 'error_code': 'PROVIDER_SAMPLE_INVALID',
                'message': '测试样本无法读取或格式无效', 'elapsed_ms': round((time.monotonic()-started)*1000)}
    finally:
        lock.release()
