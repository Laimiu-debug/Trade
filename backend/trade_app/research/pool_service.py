"""Persist cross-sectional strategies against immutable screener snapshots."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.models import ResearchRun
from trade_app.research.pool_models import StrategyPoolRun
from trade_app.research.screener_models import ScreenerRun


STRATEGY_ID = 'matrix_signal_v1'
STRATEGY_VERSION = '1.0.0-alpha'
CALCULATION_VERSION = 'legacy-matrix-plugin-frozen-pool-v1'


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _code_digest() -> str:
    root = Path(__file__).parent
    paths = [root / name for name in ('pool_service.py', 'matrix_domain.py', 'legacy_catalog.json')]
    paths.extend(root.parent / name for name in ('market/symbols.py', 'platform/symbols.py', 'platform/types.py'))
    return hashlib.sha256(b''.join(path.read_bytes() for path in paths)).hexdigest()


def _view(row: StrategyPoolRun, detail: bool = True) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'strategy_id': row.strategy_id, 'source_run_id': row.source_run_id,
            'code_sha256': row.code_sha256, 'created_at': row.created_at,
            'as_of_date': result['as_of_date'], 'summary': result['summary'],
            'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def create_matrix_run(session: Session, body: dict) -> dict:
    from trade_app.research.matrix_domain import normalize_matrix_params, evaluate_matrix_pool

    from trade_app.research.registry_service import require_enabled
    require_enabled(session, [STRATEGY_ID])
    source = session.get(ScreenerRun, body['source_run_id'])
    if source is None:
        raise TradeError('SCREENER_RUN_NOT_FOUND', '请先选择已保存的冻结筛选记录', 404)
    snapshot = json.loads(source.result_json)
    if snapshot.get('return_window_days') != 40:
        raise TradeError('MATRIX_RETURN_WINDOW_REQUIRED', '矩阵插件需要 40 日指标，请选择窗口为 40 的筛选记录', 409)
    params = normalize_matrix_params(body.get('params') or {})
    request = {'source_run_id': source.id, 'strict': body['strict'], 'params': params}
    code_hash = _code_digest()
    source_hash = _digest({'request': json.loads(source.request_json), 'result': snapshot,
                           'code_sha256': source.code_sha256})
    identity = {**request, 'source_sha256': source_hash, 'code_sha256': code_hash,
                'calculation_version': CALCULATION_VERSION, 'strategy_version': STRATEGY_VERSION}
    run_id = _digest(identity)
    existing = session.get(StrategyPoolRun, run_id)
    if existing is not None:
        return _view(existing)
    candidates, excluded = [], []
    for candidate in snapshot['pools']['input']:
        reasons = []
        if candidate['as_of_date'] != snapshot['as_of_date']:
            reasons.append('CANDIDATE_DATE_MISMATCH')
        if body['strict'] and 'HISTORICAL_AVAILABLE_AT_UNKNOWN' in candidate.get('quality_flags', []):
            reasons.append('HISTORICAL_AVAILABLE_AT_UNKNOWN')
        if reasons:
            excluded.append({'symbol': candidate['symbol'], 'dataset_id': candidate['dataset_id'],
                             'reasons': reasons})
        else:
            candidates.append(candidate)
    evaluated = evaluate_matrix_pool(candidates, params)
    result = {**evaluated, 'as_of_date': snapshot['as_of_date'], 'excluded': excluded,
              'source_sha256': source_hash, 'source_code_sha256': source.code_sha256,
              'calculation_version': CALCULATION_VERSION,
              'metric_label': 'legacy_matrix_plugin_proxy',
              'quality_flags': sorted(set(snapshot.get('quality_flags', [])) | {'MATRIX_PLUGIN_APPROXIMATION'}),
              'limitations': ['使用旧策略插件的近似指标和评分', 'Top N 仅相对于所选冻结输入池',
                              '事件质量分、S8/S9 出场及向量化矩阵回测尚未迁入']}
    result['summary']['source_count'] = len(snapshot['pools']['input'])
    result['summary']['excluded_count'] = len(excluded)
    row = StrategyPoolRun(id=run_id, strategy_id=STRATEGY_ID, source_run_id=source.id,
                          request_json=json.dumps(request, ensure_ascii=False, sort_keys=True),
                          result_json=json.dumps(result, ensure_ascii=False, allow_nan=False),
                          code_sha256=code_hash, created_at=utc_now())
    session.add(row)
    session.flush()
    return _view(row)


def list_matrix_runs(session: Session) -> list[dict]:
    return [_view(row, False) for row in session.scalars(select(StrategyPoolRun).where(
        StrategyPoolRun.strategy_id == STRATEGY_ID).order_by(
        StrategyPoolRun.created_at.desc(), StrategyPoolRun.id).limit(100))]


def get_matrix_run(session: Session, run_id: str) -> dict:
    row = session.get(StrategyPoolRun, run_id)
    if row is None or row.strategy_id != STRATEGY_ID:
        raise TradeError('MATRIX_RUN_NOT_FOUND', '矩阵策略记录不存在', 404)
    return _view(row)


def promote_matrix_signal(session: Session, data_dir: Path, run_id: str, dataset_id: str) -> dict:
    from trade_app.research.service import run_data

    run = get_matrix_run(session, run_id)
    result = run['result']
    candidate = next((item for item in result['rows']
                      if item['dataset_id'] == dataset_id and item['signal']), None)
    if candidate is None:
        raise TradeError('MATRIX_SIGNAL_NOT_FOUND', '该样本未通过入池与买点条件', 409)
    dataset = get_dataset(session, data_dir, dataset_id)
    signal_id = _digest({'source_matrix_run_id': run_id, 'dataset_id': dataset_id})
    existing = session.get(ResearchRun, signal_id)
    if existing is not None:
        return run_data(existing)
    decision = datetime.combine(date.fromisoformat(result['as_of_date']), time(23, 59, 59),
                                tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    signal_result = {'status': 'computed', 'signal': True, 'draft_eligible': True,
                     'candidate': {'symbol': dataset['symbol'], 'date': result['as_of_date'],
                                   'matrix_run_id': run_id},
                     'source_date': result['as_of_date'], 'score': None, 'executable_date': None,
                     'evaluation': candidate, 'quality_flags': result['quality_flags'],
                     'calculation_version': CALCULATION_VERSION, 'code_sha256': run['code_sha256']}
    row = ResearchRun(id=signal_id, dataset_id=dataset_id, strategy_id=STRATEGY_ID,
                      strategy_version=STRATEGY_VERSION, decision_at=decision.isoformat(),
                      strict=int(run['request']['strict']),
                      params_json=json.dumps({**run['request']['params'], 'source_matrix_run_id': run_id}, sort_keys=True),
                      result_json=json.dumps(signal_result, ensure_ascii=False, sort_keys=True), created_at=utc_now())
    session.add(row)
    session.flush()
    return run_data(row)
