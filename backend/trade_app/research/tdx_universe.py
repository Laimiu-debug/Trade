"""Durable full-market TDX ingestion and four-stage screening."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.market.tdx import DAY_RECORD, import_tdx_day
from trade_app.market.tdx_float_shares import read_tdx_float_shares
from trade_app.market.tdx_names import read_tdx_names
from trade_app.market.tdx_location import source_fingerprint
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.screener_domain import FunnelConfig, ScreenerCandidate, run_funnel
from trade_app.research.screener_metrics import build_candidate
from trade_app.research.screener_service import _digest, save_screener_run
from trade_app.research.b1_domain import B1Params, check_b1
from trade_app.research.b1_service import eligible_b1_bars, prepare_b1_universe_run, save_b1_run
from trade_app.research.trend_service import compute_trend_leaders, save_trend_run, trend_contribution
from trade_app.research.ladder_service import compute_ladder_result, ladder_contribution, save_ladder_run
from trade_app.research.sector_codes import SECTOR_CODES
from trade_app.research.sector_service import compute_sector_flow, save_sector_run, sector_code_hash
from trade_app.research.abnormal_domain import (
    ALGORITHM_VERSION, BENCHMARK_SPECS, BENCHMARK_FALLBACK,
    analyze_symbol_abnormal_events, resolve_benchmark_key,
)
from trade_app.research.abnormal_service import save_abnormal_run
from trade_app.research.trend_service import board_of
from trade_app.research.tdx_universe_models import TdxUniverseItem, TdxUniverseJob


def _is_a_share(symbol: str) -> bool:
    try:
        normalize_a_share_symbol(symbol)
    except TradeError:
        return False
    return True


def scan_tdx_universe(tdx_root: Path | None, markets: list[str], min_bars: int = 251) -> list[str]:
    if tdx_root is None:
        raise TradeError('TDX_ROOT_NOT_CONFIGURED', '请在系统设置 → 行情来源选择通达信目录或扫描本机', 409)
    root = tdx_root if tdx_root.name.lower() == 'vipdoc' or not (tdx_root / 'vipdoc').is_dir() else tdx_root / 'vipdoc'
    if not root.is_dir():
        raise TradeError('TDX_ROOT_NOT_FOUND', '通达信 vipdoc 目录不存在', 404)
    symbols = set()
    for market in sorted(set(markets)):
        folder = root / market / 'lday'
        if not folder.is_dir():
            continue
        for path in folder.glob('*.day'):
            if path.is_symlink() or not path.is_file():
                continue
            stem = path.stem.lower()
            code = stem[2:] if stem.startswith(market) else stem
            if not re.fullmatch(r'\d{6}', code):
                continue
            symbol = market + code
            if _is_a_share(symbol):
                try:
                    if path.stat().st_size >= min_bars * DAY_RECORD.size:
                        symbols.add(symbol)
                except OSError:
                    continue
            if len(symbols) > 10_000:
                raise TradeError('TDX_UNIVERSE_TOO_LARGE', '本地股票文件超过 10000 只，请核对目录', 409)
    if not symbols:
        raise TradeError('TDX_UNIVERSE_EMPTY', f'通达信目录没有满足 {min_bars} 根日线的 A 股文件', 404)
    return sorted(symbols)


def prepare_universe_job(tdx_root: Path | None, body: dict) -> dict:
    symbols = scan_tdx_universe(tdx_root, body['markets'])
    names = read_tdx_names(tdx_root, symbols)
    try:
        shares = read_tdx_float_shares(tdx_root, symbols)
        share_values = shares['values']
        share_source = shares['source']
        share_error = None
    except TradeError as exc:
        share_values = {}
        share_source = None
        share_error = exc.code
    return {'request': {**body, 'tdx_source_fingerprint': source_fingerprint(tdx_root), 'float_shares_source': share_source,
                        'float_shares_error': share_error,
                        'symbol_names': names['names'], 'symbol_name_sources': names['sources']},
            'symbols': symbols, 'float_shares': share_values}


def prepare_b1_universe_job(tdx_root: Path | None, body: dict) -> dict:
    from trade_app.research.b1_params import normalize_b1_params
    body = {**body, 'b1_params': normalize_b1_params(body.get('b1_params', {}))}
    symbols = scan_tdx_universe(tdx_root, body['markets'], min_bars=900)
    names = read_tdx_names(tdx_root, symbols)
    return {'request': {**body, 'tdx_source_fingerprint': source_fingerprint(tdx_root), 'kind': 'b1', 'float_shares_source': None,
                        'float_shares_error': None,
                        'symbol_names': names['names'], 'symbol_name_sources': names['sources']},
            'symbols': symbols, 'float_shares': {}}


def prepare_trend_universe_job(tdx_root: Path | None, body: dict) -> dict:
    symbols = scan_tdx_universe(tdx_root, body['markets'], min_bars=body['window_days'] + 1)
    names = read_tdx_names(tdx_root, symbols)
    return {'request': {**body, 'tdx_source_fingerprint': source_fingerprint(tdx_root), 'kind': 'trend', 'as_of_date': body['date_to'],
                        'float_shares_source': None, 'float_shares_error': None,
                        'symbol_names': names['names'], 'symbol_name_sources': names['sources']},
            'symbols': symbols, 'float_shares': {}}


def prepare_ladder_universe_job(tdx_root: Path | None, body: dict) -> dict:
    symbols = scan_tdx_universe(tdx_root, body['markets'], min_bars=3)
    names = read_tdx_names(tdx_root, symbols)
    return {'request': {**body, 'tdx_source_fingerprint': source_fingerprint(tdx_root), 'kind': 'ladder', 'as_of_date': body['date_to'],
                        'float_shares_source': None, 'float_shares_error': None,
                        'symbol_names': names['names'], 'symbol_name_sources': names['sources']},
            'symbols': symbols, 'float_shares': {}}


def prepare_sector_universe_job(tdx_root: Path | None, body: dict) -> dict:
    if tdx_root is None:
        raise TradeError('TDX_ROOT_NOT_CONFIGURED', '请在系统设置 → 行情来源选择通达信目录或扫描本机', 409)
    root = tdx_root if tdx_root.name.lower() == 'vipdoc' or not (tdx_root / 'vipdoc').is_dir() else tdx_root / 'vipdoc'
    if not root.is_dir():
        raise TradeError('TDX_ROOT_NOT_FOUND', '通达信 vipdoc 目录不存在', 404)
    symbols = []
    missing = []
    for code in sorted({code for codes in SECTOR_CODES.values() for code in codes}):
        found = next((market + code for market in ('sh', 'sz')
                      if any((root / market / 'lday' / filename).is_file()
                             for filename in (f'{market}{code}.day', f'{code}.day'))), None)
        if found:
            symbols.append(found)
        else:
            missing.append(code)
    if not symbols:
        raise TradeError('TDX_SECTOR_INDEX_EMPTY', '通达信目录没有可用的板块指数日线', 404)
    return {'request': {**body, 'tdx_source_fingerprint': source_fingerprint(tdx_root), 'kind': 'sector', 'as_of_date': body['date_to'],
                        'markets': sorted({symbol[:2] for symbol in symbols}),
                        'float_shares_source': None, 'float_shares_error': None,
                        'symbol_names': {}, 'symbol_name_sources': [],
                        'missing_index_codes': missing},
            'symbols': symbols, 'float_shares': {}}


def prepare_abnormal_universe_job(session: Session, data_dir: Path,
                                  tdx_root: Path | None, body: dict) -> dict:
    symbols = scan_tdx_universe(tdx_root, body['markets'], min_bars=90)
    names = read_tdx_names(tdx_root, symbols)
    benchmark_maps: dict[str, dict[str, float]] = {}
    benchmark_sources: dict[str, dict] = {}
    missing = []
    for key, (primary, primary_name) in BENCHMARK_SPECS.items():
        fallback = BENCHMARK_FALLBACK.get(key)
        candidates = [(primary, primary_name)] + ([fallback] if fallback else [])
        for index_symbol, index_name in candidates:
            try:
                meta = import_tdx_day(session, data_dir, index_symbol, tdx_root,
                                      max_bars=body['max_bars'])
                dataset = get_dataset(session, data_dir, meta['id'])
                close_map = {bar['event_date']: float(bar['close']) for bar in dataset['bars']}
                if close_map:
                    benchmark_maps[key] = close_map
                    benchmark_sources[key] = {'symbol': index_symbol, 'name': index_name,
                                              'dataset_id': meta['id'],
                                              'fallback': index_symbol != primary}
                    break
            except TradeError:
                continue
        if key not in benchmark_maps:
            benchmark_maps[key] = {}
            missing.append(key)
    if all(not values for values in benchmark_maps.values()):
        raise TradeError('BENCHMARK_INDEX_EMPTY', '通达信目录没有可用的异动扫描基准指数', 404)
    try:
        calendar_meta = import_tdx_day(session, data_dir, 'sh600519', tdx_root,
                                       max_bars=body['max_bars'])
        calendar = get_dataset(session, data_dir, calendar_meta['id'])
        calendar_dates = [bar['event_date'] for bar in calendar['bars']
                          if body['date_from'] <= bar['event_date'] <= body['date_to']]
        calendar_id = calendar_meta['id']
    except TradeError:
        calendar_dates = sorted({day for close_map in benchmark_maps.values() for day in close_map
                                 if body['date_from'] <= day <= body['date_to']})
        calendar_id = None
    return {'request': {**body, 'tdx_source_fingerprint': source_fingerprint(tdx_root), 'kind': 'abnormal', 'as_of_date': body['date_to'],
                        'float_shares_source': None, 'float_shares_error': None,
                        'symbol_names': names['names'], 'symbol_name_sources': names['sources'],
                        'benchmark_maps': benchmark_maps, 'benchmark_sources': benchmark_sources,
                        'missing_benchmarks': missing, 'calendar_dates': calendar_dates,
                        'calendar_dataset_id': calendar_id,
                        'algorithm_version': ALGORITHM_VERSION},
            'symbols': symbols, 'float_shares': {}}


def _abnormal_benchmark(request: dict, symbol: str) -> tuple[dict[str, float], dict | None]:
    preferred = [resolve_benchmark_key(symbol), 'sz_main', 'sh_main', 'gem', 'star', 'beijing']
    for key in dict.fromkeys(preferred):
        close_map = request['benchmark_maps'].get(key, {})
        if close_map:
            return close_map, request['benchmark_sources'].get(key)
    return {}, None


def _abnormal_board_included(symbol: str, name: str, filters: list[str]) -> bool:
    if not filters:
        return True
    is_st = 'ST' in ''.join(name.upper().split())
    boards = set(filters) - {'st'}
    return not ((is_st and 'st' not in filters) or
                (boards and board_of(symbol) not in boards) or
                (not boards and not is_st))


def create_universe_job(session: Session, prepared: dict) -> dict:
    if prepared['request'].get('kind') == 'b1':
        from trade_app.research.registry_service import require_enabled
        admission = require_enabled(session, ['b1_mtf_v1'])
        prepared = {**prepared, 'request': {**prepared['request'], 'registry_admission': admission}}
    active = session.scalar(select(func.count()).select_from(TdxUniverseJob).where(
        TdxUniverseJob.state.in_(('queued', 'running', 'finalizing')))) or 0
    if active >= 2:
        raise TradeError('TDX_UNIVERSE_QUEUE_FULL', '全市场筛选任务队列已满', 429)
    job_id, now = uuid.uuid4().hex, utc_now()
    row = TdxUniverseJob(id=job_id, state='queued',
                         request_json=json.dumps(prepared['request'], ensure_ascii=False),
                         total_count=len(prepared['symbols']), processed_count=0,
                         success_count=0, error_count=0, cancel_requested=0,
                         run_id=None, error_code=None, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    session.add_all(TdxUniverseItem(job_id=job_id, ordinal=index, symbol=symbol,
                                     float_shares=prepared['float_shares'].get(symbol),
                                     state='pending', dataset_id=None, candidate_json=None,
                                     error_code=None)
                    for index, symbol in enumerate(prepared['symbols']))
    session.flush()
    return _view(session, row)


def _view(session: Session, row: TdxUniverseJob) -> dict:
    request = json.loads(row.request_json)
    errors = session.scalars(select(TdxUniverseItem).where(
        TdxUniverseItem.job_id == row.id, TdxUniverseItem.error_code.is_not(None))
        .order_by(TdxUniverseItem.ordinal.desc()).limit(20)).all()
    return {'id': row.id, 'kind': request.get('kind', 'funnel'),
            'state': 'cancelling' if row.state == 'running' and row.cancel_requested else row.state,
            'total': row.total_count, 'processed': row.processed_count,
            'candidates': row.success_count, 'skipped_or_failed': row.error_count,
            'run_id': row.run_id, 'as_of_date': request['as_of_date'],
            'error_code': row.error_code,
            'markets': request['markets'], 'max_bars': request['max_bars'],
            'float_shares_error': request['float_shares_error'],
            'recent_errors': [{'symbol': item.symbol, 'code': item.error_code} for item in errors],
            'created_at': row.created_at, 'updated_at': row.updated_at}


def list_universe_jobs(session: Session) -> list[dict]:
    return [_view(session, row) for row in session.scalars(select(TdxUniverseJob).order_by(
        TdxUniverseJob.created_at.desc(), TdxUniverseJob.id.desc()).limit(30))]


def get_universe_job(session: Session, job_id: str) -> dict:
    row = session.get(TdxUniverseJob, job_id)
    if row is None:
        raise TradeError('TDX_UNIVERSE_JOB_NOT_FOUND', '全市场筛选任务不存在', 404)
    return _view(session, row)


def cancel_universe_job(session: Session, job_id: str) -> dict:
    row = session.get(TdxUniverseJob, job_id)
    if row is None:
        raise TradeError('TDX_UNIVERSE_JOB_NOT_FOUND', '全市场筛选任务不存在', 404)
    if row.state == 'queued':
        row.state = 'cancelled'
    elif row.state in ('running', 'finalizing'):
        row.cancel_requested = 1
    elif row.state != 'cancelled':
        raise TradeError('TDX_UNIVERSE_JOB_FINAL', '已结束的全市场任务不能取消', 409)
    row.updated_at = utc_now()
    session.flush()
    return _view(session, row)


def recover_universe_jobs(factory: sessionmaker) -> None:
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(TdxUniverseJob).where(
            TdxUniverseJob.state.in_(('running', 'finalizing')))):
            row.state = 'cancelled' if row.cancel_requested else 'queued'
            row.cancel_requested = 0
            row.updated_at = utc_now()
        for item in session.scalars(select(TdxUniverseItem).where(TdxUniverseItem.state == 'running')):
            item.state = 'pending'


def _finalize(session: Session, row: TdxUniverseJob, data_dir: Path) -> None:
    request = json.loads(row.request_json)
    items = session.scalars(select(TdxUniverseItem).where(TdxUniverseItem.job_id == row.id)
                            .order_by(TdxUniverseItem.ordinal)).all()
    if request.get('kind') == 'abnormal':
        events = []
        analyzed = 0
        for item in items:
            if item.candidate_json:
                candidate = json.loads(item.candidate_json)
                events.extend(candidate['events'])
                analyzed += int(candidate['analyzed'])
        events.sort(key=lambda event: (0 if event['status'] == 'triggered' else 1,
                                      0 if event['kind'] == '10d' else 1,
                                      event['as_of_date'], event['symbol']), reverse=True)
        quality = ['HISTORICAL_AVAILABLE_AT_UNKNOWN', 'TDX_INPUTS_FROZEN_SEQUENTIALLY']
        if request['calendar_dataset_id'] is None:
            quality.append('REFERENCE_CALENDAR_BENCHMARK_UNION')
        if request['missing_benchmarks']:
            quality.append('BENCHMARK_INDEX_PARTIAL')
        if any(source['fallback'] for source in request['benchmark_sources'].values()):
            quality.append('BENCHMARK_FALLBACK_USED')
        if any(not request.get('symbol_names', {}).get(item.symbol) for item in items):
            quality.append('ST_NAME_UNKNOWN')
        result = {'date_from': request['date_from'], 'date_to': request['date_to'],
                  'trade_date_to': request['calendar_dates'][-1] if request['calendar_dates']
                  else request['date_to'],
                  'scan_mode': request['scan_mode'], 'include_warnings': request['include_warnings'],
                  'cooling_days': request['cooling_days'],
                  'trigger_threshold_10': 100.0, 'trigger_threshold_30': 200.0,
                  'warn_threshold_10': 80.0, 'warn_threshold_30': 160.0,
                  'total_scanned': row.total_count, 'symbols_analyzed': analyzed,
                  'symbols_skipped_no_index': sum(item.error_code == 'BENCHMARK_NOT_FOUND'
                                                  for item in items),
                  'dates': request['calendar_dates'], 'events': events,
                  'excluded': [{'symbol': item.symbol, 'code': item.error_code}
                               for item in items if item.error_code],
                  'quality_flags': quality, 'algorithm_version': request['algorithm_version']}
        frozen_request = {key: value for key, value in request.items()
                          if key not in ('symbol_names', 'benchmark_maps')}
        frozen_request['datasets'] = [{'symbol': item.symbol, 'dataset_id': item.dataset_id}
                                      for item in items if item.dataset_id]
        code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                                   (Path(__file__).parent / 'abnormal_domain.py').read_bytes()).hexdigest()
        run_id = hashlib.sha256(json.dumps({'request': frozen_request, 'code_sha256': code_hash},
                                           ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        saved = save_abnormal_run(session, {'id': run_id, 'request': frozen_request,
                                            'result': result, 'code_sha256': code_hash})
        row.run_id = saved['id']
        row.state = 'succeeded' if row.error_count == 0 else 'partial_failed'
        row.updated_at = utc_now()
        return
    if request.get('kind') == 'sector':
        datasets = {item.symbol[-6:]: get_dataset(session, data_dir, item.dataset_id)
                    for item in items if item.dataset_id}
        result = compute_sector_flow(datasets, request)
        if request['missing_index_codes']:
            result['quality_flags'].append('SECTOR_INDEX_PARTIAL')
        result['missing_index_codes'] = request['missing_index_codes']
        result['excluded'] = [{'symbol': item.symbol, 'code': item.error_code}
                              for item in items if item.error_code]
        frozen_request = {key: value for key, value in request.items() if key != 'symbol_names'}
        frozen_request['datasets'] = [{'symbol': item.symbol, 'dataset_id': item.dataset_id}
                                      for item in items if item.dataset_id]
        code_hash = hashlib.sha256(Path(__file__).read_bytes() + sector_code_hash().encode()).hexdigest()
        run_id = hashlib.sha256(json.dumps({'request': frozen_request, 'code_sha256': code_hash},
                                           ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        saved = save_sector_run(session, {'id': run_id, 'request': frozen_request,
                                          'result': result, 'code_sha256': code_hash})
        row.run_id = saved['id']
        row.state = 'succeeded' if row.error_count == 0 else 'partial_failed'
        row.updated_at = utc_now()
        return
    if request.get('kind') == 'ladder':
        contributions = [json.loads(item.candidate_json) for item in items if item.candidate_json]
        result = compute_ladder_result(contributions, request, total_scanned=row.total_count,
                                       extra_quality=['TDX_INPUTS_FROZEN_SEQUENTIALLY'],
                                       scope='tdx_full_market')
        result['excluded'] = [{'symbol': item.symbol, 'code': item.error_code}
                              for item in items if item.error_code]
        frozen_request = {key: value for key, value in request.items() if key != 'symbol_names'}
        frozen_request['datasets'] = [{'symbol': item.symbol, 'dataset_id': item.dataset_id}
                                      for item in items if item.dataset_id]
        code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                                   (Path(__file__).parent / 'ladder_service.py').read_bytes() +
                                   (Path(__file__).parent.parent / 'market' / 'symbols.py').read_bytes() +
                                   (Path(__file__).parent.parent / 'platform' / 'symbols.py').read_bytes()).hexdigest()
        run_id = hashlib.sha256(json.dumps({'request': frozen_request, 'code_sha256': code_hash},
                                           ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        saved = save_ladder_run(session, {'id': run_id, 'request': frozen_request,
                                          'result': result, 'code_sha256': code_hash})
        row.run_id = saved['id']
        row.state = 'succeeded' if row.error_count == 0 else 'partial_failed'
        row.updated_at = utc_now()
        return
    if request.get('kind') == 'trend':
        daily: dict[str, list[tuple[str, float]]] = {}
        dates: set[str] = set()
        quality = {'TDX_INPUTS_FROZEN_SEQUENTIALLY'}
        for item in items:
            if not item.candidate_json:
                continue
            contribution = json.loads(item.candidate_json)
            dates.update(contribution['dates'])
            if contribution['availability_quality'] == 'historical_availability_unknown':
                quality.add('HISTORICAL_AVAILABLE_AT_UNKNOWN')
            if contribution['amount_missing']:
                quality.add('AMOUNT_NOT_FOUND')
            if contribution['name_missing'] and 'st' not in request['board_filters']:
                quality.add('ST_NAME_UNKNOWN')
            for day, pct in contribution['daily'].items():
                daily.setdefault(day, []).append((item.symbol[-6:], pct))
        all_dates = sorted(dates)
        rankings = {day: [symbol for symbol, _ in sorted(daily.get(day, []),
                     key=lambda pair: (-pair[1], pair[0]))[:request['daily_top_n']]]
                    for day in all_dates}
        winner_symbols = {symbol for symbols in rankings.values() for symbol in symbols}
        winner_items = [item for item in items if item.symbol[-6:] in winner_symbols and item.dataset_id]
        datasets = [get_dataset(session, data_dir, item.dataset_id) for item in winner_items]
        names = {item.symbol[-6:]: request.get('symbol_names', {}).get(item.symbol, '')
                 for item in winner_items}
        result = compute_trend_leaders(datasets, names, request, rankings=rankings,
                                       trading_dates=all_dates, total_scanned=row.total_count,
                                       extra_quality=sorted(quality))
        excluded = [{'symbol': item.symbol, 'code': item.error_code}
                    for item in items if item.error_code]
        result['excluded'] = excluded
        frozen_request = {key: value for key, value in request.items() if key != 'symbol_names'}
        frozen_request['datasets'] = [{'symbol': item.symbol, 'dataset_id': item.dataset_id}
                                      for item in items if item.dataset_id]
        code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                                   (Path(__file__).parent / 'trend_service.py').read_bytes()).hexdigest()
        run_id = hashlib.sha256(json.dumps({'request': frozen_request, 'code_sha256': code_hash},
                                           ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        saved = save_trend_run(session, {'id': run_id, 'request': frozen_request,
                                         'result': result, 'code_sha256': code_hash})
        row.run_id = saved['id']
        row.state = 'succeeded' if row.error_count == 0 else 'partial_failed'
        row.updated_at = utc_now()
        return
    if request.get('kind') == 'b1':
        saved = save_b1_run(session, prepare_b1_universe_run(request, items))
        row.run_id = saved['id']
        row.state = 'succeeded' if row.error_count == 0 else 'partial_failed'
        row.updated_at = utc_now()
        return
    candidates = [ScreenerCandidate.model_validate_json(item.candidate_json)
                  for item in items if item.candidate_json]
    config = FunnelConfig.model_validate(request['config'])
    output = run_funnel(candidates, config)
    excluded = [{'symbol': item.symbol, 'dataset_id': item.dataset_id,
                 'reason': item.error_code} for item in items if item.error_code]
    quality = sorted({flag for item in candidates for flag in item.quality_flags})
    quality.append('TDX_INPUTS_FROZEN_SEQUENTIALLY')
    if request['float_shares_error']:
        quality.append(request['float_shares_error'])
    result = {'as_of_date': request['as_of_date'],
              'return_window_days': request['return_window_days'],
              'config': config.model_dump(), 'summary': output['summary'],
              'pools': output['pools'], 'rejections': output['rejections'],
              'excluded': excluded, 'quality_flags': quality,
              'metric_label': 'legacy_formula_candidate_confidence_not_ai_model'}
    frozen_request = {'source': 'tdx_universe', 'markets': request['markets'],
                      'as_of_date': request['as_of_date'],
                      'return_window_days': request['return_window_days'],
                      'max_bars': request['max_bars'],
                      'config': request['config'],
                      'float_shares_source': request['float_shares_source'],
                      'float_shares_error': request['float_shares_error'],
                      'symbol_name_sources': request.get('symbol_name_sources', []),
                      'input_exclusions': excluded,
                      'datasets': [{'dataset_id': item.dataset_id,
                                    'float_shares': item.float_shares,
                                    'float_shares_as_of_date': None,
                                    'float_shares_source_sha256': request['float_shares_source']['sha256']
                                    if request['float_shares_source'] else None}
                                   for item in items if item.dataset_id]}
    code_hash = _digest()
    run_id = hashlib.sha256(json.dumps({'request': frozen_request, 'code_sha256': code_hash},
                                      ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    saved = save_screener_run(session, {'id': run_id, 'request': frozen_request,
                                        'result': result, 'code_sha256': code_hash})
    row.run_id = saved['id']
    row.state = 'succeeded' if row.error_count == 0 else 'partial_failed'
    row.updated_at = utc_now()


def process_one_universe_symbol(factory: sessionmaker, data_dir: Path,
                                tdx_root: Path | None) -> bool:
    finalize_id = None
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.scalar(select(TdxUniverseJob).where(TdxUniverseJob.state.in_(('queued', 'running', 'finalizing')))
                             .order_by(TdxUniverseJob.created_at, TdxUniverseJob.id).limit(1))
        if row is None:
            return False
        if row.cancel_requested:
            row.state = 'cancelled'
            row.updated_at = utc_now()
            return True
        if row.state == 'finalizing':
            finalize_id = row.id
        else:
            request = json.loads(row.request_json)
            expected_source = request.get('tdx_source_fingerprint')
            if tdx_root is None or (expected_source and expected_source != source_fingerprint(tdx_root)):
                if row.error_code != 'TDX_SOURCE_SELECTION_REQUIRED':
                    row.error_code = 'TDX_SOURCE_SELECTION_REQUIRED'
                    row.updated_at = utc_now()
                return False  # Wait for selection of the original source; never mix installations.
            if row.error_code == 'TDX_SOURCE_SELECTION_REQUIRED':
                row.error_code = None
            item = session.scalar(select(TdxUniverseItem).where(
                TdxUniverseItem.job_id == row.id, TdxUniverseItem.state == 'pending')
                .order_by(TdxUniverseItem.ordinal).limit(1))
            if item is None:
                row.state = 'finalizing'
                row.updated_at = utc_now()
                return True
            row.state = 'running'
            item.state = 'running'
            row.updated_at = utc_now()
            job_id, ordinal, symbol, shares = row.id, item.ordinal, item.symbol, item.float_shares
            request = json.loads(row.request_json)
    if finalize_id is not None:
        try:
            with factory.begin() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                row = session.get(TdxUniverseJob, finalize_id)
                if row and row.state == 'finalizing' and not row.cancel_requested:
                    _finalize(session, row, data_dir)
        except Exception as exc:
            with factory.begin() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                row = session.get(TdxUniverseJob, finalize_id)
                if row and row.state == 'finalizing':
                    row.state = 'failed'
                    row.error_code = f'FINALIZE_{type(exc).__name__}'[:80]
                    row.updated_at = utc_now()
        return True
    try:
        with factory.begin() as session:
            dataset_meta = import_tdx_day(session, data_dir, symbol, tdx_root,
                                          max_bars=request['max_bars'])
            dataset = get_dataset(session, data_dir, dataset_meta['id'])
            if request.get('kind') == 'b1':
                bars = eligible_b1_bars(dataset['bars'], request['as_of_date'])
                params = B1Params(**request['b1_params'])
                if len(bars) < params.min_total_bars:
                    candidate = None
                else:
                    source_bars = [{'date': bar['event_date'],
                                    **{key: float(bar[key]) for key in ('open', 'high', 'low', 'close')},
                                    'volume': bar['volume']} for bar in bars]
                    hit = check_b1(symbol[-6:], source_bars, params)
                    candidate = {**hit, 'symbol': symbol, 'dataset_id': dataset_meta['id'],
                                 'name': request.get('symbol_names', {}).get(symbol, '')} if hit else {}
            elif request.get('kind') == 'trend':
                candidate = trend_contribution(dataset,
                                               request.get('symbol_names', {}).get(symbol, ''),
                                               request)
            elif request.get('kind') == 'ladder':
                candidate = ladder_contribution(dataset,
                                                request.get('symbol_names', {}).get(symbol, ''),
                                                request)
            elif request.get('kind') == 'sector':
                candidate = {'code': symbol[-6:], 'dataset_id': dataset_meta['id']}
            elif request.get('kind') == 'abnormal':
                name = request.get('symbol_names', {}).get(symbol, '')
                index_close, benchmark = _abnormal_benchmark(request, symbol)
                if not index_close:
                    raise TradeError('BENCHMARK_NOT_FOUND', '缺少异动扫描基准指数')
                if _abnormal_board_included(symbol, name, request['board_filters']):
                    bars = [{'date': bar['event_date'],
                             **{field: float(bar[field]) for field in ('open', 'high', 'low', 'close')},
                             'volume': bar['volume'],
                             'amount': float(bar['amount']) if bar.get('amount') is not None else 0.0}
                            for bar in dataset['bars'] if bar['event_date'] <= request['date_to']]
                    matches = analyze_symbol_abnormal_events(
                        symbol=symbol, name=name, bars=bars,
                        index_close_by_date=index_close,
                        date_from=request['date_from'], date_to=request['date_to'],
                        include_warnings=request['include_warnings'],
                        snapshot_only=request['scan_mode'] == 'snapshot',
                        trading_dates=request['calendar_dates'],
                        cooling_days=request['cooling_days'])
                    events = [{**asdict(event), 'dataset_id': dataset_meta['id'],
                               'benchmark_symbol': benchmark['symbol'],
                               'benchmark_name': benchmark['name'],
                               'benchmark_dataset_id': benchmark['dataset_id']}
                              for event in matches]
                    candidate = {'events': events, 'analyzed': bool(bars)}
                else:
                    candidate = {'events': [], 'analyzed': False}
            else:
                candidate = build_candidate(dataset, request['as_of_date'],
                                            request['return_window_days'], shares)
                if candidate and request.get('symbol_names', {}).get(symbol):
                    candidate = candidate.model_copy(update={
                        'name': request['symbol_names'][symbol]})
        dataset_id = dataset_meta['id']
        if request.get('kind') == 'b1':
            candidate_json = json.dumps(candidate, ensure_ascii=False) if candidate else None
            error_code = 'INSUFFICIENT_BARS_AS_OF_DATE' if candidate is None else None
        elif request.get('kind') in ('trend', 'ladder', 'sector', 'abnormal'):
            candidate_json = json.dumps(candidate, ensure_ascii=False)
            error_code = None
        else:
            candidate_json = candidate.model_dump_json() if candidate else None
            error_code = None if candidate else 'INSUFFICIENT_BARS_AS_OF_DATE'
    except TradeError as exc:
        dataset_id, candidate_json, error_code = None, None, exc.code
    except Exception:
        dataset_id, candidate_json, error_code = None, None, 'TDX_UNIVERSE_ITEM_FAILED'
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.get(TdxUniverseJob, job_id)
        item = session.get(TdxUniverseItem, (job_id, ordinal))
        if row is None or item is None:
            return True
        item.state = 'done'
        item.dataset_id = dataset_id
        item.candidate_json = candidate_json
        item.error_code = error_code
        row.processed_count += 1
        row.success_count += int(candidate_json is not None)
        row.error_count += int(error_code is not None)
        if row.cancel_requested:
            row.state = 'cancelled'
        elif row.processed_count >= row.total_count:
            row.state = 'finalizing'
        else:
            row.state = 'queued'
        row.updated_at = utc_now()
    return True
