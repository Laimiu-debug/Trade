from copy import deepcopy
from decimal import Decimal
import json

import pytest
from sqlalchemy import select

from trade_app.platform.types import TradeError
from trade_app.research import portfolio_service as portfolio, portfolio_experiment_service as service
from trade_app.research.portfolio_domain import digest, run_chunk, summary
from trade_app.research.portfolio_experiment_domain import candidate_context, portfolio_metrics, sample_plan, score_points, supported_schema
from trade_app.research.portfolio_experiment_models import PortfolioExperiment, PortfolioExperimentPoint
from trade_app.research.portfolio_models import PortfolioRun, PortfolioChunk
from test_portfolio import context, create_run
from test_wyckoff_research_api import client_for, write, data


def test_grid_lhs_complete_deterministic_and_reduce_ratio_executes():
    source = context(count=70, start=60, trailing_stop_pct='.04', intraday_trailing=True, daily_weak_clear=True)
    axes = {'config.trailing_reduce_ratio': {'values': ['.25', '.5', '1']}, 'config.weak_confirm_days': {'values': [1, 3]}}
    plan = sample_plan(source, {'axes': axes, 'max_points': 6})
    assert plan['actual_points'] == 6
    assert candidate_context(source, plan['samples'][2])['config']['trailing_reduce_ratio'] == '0.5'
    with pytest.raises(TradeError, match='完整网格有 6 点'):
        sample_plan(source, {'axes': axes, 'max_points': 5})
    body = {'sampling_mode': 'lhs', 'axes': {'config.trailing_reduce_ratio': {'min': '.2', 'max': '1', 'precision': 3}}, 'seed': 4, 'sample_points': 8}
    assert sample_plan(source, body) == sample_plan(source, body)
    assert sample_plan(source, body) != sample_plan(source, {**body, 'seed': 5})
    assert source['config']['trailing_reduce_ratio'] == '0.5'
    with pytest.raises(TradeError) as error:
        sample_plan(context(count=70, start=60), {'axes': {'config.trailing_reduce_ratio': {'values': ['.25', '.5']}}})
    assert error.value.code == 'UNUSED_PORTFOLIO_AXIS'


@pytest.mark.parametrize('mode', ['matrix_raw_s1_s9', 'traditional_runtime14', 'aligned_wyckoff_events'])
def test_schema_restricts_to_current_path_and_freezes_unvaried_configuration(mode):
    source = context(count=70, start=60)
    source['mode'] = mode
    if mode == 'aligned_wyckoff_events':
        from trade_app.research.portfolio_event_domain import normalize_event_params
        source['params'] = normalize_event_params({})
    if mode == 'matrix_raw_s1_s9':
        source['params'] = {}
    schema = supported_schema(source)
    assert ('config.top_n' in schema) == (mode == 'matrix_raw_s1_s9')
    assert 'params.entry_events' not in schema  # Set-valued event options stay frozen.
    for unknown in ('config.initial_capital', 'config.execution_strict', 'config.minute_execution', 'params.unused_weight'):
        with pytest.raises(TradeError):
            sample_plan(source, {'axes': {unknown: {'values': [1]}}})
    changed = candidate_context(source, {'config.max_positions': '2', 'config.slippage_rate': '.01'})
    assert changed['config']['max_positions'] == 2 and changed['config']['fee_config']['slippage_rate'] == '0.01'
    assert changed['config']['execution_strict'] == source['config']['execution_strict']


def metric_result():
    return {'initial_capital': '10000', 'total_return': '.1', 'max_drawdown': '.03', 'quality_flags': ['selection_membership_unverified'],
        'equity': [{'date': '2025-01-01', 'total_assets': '10000'}, {'date': '2025-02-01', 'total_assets': '11000'}],
        'decisions': [{'reason': 'MAX_POSITIONS'}, {'reason': 'DAILY_TOP_K'}, {'reason': 'POOL_OR_RISK_INVALIDATION'}],
        'trades': [
            {'symbol': 'a', 'side': 'buy', 'quantity': 100, 'price': '10', 'fees': '5'},
            {'symbol': 'b', 'side': 'buy', 'quantity': 100, 'price': '10', 'fees': '5'},
            {'symbol': 'a', 'side': 'sell', 'quantity': 50, 'realized_pnl': '100'},
            {'symbol': 'b', 'side': 'sell', 'quantity': 100, 'realized_pnl': '-50'},
            {'symbol': 'a', 'side': 'sell', 'quantity': 50, 'realized_pnl': '-20'},
            {'symbol': 'c', 'side': 'buy', 'quantity': 100, 'price': '10', 'fees': '5'},
            {'symbol': 'c', 'side': 'sell', 'quantity': 50, 'realized_pnl': '500'}]}


def test_metrics_use_complete_cycles_across_symbols_not_independent_partial_legs():
    actual = portfolio_metrics(metric_result())
    assert actual['trade_count'] == 2 and actual['win_rate'] == .5
    assert actual['profit_factor'] == 1.6
    assert actual['avg_net_trade_return'] == pytest.approx((80 / 1005 - 50 / 1005) / 2)
    assert actual['entry_fill_rate'] == .6 and actual['eligible_signals'] == 5
    assert actual['open_cycles'] == 1 and actual['partial_realized_pnl'] == '500'
    points = [{'id': str(i), 'point_sha256': digest(i), 'axis_values': {'x': str(i)}, 'metrics': actual} for i in range(5)]
    axes = {'x': {'values': ['0', '1', '2', '3', '4']}}
    assert score_points(points, axes) == score_points(list(reversed(points)), axes)
    assert all('仅单股' not in note for note in score_points(points, axes)['notes'])


@pytest.mark.parametrize('price, fees, cost', [
    ('10.00004', '0', '1000.00'),
    ('10.00005', '0', '1000.01'),
    ('10.00006', '5.01', '1005.02'),
])
def test_candidate_cycle_return_uses_rounded_execution_cost(price, fees, cost):
    result = metric_result()
    result['trades'] = [
        {'symbol': 'a', 'side': 'buy', 'quantity': 100, 'price': price, 'fees': fees},
        {'symbol': 'a', 'side': 'sell', 'quantity': 50, 'realized_pnl': '100.01'},
        {'symbol': 'a', 'side': 'sell', 'quantity': 50, 'realized_pnl': '179.99'},
        {'symbol': 'b', 'side': 'buy', 'quantity': 100, 'price': price, 'fees': fees},
        {'symbol': 'b', 'side': 'sell', 'quantity': 50, 'realized_pnl': '500'},
    ]
    actual = portfolio_metrics(result)
    assert actual['trade_count'] == 1 and actual['open_cycles'] == 1
    assert actual['partial_realized_pnl'] == '500'
    assert actual['avg_net_trade_return'] == float(Decimal('280') / Decimal(cost))
    assert actual['metric_version'] == 'portfolio-grid-lhs-complete-cycles-v2'


def setup(client, tmp_path, *, points=2):
    source, _ = create_run(client, tmp_path, count=70)
    factory = client.app.state.db_factory
    while portfolio.process_one_portfolio(factory, tmp_path):
        pass
    body = {'base_run_id': source['id'], 'sampling_mode': 'grid', 'seed': 7, 'max_points': 10,
            'axes': {'config.max_holding_bars': {'values': list(range(2, 2 + points))}}}
    with factory.begin() as session:
        preview = service.preview_experiment(session, body)
        created = service.create_experiment(session, {**body, 'name': '组合参数验收', 'expected_preview_sha256': preview['preview_sha256']})
    return created, body, source


def test_real_quantum_checkpoint_owner_pause_recovery_analysis_report_and_delete(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        created, _, source = setup(client, tmp_path)
        factory = client.app.state.db_factory
        assert service.process_one_experiment(factory, tmp_path)
        with factory() as session:
            first = service.get_experiment(session, created['id'])
            assert first['completed_points'] == 0 and first['points'][0]['completed_days'] == 5
            child_id = first['points'][0]['child_run_id']
            initial_sha = session.get(PortfolioRun, child_id).checkpoint_sha256
        assert len(data(client.get('/api/v1/research/portfolios'))) == 1
        data(write(client, '/research/portfolios/' + source['id'], {}, method='DELETE'))
        assert client.get('/api/v1/research/portfolios/' + source['id']).status_code == 404
        assert not portfolio.process_one_portfolio(factory, tmp_path)
        assert write(client, '/research/portfolios/' + child_id + '/pause', {}).status_code == 409
        assert write(client, '/research/portfolios/' + child_id, {}, method='DELETE').status_code == 409
        portfolio.recover_interrupted_portfolios(factory)
        service.recover_interrupted_experiments(factory)
        with factory.begin() as session:
            assert service.get_experiment(session, created['id'])['state'] == 'paused'
            service.control_experiment(session, created['id'], 'resume')
        while service.process_one_experiment(factory, tmp_path):
            pass
        with factory() as session:
            complete = service.get_experiment(session, created['id'])
            assert complete['state'] == 'succeeded', complete['error']
            assert complete['completed_points'] == 2 and len(complete['result']['points']) == 2
            assert session.scalar(select(PortfolioChunk).where(PortfolioChunk.run_id == child_id, PortfolioChunk.ordinal == 1)).prior_sha256 == initial_sha
            for point in complete['points']:
                child = session.get(PortfolioRun, point['child_run_id'])
                ctx = json.loads(child.input_json)
                direct = run_chunk(ctx, days=2000)
                actual = service.get_point(session, created['id'], point['id'])['portfolio']
                assert actual['checkpoint'] == direct['checkpoint']
                assert actual['result']['trades'] == direct['trades']
                assert child.chunk_count == 2
            with monkeypatch.context() as changed_code:
                changed_code.setattr(service, 'code_sha256', lambda: 'changed')
                changed_code.setattr(service, 'VERSION', 'future-calculator-version')
                assert service.get_experiment(session, created['id']) == complete
        report = data(write(client, '/research/portfolio-reports', {'portfolio_run_id': child_id, 'title': '候选独立完整报告'}))
        assert report['payload']['source']['run_id'] == child_id
        with factory.begin() as session:
            service.control_experiment(session, created['id'], 'delete')
            assert service.list_experiments(session) == []
            assert session.get(PortfolioRun, child_id).deleted == 0
        assert data(client.get('/api/v1/research/portfolio-reports/' + report['id']))['payload'] == report['payload']


def test_historical_experiment_metadata_uses_its_frozen_version():
    frozen = {'version': 'portfolio-grid-lhs-complete-cycles-v1', 'plan': {'actual_points': 1},
        'source': context(), 'budget': service.BUDGET, 'frozen_child_input_bytes': 1234}
    assert service._preview(frozen)['version'] == frozen['version']


@pytest.mark.parametrize('action', ['pause', 'cancel', 'shutdown', 'code'])
def test_control_races_preserve_prior_quantum_and_never_mix_code(action, tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        created, _, _ = setup(client, tmp_path, points=1)
        factory = client.app.state.db_factory
        stopped, original = False, portfolio.run_json_process
        def raced(*args, **kwargs):
            nonlocal stopped
            result = original(*args, **kwargs)
            if action in ('pause', 'cancel'):
                with factory.begin() as session:
                    service.control_experiment(session, created['id'], action)
            elif action == 'shutdown':
                stopped = True
            else:
                monkeypatch.setattr(service, 'code_sha256', lambda: 'changed')
            return result
        monkeypatch.setattr(portfolio, 'run_json_process', raced)
        service.process_one_experiment(factory, tmp_path, should_stop=lambda: stopped)
        with factory.begin() as session:
            actual = service.get_experiment(session, created['id'])
            assert actual['completed_points'] == 0
            assert actual['state'] == {'pause': 'paused', 'cancel': 'cancelled', 'shutdown': 'paused', 'code': 'failed'}[action]
            assert actual['points'][0]['completed_days'] == (5 if action in ('pause', 'code') else 0)
            if action == 'code':
                with pytest.raises(TradeError) as error:
                    service.control_experiment(session, created['id'], 'retry')
                assert error.value.code == 'CODE_VERSION_CHANGED'


def test_preview_hash_limits_tamper_and_source_stays_independent(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        created, body, source = setup(client, tmp_path, points=1)
        factory = client.app.state.db_factory
        with factory.begin() as session:
            with pytest.raises(TradeError) as error:
                service.create_experiment(session, {**body, 'name': 'stale', 'expected_preview_sha256': '0'*64})
            assert error.value.code == 'PORTFOLIO_EXPERIMENT_PLAN_CHANGED'
            monkeypatch.setattr(service, 'MAX_INPUT_BYTES', 1)
            with pytest.raises(TradeError) as error:
                service.preview_experiment(session, body)
            assert error.value.code == 'PORTFOLIO_EXPERIMENT_INPUT_LIMIT'
            row = session.get(PortfolioExperiment, created['id'])
            row.input_json = row.input_json.replace('max_holding_bars', 'unused_config')
        assert service.process_one_experiment(factory, tmp_path)
        with factory() as session:
            row = session.get(PortfolioExperiment, created['id'])
            assert row.state == 'failed' and 'CORRUPT' in row.error
            assert session.get(PortfolioRun, source['id']).state == 'succeeded'


def test_registry_stops_new_experiments_but_keeps_frozen_candidates_executable(tmp_path):
    from test_strategy_registry import change
    with client_for(tmp_path) as client:
        created, body, _ = setup(client, tmp_path, points=1)
        factory = client.app.state.db_factory
        with factory.begin() as session:
            preview = service.preview_experiment(session, body)
            change(session, disabled=['relative_strength_breakout_v1'])
            with pytest.raises(TradeError) as error:
                service.create_experiment(session, {**body, 'name': '停用后新实验', 'expected_preview_sha256': preview['preview_sha256']})
            assert error.value.code == 'STRATEGY_DISABLED'
        while service.process_one_experiment(factory, tmp_path):
            pass
        with factory() as session:
            assert service.get_experiment(session, created['id'])['state'] == 'succeeded'
