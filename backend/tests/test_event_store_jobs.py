from copy import deepcopy
from datetime import date, timedelta
import json

import pytest
from sqlalchemy import select, text

from trade_app.main import create_app
from trade_app.market.domain import eligible_bars
from trade_app.market.service import import_dataset
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.compute_process import ComputeProcessError, ComputeResult
from trade_app.platform.compute_scheduler import ComputeScheduler
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research import event_store_service as service
from trade_app.research.event_store_models import EventStoreJob, EventStoreRecord
from trade_app.research.event_store_worker import compute_chunk, validate_result
from trade_app.research.event_profile_service import save_profile
from trade_app.research.wyckoff_domain import calculate_snapshot
from test_matrix_pool_api import started_client, data, writer
from test_wyckoff_domain import event_rich_bars


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    bars = event_rich_bars()
    with factory.begin() as session:
        dataset = import_dataset(session, tmp_path, {'symbol': '600000.SH', 'adjustment': 'none', 'bars': bars})
    body = {'dataset_ids': [dataset['id']], 'start_date': bars[-1]['event_date'],
            'end_date': bars[-1]['event_date'], 'window_days': [60], 'strict': True}
    yield factory, tmp_path, body, bars
    engine.dispose()


def enqueue(factory, path, body):
    with factory() as session:
        frozen = service.prepare_backfill(session, path, body)
    with factory.begin() as session:
        return service.enqueue_backfill(session, frozen)


def get(factory, job_id):
    with factory() as session:
        return service.get_job(session, job_id)


def finish(factory, path, job_id):
    for _ in range(40):
        job = get(factory, job_id)
        if job['state'] not in ('queued', 'running'):
            return job
        assert service.process_one_event_store(factory, path)
    pytest.fail('backfill exceeded bounded quanta')


def inline(monkeypatch):
    monkeypatch.setattr(service, 'run_json_process', lambda _module, payload, **_: ComputeResult(compute_chunk(payload), {}))


def records(factory, job_id=None):
    with factory() as session:
        return service.list_records(session, job_id=job_id)


def test_actual_child_matches_pure_snapshot_and_idempotent_overlap_preserves_creation(scenario):
    factory, path, body, bars = scenario
    job = enqueue(factory, path, body)
    assert finish(factory, path, job['id'])['state'] == 'succeeded'
    first = records(factory)[0]
    with factory() as session:
        record = service.get_record(session, first['id'])
        config = record['version']
        expected = calculate_snapshot(bars, 60, config['event_profile']['snapshot'])
        expected['quality_flags'] = []
        assert record['result'] == expected
        assert record['status'] == 'complete' and record['event_count'] > 0
        assert service.lookup_cached_snapshot(session, **{key: record[key] for key in (
            'version_id', 'dataset_id', 'symbol', 'decision_date', 'decision_at')}) == expected
        assert service.lookup_cached_snapshot(session, version_id='wrong', dataset_id=first['dataset_id'],
            symbol=first['symbol'], decision_date=first['decision_date'], decision_at=first['decision_at']) is None
    assert enqueue(factory, path, body)['id'] == job['id']
    wider = enqueue(factory, path, {**body, 'start_date': bars[-2]['event_date']})
    completed = finish(factory, path, wider['id'])
    assert completed['cache_hits'] == 1 and completed['written_count'] == 1
    assert next(row for row in records(factory) if row['id'] == first['id'])['created_at'] == first['created_at']
    with factory() as session:
        stats = service.statistics(session)
        assert stats['backfill_hit_rate'] == pytest.approx(1 / 3)


def test_future_and_late_prices_never_pollute_earlier_event_snapshot(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    base = enqueue(factory, path, body)
    finish(factory, path, base['id'])
    changed = deepcopy(bars)
    future = (date.fromisoformat(bars[-1]['event_date']) + timedelta(days=1)).isoformat()
    changed.append({'event_date': future, 'open': '9999', 'high': '9999', 'low': '9999', 'close': '9999',
                    'volume': 10000000, 'available_at': future + 'T08:00:00Z'})
    with factory.begin() as session:
        new = import_dataset(session, path, {'symbol': 'sh600000', 'adjustment': 'none', 'bars': changed})
    added = enqueue(factory, path, {**body, 'dataset_ids': [new['id']]})
    finish(factory, path, added['id'])
    with factory() as session:
        old_result = service.get_record(session, records(factory, base['id'])[0]['id'])['result']
        new_result = service.get_record(session, records(factory, added['id'])[0]['id'])['result']
        assert old_result == new_result and new_result['observed_bars'] == len(bars)
    changed[-2]['available_at'] = future + 'T08:00:00Z'
    with factory.begin() as session:
        late = import_dataset(session, path, {'symbol': '600000', 'adjustment': 'none', 'bars': changed})
    late_job = enqueue(factory, path, {**body, 'dataset_ids': [late['id']]})
    finish(factory, path, late_job['id'])
    with factory() as session:
        result = service.get_record(session, records(factory, late_job['id'])[0]['id'])['result']
        assert result['observed_bars'] == len(bars) - 1
        assert result['source_date'] == bars[-2]['event_date'] and 'stale_source_date' in result['quality_flags']


def test_profile_revision_is_frozen_before_job_and_new_revision_misses(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, _bars = scenario
    with factory.begin() as session:
        profile = save_profile(session, {'profile': {'name': 'frozen profile', 'description': '',
            'score_mode': 'dimension_weighted', 'dimensions': [{'metric_key': 'risk_score', 'weight': 1}],
            'rule_values': [{'rule_key': 'enable_spring', 'value': True}]}})
    request = {**body, 'event_profile_id': profile['profile_id'], 'event_profile_revision': 1}
    first = enqueue(factory, path, request)
    with factory.begin() as session:
        changed = {key: deepcopy(profile[key]) for key in ('name', 'description', 'score_mode', 'dimensions', 'rule_values')}
        changed['name'] = 'changed after enqueue'
        save_profile(session, {'profile': changed, 'expected_revision': 1}, profile['profile_id'])
    assert finish(factory, path, first['id'])['state'] == 'succeeded'
    second = enqueue(factory, path, {**request, 'event_profile_revision': 2})
    assert first['id'] != second['id']
    assert finish(factory, path, second['id'])['cache_hits'] == 0
    assert records(factory, first['id'])[0]['version_id'] != records(factory, second['id'])[0]['version_id']


def test_insufficient_history_is_distinct_from_complete_no_event(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    job = enqueue(factory, path, {**body, 'start_date': bars[1]['event_date'], 'end_date': bars[1]['event_date']})
    assert finish(factory, path, job['id'])['state'] == 'succeeded'
    row = records(factory)[0]
    assert row['status'] == 'insufficient_history' and row['observed_bars'] == 2
    with factory() as session:
        version = service.statistics(session)['versions'][0]
        assert version['insufficient_count'] == 1 and version['empty_event_count'] == 0


@pytest.mark.parametrize('broken', ['missing', 'tampered'])
def test_bad_source_fails_without_fabricating_empty_cache(scenario, monkeypatch, broken):
    inline(monkeypatch)
    factory, path, body, _ = scenario
    job = enqueue(factory, path, body)
    file = path / 'market' / (body['dataset_ids'][0] + '.json')
    if broken == 'missing':
        file.unlink()
    else:
        file.write_text('{}')
    failed = finish(factory, path, job['id'])
    assert failed['state'] == 'failed' and failed['error'] in ('MARKET_DATA_MISSING', 'MARKET_DATA_CORRUPT')
    assert records(factory) == []


def test_cancel_during_compute_discards_uncommitted_result_and_leaves_foreground_writable(scenario, monkeypatch):
    factory, path, body, _ = scenario
    job = enqueue(factory, path, body)
    def compute(_module, payload, **kwargs):
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            service.control_job(session, job['id'], 'cancel')
        assert kwargs['stop_reason']() == 'cancelled'
        return ComputeResult(compute_chunk(payload), {})
    monkeypatch.setattr(service, 'run_json_process', compute)
    assert service.process_one_event_store(factory, path)
    assert get(factory, job['id'])['state'] == 'cancelled' and records(factory) == []
    inline(monkeypatch)
    with factory.begin() as session:
        service.control_job(session, job['id'], 'resume')
    assert finish(factory, path, job['id'])['state'] == 'succeeded'


def test_restart_resumes_committed_quanta_without_rewriting_them(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    job = enqueue(factory, path, {**body, 'start_date': bars[-12]['event_date']})
    service.process_one_event_store(factory, path)
    assert get(factory, job['id'])['completed_count'] == 10
    saved = {row['id']: row['created_at'] for row in records(factory)}
    with factory.begin() as session:
        session.get(EventStoreJob, job['id']).attempt_id = 'interrupted-owned-attempt'
    service.recover_interrupted_event_store_jobs(factory)
    assert get(factory, job['id'])['state'] == 'queued'
    assert finish(factory, path, job['id'])['completed_count'] == 12
    assert all(row['created_at'] == saved[row['id']] for row in records(factory) if row['id'] in saved)


@pytest.mark.parametrize('code', ['COMPUTE_TIMEOUT', 'COMPUTE_SHUTDOWN', 'COMPUTE_MEMORY_LIMIT'])
def test_compute_failure_is_explicit_and_shutdown_is_resumable(scenario, monkeypatch, code):
    factory, path, body, _ = scenario
    job = enqueue(factory, path, body)
    def fail(*_, **__):
        raise ComputeProcessError(code, 'controlled fixture')
    monkeypatch.setattr(service, 'run_json_process', fail)
    service.process_one_event_store(factory, path)
    result = get(factory, job['id'])
    assert result['state'] == ('queued' if code == 'COMPUTE_SHUTDOWN' else 'failed')
    assert records(factory) == []


def test_algorithm_version_change_blocks_resume_and_preserves_old_records(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    job = enqueue(factory, path, {**body, 'start_date': bars[-12]['event_date']})
    service.process_one_event_store(factory, path)
    old = records(factory)
    monkeypatch.setattr(service, 'code_sha256', lambda: 'f' * 64)
    service.process_one_event_store(factory, path)
    assert get(factory, job['id'])['error'] == 'CODE_VERSION_CHANGED'
    assert records(factory) == old


def test_corrupt_cached_payload_is_error_not_empty_snapshot(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    job = enqueue(factory, path, body)
    finish(factory, path, job['id'])
    key = records(factory)[0]['id']
    with factory.begin() as session:
        session.get(EventStoreRecord, key).result_json = '{}'
    with factory() as session, pytest.raises(TradeError) as failure:
        service.get_record(session, key)
    assert failure.value.code == 'EVENT_STORE_CACHE_CORRUPT'
    overlap = enqueue(factory, path, {**body, 'start_date': bars[-2]['event_date']})
    assert finish(factory, path, overlap['id'])['error'] == 'EVENT_STORE_CACHE_CORRUPT'


def test_quality_gate_rejects_future_events_and_score_outliers():
    result = calculate_snapshot(event_rich_bars())
    result['snapshot']['event_dates']['future'] = '2099-01-01'
    with pytest.raises(TradeError, match='日期'):
        validate_result(result, '2025-12-31')
    result['snapshot']['event_dates'].pop('future')
    result['snapshot']['event_score'] = float('nan')
    with pytest.raises(TradeError, match='评分'):
        validate_result(result, '2025-12-31')


def test_exact_coverage_bounds_and_duplicate_aliases(scenario):
    factory, path, body, bars = scenario
    with factory.begin() as session:
        alias = import_dataset(session, path, {'symbol': 'sh600000', 'adjustment': 'none', 'bars': bars})
    with factory() as session:
        for updates in ({'dataset_ids': [body['dataset_ids'][0], alias['id']]}, {'window_days': [60, 60]},
                        {'window_days': [True]}, {'start_date': '2020-01-01'}, {'dataset_ids': []}):
            with pytest.raises(TradeError):
                service.prepare_backfill(session, path, {**body, **updates})


def test_partial_cache_and_job_survive_full_backup_restore(scenario, monkeypatch, tmp_path):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    job = enqueue(factory, path, {**body, 'start_date': bars[-12]['event_date']})
    service.process_one_event_store(factory, path)
    destination = tmp_path / 'restored'
    restore_to_new_directory(create_backup(path), destination)
    engine, restored_factory = open_database(destination)
    try:
        service.recover_interrupted_event_store_jobs(restored_factory)
        assert finish(restored_factory, destination, job['id'])['state'] == 'succeeded'
        assert len(records(restored_factory)) == 12
    finally:
        engine.dispose()


def test_api_get_is_read_only_and_create_requires_current_preview(tmp_path, monkeypatch):
    with started_client(create_app(tmp_path, auto_rebuild=False)) as client:
        post = writer(client)
        api_bars = [{**bar, **{key: str(bar[key]) for key in ('open', 'high', 'low', 'close')}} for bar in event_rich_bars()]
        dataset = data(post('/api/v1/market/datasets', {'symbol': '600000', 'adjustment': 'none', 'bars': api_bars}))
        day = event_rich_bars()[-1]['event_date']
        body = {'dataset_ids': [dataset['id']], 'start_date': day, 'end_date': day}
        preview = data(post('/api/v1/research/event-store/preview', body))
        assert preview['missing_count'] == 1
        assert post('/api/v1/research/event-store/jobs', {**body, 'expected_preview_sha256': '0' * 64}).status_code == 409
        created = data(post('/api/v1/research/event-store/jobs', {**body, 'expected_preview_sha256': preview['input_sha256']}))
        monkeypatch.setattr(service, 'run_json_process', lambda *_, **__: pytest.fail('GET started computing'))
        assert data(client.get('/api/v1/research/event-store/stats'))['record_count'] == 0
        assert data(client.get('/api/v1/research/event-store/records')) == []
        assert data(client.get('/api/v1/research/event-store/jobs/' + created['id']))['state'] == 'queued'
        assert data(post('/api/v1/research/event-store/jobs/' + created['id'] + '/cancel', {}))['state'] == 'cancelled'


def test_shared_dispatcher_yields_event_store_after_one_quantum(scenario, monkeypatch):
    inline(monkeypatch)
    factory, path, body, bars = scenario
    job = enqueue(factory, path, {**body, 'start_date': bars[-12]['event_date']})
    other = []
    scheduler = ComputeScheduler([lambda: service.process_one_event_store(factory, path), lambda: other.append('backtest') or True])
    assert scheduler.process_one() and get(factory, job['id'])['completed_count'] == 10
    assert scheduler.process_one() and other == ['backtest']
    assert get(factory, job['id'])['completed_count'] == 10
