"""Meaningful sampling, metrics and durable per-point checkpoint regressions."""
from copy import deepcopy
import json
import threading

import pytest

from trade_app.platform.compute_process import ComputeProcessError, ComputeResult, run_json_process
from trade_app.platform.types import TradeError
from trade_app.research import backtest_service, plateau_service as service
from trade_app.research.plateau_domain import digest, point_metrics, robustness, sample_axes
from trade_app.research.plateau_models import PlateauExperiment, PlateauPoint
from test_strategy_backtests import flat_bars
from test_wyckoff_research_api import client_for, data, dataset, write


SCHEMA = {'params.n': {'type': 'integer', 'minimum': 1, 'maximum': 60},
          'config.p': {'type': 'number', 'minimum': 0, 'maximum': 1},
          'params.flag': {'type': 'boolean'}}


def test_grid_is_canonical_complete_and_explicitly_rejects_oversized_product():
    axes = {'params.n': {'values': [3, '1', 3, 2]}, 'params.flag': {'values': [True, False]}}
    plan = sample_axes('grid', axes, SCHEMA, seed=None, max_points=6)
    assert plan['actual_points'] == 6 and plan['seed'] == 0
    assert plan['samples'][0] == {'params.flag': 'false', 'params.n': '1'}
    assert plan['samples'][-1] == {'params.flag': 'true', 'params.n': '3'}
    with pytest.raises(TradeError, match='完整网格有 6 点'):
        sample_axes('grid', axes, SCHEMA, seed=0, max_points=5)


def test_lhs_seed_reproducible_axis_order_independent_and_no_random_refill():
    axes = {'params.n': {'min': 1, 'max': 20}, 'config.p': {'min': '0.1', 'max': '0.9', 'precision': 4}}
    first = sample_axes('lhs', axes, SCHEMA, seed=99)
    assert first == sample_axes('lhs', dict(reversed(list(axes.items()))), SCHEMA, seed=99)
    assert first != sample_axes('lhs', axes, SCHEMA, seed=100)
    generated = sample_axes('lhs', axes, SCHEMA, seed=None)
    assert generated == sample_axes('lhs', axes, SCHEMA, seed=generated['seed'])
    collapsed = sample_axes('lhs', {'params.n': {'min': 1, 'max': 1}}, SCHEMA, seed=1, sample_points=20)
    assert collapsed['actual_points'] == 1 and collapsed['deduplicated_points'] == 19
    assert all(1 <= int(row['params.n']) <= 20 for row in first['samples'])


@pytest.mark.parametrize('mode,axes,seed', [
    ('grid', {'unused': {'values': [1]}}, 1), ('grid', {'params.n': {'values': [.5]}}, 1),
    ('grid', {'params.n': {'values': [61]}}, 1), ('grid', {'params.n': {'values': ['NaN']}}, 1),
    ('lhs', {'params.flag': {'min': 0, 'max': 1}}, 1), ('lhs', {'params.n': {'min': 2, 'max': 1}}, 1),
    ('lhs', {'params.n': {'min': 1, 'max': 5, 'precision': 2}}, 1),
    ('lhs', {'config.p': {'min': '.001', 'max': '.9', 'precision': 2}}, 1),
    ('grid', {'params.n': {'values': [1]}}, -1),
])
def test_sampling_invalid_inputs_fail_without_clamping(mode, axes, seed):
    with pytest.raises(TradeError):
        sample_axes(mode, axes, SCHEMA, seed=seed)


def fake_result(value=.12):
    return {'trades': [{'side': 'buy', 'price': '10', 'quantity': 100, 'fees': '5'},
                       {'side': 'sell', 'realized_pnl': '95'}],
            'equity': [{'date': '2025-01-01', 'total_assets': '10000'},
                       {'date': '2025-02-01', 'total_assets': '11000'}],
            'initial_capital': '10000', 'total_return': str(value), 'max_drawdown': '.05',
            'win_rate': '1', 'signal_count': 2, 'quality_flags': []}


def test_metrics_net_round_trips_and_unbounded_profit_factor_are_json_finite():
    result = point_metrics(fake_result())
    assert result['profit_factor'] is None and result['profit_factor_unbounded']
    assert result['entry_fill_rate'] == .5
    assert result['avg_net_trade_return'] == pytest.approx(95 / 1005)
    assert json.dumps(result, allow_nan=False)


def test_two_pass_robustness_is_permutation_invariant_including_neighbor_ties():
    points = [{'id': str(index), 'point_sha256': digest(index), 'axis_values': {'x': str(index)},
               'metrics': point_metrics(fake_result(index * .1))} for index in range(8)]
    axes = {'x': {'values': [str(index) for index in range(8)]}}
    first = robustness(points, axes)
    assert first == robustness(list(reversed(points)), axes)
    by_id = {row['id']: row for row in first['points']}
    for row in first['points']:
        neighbors = [by_id[key]['point_score'] for key in row['neighbor_ids']]
        assert row['neighbor_median_score'] == sorted(neighbors)[len(neighbors) // 2]
        assert row['evidence_sufficient']
    small = robustness(points[:3], axes)
    assert all(row['plateau_score'] is None for row in small['points'])
    assert small['recommended_point_id'] is None
    assert robustness([], axes)['points'] == []


def create_experiment(client, path, *, count=3):
    source = data(write(client, '/backtests', {'dataset_id': dataset(client, flat_bars())['id'],
                        'strategy_id': 'relative_strength_breakout_v1', 'strict': True}))
    assert backtest_service.process_one_backtest(client.app.state.db_factory, path)
    body = {'base_run_id': source['id'], 'sampling_mode': 'grid',
            'axes': {'config.holding_bars': {'values': list(range(2, count + 2))}}, 'max_points': count}
    with client.app.state.db_factory() as session:
        preview = service.preview_plateau(session, path, body)
    with client.app.state.db_factory.begin() as session:
        experiment = service.create_plateau(session, path, {**body, 'seed': preview['plan']['seed'],
                  'name': '检查点实验', 'expected_preview_sha256': preview['preview_sha256']})
    return experiment, body, preview


def inspect(client, run):
    with client.app.state.db_factory() as session:
        return service.get_plateau(session, run['id'])


def control(client, run, action):
    with client.app.state.db_factory.begin() as session:
        return service.control_plateau(session, run['id'], action)


def test_real_worker_checkpoint_pause_restart_resume_does_not_rerun_success(tmp_path):
    with client_for(tmp_path) as client:
        run, _, _ = create_experiment(client, tmp_path)
        factory = client.app.state.db_factory
        assert service.process_one_plateau(factory, tmp_path)
        checkpoint = inspect(client, run)
        assert checkpoint['counts'] == {'queued': 2, 'succeeded': 1}
        first = checkpoint['points'][0]
        assert first['result_sha256'] and first['attempt_number'] == 1
        control(client, run, 'pause')
        assert not service.process_one_plateau(factory, tmp_path)
        control(client, run, 'resume')
        with factory.begin() as session:
            parent = session.get(PlateauExperiment, run['id'])
            parent.state = 'running'
            point = session.get(PlateauPoint, checkpoint['points'][1]['id'])
            point.state, point.attempt_id, point.attempt_number = 'running', 'crashed', 1
        service.recover_interrupted_plateaus(factory)
        assert inspect(client, run)['state'] == 'paused'
        control(client, run, 'resume')
        assert service.process_one_plateau(factory, tmp_path)
        assert service.process_one_plateau(factory, tmp_path)
        complete = inspect(client, run)
        assert complete['state'] == 'succeeded' and complete['counts'] == {'succeeded': 3}
        assert complete['points'][0]['attempt_number'] == 1
        assert complete['points'][0]['result_sha256'] == first['result_sha256']
        assert complete['points'][1]['attempt_number'] == 2
        assert complete['result_sha256'] == digest(complete['analysis'])
        assert complete['analysis']['points'][0]['plateau_score'] is None
        assert not service.process_one_plateau(factory, tmp_path)
        control(client, run, 'delete')
        with factory() as session:
            assert service.list_plateaus(session) == []
            assert session.get(PlateauPoint, first['id']).result_sha256 == first['result_sha256']


@pytest.mark.parametrize('action', ['pause', 'cancel', 'shutdown', 'code', 'attempt'])
def test_active_publication_races_preserve_completed_checkpoints(tmp_path, monkeypatch, action):
    with client_for(tmp_path) as client:
        run, _, _ = create_experiment(client, tmp_path, count=2)
        factory = client.app.state.db_factory
        stopped = threading.Event()

        def runner(module, payload, **kwargs):
            assert module == 'trade_app.research.backtest_worker'
            assert 'database' not in payload and kwargs['budget'].memory_bytes == 512 * 1024 * 1024
            if action in ('pause', 'cancel'):
                control(client, run, action)
                assert kwargs['stop_reason']() == ('cancelled' if action == 'cancel' else None)
            elif action == 'shutdown':
                stopped.set()
            elif action == 'code':
                monkeypatch.setattr(service, 'code_sha256', lambda: 'changed')
            else:
                with factory.begin() as session:
                    point = session.get(PlateauPoint, run['points'][0]['id'])
                    point.attempt_id, point.state = 'replacement', 'queued'
                    session.get(PlateauExperiment, run['id']).state = 'queued'
            return ComputeResult({'ok': True, 'attempt_id': payload['attempt_id'], 'result': fake_result()}, {})

        monkeypatch.setattr(service, 'run_json_process', runner)
        assert service.process_one_plateau(factory, tmp_path, should_stop=stopped.is_set)
        result = inspect(client, run)
        point = result['points'][0]
        if action == 'pause':
            assert result['state'] == 'paused' and point['state'] == 'succeeded'
            assert result['counts'] == {'queued': 1, 'succeeded': 1}
        else:
            assert point['result_sha256'] is None
            assert result['state'] == {'cancel': 'cancelled', 'shutdown': 'paused', 'code': 'failed', 'attempt': 'queued'}[action]


def test_failed_point_retry_retains_success_and_frozen_payload(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        run, _, _ = create_experiment(client, tmp_path, count=2)
        calls = []

        def runner(_, payload, **__):
            calls.append(deepcopy(payload))
            if len(calls) == 1:
                raise ComputeProcessError('COMPUTE_TIMEOUT', 'fixture')
            return ComputeResult({'ok': True, 'attempt_id': payload['attempt_id'], 'result': fake_result()}, {})

        monkeypatch.setattr(service, 'run_json_process', runner)
        for _ in range(2):
            service.process_one_plateau(client.app.state.db_factory, tmp_path)
        partial = inspect(client, run)
        assert partial['state'] == 'succeeded' and partial['counts'] == {'failed': 1, 'succeeded': 1}
        control(client, run, 'retry-failed')
        service.process_one_plateau(client.app.state.db_factory, tmp_path)
        complete = inspect(client, run)
        assert complete['counts'] == {'succeeded': 2}
        assert [point['attempt_number'] for point in complete['points']] == [2, 1]
        assert calls[0]['bars'] == calls[2]['bars'] and calls[0]['params'] == calls[2]['params']


def test_tampered_input_and_preview_are_rejected(tmp_path):
    with client_for(tmp_path) as client:
        run, body, preview = create_experiment(client, tmp_path, count=2)
        factory = client.app.state.db_factory
        with factory.begin() as session:
            with pytest.raises(TradeError) as error:
                service.create_plateau(session, tmp_path, {**body, 'name': 'stale', 'expected_preview_sha256': '0' * 64})
            assert error.value.code == 'PLATEAU_PREVIEW_CHANGED'
            parent = session.get(PlateauExperiment, run['id'])
            payload = json.loads(parent.input_json)
            payload['bars'][0]['close'] = '999'
            parent.input_json = json.dumps(payload)
        service.process_one_plateau(factory, tmp_path)
        assert inspect(client, run)['error'].startswith('PLATEAU_INPUT_CORRUPT:')


def test_api_preview_create_idempotence_history_controls_and_point_detail(tmp_path):
    with client_for(tmp_path) as client:
        from trade_app.api.plateau_routes import router
        if not any(getattr(route, 'path', '') == '/api/v1/research/plateaus' for route in client.app.routes):
            client.app.include_router(router)
        run, body, preview = create_experiment(client, tmp_path, count=2)
        checked = data(write(client, '/research/plateaus/preview', body))
        assert checked['preview_sha256'] == preview['preview_sha256']
        create = {**body, 'name': 'API 实验', 'seed': checked['plan']['seed'], 'expected_preview_sha256': checked['preview_sha256']}
        created = data(write(client, '/research/plateaus', create, key='plateau-fixture-key'))
        assert data(write(client, '/research/plateaus', create, key='plateau-fixture-key'))['id'] == created['id']
        assert len(data(client.get('/api/v1/research/plateaus'))) == 2
        prefix = '/research/plateaus/' + created['id']
        assert write(client, prefix, {}, 'DELETE').status_code == 409
        assert data(write(client, prefix + '/pause', {}))['state'] == 'paused'
        assert data(write(client, prefix + '/resume', {}))['state'] == 'queued'
        assert data(write(client, prefix + '/cancel', {}))['state'] == 'cancelled'
        assert data(client.get('/api/v1' + prefix + '/points/' + created['points'][0]['id']))['result'] is None
        assert client.get('/api/v1' + prefix + '/export.json').status_code == 200
        assert data(write(client, prefix, {}, 'DELETE'))['capabilities']['delete']


def test_schema_rejects_unimplemented_portfolio_and_ranking_axes():
    schema = service.supported_schema('limit_up_arb_v1')
    assert 'params.sector_rank_weight' not in schema
    for unsupported in ('config.max_symbols', 'config.max_positions', 'config.intraday_trailing_reduce_ratio'):
        with pytest.raises(TradeError):
            sample_axes('grid', {unsupported: {'values': [1]}}, schema, seed=0)


@pytest.mark.parametrize('action', ['cancel', 'shutdown'])
def test_actual_owned_process_stops_and_does_not_publish(tmp_path, monkeypatch, action):
    (tmp_path / 'plateau_slow_fixture.py').write_text(
        'import json,sys,time\n'
        'from trade_app.platform.compute_process import apply_worker_memory_limit\n'
        'apply_worker_memory_limit()\njson.load(sys.stdin)\ntime.sleep(30)\n', encoding='utf-8')
    entered, stopped = threading.Event(), threading.Event()
    errors = []

    def runner(_, payload, *, budget, stop_reason):
        entered.set()
        try:
            return run_json_process('plateau_slow_fixture', payload, budget=budget, stop_reason=stop_reason,
                                    module_paths=(tmp_path,))
        except ComputeProcessError as error:
            errors.append(error.code)
            raise

    with client_for(tmp_path) as client:
        run, _, _ = create_experiment(client, tmp_path, count=2)
        monkeypatch.setattr(service, 'run_json_process', runner)
        worker = threading.Thread(target=service.process_one_plateau,
                    args=(client.app.state.db_factory, tmp_path), kwargs={'should_stop': stopped.is_set})
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
            assert all(point['result_sha256'] is None for point in result['points'])
            assert errors == ['COMPUTE_CANCELLED' if action == 'cancel' else 'COMPUTE_SHUTDOWN']
        finally:
            stopped.set()
            worker.join(3)


def test_worker_uses_frozen_bars_and_resource_budget_is_not_reset_by_resume(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        run, _, _ = create_experiment(client, tmp_path, count=2)
        monkeypatch.setattr(service, 'get_dataset', lambda *args: (_ for _ in ()).throw(AssertionError('must use frozen bars')))
        assert service.process_one_plateau(client.app.state.db_factory, tmp_path)
        control(client, run, 'pause')
        with client.app.state.db_factory.begin() as session:
            row = session.get(PlateauExperiment, run['id'])
            row.elapsed_ms = service.TOTAL_COMPUTE_MS
        with pytest.raises(TradeError) as error:
            control(client, run, 'resume')
        assert error.value.code == 'PLATEAU_BUDGET_EXHAUSTED'
        result = inspect(client, run)
        assert result['counts']['succeeded'] == 1 and result['points'][0]['attempt_number'] == 1
