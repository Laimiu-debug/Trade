"""Frozen-universe four-step screener runs."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.tdx_float_shares import read_tdx_float_shares
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.screener_domain import FunnelConfig, run_funnel
from trade_app.research.screener_metrics import build_candidate
from trade_app.research.screener_models import ScreenerRun
from trade_app.research.models import ResearchRun
from trade_app.research.service import run_data


def _digest() -> str:
    root = Path(__file__).resolve().parent
    files = [root / name for name in (
        'screener_domain.py', 'screener_metrics.py', 'screener_service.py',
        'tdx_universe.py')]
    files.extend(root.parent / 'market' / name for name in
                 ('tdx_float_shares.py', 'tdx_names.py', 'tdx.py'))
    return hashlib.sha256(b''.join(path.read_bytes() for path in files)).hexdigest()


def prepare_screener_run(session: Session, data_dir: Path, body: dict,
                         tdx_root: Path | None = None) -> dict:
    config = FunnelConfig.model_validate(body['config'])
    seen_ids, seen_symbols = set(), set()
    candidates, excluded = [], []
    sourced = [item for item in body['datasets'] if item.get('float_shares_source_sha256')]
    source_check = None
    if sourced:
        with session.no_autoflush:
            symbols = [get_dataset(session, data_dir, item['dataset_id'])['symbol'] for item in sourced]
        source_check = read_tdx_float_shares(tdx_root, symbols)
    for item in body['datasets']:
        dataset_id = item['dataset_id']
        if dataset_id in seen_ids:
            raise TradeError('DUPLICATE_DATASET', '同一冻结行情样本不能重复加入筛选池')
        seen_ids.add(dataset_id)
        dataset = get_dataset(session, data_dir, dataset_id)
        if dataset['symbol'] in seen_symbols:
            raise TradeError('DUPLICATE_SYMBOL', '同一证券只能选择一个冻结行情样本')
        seen_symbols.add(dataset['symbol'])
        if dataset['adjustment'] != 'none':
            raise TradeError('ADJUSTMENT_UNSUPPORTED', '四步漏斗当前只接受不复权日线样本')
        source_hash = item.get('float_shares_source_sha256')
        if source_hash:
            current_value = source_check['requested_values'].get(dataset['symbol']) if source_check else None
            if source_check['source']['sha256'] != source_hash or current_value is None or item.get('float_shares') is None or not math.isclose(current_value, item['float_shares'], rel_tol=0, abs_tol=0.1):
                raise TradeError('FLOAT_SHARES_SOURCE_CHANGED', '通达信股本来源或数值已变化，请重新读取', 409)
        candidate = build_candidate(dataset, body['as_of_date'], body['return_window_days'],
                                    item.get('float_shares'), item.get('float_shares_as_of_date'))
        if candidate is None:
            excluded.append({'symbol': dataset['symbol'], 'dataset_id': dataset_id,
                             'reason': 'INSUFFICIENT_BARS_AS_OF_DATE'})
        else:
            candidates.append(candidate)
    output = run_funnel(candidates, config)
    input_quality = sorted({flag for item in candidates for flag in item.quality_flags})
    result = {'as_of_date': body['as_of_date'], 'return_window_days': body['return_window_days'],
              'config': config.model_dump(), 'summary': output['summary'],
              'pools': output['pools'], 'rejections': output['rejections'], 'excluded': excluded,
              'quality_flags': input_quality,
              'metric_label': 'legacy_formula_candidate_confidence_not_ai_model'}
    code_hash = _digest()
    identity = {'request': body, 'code_sha256': code_hash}
    run_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return {'id': run_id, 'request': body, 'result': result, 'code_sha256': code_hash}


def save_screener_run(session: Session, prepared: dict) -> dict:
    row = session.get(ScreenerRun, prepared['id'])
    if row is None:
        row = ScreenerRun(id=prepared['id'],
                          request_json=json.dumps(prepared['request'], ensure_ascii=False, sort_keys=True),
                          result_json=json.dumps(prepared['result'], ensure_ascii=False),
                          code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, detail=True)


def _view(row: ScreenerRun, *, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at, 'code_sha256': row.code_sha256,
            'as_of_date': result['as_of_date'], 'summary': result['summary'],
            'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def list_screener_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(ScreenerRun).order_by(
        ScreenerRun.created_at.desc(), ScreenerRun.id.desc()).limit(100))]


def get_screener_run(session: Session, run_id: str) -> dict:
    row = session.get(ScreenerRun, run_id)
    if row is None:
        raise TradeError('SCREENER_RUN_NOT_FOUND', '筛选记录不存在', 404)
    return _view(row, detail=True)


def promote_screener_finalist(session: Session, data_dir: Path,
                              run_id: str, dataset_id: str) -> dict:
    """Explicitly promote one final-stage frozen candidate to an observation signal."""
    row = session.get(ScreenerRun, run_id)
    if row is None:
        raise TradeError('SCREENER_RUN_NOT_FOUND', '筛选记录不存在', 404)
    result = json.loads(row.result_json)
    candidate = next((item for item in result['pools']['step4']
                      if item['dataset_id'] == dataset_id), None)
    if candidate is None:
        raise TradeError('SCREENER_FINALIST_NOT_FOUND', '该样本不是最终观察池成员', 404)
    dataset = get_dataset(session, data_dir, dataset_id)
    identity = {'source_screener_run_id': run_id, 'dataset_id': dataset_id,
                'code_sha256': row.code_sha256}
    signal_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()
    existing = session.get(ResearchRun, signal_id)
    if existing is not None:
        return run_data(existing)
    decision_at = datetime.combine(date.fromisoformat(result['as_of_date']),
                                   time(23, 59, 59), tzinfo=ZoneInfo('Asia/Shanghai'))
    signal_result = {'status': 'computed', 'signal': True,
                     'candidate': {'symbol': dataset['symbol'], 'dataset_id': dataset_id,
                                   'screener_run_id': run_id, 'score': candidate['score'],
                                   'close_date': candidate['as_of_date']},
                     'source_date': candidate['as_of_date'],
                     'quality_flags': candidate['quality_flags'],
                     'score': None, 'executable_date': None}
    signal = ResearchRun(id=signal_id, dataset_id=dataset_id,
                         strategy_id='four_step_funnel_v1', strategy_version='legacy_funnel_frozen_v1',
                         decision_at=decision_at.astimezone(timezone.utc).isoformat(),
                         strict=0, params_json=json.dumps({'source_screener_run_id': run_id,
                                                           'config': result['config']},
                                                          ensure_ascii=False, sort_keys=True),
                         result_json=json.dumps(signal_result, ensure_ascii=False, sort_keys=True),
                         created_at=utc_now())
    session.add(signal)
    session.flush()
    return run_data(signal)
