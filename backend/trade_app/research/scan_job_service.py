"""Durable frozen scans, with CPU work and content verification outside writes.

Each quantum yields to the shared CPU dispatcher. Private checkpoints live in
SQLite, so ordinary database backups can resume without unarchived temp files.
Only final publication makes ResearchRuns and the aggregate scan visible.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time as clock
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from trade_app.market.models import MarketDataset
from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.compute_process import ComputeBudget, ComputeProcessError, run_json_process
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.models import ResearchRun
from trade_app.research.scan_models import StrategyScanRun
from trade_app.research.scan_job_models import StrategyScanChunk, StrategyScanJob
from trade_app.research.scan_job_worker import (MAX_CHUNK_BYTES, MAX_CHUNK_EVALUATIONS,
                                                digest, encode, read_dataset, run_identity)
from trade_app.research.scan_service import (CALCULATION_VERSION, MAX_RANGE_EVALUATIONS,
                                            MAX_SINGLE_EVALUATIONS, PROFILE_STRATEGIES,
                                            _aggregate, _evidence, _request)
from trade_app.research.service import describe_strategy, normalize_strategy_params

SCAN_JOB_VERSION = 'frozen-scan-checkpoints-v2-signal-context'
SCAN_BUDGET = ComputeBudget(version='scan-quantum-v1', timeout_seconds=30,
                            input_bytes=1024 * 1024, output_bytes=MAX_CHUNK_BYTES)
MAX_JOB_BYTES = 16 * 1024 * 1024
MAX_JOB_COMPUTE_MS = 600_000
MAX_QUEUED_JOBS = 10


def code_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = ('research/scan_job_service.py', 'research/scan_job_worker.py',
             'research/scan_service.py', 'research/legacy_catalog.json',
             'research/event_profile_service.py', 'platform/compute_process.py', 'platform/runtime.py',
             'market/service.py', 'market/symbols.py', 'platform/symbols.py', 'platform/types.py')
    return hashlib.sha256(b''.join((root / path).read_bytes() for path in paths)).hexdigest()


def prepare_scan_job(session: Session, data_dir: Path, body: dict) -> dict:
    """Read-only preparation. Call before opening the HTTP write transaction."""
    request = _request(body)
    context = request.get('signal_context')
    from trade_app.research.signal_context import describe_context_strategy, normalize_context_params, candidate
    from trade_app.market.domain import eligible_bars
    from trade_app.research.registry_service import require_enabled
    admission = require_enabled(session, [row['strategy_id'] for row in request['strategies']])
    descriptors = [{**(describe_context_strategy if context else describe_strategy)(row['strategy_id']),
                    'params': (normalize_context_params if context else normalize_strategy_params)(row['strategy_id'], row['params'])}
                   for row in request['strategies']]
    datasets = []
    seen = set()
    plan = []
    for dataset_id in request['dataset_ids']:
        dataset = get_dataset(session, data_dir, dataset_id)
        exchange, code = normalize_a_share_symbol(dataset['symbol'])
        symbol = exchange + code
        if symbol in seen:
            raise TradeError('DUPLICATE_SCAN_SYMBOL', '同一证券只能选择一个冻结行情样本，别名也视为重复')
        seen.add(symbol)
        datasets.append({'id': dataset_id, 'symbol': symbol, 'imported_symbol': dataset['symbol'],
                         'provider': dataset['provider'], 'adjustment': dataset['adjustment'],
                         'content_sha256': dataset_id})
        days = ([request['as_of_date']] if request['mode'] == 'single_date' else
                [bar['event_date'] for bar in dataset['bars']
                 if request['date_from'] <= bar['event_date'] <= request['date_to']])
        for day in days:
            decision = datetime.combine(date.fromisoformat(day), time(23, 59, 59),
                                        tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc).isoformat()
            metrics = None
            if context:
                bars, _ = eligible_bars(dataset['bars'], decision, request['strict'])
                metrics = candidate([bar for bar in bars if bar['event_date'] <= day],
                                    {**dataset,'symbol':symbol},context,day)
            for strategy in descriptors:
                plan.append({'dataset_id': dataset_id, 'symbol': symbol, 'decision_date': day,
                             'decision_at': decision, 'strategy_id': strategy['strategy_id'],
                             **({'signal_candidate':metrics} if context else {})})
    limit = MAX_SINGLE_EVALUATIONS if request['mode'] == 'single_date' else MAX_RANGE_EVALUATIONS
    if not plan:
        raise TradeError('SCAN_RANGE_EMPTY', '所选区间没有样本交易日')
    if len(plan) > limit:
        raise TradeError('SCAN_TOO_LARGE', f'本次扫描共 {len(plan)} 次判断，当前上限为 {limit}')
    if context:
        # S3 admission is a cross-security Top N. Freeze its complete selected
        # same-day pool and use canonical symbol order for reproducible ties.
        from trade_app.research.matrix_domain import evaluate_matrix_pool, MATRIX_PARAM_KEYS
        matrix = next((row for row in descriptors if row['strategy_id']=='matrix_signal_v1'),None)
        if matrix:
            matrix_params = {key:matrix['params'][key] for key in MATRIX_PARAM_KEYS}
            for day in sorted({item['decision_date'] for item in plan}):
                items = sorted((item for item in plan if item['decision_date']==day and item['strategy_id']=='matrix_signal_v1'),key=lambda item:item['symbol'])
                candidates = [item['signal_candidate'] for item in items if item['signal_candidate'] is not None]
                evaluated = evaluate_matrix_pool(candidates,matrix_params)
                rows = {row['dataset_id']:row for row in evaluated['rows']}
                pool_hash = digest({'decision_date':day,'candidates':candidates,'params':matrix_params})
                for item in items:
                    item['signal_pool'] = {'sha256':pool_hash,'candidate_count':len(candidates),
                        'tie_order':'canonical_symbol_ascending','evaluation':rows.get(item['dataset_id'])}
    uses_profile = bool(context) or any(row['strategy_id'] in PROFILE_STRATEGIES for row in descriptors)
    if not uses_profile and (body.get('event_profile_id') is not None or body.get('event_profile_revision') is not None):
        raise TradeError('EVENT_PROFILE_NOT_APPLICABLE', '所选策略不使用维科夫事件模板')
    request['registry_admission'] = admission
    request['event_profile'] = (freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
                                if uses_profile else None)
    request['datasets'] = sorted(datasets, key=lambda row: row['symbol'])
    request['strategies'] = [{key: value for key, value in descriptor.items() if key != 'code_sha256'}
                             for descriptor in descriptors]
    frozen = {'version': SCAN_JOB_VERSION, 'request': request, 'strategies': descriptors,
              'plan': sorted(plan, key=lambda row: (row['symbol'], row['decision_date'], row['strategy_id'])),
              'code_sha256': code_sha256(), 'compute_budget': SCAN_BUDGET.to_dict(),
              'max_job_bytes': MAX_JOB_BYTES, 'max_compute_ms': MAX_JOB_COMPUTE_MS,
              'chunk_evaluations': MAX_CHUNK_EVALUATIONS}
    # Detach all caller-owned lists; the submitted parameters cannot change later.
    return json.loads(encode(frozen))


def _queue_guard(session: Session) -> None:
    count = session.scalar(select(func.count()).select_from(StrategyScanJob).where(
        StrategyScanJob.state.in_(('queued', 'running')))) or 0
    if count >= MAX_QUEUED_JOBS:
        raise TradeError('SCAN_QUEUE_FULL', '扫描队列已满，请等待现有任务完成', 429)


def enqueue_scan_job(session: Session, prepared: dict) -> dict:
    """Accept trusted prepare_scan_job output; no file I/O or calculations here."""
    encoded = encode(prepared)
    job_id = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    existing = session.get(StrategyScanJob, job_id)
    if existing:
        return _data(existing, detail=True)
    from trade_app.research.registry_service import require_enabled
    require_enabled(session, [row['strategy_id'] for row in prepared['strategies']],
                    expected_revision=prepared['request'].get('registry_admission', {}).get('revision'))
    _queue_guard(session)
    for item in prepared['request']['datasets']:
        row = session.get(MarketDataset, item['id'])
        if row is None or row.symbol != item['imported_symbol']:
            raise TradeError('SCAN_INPUT_CHANGED', '冻结行情引用已失效', 409)
    now = utc_now()
    row = StrategyScanJob(id=job_id, request_json=encoded, code_sha256=prepared['code_sha256'],
                          state='queued', cancel_requested=0, completed_count=0,
                          total_count=len(prepared['plan']), checkpoint_bytes=0, elapsed_ms=0,
                          attempt_id=None, attempt_number=0, scan_id=None, error=None,
                          created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _data(row, detail=True)


def _get(session: Session, job_id: str) -> StrategyScanJob:
    row = session.get(StrategyScanJob, job_id)
    if row is None:
        raise TradeError('SCAN_JOB_NOT_FOUND', '扫描任务不存在', 404)
    return row


def _data(row: StrategyScanJob, *, detail: bool = False) -> dict:
    frozen = json.loads(row.request_json)
    request = frozen['request']
    return {'id': row.id, 'state': 'cancelling' if row.state == 'running' and row.cancel_requested else row.state,
            'input_sha256': row.id, 'code_sha256': row.code_sha256,
            'completed_count': row.completed_count, 'total_count': row.total_count,
            'progress': row.completed_count / row.total_count, 'scan_id': row.scan_id,
            'attempt_number': row.attempt_number, 'checkpoint_bytes': row.checkpoint_bytes,
            'elapsed_ms': row.elapsed_ms, 'error': row.error,
            'created_at': row.created_at, 'updated_at': row.updated_at,
            'dataset_count': len(request['datasets']), 'strategy_count': len(request['strategies']),
            'mode': request['mode'], 'as_of_date': request['as_of_date'],
            'date_from': request['date_from'], 'date_to': request['date_to'],
            'capabilities': {'cancel': row.state in ('queued', 'running'),
                             'retry': row.state in ('failed', 'cancelled'), 'pause': False, 'resume': False},
            **({'request': request, 'compute_budget': frozen['compute_budget'],
                'limits': {'chunk_evaluations': frozen['chunk_evaluations'],
                           'max_job_bytes': frozen['max_job_bytes'], 'max_compute_ms': frozen['max_compute_ms']}}
               if detail else {})}


def get_scan_job(session: Session, job_id: str) -> dict:
    return _data(_get(session, job_id), detail=True)


def list_scan_jobs(session: Session) -> list[dict]:
    return [_data(row) for row in session.scalars(select(StrategyScanJob).order_by(
        StrategyScanJob.created_at.desc(), StrategyScanJob.id).limit(100))]


def cancel_scan_job(session: Session, job_id: str) -> dict:
    row = _get(session, job_id)
    if row.state in ('queued', 'running'):
        if row.attempt_id is None:
            row.state = 'cancelled'
        else:
            row.cancel_requested = 1
    elif row.state != 'cancelled':
        raise TradeError('SCAN_JOB_FINAL', '当前扫描状态不能取消', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(row, detail=True)


def retry_scan_job(session: Session, job_id: str) -> dict:
    row = _get(session, job_id)
    if row.state not in ('failed', 'cancelled'):
        raise TradeError('SCAN_JOB_NOT_RETRYABLE', '当前扫描状态不能重试', 409)
    _queue_guard(session)
    # Code/files are verified by the worker outside this writer transaction.
    row.state, row.error = 'queued', None
    row.cancel_requested, row.attempt_id, row.elapsed_ms = 0, None, 0
    row.updated_at = utc_now()
    session.flush()
    return _data(row, detail=True)


def recover_interrupted_scan_jobs(factory: sessionmaker) -> None:
    """Keep verified committed checkpoints; discard only an uncommitted attempt."""
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(StrategyScanJob).where(StrategyScanJob.state == 'running')):
            row.state = 'cancelled' if row.cancel_requested else 'queued'
            row.cancel_requested, row.attempt_id = 0, None
            row.error, row.updated_at = 'RECOVERED_AFTER_RESTART', utc_now()


def _verify_frozen(job_id: str, frozen: dict) -> None:
    if digest(frozen) != job_id:
        raise TradeError('SCAN_INPUT_CORRUPT', '扫描冻结输入校验失败', 409)
    if frozen['code_sha256'] != code_sha256():
        raise TradeError('CODE_VERSION_CHANGED', '扫描算法已更新，请用原参数创建新任务', 409)
    for descriptor in frozen['strategies']:
        if frozen['request'].get('signal_context'):
            from trade_app.research.signal_context import describe_context_strategy
            current = describe_context_strategy(descriptor['strategy_id'])
        else:
            current = describe_strategy(descriptor['strategy_id'])
        if any(descriptor[key] != value for key, value in current.items()):
            raise TradeError('CODE_VERSION_CHANGED', '单股策略算法已更新，请创建新扫描任务', 409)


def _validate_runs(runs: list[dict], items: list[dict], frozen: dict) -> None:
    if not isinstance(runs, list) or len(runs) != len(items):
        raise TradeError('SCAN_CHECKPOINT_CORRUPT', '扫描批次证据数量不匹配', 409)
    strategies = {row['strategy_id']: row for row in frozen['strategies']}
    context = frozen['request'].get('signal_context')
    for run, item in zip(runs, items):
        descriptor = strategies[item['strategy_id']]
        profile = frozen['request']['event_profile'] if context or item['strategy_id'] in PROFILE_STRATEGIES else None
        identity = run_identity(item, descriptor, frozen['request']['strict'], profile, context)
        fields = ('dataset_id', 'strategy_id', 'strategy_version', 'decision_at', 'strict', 'params')
        if (run.get('id') != digest(identity) or any(run.get(key) != identity[key] for key in fields)
                or not isinstance(run.get('result'), dict)
                or run['result'].get('code_sha256') != descriptor['code_sha256']
                or run['result'].get('calculation_version') != descriptor['calculation_version']
                or (profile is not None and run['result'].get('event_profile') != profile)
                or (context is not None and (run['result'].get('signal_context') != context
                    or run['result'].get('candidate') != item['signal_candidate']
                    or run['result'].get('signal_pool') != item.get('signal_pool')))):
            raise TradeError('SCAN_CHECKPOINT_CORRUPT', '扫描批次证据与冻结输入不一致', 409)


def _publication(factory: sessionmaker, data_dir: Path, job_id: str, frozen: dict) -> tuple[dict, list[dict]]:
    """Verify and encode all content before acquiring the publication writer."""
    _verify_frozen(job_id, frozen)
    datasets = {row['id']: row for row in frozen['request']['datasets']}
    for dataset_id, descriptor in datasets.items():
        actual = read_dataset(data_dir / 'market', dataset_id)
        if actual['symbol'] != descriptor['imported_symbol']:
            raise TradeError('SCAN_INPUT_CHANGED', '冻结行情证券标识不一致', 409)
    with factory() as session:
        chunks = list(session.scalars(select(StrategyScanChunk).where(
            StrategyScanChunk.job_id == job_id).order_by(StrategyScanChunk.start_index)))
    runs, cursor, size = [], 0, 0
    for chunk in chunks:
        raw = chunk.result_json.encode('utf-8')
        size += len(raw)
        if (chunk.start_index != cursor or not cursor < chunk.end_index <= len(frozen['plan'])
                or len(raw) != chunk.byte_count or len(raw) > MAX_CHUNK_BYTES
                or hashlib.sha256(raw).hexdigest() != chunk.content_sha256 or size > MAX_JOB_BYTES):
            raise TradeError('SCAN_CHECKPOINT_CORRUPT', '扫描检查点校验失败', 409)
        part = json.loads(chunk.result_json)
        _validate_runs(part, frozen['plan'][cursor:chunk.end_index], frozen)
        runs.extend(part)
        cursor = chunk.end_index
    if cursor != len(frozen['plan']):
        raise TradeError('SCAN_CHECKPOINT_CORRUPT', '扫描检查点不完整', 409)
    by_id = {row['id']: row for row in runs}
    evidence = []
    for run, item in zip(runs, frozen['plan']):
        dataset = datasets[item['dataset_id']]
        evidence.append(_evidence(run, {'id': dataset['id'], 'symbol': dataset['imported_symbol']},
                                  item['symbol'], item['decision_date']))
    evidence.sort(key=lambda row: (row['decision_date'], row['symbol'], row['strategy_id']))
    request = frozen['request']
    result = {'calculation_version': CALCULATION_VERSION, 'mode': request['mode'],
              'decision_timezone': 'Asia/Shanghai', 'decision_time': '23:59:59',
              'evaluation_count': len(evidence), 'signal_count': sum(row['signal'] for row in evidence),
              'intersection_supported': len(request['strategies']) >= 2,
              'rows': evidence, 'run_ids': [row['run_id'] for row in evidence],
              **_aggregate(evidence, request['strategies']),
              'notes': ['只统计源日期等于扫描日期的触发，旧源日期结果仍保留研究证据',
                        '交集和并集按同一证券、同一扫描日期计算',
                        '分数为各策略自身指标，不能跨策略当作排名',
                        '冻结样本分批扫描，未实现旧交叉验证的收益、回撤和组合回测']}
    scan_code = digest({'scan_job_code_sha256': frozen['code_sha256'],
                        'child_code_sha256': sorted({row['code_sha256'] for row in frozen['strategies']})})
    now = utc_now()
    scan = {'id': digest({'request': request, 'result': result, 'code_sha256': scan_code}),
            'request_json': encode(request), 'result_json': encode(result),
            'code_sha256': scan_code, 'created_at': now}
    serialized = [{key: run[key] for key in ('id', 'dataset_id', 'strategy_id', 'strategy_version', 'decision_at')}
                  | {'strict': int(run['strict']), 'params_json': encode(run['params']),
                     'result_json': encode(run['result']), 'created_at': now} for run in runs]
    publication_bytes = sum(len(encode(row).encode('utf-8')) for row in serialized) + len(encode(scan).encode('utf-8'))
    if publication_bytes > MAX_JOB_BYTES:
        raise TradeError('SCAN_RESULT_LIMIT', '扫描发布证据超过大小预算，请缩小扫描')
    # Existing child identities are reused only if all their frozen evidence agrees.
    ids = list(by_id)
    with factory() as session:
        for offset in range(0, len(ids), 250):
            for previous in session.scalars(select(ResearchRun).where(ResearchRun.id.in_(ids[offset:offset + 250]))):
                expected = by_id[previous.id]
                if (json.loads(previous.result_json) != expected['result']
                        or json.loads(previous.params_json) != expected['params']
                        or any(getattr(previous, key) != expected[key] for key in (
                            'dataset_id', 'strategy_id', 'strategy_version', 'decision_at'))
                        or bool(previous.strict) != expected['strict']):
                    raise TradeError('RESEARCH_IDENTITY_CONFLICT', '已有单股研究证据与当前计算不一致', 409)
    return scan, serialized


def process_one_scan_chunk(factory: sessionmaker, data_dir: Path, *,
                            should_stop: Callable[[], bool] | None = None) -> bool:
    """Run one quantum only. The shared CPU scheduler owns concurrency/fairness."""
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(StrategyScanJob.id).where(StrategyScanJob.attempt_id.is_not(None)).limit(1)):
            return False
        row = session.scalar(select(StrategyScanJob).where(StrategyScanJob.state.in_(('queued', 'running')),
                                                          StrategyScanJob.cancel_requested == 0).order_by(
            StrategyScanJob.updated_at, StrategyScanJob.id).limit(1))
        if row is None:
            return False
        row.state, row.attempt_id, row.error = 'running', new_id(), None
        row.attempt_number += 1
        row.updated_at = utc_now()
        job_id, attempt_id, start = row.id, row.attempt_id, row.completed_count
        elapsed_ms, checkpoint_bytes = row.elapsed_ms, row.checkpoint_bytes
        frozen = json.loads(row.request_json)

    def stop_reason() -> str | None:
        if should_stop and should_stop():
            return 'shutdown'
        with factory() as session:
            current = session.get(StrategyScanJob, job_id)
            if (current is None or current.state != 'running' or current.attempt_id != attempt_id
                    or current.cancel_requested):
                return 'cancelled'
        return None

    def check_stop() -> None:
        reason = stop_reason()
        if reason:
            raise ComputeProcessError('COMPUTE_SHUTDOWN' if reason == 'shutdown' else 'COMPUTE_CANCELLED',
                                      '扫描计算中断')

    started = clock.monotonic()
    try:
        _verify_frozen(job_id, frozen)
        check_stop()
        if elapsed_ms >= frozen['max_compute_ms']:
            raise TradeError('SCAN_COMPUTE_BUDGET', '扫描累计计算时间超过预算，请缩小扫描')
        plan = frozen['plan']
        if start < len(plan):
            end = min(start + frozen['chunk_evaluations'], len(plan))
            while plan[end - 1]['dataset_id'] != plan[start]['dataset_id']:
                end -= 1
            payload = {'attempt_id': attempt_id, 'input_sha256': job_id, 'start_index': start,
                       'market_dir': str((data_dir / 'market').resolve()),
                       'items': plan[start:end], 'strategies': frozen['strategies'],
                       'strict': frozen['request']['strict'], 'event_profile': frozen['request']['event_profile']}
            if frozen['request'].get('signal_context'):
                payload['signal_context'] = frozen['request']['signal_context']
            budget = ComputeBudget(**frozen['compute_budget'])
            budget = replace(budget, timeout_seconds=min(budget.timeout_seconds,
                             (frozen['max_compute_ms'] - elapsed_ms) / 1000))
            computed = run_json_process('trade_app.research.scan_job_worker', payload,
                                        budget=budget, stop_reason=stop_reason)
            envelope = computed.value
            if envelope.get('ok') is not True:
                raise TradeError(str((envelope.get('error') or {}).get('code', 'SCAN_COMPUTE_FAILED')),
                                 '扫描计算失败，请检查冻结输入或缩小扫描', 409)
            if (envelope.get('attempt_id') != attempt_id or envelope.get('input_sha256') != job_id
                    or envelope.get('start_index') != start):
                raise TradeError('SCAN_ATTEMPT_MISMATCH', '扫描计算尝试不匹配', 409)
            _validate_runs(envelope.get('runs'), plan[start:end], frozen)
            encoded = encode(envelope['runs'])
            raw = encoded.encode('utf-8')
            if len(raw) > MAX_CHUNK_BYTES or checkpoint_bytes + len(raw) > frozen['max_job_bytes']:
                raise TradeError('SCAN_CHECKPOINT_LIMIT', '扫描检查点超过大小预算，请缩小扫描')
            checksum = hashlib.sha256(raw).hexdigest()
            _verify_frozen(job_id, frozen)
            check_stop()
            if elapsed_ms + int((clock.monotonic() - started) * 1000) > frozen['max_compute_ms']:
                raise TradeError('SCAN_COMPUTE_BUDGET', '扫描累计计算时间超过预算，请缩小扫描')
            with factory.begin() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                row = _get(session, job_id)
                if row.attempt_id != attempt_id or row.state != 'running':
                    return True
                if row.cancel_requested:
                    raise ComputeProcessError('COMPUTE_CANCELLED', '扫描已取消')
                session.add(StrategyScanChunk(job_id=job_id, start_index=start, end_index=end,
                                             attempt_id=attempt_id, content_sha256=checksum,
                                             byte_count=len(raw), result_json=encoded))
                row.completed_count = end
                row.checkpoint_bytes += len(raw)
                row.elapsed_ms += int((clock.monotonic() - started) * 1000)
                row.updated_at = utc_now()
                if end < len(plan):
                    row.attempt_id = None
            if end < len(plan):
                return True
        scan, runs = _publication(factory, data_dir, job_id, frozen)
        check_stop()
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = _get(session, job_id)
            if row.attempt_id != attempt_id or row.state != 'running':
                return True
            if row.completed_count != len(frozen['plan']):
                raise TradeError('SCAN_CHECKPOINT_CORRUPT', '扫描发布进度与检查点不一致', 409)
            if row.cancel_requested:
                raise ComputeProcessError('COMPUTE_CANCELLED', '扫描已取消')
            if should_stop and should_stop():
                raise ComputeProcessError('COMPUTE_SHUTDOWN', '扫描正在退出')
            # All hashes/JSON/aggregation completed above. Only bounded bulk inserts here.
            session.execute(insert(ResearchRun).on_conflict_do_nothing(index_elements=['id']), runs)
            session.execute(insert(StrategyScanRun).on_conflict_do_nothing(index_elements=['id']), scan)
            session.execute(delete(StrategyScanChunk).where(StrategyScanChunk.job_id == job_id))
            row.state, row.scan_id, row.attempt_id = 'succeeded', scan['id'], None
            row.cancel_requested, row.error, row.checkpoint_bytes = 0, None, 0
            row.updated_at = utc_now()
        return True
    except Exception as exc:
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(StrategyScanJob, job_id)
            if row is not None and row.state == 'running' and row.attempt_id == attempt_id:
                error = getattr(exc, 'code', 'SCAN_COMPUTE_FAILED')
                if row.cancel_requested or error == 'COMPUTE_CANCELLED':
                    row.state, row.error = 'cancelled', None
                elif error == 'COMPUTE_SHUTDOWN':
                    row.state, row.error = 'queued', 'INTERRUPTED_SHUTDOWN'
                else:
                    row.state, row.error = 'failed', error
                row.attempt_id, row.cancel_requested = None, 0
                row.updated_at = utc_now()
        return True
