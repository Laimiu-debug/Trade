"""Auditable B1 scans over explicitly selected immutable daily datasets."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.b1_domain import B1Params, check_b1
from trade_app.research.b1_models import B1Run
from trade_app.research.models import ResearchRun
from trade_app.research.service import run_data


def eligible_b1_bars(bars: list[dict], as_of_date: str) -> list[dict]:
    cutoff = datetime.combine(date.fromisoformat(as_of_date) + timedelta(days=1), time.min,
                              tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    return [bar for bar in bars if bar['event_date'] <= as_of_date and
            (bar['available_at'] is None or datetime.fromisoformat(bar['available_at']) < cutoff)]


def prepare_b1_run(session: Session, data_dir: Path, body: dict) -> dict:
    from trade_app.research.b1_params import normalize_b1_params
    from trade_app.research.registry_service import require_enabled
    admission = require_enabled(session, ['b1_mtf_v1'])
    body = {**body, 'b1_params': normalize_b1_params(body.get('b1_params', {}))}
    params = B1Params(**body['b1_params'])
    seen = set()
    hits, excluded = [], []
    quality = set()
    as_of_date = body['as_of_date']
    for dataset_id in body['dataset_ids']:
        dataset = get_dataset(session, data_dir, dataset_id)
        symbol = dataset['symbol']
        if symbol in seen:
            raise TradeError('DUPLICATE_SYMBOL', 'B1 扫描中同一证券只能选择一个冻结样本')
        seen.add(symbol)
        if dataset['adjustment'] != 'none':
            raise TradeError('ADJUSTMENT_UNSUPPORTED', 'B1 扫描只接受不复权日线')
        if dataset['availability_quality'] == 'historical_availability_unknown':
            quality.add('HISTORICAL_AVAILABLE_AT_UNKNOWN')
        bars = eligible_b1_bars(dataset['bars'], as_of_date)
        if len(bars) < params.min_total_bars:
            excluded.append({'symbol': symbol, 'dataset_id': dataset_id,
                             'reason': 'INSUFFICIENT_BARS_AS_OF_DATE'})
            continue
        source_bars = [{'date': bar['event_date'],
                        **{key: float(bar[key]) for key in ('open', 'high', 'low', 'close')},
                        'volume': bar['volume']} for bar in bars]
        hit = check_b1(symbol[-6:], source_bars, params)
        if hit:
            hits.append({**hit, 'symbol': symbol, 'dataset_id': dataset_id})
    result = {'total_scanned': len(body['dataset_ids']), 'hit_count': len(hits),
              'hits': hits, 'excluded': excluded, 'b1_params': body['b1_params'],
              'as_of_date': as_of_date, 'quality_flags': sorted(quality),
              'sort_order': 'input_dataset_order'}
    code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                               (Path(__file__).with_name('b1_domain.py')).read_bytes() +
                               (Path(__file__).with_name('b1_params.py')).read_bytes()).hexdigest()
    identity = json.dumps({'request': body, 'code_sha256': code_hash},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'id': hashlib.sha256(identity).hexdigest(), 'request': body,
            'result': result, 'code_sha256': code_hash, 'registry_admission': admission}


def save_user_b1_run(session: Session, prepared: dict) -> dict:
    from trade_app.research.registry_service import require_enabled
    require_enabled(session, ['b1_mtf_v1'], expected_revision=prepared['registry_admission']['revision'])
    return save_b1_run(session, prepared)


def prepare_b1_universe_run(request: dict, items: list) -> dict:
    """Publish a completed sequential TDX scan using its frozen dataset references."""
    hits = [json.loads(item.candidate_json) for item in items if item.candidate_json]
    excluded = [{'symbol': item.symbol, 'dataset_id': item.dataset_id,
                 'reason': item.error_code} for item in items if item.error_code]
    frozen_request = {'source': 'tdx_universe', 'markets': request['markets'],
                      'as_of_date': request['as_of_date'], 'max_bars': request['max_bars'],
                      'b1_params': request['b1_params'], 'input_exclusions': excluded,
                      'symbol_name_sources': request.get('symbol_name_sources', []),
                      'datasets': [{'dataset_id': item.dataset_id} for item in items if item.dataset_id]}
    result = {'total_scanned': len(items), 'hit_count': len(hits), 'hits': hits,
              'excluded': excluded, 'b1_params': request['b1_params'],
              'as_of_date': request['as_of_date'],
              'quality_flags': ['HISTORICAL_AVAILABLE_AT_UNKNOWN', 'TDX_INPUTS_FROZEN_SEQUENTIALLY'],
              'sort_order': 'symbol_ascending'}
    code_hash = hashlib.sha256(Path(__file__).read_bytes() +
                               (Path(__file__).with_name('b1_domain.py')).read_bytes() +
                               (Path(__file__).with_name('b1_params.py')).read_bytes()).hexdigest()
    identity = json.dumps({'request': frozen_request, 'code_sha256': code_hash},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'id': hashlib.sha256(identity).hexdigest(), 'request': frozen_request,
            'result': result, 'code_sha256': code_hash}


def _view(row: B1Run, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at,
            'as_of_date': result['as_of_date'], 'total_scanned': result['total_scanned'],
            'hit_count': result['hit_count'], 'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def save_b1_run(session: Session, prepared: dict) -> dict:
    row = session.get(B1Run, prepared['id'])
    if row is None:
        row = B1Run(id=prepared['id'], request_json=json.dumps(prepared['request'], ensure_ascii=False),
                    result_json=json.dumps(prepared['result'], ensure_ascii=False),
                    code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, True)


def list_b1_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(B1Run).order_by(
        B1Run.created_at.desc(), B1Run.id.desc()).limit(100))]


def get_b1_run(session: Session, run_id: str) -> dict:
    row = session.get(B1Run, run_id)
    if row is None:
        raise TradeError('B1_RUN_NOT_FOUND', 'B1 扫描记录不存在', 404)
    return _view(row, True)


def promote_b1_hit(session: Session, data_dir: Path, run_id: str, symbol: str) -> dict:
    """Explicitly turn one frozen B1 hit into a draft-eligible observation signal."""
    row = session.get(B1Run, run_id)
    if row is None:
        raise TradeError('B1_RUN_NOT_FOUND', 'B1 扫描记录不存在', 404)
    result = json.loads(row.result_json)
    hit = next((item for item in result['hits'] if item['symbol'] == symbol), None)
    if hit is None:
        raise TradeError('B1_HIT_NOT_FOUND', '该证券不是这次 B1 扫描的命中结果', 404)
    dataset = get_dataset(session, data_dir, hit['dataset_id'])
    eligible = eligible_b1_bars(dataset['bars'], result['as_of_date'])
    if not eligible:
        raise TradeError('B1_SOURCE_MISSING', 'B1 命中没有可用的冻结日线', 409)
    identity = {'source_b1_run_id': run_id, 'dataset_id': dataset['id'],
                'symbol': symbol, 'code_sha256': row.code_sha256}
    signal_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()
    existing = session.get(ResearchRun, signal_id)
    if existing is not None:
        return run_data(existing)
    decision_at = datetime.combine(date.fromisoformat(result['as_of_date']),
                                   time(23, 59, 59), tzinfo=ZoneInfo('Asia/Shanghai'))
    signal_result = {'status': 'computed', 'signal': True,
                     'candidate': {'symbol': dataset['symbol'], 'b1_run_id': run_id,
                                   'dataset_id': dataset['id'], 'close': hit['close'],
                                   'kdj_j': hit['kdj_j'], 'volume_ratio': hit['volume_ratio']},
                     'source_date': eligible[-1]['event_date'],
                     'quality_flags': result['quality_flags'],
                     'score': None, 'executable_date': None}
    signal = ResearchRun(id=signal_id, dataset_id=dataset['id'],
                         strategy_id='b1_mtf_v1', strategy_version='legacy_b1_frozen_v1',
                         decision_at=decision_at.astimezone(timezone.utc).isoformat(),
                         strict=0, params_json=json.dumps({'source_b1_run_id': run_id,
                                                           'b1_params': result['b1_params']},
                                                          ensure_ascii=False, sort_keys=True),
                         result_json=json.dumps(signal_result, ensure_ascii=False, sort_keys=True),
                         created_at=utc_now())
    session.add(signal)
    session.flush()
    return run_data(signal)
