"""Durable attempts and bounded independent background execution."""
import asyncio
import hashlib
import json
import threading
import time

import pytest

import trade_app.main as main_module
from trade_app.platform.compute_process import ComputeProcessError, ComputeResult, run_json_process
from trade_app.platform.instance_lock import InstanceLock
from trade_app.research import backtest_service
from trade_app.research.backtest_models import BacktestRun
from test_strategy_backtests import flat_bars, execute, one_signal
from test_wyckoff_research_api import client_for, write, data, dataset


def queue(client):
    ds = dataset(client, flat_bars())
    return data(write(client, '/backtests', {'dataset_id': ds['id'],
                'strategy_id': 'relative_strength_breakout_v1', 'strict': True}))


def get(client, run):
    return data(client.get('/api/v1/backtests/' + run['id']))


def test_real_worker_publishes_complete_digest_and_frozen_budget(tmp_path):
    with client_for(tmp_path) as client:
        queued = queue(client)
        assert queued['config']['compute_budget']['timeout_seconds'] == 120
        assert queued['config']['compute_budget']['memory_bytes'] == 512 * 1024 * 1024
        assert queued['attempt_number'] == 0 and queued['result_sha256'] is None
        assert queued['capabilities'] == {'cancel': True, 'retry': False, 'pause': False, 'resume': False}
        assert backtest_service.process_one_backtest(client.app.state.db_factory, tmp_path)
        complete = get(client, queued)
        assert complete['state'] == 'succeeded', complete['error']
        assert complete['attempt_number'] == 1
        encoded = json.dumps(complete['result'], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        assert complete['result_sha256'] == hashlib.sha256(encoded.encode()).hexdigest()
        assert complete['result']['compute']['budget'] == queued['config']['compute_budget']
        assert complete['result']['compute']['peak_memory_bytes'] > 0
        assert not backtest_service.process_one_backtest(client.app.state.db_factory, tmp_path)
        with client.app.state.db_factory() as session:
            assert session.get(BacktestRun, queued['id']).attempt_id is None


@pytest.fixture
def slow_runner(tmp_path, monkeypatch):
    (tmp_path / 'backtest_slow_fixture.py').write_text(
        'import json,sys,time\n'
        'from trade_app.platform.compute_process import apply_worker_memory_limit\n'
        'apply_worker_memory_limit()\n'
        'json.load(sys.stdin)\n'
        'time.sleep(30)\n', encoding='utf-8')
    entered = threading.Event()
    captured = {}

    def runner(_module, payload, *, budget, stop_reason):
        captured['payload'] = payload
        entered.set()
        try:
            return run_json_process('backtest_slow_fixture', payload, budget=budget,
                                    stop_reason=stop_reason, module_paths=(tmp_path,))
        except ComputeProcessError as exc:
            captured['failure'] = exc
            raise

    monkeypatch.setattr(backtest_service, 'run_json_process', runner)
    return entered, captured


@pytest.mark.parametrize('action', ['cancel', 'shutdown'])
def test_real_active_child_cancel_or_shutdown_is_bounded_and_never_published(tmp_path, slow_runner, action):
    entered, captured = slow_runner
    stop = threading.Event()
    with client_for(tmp_path) as client:
        queued = queue(client)
        factory = client.app.state.db_factory
        worker = threading.Thread(target=backtest_service.process_one_backtest, args=(factory, tmp_path),
                                  kwargs={'should_stop': stop.is_set})
        worker.start()
        try:
            assert entered.wait(2)
            assert get(client, queued)['state'] == 'running'
            assert not backtest_service.process_one_backtest(factory, tmp_path)
            # Only immutable calculation data enters the child, never the factory or paths.
            assert set(captured['payload']) == {'attempt_id', 'symbol', 'bars', 'strategy_id', 'params', 'config'}
            started = time.monotonic()
            if action == 'cancel':
                response = data(write(client, '/backtests/' + queued['id'] + '/cancel', {}))
                assert response['state'] == 'cancelling'
            else:
                stop.set()
            worker.join(timeout=3)
            assert not worker.is_alive()
            assert time.monotonic() - started < 3
            complete = get(client, queued)
            assert complete['state'] == ('cancelled' if action == 'cancel' else 'queued')
            assert complete['error'] == (None if action == 'cancel' else 'INTERRUPTED_SHUTDOWN')
            assert complete['result'] is None and complete['result_sha256'] is None
            assert complete['attempt_number'] == 1
            assert captured['failure'].code == ('COMPUTE_CANCELLED' if action == 'cancel' else 'COMPUTE_SHUTDOWN')
        finally:
            stop.set()
            worker.join(timeout=3)


def test_frozen_timeout_marks_failure_retry_retains_budget_and_new_attempt(tmp_path, slow_runner, monkeypatch):
    with client_for(tmp_path) as client:
        queued = queue(client)
        factory = client.app.state.db_factory
        # Inject a short frozen budget to exercise the boundary without a 120s test.
        with factory.begin() as session:
            row = session.get(BacktestRun, queued['id'])
            config = json.loads(row.config_json)
            config['compute_budget']['timeout_seconds'] = 0.3
            row.config_json = json.dumps(config)
        assert backtest_service.process_one_backtest(factory, tmp_path)
        failed = get(client, queued)
        assert failed['state'] == 'failed' and failed['error'].startswith('COMPUTE_TIMEOUT:')
        assert failed['result'] is None and failed['result_sha256'] is None
        assert failed['capabilities']['retry']
        retried = data(write(client, '/backtests/' + queued['id'] + '/retry', {}))
        assert retried['config']['compute_budget']['timeout_seconds'] == 0.3
        monkeypatch.setattr(backtest_service, 'run_json_process', lambda _, payload, **__: ComputeResult(
            {'ok': True, 'attempt_id': payload['attempt_id'], 'result': {'trade_count': 0}}, {'test': True}))
        assert backtest_service.process_one_backtest(factory, tmp_path)
        complete = get(client, queued)
        assert complete['state'] == 'succeeded'
        assert complete['attempt_number'] == 2 and complete['result_sha256']


@pytest.mark.parametrize('mutation', ['attempt', 'cancel', 'code', 'envelope'])
def test_publication_rechecks_attempt_cancel_code_and_envelope(tmp_path, monkeypatch, mutation):
    with client_for(tmp_path) as client:
        queued = queue(client)
        factory = client.app.state.db_factory

        def runner(_, payload, **__):
            if mutation in ('attempt', 'cancel'):
                with factory.begin() as session:
                    row = session.get(BacktestRun, queued['id'])
                    if mutation == 'attempt':
                        row.attempt_id = 'replacement-attempt'
                        row.state = 'queued'
                    else:
                        row.cancel_requested = 1
            elif mutation == 'code':
                monkeypatch.setattr(backtest_service, 'code_sha256', lambda: 'changed-during-compute')
            return ComputeResult({'ok': True, 'attempt_id': 'wrong-attempt' if mutation == 'envelope' else payload['attempt_id'],
                                  'result': {'trade_count': 0}}, {})

        monkeypatch.setattr(backtest_service, 'run_json_process', runner)
        assert backtest_service.process_one_backtest(factory, tmp_path)
        result = get(client, queued)
        assert result['result'] is None and result['result_sha256'] is None
        if mutation == 'attempt':
            assert result['state'] == 'queued'
            with factory() as session:
                assert session.get(BacktestRun, queued['id']).attempt_id == 'replacement-attempt'
        elif mutation == 'cancel':
            assert result['state'] == 'cancelled' and result['error'] is None
        else:
            assert result['state'] == 'failed'
            assert result['error'].startswith('CODE_VERSION_CHANGED:' if mutation == 'code' else 'COMPUTE_INVALID_RESULT:')


def test_restart_recovery_invalidates_attempt_and_does_not_republish_partial_result(tmp_path):
    with client_for(tmp_path) as client:
        queued = queue(client)
        factory = client.app.state.db_factory
        with factory.begin() as session:
            row = session.get(BacktestRun, queued['id'])
            row.state, row.attempt_id, row.attempt_number = 'running', 'lost-worker', 3
            row.result_json, row.result_sha256 = '{"partial":true}', 'invalid'
        backtest_service.recover_interrupted_backtests(factory)
        recovered = get(client, queued)
        assert recovered['state'] == 'queued' and recovered['error'] == 'RECOVERED_AFTER_RESTART'
        assert recovered['result'] is None and recovered['result_sha256'] is None
        assert recovered['attempt_number'] == 3
        assert backtest_service.process_one_backtest(factory, tmp_path)
        assert get(client, queued)['attempt_number'] == 4
        assert get(client, queued)['state'] == 'succeeded'


def test_independent_domain_loops_continue_while_backtest_waits_and_stop_promptly(tmp_path, monkeypatch):
    entered = threading.Event()
    progress = {name: threading.Event() for name in ('analytics', 'online', 'tdx')}

    def backtest(*_, should_stop):
        entered.set()
        while not should_stop():
            time.sleep(0.02)
        return True

    monkeypatch.setattr(main_module, 'process_one_backtest', backtest)
    monkeypatch.setattr(main_module, 'process_one', lambda *_: progress['analytics'].set())
    monkeypatch.setattr(main_module, 'process_one_sync_symbol', lambda *_: progress['online'].set())
    monkeypatch.setattr(main_module, 'process_one_universe_symbol', lambda *_: progress['tdx'].set())
    app = main_module.create_app(tmp_path)

    async def exercise():
        async with app.router.lifespan_context(app):
            deadline = time.monotonic() + 2
            while not (entered.is_set() and all(event.is_set() for event in progress.values())):
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
            assert len(app.state.background_threads) == 4
            started = time.monotonic()
        assert time.monotonic() - started < 1
        assert all(not worker.is_alive() for worker in app.state.background_threads)

    asyncio.run(exercise())
    lock = InstanceLock(tmp_path)
    lock.acquire()
    lock.release()


def test_shutdown_deadline_retains_lock_until_slow_provider_releases(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_provider(*_):
        entered.set()
        release.wait(3)

    monkeypatch.setattr(main_module, 'BACKGROUND_SHUTDOWN_SECONDS', 0.15)
    monkeypatch.setattr(main_module, 'process_one_sync_symbol', slow_provider)
    monkeypatch.setattr(main_module, 'process_one', lambda *_: False)
    monkeypatch.setattr(main_module, 'process_one_backtest', lambda *_, **__: False)
    monkeypatch.setattr(main_module, 'process_one_universe_symbol', lambda *_: False)
    app = main_module.create_app(tmp_path)

    async def exercise():
        try:
            async with app.router.lifespan_context(app):
                deadline = time.monotonic() + 2
                while not entered.is_set():
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)
                started = time.monotonic()
            assert time.monotonic() - started < 0.8
            with pytest.raises(RuntimeError, match='already in use'):
                InstanceLock(tmp_path).acquire()
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while True:
            lock = InstanceLock(tmp_path)
            try:
                lock.acquire()
                lock.release()
                break
            except RuntimeError:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)

    asyncio.run(exercise())


def test_loose_unknown_availability_keeps_known_at_null(monkeypatch):
    from trade_app.research import backtest_domain
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', one_signal)
    bars = flat_bars()
    bars[0]['available_at'] = None
    result = execute(bars, strict=False)
    assert result['trades']
    assert 'historical_availability_unknown' in result['quality_flags']
    assert all(trade['known_at'] is None for trade in result['trades'])
    assert all(item['known_at'] is None for item in result['decisions'])
