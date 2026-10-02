"""Trend leader timeline over explicit immutable daily market datasets."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.tdx_names import read_tdx_names
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.trend_models import TrendLeaderRun


def board_of(symbol: str) -> str | None:
    code = symbol[-6:]
    if code.startswith(('688', '689')):
        return 'star'
    if code.startswith(('300', '301')):
        return 'gem'
    if code.startswith(('4', '8')):
        return 'beijing'
    if code.startswith(('60', '00', '001', '002', '003')):
        return 'main'
    return None


def _eligible(bar: dict, day: str) -> bool:
    if bar['event_date'] > day:
        return False
    available = bar.get('available_at')
    if available is None:
        return True
    cutoff = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time.min,
                              tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    return datetime.fromisoformat(available) < cutoff


def trend_contribution(dataset: dict, name: str, body: dict) -> dict:
    """Calculate one symbol's eligible daily returns without retaining the market in memory."""
    filters = set(body['board_filters'])
    boards = filters - {'st'}
    symbol = dataset['symbol']
    is_st = 'ST' in ''.join(name.upper().split())
    included = not filters or not ((is_st and 'st' not in filters) or
                                   (boards and board_of(symbol) not in boards) or
                                   (not boards and not is_st))
    bars = dataset['bars']
    window = body['window_days']
    dates: list[str] = []
    daily: dict[str, float] = {}
    if included:
        for index, bar in enumerate(bars):
            day = bar['event_date']
            if day < body['date_from'] or day > body['date_to'] or not _eligible(bar, day):
                continue
            dates.append(day)
            if index < window or not all(_eligible(prior, day) for prior in bars[index - window:index]):
                continue
            start = float(bars[index - window]['close'])
            amounts = [prior.get('amount') for prior in bars[index - window + 1:index + 1]]
            if start <= 0 or any(amount is None for amount in amounts):
                continue
            if sum(float(amount) for amount in amounts) / window < body['min_amount_avg']:
                continue
            pct = (float(bar['close']) - start) / start * 100
            if pct > 0:
                daily[day] = pct
    return {'symbol': symbol, 'dataset_id': dataset['id'], 'dates': dates, 'daily': daily,
            'availability_quality': dataset['availability_quality'],
            'amount_missing': any(bar.get('amount') is None for bar in bars),
            'name_missing': not bool(name)}


def compute_trend_leaders(datasets: list[dict], names: dict[str, str], body: dict,
                          *, rankings: dict[str, list[str]] | None = None,
                          trading_dates: list[str] | None = None,
                          total_scanned: int | None = None,
                          extra_quality: list[str] | None = None) -> dict:
    date_from, date_to = body['date_from'], body['date_to']
    window, top_n = body['window_days'], body['daily_top_n']
    filters = set(body['board_filters'])
    contributions = ([trend_contribution(dataset, names.get(dataset['symbol'], ''), body)
                      for dataset in datasets] if rankings is None else [])
    all_dates = trading_dates if trading_dates is not None else sorted(
        {day for item in contributions for day in item['dates']})
    daily_leaders: dict[str, list[str]] = {}
    dataset_by_symbol = {dataset['symbol']: dataset for dataset in datasets}
    quality: set[str] = set()
    if any(dataset['availability_quality'] == 'historical_availability_unknown'
           for dataset in datasets):
        quality.add('HISTORICAL_AVAILABLE_AT_UNKNOWN')
    if any(any(bar.get('amount') is None for bar in dataset['bars']) for dataset in datasets):
        quality.add('AMOUNT_NOT_FOUND')
    if not all(names.get(dataset['symbol']) for dataset in datasets) and 'st' not in filters:
        quality.add('ST_NAME_UNKNOWN')
    if rankings is None:
        for eval_date in all_dates:
            candidates = [(item['symbol'], item['daily'][eval_date]) for item in contributions
                          if eval_date in item['daily']]
            candidates.sort(key=lambda item: (-item[1], item[0]))
            daily_leaders[eval_date] = [symbol for symbol, _ in candidates[:top_n]]
    else:
        daily_leaders = rankings
    leader_dates: dict[str, list[str]] = defaultdict(list)
    for eval_date in all_dates:
        for symbol in daily_leaders[eval_date]:
            leader_dates[symbol].append(eval_date)
    leaders, series = [], []
    for symbol, dates in leader_dates.items():
        dataset = dataset_by_symbol[symbol]
        bars = [bar for bar in dataset['bars'] if bar['event_date'] <= date_to and
                _eligible(bar, date_to)]
        first = next((index for index, bar in enumerate(bars)
                      if bar['event_date'] >= date_from), None)
        if first is None:
            continue
        base = float(bars[first]['close'])
        if base <= 0:
            continue
        by_date = {bar['event_date']: index for index, bar in enumerate(bars)}
        points = [{'date': day, 'return_pct': round((float(bars[by_date[day]]['close']) - base)
                                                      / base * 100, 2)}
                  for day in all_dates if day in by_date]
        end = len(bars) - 1
        previous = float(bars[end - 1]['close']) if end else 0
        day_return = (float(bars[end]['close']) - previous) / previous * 100 if previous else 0
        window_base = float(bars[end - window]['close']) if end >= window else 0
        window_return = ((float(bars[end]['close']) - window_base)
                         / window_base * 100) if window_base > 0 else 0
        amount_slice = bars[max(0, end - window + 1):end + 1]
        valid_amounts = [float(bar['amount']) for bar in amount_slice if bar.get('amount') is not None]
        leaders.append({'symbol': symbol, 'name': names.get(symbol, ''),
                        'dataset_id': dataset['id'], 'return_pct': round(day_return, 2),
                        'window_return_pct': round(window_return, 2),
                        'amount_avg': round(sum(valid_amounts) / len(valid_amounts), 2)
                                      if valid_amounts else 0,
                        'board': board_of(symbol), 'leader_days': len(dates),
                        'first_leader_date': dates[0], 'last_leader_date': dates[-1],
                        'max_return_pct': max((point['return_pct'] for point in points), default=0)})
        series.append({'symbol': symbol, 'name': names.get(symbol, ''),
                       'dataset_id': dataset['id'], 'points': points})
    leaders.sort(key=lambda row: (-row['leader_days'], -row['max_return_pct'], row['symbol']))
    series.sort(key=lambda row: (-len(leader_dates[row['symbol']]), row['symbol']))
    quality.update(extra_quality or [])
    return {'date_from': date_from, 'date_to': date_to, 'window_days': window,
            'daily_top_n': top_n, 'total_scanned': total_scanned or len(datasets), 'dates': all_dates,
            'daily_leaders': daily_leaders, 'leaders': leaders, 'series': series,
            'quality_flags': sorted(quality),
            'data_scope': 'tdx_full_market' if rankings is not None else 'selected_frozen_datasets'}


def prepare_trend_run(session: Session, data_dir: Path, tdx_root: Path | None,
                      body: dict) -> dict:
    datasets = [get_dataset(session, data_dir, dataset_id) for dataset_id in body['dataset_ids']]
    symbols = [dataset['symbol'] for dataset in datasets]
    if len(set(symbols)) != len(symbols):
        raise TradeError('DUPLICATE_SYMBOL', '趋势龙头扫描中同一证券只能选择一个冻结样本')
    if any(dataset['adjustment'] != 'none' for dataset in datasets):
        raise TradeError('ADJUSTMENT_UNSUPPORTED', '趋势龙头扫描只接受不复权日线')
    full_symbols = []
    for symbol in symbols:
        code = symbol[-6:]
        prefix = 'bj' if code.startswith(('4', '8')) else 'sh' if code.startswith(('6', '5', '9')) else 'sz'
        full_symbols.append(prefix + code)
    mapped = read_tdx_names(tdx_root, full_symbols)
    names = {symbol: mapped['names'].get(full, '') for symbol, full in zip(symbols, full_symbols)}
    result = compute_trend_leaders(datasets, names, body)
    request = {**body, 'symbol_name_sources': mapped['sources']}
    code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                               (Path(__file__).parents[1] / 'market' / 'tdx_names.py').read_bytes()).hexdigest()
    identity = json.dumps({'request': request, 'code_sha256': code_hash},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'id': hashlib.sha256(identity).hexdigest(), 'request': request,
            'result': result, 'code_sha256': code_hash}


def _view(row: TrendLeaderRun, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at,
            'date_from': result['date_from'], 'date_to': result['date_to'],
            'total_scanned': result['total_scanned'],
            'leader_count': len(result['leaders']),
            'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def save_trend_run(session: Session, prepared: dict) -> dict:
    row = session.get(TrendLeaderRun, prepared['id'])
    if row is None:
        row = TrendLeaderRun(id=prepared['id'],
                             request_json=json.dumps(prepared['request'], ensure_ascii=False),
                             result_json=json.dumps(prepared['result'], ensure_ascii=False),
                             code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, True)


def list_trend_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(TrendLeaderRun).order_by(
        TrendLeaderRun.created_at.desc(), TrendLeaderRun.id.desc()).limit(100))]


def get_trend_run(session: Session, run_id: str) -> dict:
    row = session.get(TrendLeaderRun, run_id)
    if row is None:
        raise TradeError('TREND_RUN_NOT_FOUND', '趋势龙头记录不存在', 404)
    return _view(row, True)
