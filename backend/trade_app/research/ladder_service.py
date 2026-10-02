"""Limit-up ladder, preserving the former market_momentum counting rules."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.symbols import a_share_board, normalize_a_share_symbol, standard_limit_up_ratio
from trade_app.market.tdx_names import read_tdx_names
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.ladder_models import LimitUpLadderRun


def _ratio(symbol: str) -> float:
    return standard_limit_up_ratio(symbol)


def _eligible(bar: dict, day: str) -> bool:
    if bar['event_date'] > day:
        return False
    available = bar.get('available_at')
    if available is None:
        return True
    cutoff = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time.min,
                              tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    return datetime.fromisoformat(available) < cutoff


def ladder_contribution(dataset: dict, name: str, body: dict) -> dict:
    symbol = dataset['symbol']
    filters = set(body['board_filters'])
    boards = filters - {'st'}
    is_st = 'ST' in ''.join(name.upper().split())
    included = not filters or not ((is_st and 'st' not in filters) or
                                   (boards and a_share_board(symbol) not in boards) or
                                   (not boards and not is_st))
    dates: list[str] = []
    heights: dict[str, int] = {}
    bars = dataset['bars']
    if included:
        chain = 0
        for index, bar in enumerate(bars):
            day = bar['event_date']
            if day > body['date_to']:
                break
            if index > 0 and _eligible(bar, day) and _eligible(bars[index - 1], day):
                previous = float(bars[index - 1]['close'])
                pct = (float(bar['close']) - previous) / previous if previous > 0 else 0
                chain = chain + 1 if pct >= _ratio(symbol) - 0.002 else 0
            else:
                chain = 0
            if body['date_from'] <= day and _eligible(bar, day):
                dates.append(day)
                if chain:
                    heights[day] = chain
    return {'symbol': symbol, 'dataset_id': dataset['id'], 'name': name,
            'dates': dates, 'heights': heights,
            'availability_quality': dataset['availability_quality'],
            'name_missing': not bool(name)}


def compute_ladder_result(contributions: list[dict], body: dict, *, total_scanned: int | None = None,
                          extra_quality: list[str] | None = None,
                          scope: str = 'selected_frozen_datasets') -> dict:
    trading_dates = sorted({day for item in contributions for day in item['dates']})
    recent_start = trading_dates[max(0, len(trading_dates) - max(1, body['recent_days']))] if trading_dates else None
    timeline: list[dict] = []
    summaries: list[dict] = []
    quality = set(extra_quality or [])
    for item in contributions:
        if item['availability_quality'] == 'historical_availability_unknown':
            quality.add('HISTORICAL_AVAILABLE_AT_UNKNOWN')
        if item['name_missing'] and 'st' not in body['board_filters']:
            quality.add('ST_NAME_UNKNOWN')
        points = [{'symbol': item['symbol'], 'name': item['name'],
                   'dataset_id': item['dataset_id'], 'date': day, 'board_height': height}
                  for day, height in item['heights'].items()
                  if day >= recent_start or height >= body['historical_min_boards']]
        if not points:
            continue
        timeline.extend(points)
        summaries.append({'symbol': item['symbol'], 'name': item['name'],
                          'dataset_id': item['dataset_id'],
                          'max_board_height': max(point['board_height'] for point in points),
                          'active_days': len(points),
                          'latest_board_height': item['heights'].get(trading_dates[-1], 0)})
    timeline.sort(key=lambda row: (row['date'], -row['board_height'], row['symbol']))
    summaries.sort(key=lambda row: (-row['max_board_height'], -row['active_days'], row['symbol']))
    return {'date_from': body['date_from'], 'date_to': body['date_to'],
            'recent_days': body['recent_days'],
            'historical_min_boards': body['historical_min_boards'],
            'total_scanned': total_scanned if total_scanned is not None else len(contributions),
            'dates': trading_dates, 'timeline': timeline, 'summaries': summaries,
            'quality_flags': sorted(quality), 'data_scope': scope}


def _code_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes() +
                          (Path(__file__).parent.parent / 'market' / 'symbols.py').read_bytes() +
                          (Path(__file__).parent.parent / 'platform' / 'symbols.py').read_bytes()).hexdigest()


def prepare_ladder_run(session: Session, data_dir: Path, tdx_root: Path | None, body: dict) -> dict:
    datasets = [get_dataset(session, data_dir, dataset_id) for dataset_id in body['dataset_ids']]
    symbols = [dataset['symbol'] for dataset in datasets]
    canonical_symbols = [normalize_a_share_symbol(symbol) for symbol in symbols]
    if len(set(canonical_symbols)) != len(canonical_symbols):
        raise TradeError('DUPLICATE_SYMBOL', '涨停梯队中同一证券只能选择一个冻结样本')
    if any(dataset['adjustment'] != 'none' for dataset in datasets):
        raise TradeError('ADJUSTMENT_UNSUPPORTED', '涨停梯队只接受不复权日线')
    full_symbols = [exchange + code for exchange, code in canonical_symbols]
    mapped = read_tdx_names(tdx_root, full_symbols)
    names = {symbol: mapped['names'].get(full, '') for symbol, full in zip(symbols, full_symbols)}
    contributions = [ladder_contribution(dataset, names[dataset['symbol']], body)
                     for dataset in datasets]
    request = {**body, 'symbol_name_sources': mapped['sources']}
    result = compute_ladder_result(contributions, body)
    code_hash = _code_hash()
    identity = json.dumps({'request': request, 'code_sha256': code_hash},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'id': hashlib.sha256(identity).hexdigest(), 'request': request,
            'result': result, 'code_sha256': code_hash}


def _view(row: LimitUpLadderRun, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at,
            'date_from': result['date_from'], 'date_to': result['date_to'],
            'total_scanned': result['total_scanned'],
            'stock_count': len(result['summaries']),
            'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def save_ladder_run(session: Session, prepared: dict) -> dict:
    row = session.get(LimitUpLadderRun, prepared['id'])
    if row is None:
        row = LimitUpLadderRun(id=prepared['id'],
                               request_json=json.dumps(prepared['request'], ensure_ascii=False),
                               result_json=json.dumps(prepared['result'], ensure_ascii=False),
                               code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, True)


def list_ladder_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(LimitUpLadderRun).order_by(
        LimitUpLadderRun.created_at.desc(), LimitUpLadderRun.id.desc()).limit(100))]


def get_ladder_run(session: Session, run_id: str) -> dict:
    row = session.get(LimitUpLadderRun, run_id)
    if row is None:
        raise TradeError('LADDER_RUN_NOT_FOUND', '涨停梯队记录不存在', 404)
    return _view(row, True)
