from copy import deepcopy
from decimal import Decimal
import json

import pytest

from trade_app.platform.compute_process import ComputeProcessError, ComputeResult, run_json_process
from trade_app.platform.types import TradeError
from trade_app.research import backtest_domain, backtest_service, walk_forward_service as service
from trade_app.research.plateau_domain import digest
from trade_app.research.walk_forward_domain import build_folds, select_candidate
from trade_app.research.walk_forward_models import WalkForwardJob, WalkForwardTask
from test_strategy_backtests import execute, flat_bars
from test_wyckoff_research_api import client_for, data, dataset, write


def rising_bars(count=105):
    bars = flat_bars(count)
    for index, bar in enumerate(bars):
        close = Decimal('10') + index * Decimal('.12')
        bar.update(open=str(close - Decimal('.03')), high=str(close + Decimal('.2')),
                   low=str(close - Decimal('.2')), close=str(close), volume=100000 + index * 1000)
    return bars


def test_anchored_folds_gap_warmup_complete_test_windows_and_limits():
    bars = flat_bars(120)
    plan = build_folds(bars, initial_train_bars=40, test_bars=20, warmup_bars=10, gap_bars=2, max_folds=5,
                       candidate_count=3)
    assert plan['actual_folds'] == 3 and plan['unused_tail_bars'] == 4
    folds = plan['folds']
    assert folds[0]['train_start_index'] == 10 and folds[0]['train_end_index'] == 49
    assert folds[0]['test_start_index'] == 52 and folds[0]['test_end_index'] == 71
    assert folds[1]['train_start_index'] == 10 and folds[1]['train_end_index'] == 71
    assert folds[1]['test_start_index'] == 74
    assert plan['evaluation_points'] == 12
    with pytest.raises(TradeError) as error:
        build_folds(bars, initial_train_bars=40, test_bars=20, candidate_count=200)
    assert error.value.code == 'WALK_FORWARD_POINT_LIMIT'
    with pytest.raises(TradeError):
        build_folds(bars[:40], initial_train_bars=32, test_bars=20, candidate_count=2)


def test_warmup_is_indicator_only_first_test_open_is_pit_and_no_inherited_position(monkeypatch):
    bars = flat_bars(50)
    prefixes = []
    def always_signal(_strategy, *, bars, **__):
        prefixes.append([item['event_date'] for item in bars])
        return {'status': 'computed', 'signal': True, 'draft_eligible': True, 'source_date': bars[-1]['event_date'], 'quality_flags': []}
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', always_signal)
    result = execute(bars, trade_start_date=bars[40]['event_date'], holding_bars=3)
    assert len(result['equity']) == 10
    assert result['equity'][0]['date'] == bars[40]['event_date']
    assert result['trades'][0]['date'] == bars[40]['event_date'] and result['trades'][0]['side'] == 'buy'
    assert prefixes[0] == [item['event_date'] for item in bars[:40]]
    assert all(trade['date'] >= bars[40]['event_date'] for trade in result['trades'])
    assert result['evaluation_window']['warmup_bars'] == 40
    # Late prior-day information prevents the first test-open entry.
    bars[39]['available_at'] = bars[40]['event_date'] + 'T02:00:00+00:00'
    late = execute(bars, trade_start_date=bars[40]['event_date'], holding_bars=3)
    assert late['decisions'][0]['status'] == 'skipped_unavailable'
    assert late['trades'][0]['date'] > bars[40]['event_date']
    assert 'evaluation_window' not in execute(flat_bars())


def source(client, path, bars=None):
    ds = dataset(client, bars if bars is not None else rising_bars())
    run = data(write(client, '/backtests', {'dataset_id': ds['id'], 'strategy_id': 'relative_strength_breakout_v1',
                       'params': {'min_ret40': '.01', 'min_vol_slope20': '0'}, 'holding_bars': 3, 'strict': True}))
    assert backtest_service.process_one_backtest(client.app.state.db_factory, path)
    assert data(client.get('/api/v1/backtests/' + run['id']))['state'] == 'succeeded'
    return run


def create(client, path, bars=None, **overrides):
    base = source(client, path, bars)
    body = {'base_run_id': base['id'], 'sampling_mode': 'grid', 'seed': 4, 'max_points': 2,
            'axes': {'config.holding_bars': {'values': [2, 4]}}, 'initial_train_bars': 55,
            'test_bars': 20, 'max_folds': 2, 'gap_bars': 1, 'warmup_bars': 0, 'min_train_trades': 1, **overrides}
    factory = client.app.state.db_factory
    with factory() as session:
        preview = service.preview_walk_forward(session, path, body)
    with factory.begin() as session:
        run = service.create_walk_forward(session, path, {**body, 'name': 'WF验收',
                   'expected_preview_sha256': preview['preview_sha256']})
    return run, body, preview


def inspect(client, run):
    with client.app.state.db_factory() as session:
        return service.get_walk_forward(session, run['id'])


def control(client, run, action):
    with client.app.state.db_factory.begin() as session:
        return service.control_walk_forward(session, run['id'], action)


def finish(client, path, run, limit=30):
    for _ in range(limit):
        current = inspect(client, run)
        if current['state'] in ('succeeded', 'failed'):
            return current
        assert service.process_one_walk_forward(client.app.state.db_factory, path)
    raise AssertionError('workflow did not finish within bounded quanta')


def test_real_worker_train_selection_checkpoint_then_test_restart_without_reselection(tmp_path):
    with client_for(tmp_path) as client:
        run, _, _ = create(client, tmp_path)
        factory = client.app.state.db_factory
        for _ in range(3):  # Two training points then the durable selection quantum.
            assert service.process_one_walk_forward(factory, tmp_path)
        checkpoint = inspect(client, run)
        fold = checkpoint['folds'][0]
        assert fold['selection']['selected_candidate_sha256']
        assert next(task for task in checkpoint['tasks'] if task['role'] == 'test' and task['fold_index'] == 0)['state'] == 'queued'
        assert all(task['attempt_number'] == 0 for task in checkpoint['tasks'] if task['role'] == 'test')
        service.recover_interrupted_walk_forwards(factory)
        assert inspect(client, run)['state'] == 'paused'
        control(client, run, 'resume')
        complete = finish(client, tmp_path, run)
        assert complete['state'] == 'succeeded', complete['error']
        assert complete['folds'][0]['selection_sha256'] == fold['selection_sha256']
        assert all(task['attempt_number'] == 1 for task in complete['tasks'])
        assert complete['analysis']['completed_test_folds'] == 2
        assert complete['result_sha256'] == digest(complete['analysis'])
        curve = complete['analysis']['oos_normalized_curve']
        assert len(curve) == 40 and len({point['date'] for point in curve}) == 40
        for fold in complete['folds']:
            test = next(task for task in complete['tasks'] if task['role'] == 'test' and task['fold_index'] == fold['index'])
            with factory() as session:
                detail = service.get_task(session, run['id'], test['id'])
            assert detail['result']['equity'][0]['date'] == fold['test_start']
            assert detail['result']['equity'][-1]['date'] == fold['test_end']
            assert all(trade['date'] >= fold['test_start'] for trade in detail['result']['trades'])
        control(client, run, 'delete')
        with factory() as session:
            assert service.list_walk_forwards(session) == []


def test_future_test_price_shock_cannot_change_train_selection_or_training_metrics(tmp_path):
    with client_for(tmp_path) as client:
        baseline = rising_bars(80)
        first, _, _ = create(client, tmp_path, baseline, max_folds=1)
        original = finish(client, tmp_path, first)
        changed = deepcopy(baseline)
        for index in range(56, len(changed)):
            value = Decimal('5') - Decimal('.03') * (index - 56)
            changed[index].update(open=str(value), high=str(value + Decimal('.1')), low=str(value - Decimal('.1')), close=str(value))
        second, _, _ = create(client, tmp_path, changed, max_folds=1)
        shocked = finish(client, tmp_path, second)
        selection1, selection2 = original['folds'][0]['selection'], shocked['folds'][0]['selection']
        assert selection1['selected_candidate_sha256'] == selection2['selected_candidate_sha256']
        assert [(point['candidate_sha256'], point['point_score'], point['metrics']) for point in selection1['ranking']] == [
                (point['candidate_sha256'], point['point_score'], point['metrics']) for point in selection2['ranking']]
        assert original['analysis']['oos_compounded_return'] != shocked['analysis']['oos_compounded_return']


def test_failed_training_is_recorded_and_no_candidate_skips_test(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        run, _, _ = create(client, tmp_path, max_folds=1)
        monkeypatch.setattr(service, 'run_json_process', lambda *args, **kwargs: (_ for _ in ()).throw(ComputeProcessError('COMPUTE_TIMEOUT', 'fixture')))
        result = finish(client, tmp_path, run)
        assert result['state'] == 'failed' and result['analysis']['completed_test_folds'] == 0
        assert result['folds'][0]['selection']['reason'] == 'NO_ELIGIBLE_TRAINING_CANDIDATE'
        test = next(task for task in result['tasks'] if task['role'] == 'test')
        assert test['state'] == 'skipped' and test['attempt_number'] == 0
        # Observed selection cannot be altered by retrying failed training later.
        with pytest.raises(TradeError) as error:
            control(client, run, 'retry')
        assert error.value.code == 'WALK_FORWARD_SELECTION_FROZEN'


@pytest.mark.parametrize('action', ['pause', 'cancel', 'shutdown', 'code', 'attempt'])
def test_owned_attempt_publication_races(tmp_path, monkeypatch, action):
    from threading import Event
    stopped = Event()
    with client_for(tmp_path) as client:
        run, _, _ = create(client, tmp_path, max_folds=1)
        factory = client.app.state.db_factory
        original = service.run_json_process
        def runner(module, payload, **kwargs):
            result = original(module, payload, **kwargs)
            assert len(payload['bars']) == 55  # No test bars even reach the train worker.
            if action in ('pause', 'cancel'):
                control(client, run, action)
            elif action == 'shutdown':
                stopped.set()
            elif action == 'code':
                monkeypatch.setattr(service, 'code_sha256', lambda: 'changed')
            else:
                with factory.begin() as session:
                    row = session.get(WalkForwardJob, run['id'])
                    row.state, row.attempt_id = 'queued', 'new'
            return result
        monkeypatch.setattr(service, 'run_json_process', runner)
        assert service.process_one_walk_forward(factory, tmp_path, should_stop=stopped.is_set)
        result = inspect(client, run)
        task = next(task for task in result['tasks'] if task['role'] == 'train' and task['ordinal'] == 0)
        if action == 'pause':
            assert result['state'] == 'paused' and task['state'] == 'succeeded'
        else:
            assert task['result_sha256'] is None
            assert result['state'] == {'cancel': 'cancelled', 'shutdown': 'paused', 'code': 'failed', 'attempt': 'queued'}[action]


def test_test_failure_retry_uses_exact_frozen_selection_and_never_retrains(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        run, _, _ = create(client, tmp_path, max_folds=1)
        factory = client.app.state.db_factory
        for _ in range(3):
            service.process_one_walk_forward(factory, tmp_path)
        before = inspect(client, run)
        original = service.run_json_process
        monkeypatch.setattr(service, 'run_json_process', lambda *args, **kwargs: (_ for _ in ()).throw(ComputeProcessError('COMPUTE_TIMEOUT', 'fixture')))
        failed = finish(client, tmp_path, run)
        assert failed['state'] == 'failed'
        control(client, run, 'retry')
        monkeypatch.setattr(service, 'run_json_process', original)
        result = finish(client, tmp_path, run)
        assert result['state'] == 'succeeded'
        assert result['folds'][0]['selection_sha256'] == before['folds'][0]['selection_sha256']
        assert [task['attempt_number'] for task in result['tasks'] if task['role'] == 'train'] == [1, 1]
        assert next(task for task in result['tasks'] if task['role'] == 'test')['attempt_number'] == 2


def test_api_plan_preview_idempotent_create_controls_export_and_delete(tmp_path):
    with client_for(tmp_path) as client:
        base = source(client, tmp_path)
        body = {'base_run_id': base['id'], 'sampling_mode': 'lhs', 'axes': {
                'config.holding_bars': {'min': 2, 'max': 8, 'precision': 0}}, 'seed': 55,
                'sample_points': 3, 'max_points': 4, 'initial_train_bars': 55, 'test_bars': 20,
                'max_folds': 1, 'min_train_trades': 1}
        preview = data(write(client, '/research/walk-forwards/preview', body))
        assert preview['plan']['temporal']['actual_folds'] == 1
        payload = {**body, 'name': 'API滚动验证', 'expected_preview_sha256': preview['preview_sha256']}
        run = data(write(client, '/research/walk-forwards', payload, key='wf-api-key'))
        assert data(write(client, '/research/walk-forwards', payload, key='wf-api-key'))['id'] == run['id']
        prefix = '/research/walk-forwards/' + run['id']
        assert write(client, prefix, {}, 'DELETE').status_code == 409
        assert data(write(client, prefix + '/pause', {}))['state'] == 'paused'
        assert data(write(client, prefix + '/resume', {}))['state'] == 'queued'
        assert data(write(client, prefix + '/cancel', {}))['state'] == 'cancelled'
        assert data(write(client, prefix + '/retry', {}))['state'] == 'queued'
        result = finish(client, tmp_path, run)
        assert result['state'] == 'succeeded'
        test = next(task for task in result['tasks'] if task['role'] == 'test')
        assert data(client.get('/api/v1' + prefix + '/tasks/' + test['id']))['result']['evaluation_window']
        exported = client.get('/api/v1' + prefix + '/export.json')
        assert exported.status_code == 200 and exported.json()['result_sha256'] == result['result_sha256']
        assert data(write(client, prefix, {}, 'DELETE'))['capabilities']['delete']
        assert data(client.get('/api/v1/research/walk-forwards')) == []


@pytest.mark.parametrize('action', ['cancel', 'shutdown'])
def test_actual_compute_process_terminates_on_cancel_and_shutdown(tmp_path, monkeypatch, action):
    import threading
    (tmp_path / 'walk_forward_slow_fixture.py').write_text(
        'import json,sys,time\nfrom trade_app.platform.compute_process import apply_worker_memory_limit\n'
        'apply_worker_memory_limit()\njson.load(sys.stdin)\ntime.sleep(30)\n', encoding='utf-8')
    entered, stopped = threading.Event(), threading.Event()
    with client_for(tmp_path) as client:
        run, _, _ = create(client, tmp_path, max_folds=1)
        def runner(_, payload, *, budget, stop_reason):
            entered.set()
            return run_json_process('walk_forward_slow_fixture', payload, budget=budget, stop_reason=stop_reason,
                                    module_paths=(tmp_path,))
        monkeypatch.setattr(service, 'run_json_process', runner)
        worker = threading.Thread(target=service.process_one_walk_forward, args=(client.app.state.db_factory, tmp_path),
                                  kwargs={'should_stop': stopped.is_set})
        worker.start()
        try:
            assert entered.wait(3)
            if action == 'cancel':
                control(client, run, 'cancel')
            else:
                stopped.set()
            worker.join(3)
            assert not worker.is_alive()
            result = inspect(client, run)
            assert result['state'] == ('cancelled' if action == 'cancel' else 'paused')
            assert all(task['result_sha256'] is None for task in result['tasks'])
        finally:
            stopped.set()
            worker.join(3)


def test_changed_plan_and_budget_cannot_be_resumed(tmp_path):
    with client_for(tmp_path) as client:
        run, _, _ = create(client, tmp_path, max_folds=1)
        control(client, run, 'pause')
        factory = client.app.state.db_factory
        with factory.begin() as session:
            row = session.get(WalkForwardJob, run['id'])
            original_plan = row.plan_json
            plan = json.loads(row.plan_json)
            plan['temporal']['min_train_trades'] = 0
            row.plan_json = json.dumps(plan)
        with pytest.raises(TradeError) as error:
            control(client, run, 'resume')
        assert error.value.code == 'WALK_FORWARD_INPUT_CORRUPT'
        with factory.begin() as session:
            row = session.get(WalkForwardJob, run['id'])
            row.plan_json = original_plan
            row.elapsed_ms = 3_600_000
        with pytest.raises(TradeError) as error:
            control(client, run, 'resume')
        assert error.value.code == 'WALK_FORWARD_BUDGET_EXHAUSTED'
