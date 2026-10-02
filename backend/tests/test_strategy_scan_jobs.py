"""Durable scan regression: real child parity, isolation, cancellation and restore."""
from copy import deepcopy
import json
from threading import Event, Thread

import pytest
from sqlalchemy import func, select, text

from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.compute_process import ComputeProcessError, ComputeResult
from trade_app.platform.compute_scheduler import ComputeScheduler
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research.event_profile_service import save_profile
from trade_app.research.models import ResearchRun
from trade_app.research.scan_models import StrategyScanRun
from trade_app.research.scan_job_models import StrategyScanJob, StrategyScanChunk
from trade_app.research import scan_job_service as jobs
from trade_app.research.scan_job_worker import compute_chunk, encode
from trade_app.research.scan_service import create_scan, get_scan
from trade_app.research.service import create_run, describe_strategy, strategy_catalog
from test_strategy_scans import _bars, _import, RELATIVE, WULONG


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    first = _import(factory, tmp_path, '600000', _bars(wulong=True))
    second = _import(factory, tmp_path, '600001', _bars())
    body = {'dataset_ids': [first, second], 'strategies': [{'strategy_id': RELATIVE, 'params': {}},
                                                         {'strategy_id': WULONG, 'params': {}}],
            'as_of_date': '2025-03-29', 'strict': True}
    yield factory, tmp_path, body
    engine.dispose()


def enqueue(factory, path, body):
    with factory() as session:
        prepared = jobs.prepare_scan_job(session, path, body)
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        return jobs.enqueue_scan_job(session, prepared)


def get(factory, job_id):
    with factory() as session:
        return jobs.get_scan_job(session, job_id)


def finish(factory, path, job_id):
    for _ in range(100):
        if get(factory, job_id)['state'] not in ('queued', 'running'):
            return get(factory, job_id)
        assert jobs.process_one_scan_chunk(factory, path)
    pytest.fail('scan did not complete within bounded quanta')


def assert_no_publication(factory):
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0
        assert session.scalar(select(func.count()).select_from(StrategyScanRun)) == 0


def inline_child(monkeypatch, callback=None):
    def run(_module, payload, **_kwargs):
        result = compute_chunk(payload)
        if callback:
            callback(payload, result)
        return ComputeResult(result, {'elapsed_ms': 1})
    monkeypatch.setattr(jobs, 'run_json_process', run)


def test_real_child_same_runs_and_scan_evidence_as_sync_and_atomic_visibility(scenario):
    factory, path, body = scenario
    job = enqueue(factory, path, body)
    assert job['state'] == 'queued' and job['total_count'] == 4
    assert enqueue(factory, path, {**body, 'dataset_ids': body['dataset_ids'][::-1]})['id'] == job['id']
    assert jobs.process_one_scan_chunk(factory, path)
    partial = get(factory, job['id'])
    assert partial['completed_count'] == 2 and partial['state'] == 'running'
    assert_no_publication(factory)
    assert partial['checkpoint_bytes'] > 0
    completed = finish(factory, path, job['id'])
    assert completed['state'] == 'succeeded', completed
    assert completed['completed_count'] == 4 and completed['checkpoint_bytes'] == 0
    with factory() as session:
        actual = get_scan(session, completed['scan_id'])
        assert session.scalar(select(func.count()).select_from(StrategyScanChunk)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 4
    with factory.begin() as session:
        expected = create_scan(session, path, body)
    assert actual['request'] == expected['request']
    assert {k: v for k, v in actual['result'].items() if k != 'notes'} == {
        k: v for k, v in expected['result'].items() if k != 'notes'}
    assert actual['signal_count'] == 3 and actual['intersection_count'] == 1


def test_all_single_strategy_descriptors_preserve_create_run_identity(scenario, monkeypatch):
    factory, path, body = scenario
    strategies = [row for row in strategy_catalog() if row['signal_params'] is not None]
    inline_child(monkeypatch)
    for row in strategies:
        request = {**body, 'dataset_ids': body['dataset_ids'][:1],
                   'strategies': [{'strategy_id': row['id'], 'params': {}}]}
        job = enqueue(factory, path, request)
        completed = finish(factory, path, job['id'])
        assert completed['state'] == 'succeeded', (row['id'], completed)
        with factory() as session:
            scan = get_scan(session, completed['scan_id'])
        child = scan['result']['rows'][0]
        with factory.begin() as session:
            direct = create_run(session, path, {'strategy_id': row['id'], 'params': {},
                'dataset_id': body['dataset_ids'][0], 'strict': True, 'decision_at': child['decision_at']})
        assert direct['id'] == child['run_id']
        assert describe_strategy(row['id'])['code_sha256'] == direct['result']['code_sha256']


def test_foreground_writer_available_during_child_compute(scenario, monkeypatch):
    factory, path, body = scenario
    job = enqueue(factory, path, body)
    def save_during_compute(_payload, _result):
        # This would time out if compute still held the SQLite writer lock.
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            session.execute(text("CREATE TABLE foreground_scan_probe (value TEXT)"))
            session.execute(text("INSERT INTO foreground_scan_probe VALUES ('saved')"))
    inline_child(monkeypatch, save_during_compute)
    assert jobs.process_one_scan_chunk(factory, path)
    with factory() as session:
        assert session.scalar(text('SELECT value FROM foreground_scan_probe')) == 'saved'
    assert get(factory, job['id'])['completed_count'] == 2


def test_frozen_profile_and_parameters_survive_later_edits(scenario, monkeypatch):
    factory, path, body = scenario
    with factory.begin() as session:
        profile = save_profile(session, {'profile': {'name': 'Frozen',
            'dimensions': [{'metric_key': 'risk_score'}]}})
    request = {**body, 'strategies': [{'strategy_id': 'wyckoff_trend_v1', 'params': {}}],
               'event_profile_id': profile['profile_id'], 'event_profile_revision': 1}
    job = enqueue(factory, path, request)
    frozen = deepcopy(job['request']['event_profile'])
    request['strategies'][0]['params']['made_up'] = '999'
    with factory.begin() as session:
        save_profile(session, {'expected_revision': 1, 'profile': {'name': 'Changed',
            'dimensions': [{'metric_key': 'event_background_score'}]}}, profile['profile_id'])
    inline_child(monkeypatch)
    completed = finish(factory, path, job['id'])
    assert completed['state'] == 'succeeded', completed
    with factory() as session:
        scan = get_scan(session, completed['scan_id'])
        for run_id in scan['result']['run_ids']:
            assert json.loads(session.get(ResearchRun, run_id).result_json)['event_profile'] == frozen


def test_backup_restore_resumes_private_checkpoints_without_recompute(scenario, tmp_path, monkeypatch):
    factory, path, body = scenario
    inline_child(monkeypatch)
    job = enqueue(factory, path, body)
    assert jobs.process_one_scan_chunk(factory, path)
    assert_no_publication(factory)
    destination = tmp_path / 'restored'
    restore_to_new_directory(create_backup(path), destination)
    engine2, factory2 = open_database(destination)
    try:
        jobs.recover_interrupted_scan_jobs(factory2)
        recovered = get(factory2, job['id'])
        assert recovered['state'] == 'queued' and recovered['completed_count'] == 2
        observed = []
        inline_child(monkeypatch, lambda payload, _result: observed.append(payload['start_index']))
        completed = finish(factory2, destination, job['id'])
        assert completed['state'] == 'succeeded' and observed == [2]
        assert get(factory, job['id'])['state'] == 'running'
    finally:
        engine2.dispose()


def test_cancel_before_and_during_compute_never_publishes(scenario, monkeypatch):
    factory, path, body = scenario
    job = enqueue(factory, path, body)
    with factory.begin() as session:
        cancelled = jobs.cancel_scan_job(session, job['id'])
    assert cancelled['state'] == 'cancelled'
    assert jobs.process_one_scan_chunk(factory, path) is False
    with factory.begin() as session:
        jobs.retry_scan_job(session, job['id'])
    def cancel(_payload, _result):
        with factory.begin() as session:
            assert jobs.cancel_scan_job(session, job['id'])['state'] == 'cancelling'
    inline_child(monkeypatch, cancel)
    assert jobs.process_one_scan_chunk(factory, path)
    assert get(factory, job['id'])['state'] == 'cancelled'
    assert get(factory, job['id'])['completed_count'] == 0
    assert_no_publication(factory)


def test_shutdown_and_stale_attempt_cannot_publish(scenario, monkeypatch):
    factory, path, body = scenario
    job = enqueue(factory, path, body)
    stop = Event()
    inline_child(monkeypatch, lambda _payload, _result: stop.set())
    assert jobs.process_one_scan_chunk(factory, path, should_stop=stop.is_set)
    assert get(factory, job['id'])['state'] == 'queued'
    assert_no_publication(factory)
    def supersede(_payload, _result):
        with factory.begin() as session:
            row = session.get(StrategyScanJob, job['id'])
            row.attempt_id, row.state = 'a-later-attempt', 'running'
    inline_child(monkeypatch, supersede)
    assert jobs.process_one_scan_chunk(factory, path)
    assert_no_publication(factory)
    with factory() as session:
        assert session.get(StrategyScanJob, job['id']).attempt_id == 'a-later-attempt'


@pytest.mark.parametrize('failure', ['market_file', 'input_hash', 'checkpoint_hash', 'code'])
def test_modified_input_or_evidence_fails_without_partial_results(scenario, monkeypatch, failure):
    factory, path, body = scenario
    inline_child(monkeypatch)
    job = enqueue(factory, path, body)
    assert jobs.process_one_scan_chunk(factory, path)
    if failure == 'market_file':
        dataset_path = path / 'market' / (body['dataset_ids'][0] + '.json')
        dataset_path.write_bytes(dataset_path.read_bytes() + b' ')
    elif failure == 'input_hash':
        with factory.begin() as session:
            row = session.get(StrategyScanJob, job['id'])
            frozen = json.loads(row.request_json)
            frozen['request']['strict'] = False
            row.request_json = encode(frozen)
    elif failure == 'checkpoint_hash':
        with factory.begin() as session:
            row = session.scalar(select(StrategyScanChunk))
            row.result_json += ' '
    else:
        monkeypatch.setattr(jobs, 'code_sha256', lambda: 'changed')
    completed = finish(factory, path, job['id'])
    assert completed['state'] == 'failed', completed
    assert completed['error'] in {'MARKET_DATA_CORRUPT', 'SCAN_INPUT_CORRUPT', 'SCAN_CHECKPOINT_CORRUPT', 'CODE_VERSION_CHANGED'}
    assert_no_publication(factory)


def test_late_and_future_bars_remain_excluded_in_async_scan(scenario, monkeypatch):
    factory, path, body = scenario
    bars = _bars()
    bars[-1]['available_at'] = '2025-03-30T08:00:00Z'
    future = {**bars[-1], 'event_date': '2025-03-30', 'high': '1000', 'close': '999', 'volume': 100000000}
    dataset_id = _import(factory, path, '600003', [*bars, future])
    job = enqueue(factory, path, {**body, 'dataset_ids': [dataset_id],
                                  'strategies': [{'strategy_id': RELATIVE, 'params': {}}]})
    inline_child(monkeypatch)
    completed = finish(factory, path, job['id'])
    assert completed['state'] == 'succeeded', completed
    with factory() as session:
        row = get_scan(session, completed['scan_id'])['result']['rows'][0]
    assert row['source_date'] == '2025-03-28' and row['signal'] is False
    assert row['run_signal'] is True and row['draft_eligible'] is False


def test_invalid_request_no_job_or_child_side_effects(scenario):
    factory, path, body = scenario
    for invalid in ({**body, 'strategies': [{'strategy_id': RELATIVE, 'params': {'unknown': '1'}}]},
                    {**body, 'dataset_ids': body['dataset_ids'][:1] * 2}):
        with pytest.raises(TradeError):
            enqueue(factory, path, invalid)
    assert_no_publication(factory)
    with factory() as session:
        assert jobs.list_scan_jobs(session) == []


def test_timeout_retains_prior_checkpoint_and_retry_resumes(scenario, monkeypatch):
    factory, path, body = scenario
    inline_child(monkeypatch)
    job = enqueue(factory, path, body)
    jobs.process_one_scan_chunk(factory, path)
    def timeout(*_args, **_kwargs):
        raise ComputeProcessError('COMPUTE_TIMEOUT', 'bounded child timeout')
    monkeypatch.setattr(jobs, 'run_json_process', timeout)
    jobs.process_one_scan_chunk(factory, path)
    assert get(factory, job['id'])['state'] == 'failed'
    assert get(factory, job['id'])['completed_count'] == 2
    with factory.begin() as session:
        jobs.retry_scan_job(session, job['id'])
    observed = []
    inline_child(monkeypatch, lambda payload, _result: observed.append(payload['start_index']))
    assert finish(factory, path, job['id'])['state'] == 'succeeded'
    assert observed == [2]


def test_cancel_at_final_publication_boundary_remains_atomic(scenario, monkeypatch):
    factory, path, body = scenario
    inline_child(monkeypatch)
    job = enqueue(factory, path, body)
    original = jobs._publication
    def cancel_before_write(*args):
        result = original(*args)
        with factory.begin() as session:
            jobs.cancel_scan_job(session, job['id'])
        return result
    monkeypatch.setattr(jobs, '_publication', cancel_before_write)
    assert finish(factory, path, job['id'])['state'] == 'cancelled'
    assert_no_publication(factory)


def test_shared_scheduler_fairness_and_cross_instance_single_cpu():
    calls = []
    first = ComputeScheduler([lambda: calls.append('backtest') or True,
                              lambda: calls.append('scan') or True,
                              lambda: calls.append('plateau') or True])
    assert all(first.process_one() for _ in range(6))
    assert calls == ['backtest', 'scan', 'plateau'] * 2
    entered, release = Event(), Event()
    def blocking():
        entered.set()
        assert release.wait(3)
        return True
    busy = ComputeScheduler([blocking])
    thread = Thread(target=busy.process_one)
    thread.start()
    assert entered.wait(1)
    try:
        assert first.process_one() is False
        assert len(calls) == 6
    finally:
        release.set()
        thread.join(3)
    assert first.process_one() and calls[-1] == 'backtest'


def test_queue_compute_and_result_limits_are_explicit(scenario, monkeypatch):
    factory, path, body = scenario
    for index in range(jobs.MAX_QUEUED_JOBS):
        enqueue(factory, path, {**body, 'as_of_date': f'2025-03-{index + 1:02d}'})
    with pytest.raises(TradeError) as failure:
        enqueue(factory, path, body)
    assert failure.value.code == 'SCAN_QUEUE_FULL'
    with factory.begin() as session:
        for row in session.scalars(select(StrategyScanJob)):
            row.elapsed_ms = jobs.MAX_JOB_COMPUTE_MS
    monkeypatch.setattr(jobs, 'run_json_process', lambda *a, **k: pytest.fail('budgeted-out job spawned a child'))
    assert jobs.process_one_scan_chunk(factory, path)
    with factory() as session:
        failed = [row for row in jobs.list_scan_jobs(session) if row['state'] == 'failed']
    assert len(failed) == 1 and failed[0]['error'] == 'SCAN_COMPUTE_BUDGET'
    assert_no_publication(factory)


def test_output_limit_and_incorrect_attempt_envelope_are_not_published(scenario, monkeypatch):
    factory, path, body = scenario
    job = enqueue(factory, path, body)
    inline_child(monkeypatch, lambda _payload, result: result.update(attempt_id='wrong-attempt'))
    jobs.process_one_scan_chunk(factory, path)
    assert get(factory, job['id'])['error'] == 'SCAN_ATTEMPT_MISMATCH'
    with factory.begin() as session:
        jobs.retry_scan_job(session, job['id'])
    def oversized(_payload, result):
        result['runs'][0]['result']['extra'] = 'x' * (jobs.MAX_CHUNK_BYTES + 1)
    inline_child(monkeypatch, oversized)
    jobs.process_one_scan_chunk(factory, path)
    assert get(factory, job['id'])['error'] == 'SCAN_CHECKPOINT_LIMIT'
    assert_no_publication(factory)


def test_http_queue_security_idempotency_and_published_evidence(tmp_path, monkeypatch):
    from trade_app.main import create_app
    from test_matrix_pool_api import started_client, writer, data

    app = create_app(tmp_path, auto_rebuild=False)
    with started_client(app) as client:
        post = writer(client)
        dataset = data(post('/api/v1/market/datasets', {'symbol': '600000', 'bars': _bars(wulong=True),
                                                     'adjustment': 'none'}))
        body = {'dataset_ids': [dataset['id']], 'strategies': [{'strategy_id': WULONG, 'params': {}}],
                'as_of_date': '2025-03-29', 'strict': True}
        endpoint = '/api/v1/research/scan-jobs'
        assert client.post(endpoint, json=body).status_code == 403
        queued = data(post(endpoint, body, key='stable-scan'))
        assert queued['state'] == 'queued'
        assert data(post(endpoint, body, key='stable-scan')) == queued
        assert post(endpoint, {**body, 'strict': False}, key='stable-scan').status_code == 409
        assert data(client.get(endpoint))[0]['id'] == queued['id']
        assert data(client.get('/api/v1/research/scans')) == []
        inline_child(monkeypatch)
        completed = finish(app.state.db_factory, tmp_path, queued['id'])
        assert completed['state'] == 'succeeded'
        detail = data(client.get(endpoint + '/' + queued['id']))
        scan = data(client.get('/api/v1/research/scans/' + detail['scan_id']))
        assert scan['signal_count'] == 1
        run = data(client.get('/api/v1/research/runs/' + scan['result']['run_ids'][0]))
        assert run['result']['signal'] is True
        assert post(endpoint + '/' + queued['id'] + '/cancel', {}).status_code == 409
        assert client.get(endpoint + '/missing').status_code == 404
