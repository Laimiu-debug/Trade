"""Immutable event cache and resumable, explicitly requested backfill jobs.

One shared CPU scheduling quantum verifies and publishes at most ten snapshots.
Committed cache entries are checkpoints included in the ordinary database backup.
Reads never start computation and exceptions never turn into empty snapshots.
"""
from dataclasses import replace
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import time as clock
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from trade_app.market.models import MarketDataset
from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol, market_symbol_key
from trade_app.platform.compute_process import ComputeBudget, ComputeProcessError, run_json_process
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.event_store_models import EventStoreJob, EventStoreRecord, EventStoreVersion
from trade_app.research.event_store_worker import CHUNK_SIZE, MAX_CHUNK_BYTES, record_identity, validate_result
from trade_app.research.scan_job_worker import digest, encode, read_dataset
from trade_app.research.wyckoff_domain import CALCULATION_VERSION

VERSION = 'wyckoff-event-store-v1'
MAX_EVALUATIONS = 3000
MAX_RESULT_BYTES = 30 * 1024 * 1024
MAX_COMPUTE_MS = 600_000
BUDGET = ComputeBudget(version=VERSION, timeout_seconds=30, input_bytes=1024 * 1024,
                       output_bytes=MAX_CHUNK_BYTES)


def code_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    files = ('research/event_store_service.py', 'research/event_store_worker.py',
             'research/wyckoff_domain.py', 'research/scan_job_worker.py',
             'research/event_profiles.py', 'research/event_profile_service.py',
             'market/domain.py', 'market/symbols.py', 'platform/symbols.py', 'platform/types.py',
             'platform/compute_process.py', 'platform/runtime.py')
    return hashlib.sha256(b''.join(path.encode() + (root / path).read_bytes() for path in files)).hexdigest()


def _date(raw) -> str:
    try:
        if date.fromisoformat(raw).isoformat() != raw:
            raise ValueError(raw)
        return raw
    except (TypeError, ValueError) as exc:
        raise TradeError('INVALID_EVENT_STORE_DATE', '日期须为 YYYY-MM-DD') from exc


def prepare_backfill(session: Session, data_dir: Path, body: dict) -> dict:
    if set(body) - {'dataset_ids', 'start_date', 'end_date', 'window_days', 'strict',
                   'event_profile_id', 'event_profile_revision'}:
        raise TradeError('INVALID_EVENT_STORE_REQUEST', '包含不支持的事件仓参数')
    start, end = _date(body.get('start_date')), _date(body.get('end_date'))
    if not 0 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 365:
        raise TradeError('EVENT_STORE_RANGE_LIMIT', '回填范围须为 1 至 366 个自然日')
    ids, windows = body.get('dataset_ids'), body.get('window_days', [60])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or len(ids) != len(set(ids)):
        raise TradeError('EVENT_STORE_DATASET_LIMIT', '请选择 1 至 100 个不重复行情样本')
    if (not isinstance(windows, list) or not 1 <= len(windows) <= 3
            or any(type(window) is not int or not 20 <= window <= 240 for window in windows)
            or len(windows) != len(set(windows))):
        raise TradeError('EVENT_STORE_WINDOW_LIMIT', '请选择 1 至 3 个不重复窗口，每个窗口为 20 至 240 日')
    strict = body.get('strict', True)
    if type(strict) is not bool:
        raise TradeError('INVALID_EVENT_STORE_STRICT', '严格可得时间须为布尔值')
    profile = freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
    code = code_sha256()
    versions = {}
    for window in sorted(windows):
        config = {'version': VERSION, 'calculation_version': CALCULATION_VERSION, 'code_sha256': code,
                  'window_days': window, 'strict': strict, 'event_profile': profile}
        versions[digest(config)] = config
    datasets, plan, seen, warnings = [], [], set(), []
    for dataset_id in ids:
        dataset = get_dataset(session, data_dir, dataset_id)
        exchange, digits = normalize_a_share_symbol(dataset['symbol'])
        symbol = exchange + digits
        if symbol in seen:
            raise TradeError('DUPLICATE_EVENT_STORE_SYMBOL', '同一证券只能选择一个冻结行情样本，别名也视为重复')
        seen.add(symbol)
        datasets.append({key: dataset[key] for key in ('id', 'symbol', 'provider', 'adjustment', 'first_date', 'last_date', 'availability_quality')})
        days = [bar['event_date'] for bar in dataset['bars'] if start <= bar['event_date'] <= end]
        if not days:
            warnings.append({'dataset_id': dataset_id, 'code': 'no_observed_dates_in_range'})
        if dataset['last_date'] < end:
            warnings.append({'dataset_id': dataset_id, 'code': 'dataset_ends_before_range'})
        for day in days:
            decision = datetime.combine(date.fromisoformat(day), time(23, 59, 59), ZoneInfo('Asia/Shanghai')).isoformat()
            for version_id in versions:
                plan.append({'version_id': version_id, 'dataset_id': dataset_id, 'symbol': symbol,
                             'decision_date': day, 'decision_at': decision})
    if not plan or len(plan) > MAX_EVALUATIONS:
        raise TradeError('EVENT_STORE_EVALUATION_LIMIT', f'回填需有 1 至 {MAX_EVALUATIONS} 个样本日/窗口判断，当前 {len(plan)} 个')
    prepared = {'version': VERSION, 'code_sha256': code, 'start_date': start, 'end_date': end,
        'datasets': sorted(datasets, key=lambda row: row['id']), 'versions': versions,
        'plan': sorted(plan, key=lambda item: (item['dataset_id'], item['decision_date'], item['version_id'])),
        'warnings': warnings, 'budget': BUDGET.to_dict(), 'max_compute_ms': MAX_COMPUTE_MS,
        'max_result_bytes': MAX_RESULT_BYTES, 'chunk_size': CHUNK_SIZE}
    return json.loads(encode(prepared))


def preview_backfill(session: Session, prepared: dict) -> dict:
    keys = [digest(record_identity(item)) for item in prepared['plan']]
    count = session.scalar(select(func.count()).select_from(EventStoreRecord).where(EventStoreRecord.id.in_(keys))) or 0
    return {'input_sha256': digest(prepared), 'total_count': len(keys), 'cached_count': count,
        'missing_count': len(keys) - count, 'datasets': prepared['datasets'], 'versions': prepared['versions'],
        'start_date': prepared['start_date'], 'end_date': prepared['end_date'], 'warnings': prepared['warnings'],
        'budget': prepared['budget'], 'limits': {'evaluations': MAX_EVALUATIONS, 'chunk_size': CHUNK_SIZE,
            'max_result_bytes': MAX_RESULT_BYTES, 'max_compute_ms': MAX_COMPUTE_MS}}


def _queue_guard(session):
    count = session.scalar(select(func.count()).select_from(EventStoreJob).where(EventStoreJob.state.in_(('queued', 'running')))) or 0
    if count >= 10:
        raise TradeError('EVENT_STORE_QUEUE_FULL', '事件仓回填队列已满', 429)


def enqueue_backfill(session: Session, prepared: dict) -> dict:
    key = digest(prepared)
    existing = session.get(EventStoreJob, key)
    if existing:
        return _job_data(existing, detail=True)
    _queue_guard(session)
    for item in prepared['datasets']:
        dataset = session.get(MarketDataset, item['id'])
        if dataset is None or dataset.symbol != item['symbol']:
            raise TradeError('EVENT_STORE_INPUT_CHANGED', '冻结行情引用已失效', 409)
    now = utc_now()
    for version_id, config in prepared['versions'].items():
        session.execute(insert(EventStoreVersion).values(id=version_id, config_json=encode(config),
            code_sha256=config['code_sha256'], window_days=config['window_days'], strict=int(config['strict']),
            profile_id=config['event_profile']['profile_id'], profile_revision=config['event_profile']['revision'],
            created_at=now).on_conflict_do_nothing(index_elements=['id']))
    row = EventStoreJob(id=key, request_json=encode(prepared), state='queued', cancel_requested=0,
        total_count=len(prepared['plan']), completed_count=0, cache_hits=0, written_count=0,
        result_bytes=0, elapsed_ms=0, attempt_id=None, attempt_number=0, error=None, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _job_data(row, detail=True)


def _job(session, job_id):
    row = session.get(EventStoreJob, job_id)
    if row is None:
        raise TradeError('EVENT_STORE_JOB_NOT_FOUND', '事件仓回填任务不存在', 404)
    return row


def _job_data(row, *, detail=False):
    result = {key: getattr(row, key) for key in ('id', 'state', 'total_count', 'completed_count', 'cache_hits',
        'written_count', 'result_bytes', 'elapsed_ms', 'attempt_number', 'error', 'created_at', 'updated_at')}
    result['state'] = 'cancelling' if row.state == 'running' and row.cancel_requested else row.state
    result['progress'] = row.completed_count / row.total_count
    result['capabilities'] = {'cancel': row.state in ('queued', 'running'), 'resume': row.state in ('cancelled', 'failed')}
    if detail:
        frozen = json.loads(row.request_json)
        result['request'] = {key: value for key, value in frozen.items() if key != 'plan'}
    return result


def get_job(session, job_id):
    return _job_data(_job(session, job_id), detail=True)


def list_jobs(session):
    return [_job_data(row) for row in session.scalars(select(EventStoreJob).order_by(EventStoreJob.created_at.desc()).limit(100))]


def control_job(session, job_id, action):
    row = _job(session, job_id)
    if action == 'cancel' and row.state in ('queued', 'running'):
        if row.attempt_id:
            row.cancel_requested = 1
        else:
            row.state = 'cancelled'
    elif action == 'cancel' and row.state == 'cancelled':
        pass
    elif action == 'resume' and row.state in ('cancelled', 'failed'):
        _queue_guard(session)
        row.state, row.error, row.attempt_id = 'queued', None, None
        row.cancel_requested, row.elapsed_ms = 0, 0
    else:
        raise TradeError('EVENT_STORE_JOB_STATE', '当前回填任务状态不支持此操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _job_data(row, detail=True)


def recover_interrupted_event_store_jobs(factory: sessionmaker) -> None:
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(EventStoreJob).where(EventStoreJob.state == 'running')):
            row.state = 'cancelled' if row.cancel_requested else 'queued'
            row.cancel_requested, row.attempt_id = 0, None
            row.error, row.updated_at = 'RECOVERED_AFTER_RESTART', utc_now()


def _summary(row):
    return {key: getattr(row, key) for key in ('id', 'version_id', 'dataset_id', 'symbol', 'decision_date',
        'decision_at', 'source_date', 'status', 'event_count', 'risk_count', 'primary_event', 'observed_bars',
        'content_sha256', 'byte_count', 'created_at')}


def _validated_record(row):
    identity = record_identity(_summary(row))
    try:
        result = json.loads(row.result_json)
        valid = row.id == digest(identity) and row.content_sha256 == digest({'identity': identity, 'result': result})
        if not valid or row.byte_count != len(row.result_json.encode('utf-8')):
            raise ValueError('digest')
        validate_result(result, row.decision_date)
        snapshot = result['snapshot']
        expected = {'status': 'complete' if result['has_data'] else 'insufficient_history',
            'source_date': result['source_date'], 'observed_bars': result['observed_bars'],
            'event_count': len(snapshot['events']), 'risk_count': len(snapshot['risk_events']),
            'primary_event': snapshot.get('signal', '')}
        if any(getattr(row, field) != value for field, value in expected.items()):
            raise ValueError('summary')
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise TradeError('EVENT_STORE_CACHE_CORRUPT', '事件缓存内容校验失败，不可作为空结果使用', 409) from exc
    return result


def lookup_cached_snapshot(session: Session, *, version_id: str, dataset_id: str, symbol: str,
                           decision_date: str, decision_at: str) -> dict | None:
    """Only exact-version lookup; a miss is None and never schedules or computes."""
    identity = dict(version_id=version_id, dataset_id=dataset_id, symbol=symbol,
                    decision_date=decision_date, decision_at=decision_at)
    row = session.get(EventStoreRecord, digest(identity))
    return _validated_record(row) if row else None


def get_record(session, record_id):
    row = session.get(EventStoreRecord, record_id)
    if row is None:
        raise TradeError('EVENT_STORE_RECORD_NOT_FOUND', '事件快照不存在', 404)
    version = session.get(EventStoreVersion, row.version_id)
    config = json.loads(version.config_json)
    if digest(config) != row.version_id:
        raise TradeError('EVENT_STORE_VERSION_CORRUPT', '事件算法版本内容校验失败', 409)
    return {**_summary(row), 'result': _validated_record(row), 'version': config,
            'current_code': config['code_sha256'] == code_sha256()}


def list_records(session, *, job_id=None, version_id=None, symbol=None, limit=100, offset=0):
    if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
        raise TradeError('EVENT_STORE_PAGE_LIMIT', '分页范围无效')
    query = select(EventStoreRecord)
    if job_id:
        frozen = json.loads(_job(session, job_id).request_json)
        query = query.where(EventStoreRecord.id.in_([digest(record_identity(item)) for item in frozen['plan']]))
    if version_id:
        query = query.where(EventStoreRecord.version_id == version_id)
    if symbol:
        query = query.where(EventStoreRecord.symbol == market_symbol_key(symbol))
    return [_summary(row) for row in session.scalars(query.order_by(EventStoreRecord.decision_date.desc(),
        EventStoreRecord.symbol, EventStoreRecord.id).offset(offset).limit(limit))]


def statistics(session):
    code = code_sha256()
    versions = []
    for row in session.scalars(select(EventStoreVersion).order_by(EventStoreVersion.created_at.desc()).limit(100)):
        counts = dict(session.execute(select(EventStoreRecord.status, func.count()).where(
            EventStoreRecord.version_id == row.id).group_by(EventStoreRecord.status)).all())
        empty = session.scalar(select(func.count()).select_from(EventStoreRecord).where(
            EventStoreRecord.version_id == row.id, EventStoreRecord.status == 'complete',
            EventStoreRecord.event_count == 0, EventStoreRecord.risk_count == 0)) or 0
        versions.append({'id': row.id, 'window_days': row.window_days, 'strict': bool(row.strict),
            'profile_id': row.profile_id, 'profile_revision': row.profile_revision, 'code_sha256': row.code_sha256,
            'current_code': row.code_sha256 == code, 'created_at': row.created_at,
            'complete_count': counts.get('complete', 0), 'insufficient_count': counts.get('insufficient_history', 0),
            'empty_event_count': empty})
    states = dict(session.execute(select(EventStoreJob.state, func.count()).group_by(EventStoreJob.state)).all())
    hits, writes = session.execute(select(func.coalesce(func.sum(EventStoreJob.cache_hits), 0),
        func.coalesce(func.sum(EventStoreJob.written_count), 0))).one()
    return {'algorithm_version': CALCULATION_VERSION, 'current_code_sha256': code,
        'record_count': session.scalar(select(func.count()).select_from(EventStoreRecord)) or 0,
        'stored_bytes': session.scalar(select(func.coalesce(func.sum(EventStoreRecord.byte_count), 0))) or 0,
        'versions': versions, 'job_states': states, 'backfill_cache_hits': hits, 'backfill_writes': writes,
        'backfill_hit_rate': hits / (hits + writes) if hits + writes else None,
        'method': '统计仅针对显式回填任务；GET 不计算或填库。数据/模板/算法变化生成新版本，旧实验与旧快照不覆盖。未接入既有回测热路径。'}


def _verify(job_id, frozen):
    if digest(frozen) != job_id:
        raise TradeError('EVENT_STORE_INPUT_CORRUPT', '回填冻结输入校验失败', 409)
    if frozen['code_sha256'] != code_sha256():
        raise TradeError('CODE_VERSION_CHANGED', '事件算法版本变化，请重新预览并创建回填任务', 409)
    if any(digest(config) != key for key, config in frozen['versions'].items()):
        raise TradeError('EVENT_STORE_VERSION_CORRUPT', '回填参数版本校验失败', 409)


def process_one_event_store(factory: sessionmaker, data_dir: Path, *, should_stop=None) -> bool:
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.scalar(select(EventStoreJob).where(EventStoreJob.state.in_(('queued', 'running')),
            EventStoreJob.attempt_id.is_(None)).order_by(EventStoreJob.updated_at, EventStoreJob.id).limit(1))
        if row is None:
            return False
        row.state, row.attempt_id, row.error = 'running', new_id(), None
        row.attempt_number += 1
        row.updated_at = utc_now()
        job_id, attempt, start = row.id, row.attempt_id, row.completed_count
        elapsed, stored = row.elapsed_ms, row.result_bytes
        frozen = json.loads(row.request_json)
    def stop_reason():
        if should_stop and should_stop():
            return 'shutdown'
        with factory() as session:
            live = session.get(EventStoreJob, job_id)
            if live is None or live.state != 'running' or live.attempt_id != attempt or live.cancel_requested:
                return 'cancelled'
        return None
    def check_stop():
        reason = stop_reason()
        if reason:
            raise ComputeProcessError('COMPUTE_SHUTDOWN' if reason == 'shutdown' else 'COMPUTE_CANCELLED', '事件回填中断')
    started = clock.monotonic()
    try:
        _verify(job_id, frozen)
        check_stop()
        if elapsed >= frozen['max_compute_ms']:
            raise TradeError('EVENT_STORE_TIME_LIMIT', '回填累计耗时超过预算，请缩小范围')
        items = frozen['plan'][start:start + CHUNK_SIZE]
        if not items:
            raise TradeError('EVENT_STORE_PROGRESS_CORRUPT', '回填进度与计划不一致', 409)
        items = [item for item in items if item['dataset_id'] == items[0]['dataset_id']]
        # Verify source even on an all-cache-hit quantum; missing input is not a hit.
        dataset = read_dataset(data_dir / 'market', items[0]['dataset_id'])
        expected = next(item for item in frozen['datasets'] if item['id'] == items[0]['dataset_id'])
        if dataset['symbol'] != expected['symbol']:
            raise TradeError('EVENT_STORE_INPUT_CHANGED', '冻结来源标识不一致', 409)
        missing, hits, byte_count = [], 0, 0
        with factory() as session:
            for item in items:
                cached = session.get(EventStoreRecord, digest(record_identity(item)))
                if cached:
                    _validated_record(cached)
                    hits += 1
                    byte_count += cached.byte_count
                else:
                    missing.append(item)
        records = []
        if missing:
            payload = {'attempt_id': attempt, 'input_sha256': job_id, 'items': missing,
                'market_dir': str((data_dir / 'market').resolve()), 'versions': frozen['versions']}
            budget = replace(ComputeBudget(**frozen['budget']), timeout_seconds=min(
                frozen['budget']['timeout_seconds'], (frozen['max_compute_ms'] - elapsed) / 1000))
            computed = run_json_process('trade_app.research.event_store_worker', payload, budget=budget, stop_reason=stop_reason)
            envelope = computed.value
            if envelope.get('ok') is not True:
                raise TradeError(str((envelope.get('error') or {}).get('code', 'EVENT_STORE_COMPUTE_FAILED')),
                                 '事件回填计算失败，未写入空缓存', 409)
            records = envelope.get('records')
            if (envelope.get('attempt_id') != attempt or envelope.get('input_sha256') != job_id
                    or not isinstance(records, list) or len(records) != len(missing)):
                raise TradeError('EVENT_STORE_ATTEMPT_MISMATCH', '事件计算批次身份不一致', 409)
            for item, record in zip(missing, records):
                if record_identity(record) != record_identity(item):
                    raise TradeError('EVENT_STORE_RESULT_INVALID', '事件证据与冻结任务不一致', 409)
                _validated_record(EventStoreRecord(**record))
                byte_count += record['byte_count']
        if stored + byte_count > frozen['max_result_bytes']:
            raise TradeError('EVENT_STORE_STORAGE_LIMIT', '回填结果超过单任务证据预算，请缩小范围')
        _verify(job_id, frozen)
        read_dataset(data_dir / 'market', items[0]['dataset_id'])
        check_stop()
        duration = int((clock.monotonic() - started) * 1000)
        if elapsed + duration > frozen['max_compute_ms']:
            raise TradeError('EVENT_STORE_TIME_LIMIT', '回填累计耗时超过预算')
        now = utc_now()
        for record in records:
            record['created_at'] = now
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = _job(session, job_id)
            if row.state != 'running' or row.attempt_id != attempt:
                return True
            if row.cancel_requested or (should_stop and should_stop()):
                raise ComputeProcessError('COMPUTE_CANCELLED' if row.cancel_requested else 'COMPUTE_SHUTDOWN', '回填停止')
            if records:
                session.execute(insert(EventStoreRecord).on_conflict_do_nothing(index_elements=['id']), records)
            row.completed_count = start + len(items)
            row.cache_hits += hits
            row.written_count += len(records)
            row.result_bytes += byte_count
            row.elapsed_ms += duration
            row.state = 'succeeded' if row.completed_count == row.total_count else 'running'
            row.attempt_id, row.updated_at = None, now
        return True
    except Exception as exc:
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(EventStoreJob, job_id)
            if row is not None and row.state == 'running' and row.attempt_id == attempt:
                code = getattr(exc, 'code', 'EVENT_STORE_COMPUTE_FAILED')
                if row.cancel_requested or code == 'COMPUTE_CANCELLED':
                    row.state, row.error = 'cancelled', None
                elif code == 'COMPUTE_SHUTDOWN':
                    row.state, row.error = 'queued', 'INTERRUPTED_SHUTDOWN'
                else:
                    row.state, row.error = 'failed', code
                row.attempt_id, row.cancel_requested, row.updated_at = None, 0, utc_now()
        return True
