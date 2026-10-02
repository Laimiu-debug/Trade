from copy import deepcopy
from decimal import Decimal
import json

import pytest
from sqlalchemy import select

from trade_app.platform.types import TradeError
from trade_app.research import portfolio_service as portfolio, portfolio_walk_forward_service as service
from trade_app.research.portfolio_domain import digest, run_chunk, summary
from trade_app.research.portfolio_experiment_domain import sample_plan, portfolio_metrics
from trade_app.research.portfolio_walk_forward_domain import build_plan, temporal_context, select_training, summarize
from trade_app.research.portfolio_walk_forward_models import PortfolioWalkForward as Run, PortfolioWalkForwardFold as Fold, PortfolioWalkForwardTask as Task
from trade_app.research.portfolio_models import PortfolioRun, PortfolioChunk
from trade_app.research.service import normalize_strategy_params
from test_portfolio import context, create_run
from test_wyckoff_research_api import client_for, write, data


def rising_source():
    source = context(count=120, start=60)
    source['params'] = normalize_strategy_params(source['strategy_id'], {'min_ret40': '.01', 'min_vol_slope20': '0'})
    for index, bar in enumerate(source['datasets'][0]['bars']):
        price = Decimal('10') + Decimal('.03') * index
        bar.update(open=str(price - Decimal('.01')), high=str(price + Decimal('.02')), low=str(price - Decimal('.02')), close=str(price), volume=1000 + index)
    return source


def test_anchored_plan_nonoverlap_gap_limits_and_exact_phase_prefix():
    source = rising_source()
    sampling = sample_plan(source, {'axes': {'config.max_holding_bars': {'values': [2, 5]}}})
    plan = build_plan(source, sampling, {'initial_train_bars': 32, 'test_bars': 10, 'gap_bars': 1, 'max_folds': 2})
    first, second = plan['folds']
    assert first['train_start'] == second['train_start']
    assert first['train_end'] < first['test_start'] <= first['test_end'] < second['test_start']
    assert second['train_end'] == first['test_end']
    assert plan['unused_tail_bars'] == 6 and plan['evaluation_points'] == 6
    train = temporal_context(source, first, 'train', sampling['samples'][0])
    test = temporal_context(source, first, 'test', sampling['samples'][0])
    assert max(bar['event_date'] for item in train['datasets'] for bar in item['bars']) == first['train_end']
    assert train['calendar'][0] == first['train_start'] and test['calendar'][0] == first['test_start']
    assert len(test['datasets'][0]['bars']) > len(test['calendar'])  # Indicator warmup is preserved.
    assert test['datasets'][0]['bars_sha256'] == digest(test['datasets'][0]['bars'])
    assert test['config']['initial_capital'] == source['config']['initial_capital']
    with pytest.raises(TradeError):
        build_plan(source, sampling, {'initial_train_bars': 1000})


def test_future_price_shock_cannot_change_training_or_selected_candidate():
    source = rising_source()
    sampling = sample_plan(source, {'axes': {'config.max_holding_bars': {'values': [2, 5]}}})
    fold = build_plan(source, sampling, {'initial_train_bars': 32, 'test_bars': 10, 'max_folds': 1})['folds'][0]
    changed = deepcopy(source)
    for bar in changed['datasets'][0]['bars']:
        if bar['event_date'] > fold['train_end']:
            bar.update(open='100', close='1', high='101', low='.5', volume=100000)
    choices = []
    test_results = []
    for frozen in (source, changed):
        points = []
        for axis in sampling['samples']:
            ctx = temporal_context(frozen, fold, 'train', axis)
            output = run_chunk(ctx, days=2000)
            assert ctx == temporal_context(source, fold, 'train', axis)
            result = {**summary(ctx, output['checkpoint']), **output}
            points.append({'id': digest(axis), 'point_sha256': digest(axis), 'axis_values': axis, 'metrics': portfolio_metrics(result)})
        selection = select_training(points, sampling['axes'], 1)
        choices.append(selection)
        test_context = temporal_context(frozen, fold, 'test', selection['axis_values'])
        test_output = run_chunk(test_context, days=2000)
        assert all(row['date'] >= fold['test_start'] for row in test_output['equity'] + test_output['trades'])
        if test_output['trades']:
            assert test_output['trades'][0]['side'] == 'buy'  # No inherited training holdings.
        test_results.append(test_output['checkpoint']['ending_assets'])
    assert choices[0] == choices[1]
    assert test_results[0] != test_results[1]
    assert select_training(points, sampling['axes'], 1000)['reason'] == 'NO_ELIGIBLE_TRAINING_CANDIDATE'


def test_oos_summary_reset_normalization_and_open_positions_are_not_forced_sales():
    output = summarize([{'index': 0, 'test_result': {'initial_capital': '100', 'ending_assets': '110', 'total_return': '.1', 'trade_count': 1,
        'quality_flags': ['selection_membership_unverified'], 'equity': [{'date': '2025-01-01', 'total_assets': '110'}]}},
        {'index': 1, 'test_result': None},
        {'index': 2, 'test_result': {'initial_capital': '100', 'ending_assets': '90', 'total_return': '-.1', 'trade_count': 0,
        'quality_flags': [], 'equity': [{'date': '2025-02-01', 'total_assets': '90'}]}}])
    assert output['oos_compounded_return'] == pytest.approx(-.01)
    assert output['oos_max_drawdown'] == pytest.approx(.1)
    assert output['completed_test_folds'] == 2 and output['oos_complete_cycles'] == 1


def setup(client, tmp_path, *, minimum=1, candidates=2):
    source, _ = create_run(client, tmp_path, count=104)
    factory = client.app.state.db_factory
    while portfolio.process_one_portfolio(factory, tmp_path):
        pass
    body = {'base_run_id': source['id'], 'axes': {'config.max_holding_bars': {'values': [2, 5][:candidates]}},
        'seed': 5, 'sampling_mode': 'grid', 'max_points': 10, 'initial_train_bars': 32,
        'test_bars': 10, 'gap_bars': 1, 'max_folds': 1, 'min_train_cycles': minimum}
    with factory.begin() as session:
        preview = service.preview_walk_forward(session, body)
        created = service.create_walk_forward(session, {**body, 'name': '组合滚动验证', 'expected_preview_sha256': preview['preview_sha256']})
    return created, source


def test_real_train_selection_test_checkpoint_recovery_reports_and_delete(tmp_path):
    with client_for(tmp_path) as client:
        created, _ = setup(client, tmp_path)
        factory = client.app.state.db_factory
        assert service.process_one_walk_forward(factory, tmp_path)
        with factory() as session:
            first = service.get_walk_forward(session, created['id'])
            assert first['tasks'][0]['completed_days'] == 5 and first['folds'][0]['selection'] is None
            initial_child = session.get(PortfolioRun, first['tasks'][0]['child_run_id'])
            initial_sha, child_id = initial_child.checkpoint_sha256, initial_child.id
        portfolio.recover_interrupted_portfolios(factory)
        service.recover_interrupted_walk_forwards(factory)
        with factory.begin() as session:
            service.control_walk_forward(session, created['id'], 'resume')
        selection_sha = None
        for _ in range(30):
            worked = service.process_one_walk_forward(factory, tmp_path)
            with factory() as session:
                current = service.get_walk_forward(session, created['id'])
                if current['folds'][0]['selection_sha256']:
                    if selection_sha:
                        assert current['folds'][0]['selection_sha256'] == selection_sha
                    selection_sha = current['folds'][0]['selection_sha256']
            if not worked:
                break
        assert current['state'] == 'succeeded', current['error']
        assert current['completed_tasks'] == 3 and current['result']['completed_test_folds'] == 1
        assert len(current['result']['oos_normalized_curve']) == 10
        assert [task['chunk_count'] for task in current['tasks']] == [7, 7, 2]
        with factory() as session:
            assert session.scalar(select(PortfolioChunk).where(PortfolioChunk.run_id == child_id, PortfolioChunk.ordinal == 1)).prior_sha256 == initial_sha
            for task in current['tasks']:
                child = session.get(PortfolioRun, task['child_run_id'])
                ctx = json.loads(child.input_json)
                fold = current['temporal']['folds'][0]
                assert ctx['datasets'][0]['bars'][-1]['event_date'] == fold[task['phase'] + '_end']
                expected = run_chunk(ctx, days=2000)
                actual = service.get_task(session, created['id'], task['id'])['portfolio']
                assert actual['result']['trades'] == expected['trades'] and actual['checkpoint'] == expected['checkpoint']
        test = current['tasks'][-1]
        assert test['axis_values'] == current['folds'][0]['selection']['axis_values']
        assert len(data(client.get('/api/v1/research/portfolios'))) == 1
        report = data(write(client, '/research/portfolio-reports', {'portfolio_run_id': test['child_run_id'], 'title': '独立样本外折报告'}))
        assert report['payload']['input']['calendar'][0] == current['folds'][0]['test_start']
        with factory.begin() as session:
            service.control_walk_forward(session, created['id'], 'delete')
            assert service.list_walk_forwards(session) == []
        assert data(client.get('/api/v1/research/portfolio-reports/' + report['id']))['payload'] == report['payload']


def test_no_training_candidate_skips_test_without_reopening_selection(tmp_path):
    with client_for(tmp_path) as client:
        created, _ = setup(client, tmp_path, minimum=1000, candidates=1)
        factory = client.app.state.db_factory
        while service.process_one_walk_forward(factory, tmp_path):
            pass
        with factory() as session:
            current = service.get_walk_forward(session, created['id'])
            assert current['state'] == 'succeeded', current['error']
            assert current['tasks'][-1]['state'] == 'skipped' and current['tasks'][-1]['child_run_id'] is None
            assert current['result']['completed_test_folds'] == 0 and current['result']['oos_compounded_return'] is None
            assert current['folds'][0]['selection']['reason'] == 'NO_ELIGIBLE_TRAINING_CANDIDATE'


def test_cancel_then_retry_preserves_same_child_and_code_change_cannot_resume(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        created, _ = setup(client, tmp_path, candidates=1)
        factory = client.app.state.db_factory
        assert service.process_one_walk_forward(factory, tmp_path)
        with factory.begin() as session:
            first = service.get_walk_forward(session, created['id'])
            service.control_walk_forward(session, created['id'], 'cancel')
            service.control_walk_forward(session, created['id'], 'retry')
        assert service.process_one_walk_forward(factory, tmp_path)
        with factory.begin() as session:
            next_value = service.get_walk_forward(session, created['id'])
            assert first['tasks'][0]['child_run_id'] == next_value['tasks'][0]['child_run_id']
            assert next_value['tasks'][0]['chunk_count'] == 2
            service.control_walk_forward(session, created['id'], 'pause')
            monkeypatch.setattr(service, 'code_sha256', lambda: 'changed')
            with pytest.raises(TradeError) as error:
                service.control_walk_forward(session, created['id'], 'resume')
            assert error.value.code == 'CODE_VERSION_CHANGED'
