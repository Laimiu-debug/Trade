from copy import deepcopy
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.strategy_plugins import (WulongClusterPlugin, TrendKingPlugin, LimitUpArbPlugin,
    EmotionLimitUpPlugin, ForceRhythmPlugin, ScoreOnlyRankPlugin, ThsMainRetailSignalPlugin)
from trade_app.market.service import import_dataset
from trade_app.main import create_app
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research import signal_workspace_service as service
from trade_app.research.signal_workspace_domain import rank_score, signal_row, range_performance
from trade_app.research.signal_workspace_models import SignalWorkspaceReport
from trade_app.research.scan_service import create_scan, delete_scan
from trade_app.research.scan_job_service import process_one_scan_chunk, get_scan_job
from trade_app.research.screener_models import ScreenerRun
from trade_app.research.service import strategy_catalog
from test_strategy_scans import _bars, RELATIVE, WULONG
from test_matrix_pool_api import started_client, data, writer


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    bars = _bars(wulong=True)
    for index, close in enumerate((11, 9, 12), 1):
        day = (date(2025, 3, 29) + timedelta(days=index)).isoformat()
        bars.append({'event_date': day, 'open': str(close), 'high': str(close), 'low': str(close), 'close': str(close), 'volume': 1000, 'available_at': day + 'T08:00:00+00:00'})
    with factory.begin() as session:
        dataset = import_dataset(session, tmp_path, {'symbol': '600000.SH', 'adjustment': 'none', 'bars': bars})
        scan = create_scan(session, tmp_path, {'dataset_ids': [dataset['id']], 'strategies': [
            {'strategy_id': WULONG, 'params': {}}, {'strategy_id': RELATIVE, 'params': {}}],
            'date_from': '2025-03-28', 'date_to': '2025-03-29', 'strict': True})
    yield factory, tmp_path, dataset, scan
    engine.dispose()


def prepare(scenario, **changes):
    factory, path, _dataset, scan = scenario
    with factory() as session:
        return service.prepare_report(session, path, {'scan_id': scan['id'], 'as_of_date': '2025-03-29', **changes})


@pytest.mark.parametrize('strategy,plugin', [
    (WULONG, WulongClusterPlugin()), ('trend_king_v1', TrendKingPlugin()),
    ('limit_up_arb_v1', LimitUpArbPlugin()), ('emotion_limit_up_v1', EmotionLimitUpPlugin()),
    ('ths_force_rhythm_v1', ForceRhythmPlugin()), ('score_only_rank_v1', ScoreOnlyRankPlugin()),
    ('ths_main_force_flip_v1', ThsMainRetailSignalPlugin('ths_main_force_flip_v1', trigger_key='purple_to_yellow'))])
def test_rank_formula_matches_original_plugin(strategy, plugin):
    metrics = {'ret40': .13, 'up_down_volume_ratio': 1.36}
    result = {'indicator': {'entry_quality_score': 72, 'signal_score': 72}, 'evaluation': {'signal_score': 72}}
    expected = plugin.rank_signals(signal=SimpleNamespace(entry_quality_score=72), row=SimpleNamespace(**metrics), params={}, fallback_score=50)
    assert rank_score(strategy, result, metrics)['rank_score'] == pytest.approx(expected)


def test_range_v2_excludes_post_end_price_and_drawdown_and_missing_is_null():
    bars = [{'event_date': f'2025-01-0{index}', 'close': str(close)} for index, close in enumerate((10, 12, 9, 11, 1000, 1), 1)]
    result = range_performance(bars, '2025-01-01', '2025-01-04')
    assert result['return_pct'] == 10 and result['max_drawdown_pct'] == 25
    assert result['end_date'] == '2025-01-04' and result['bar_count'] == 4
    assert range_performance(bars, '2024-01-01', '2024-01-04')['return_pct'] is None
    assert range_performance(bars, '2025-01-01', '2025-01-01')['status'] == 'single_bar'


def test_backdated_event_cannot_advance_confirmation_or_delayed_entry():
    bars = [{'event_date': day, 'close': '10'} for day in ('2025-01-08', '2025-01-09', '2025-01-11', '2025-01-13', '2025-01-15')]
    run = {'id': 'run', 'dataset_id': 'dataset', 'strategy_id': 'wyckoff_trend_v1', 'strategy_version': '1',
           'decision_at': '2025-01-10T18:00:00+00:00', 'result': {'source_date': '2025-01-11', 'signal': True,
           'indicator': {'trigger_date': '2025-01-08', 'signal': 'SOS', 'event_confirmation_map': {'SOS': 'confirmed'}, 'health_score': 60, 'event_score': 80, 'entry_quality_score': 70}}}
    descriptor = {'capabilities': {'supports_signal_age_filter': True, 'supports_entry_delay': True}}
    result = signal_row(run, [], bars, '2025-01-15', descriptor, 2)
    assert result['trigger_date'] == '2025-01-08' and result['confirmation_date'] == '2025-01-11'
    assert result['executable_date'] == '2025-01-15' and result['signal_age_bars'] == 4
    assert result['timeliness'] == 'expired'
    result = signal_row(run, [], bars[:3], '2025-01-11', descriptor, 1)
    assert result['executable_date'] is None and result['execution_status'] == 'pending_future_bar'
    run['result']['indicator']['event_confirmation_map']['SOS'] = 'pending'
    assert signal_row(run, [], bars, '2025-01-15', descriptor, 1)['executable_date'] is None


def test_real_signals_future_prices_do_not_affect_rank_and_report_cutoff(scenario):
    first = prepare(scenario)
    row = next(row for row in first['result']['rows'] if row['strategy_id'] == WULONG and row['decision_date'] == '2025-03-29')
    assert row['signal'] and row['rank_score'] is not None and row['timeliness'] == 'active'
    assert row['executable_date'] is None
    later = prepare(scenario, as_of_date='2025-04-01', entry_delay_bars=2)
    next_row = next(item for item in later['result']['rows'] if item['run_id'] == row['run_id'])
    assert next_row['rank_score'] == row['rank_score']
    assert next_row['executable_date'] == '2025-03-31' and next_row['timeliness'] == 'expired'
    for item in later['result']['per_symbol']:
        assert item['range_performance']['end_date'] == '2025-03-29'
        assert item['range_performance']['return_pct'] == pytest.approx(round((10.9 / 10.6 - 1) * 100, 2))


def test_filters_overlap_evidence_and_immutable_history(scenario):
    factory, path, _dataset, scan = scenario
    prepared = prepare(scenario, min_overlap=2)
    assert prepared['result']['per_symbol'][0]['overlap_count'] == 2
    assert prepared['result']['same_day_intersection']
    excluded = prepare(scenario, markets=['bj'])
    assert excluded['result']['signal_count'] == 0
    assert excluded['result']['same_day_intersection'] == []
    assert any('market_filter' in row['excluded_reasons'] for row in excluded['result']['rows'])
    with factory.begin() as session:
        saved = service.save_report(session, prepared)
        assert service.save_report(session, prepared)['id'] == saved['id']
        delete_scan(session, scan['id'])
    with factory() as session:
        assert service.get_report(session, saved['id']) == saved
        assert len(service.list_reports(session)) == 1
    with factory.begin() as session:
        assert service.delete_report(session, saved['id'])['source_scan_and_runs_preserved']
    with factory() as session:
        assert service.list_reports(session) == []


def test_report_corruption_and_date_age_limits_are_explicit(scenario):
    for changes in ({'as_of_date': '2025-03-28'}, {'signal_age_min': 5, 'signal_age_max': 1}, {'entry_delay_bars': 0}, {'min_rank_score': float('nan')}):
        with pytest.raises(TradeError): prepare(scenario, **changes)
    factory, _path, _dataset, _scan = scenario
    prepared = prepare(scenario)
    with factory.begin() as session:
        service.save_report(session, prepared)
        session.get(SignalWorkspaceReport, prepared['id']).result_json = '{}'
    with factory() as session:
        with pytest.raises(TradeError, match='摘要'): service.get_report(session, prepared['id'])


def test_source_pool_membership_and_no_backdated_pool(scenario):
    factory, path, dataset, scan = scenario
    with factory.begin() as session:
        session.add(ScreenerRun(id='pool', request_json='{}', result_json=json.dumps({'as_of_date': '2025-03-29', 'summary': {}, 'quality_flags': [], 'pools': {'step4': [{'dataset_id': dataset['id']}]}}), code_sha256='x', created_at='2025-03-29T18:00:00Z'))
    source = {'kind': 'trend_pool', 'screener_run_id': 'pool', 'step': 'step4'}
    with factory() as session:
        assert service.resolve_source(session, source)['dataset_ids'] == [dataset['id']]
        with pytest.raises(TradeError, match='不属于'): service.resolve_source(session, {**source, 'dataset_ids': ['bad']})
        with pytest.raises(TradeError, match='晚于'): service.prepare_workspace_scan(session, path, {'source': source, 'scan': {'as_of_date': '2025-03-28', 'strategies': [{'strategy_id': WULONG, 'params': {}}]}})


def test_explicit_source_scan_reuses_durable_worker_and_freezes_source(scenario):
    factory, path, dataset, _scan = scenario
    body = {'source': {'kind': 'fixed', 'dataset_ids': [dataset['id']]}, 'scan': {'as_of_date': '2025-03-29', 'strict': True, 'strategies': [{'strategy_id': WULONG, 'params': {}}]}}
    with factory() as session: frozen = service.prepare_workspace_scan(session, path, body)
    with factory.begin() as session: job = service.save_workspace_scan(session, frozen)
    for _ in range(4):
        process_one_scan_chunk(factory, path)
        with factory() as session: job = get_scan_job(session, job['id'])
        if job['state'] == 'succeeded': break
    assert job['state'] == 'succeeded'
    with factory() as session:
        report = service.prepare_report(session, path, {'scan_id': job['scan_id'], 'as_of_date': '2025-03-29'})
    assert report['result']['source']['kind'] == 'fixed'
    assert report['result']['source']['dataset_ids'] == [dataset['id']]


def test_late_bar_cannot_supply_delayed_entry_before_its_availability(scenario):
    factory, path, dataset, _scan = scenario
    bars = deepcopy(dataset['bars']) if 'bars' in dataset else None
    with factory.begin() as session:
        from trade_app.market.service import get_dataset
        bars = get_dataset(session, path, dataset['id'])['bars']
        bars[-2]['available_at'] = '2025-04-02T08:00:00+00:00'
        other = import_dataset(session, path, {'symbol': '600001.SH', 'adjustment': 'none', 'bars': bars})
        scan = create_scan(session, path, {'dataset_ids': [other['id']], 'as_of_date': '2025-03-29', 'strict': True, 'strategies': [{'strategy_id': WULONG, 'params': {}}]})
    with factory() as session:
        report = service.prepare_report(session, path, {'scan_id': scan['id'], 'as_of_date': '2025-04-01', 'entry_delay_bars': 2})
    assert report['result']['rows'][0]['executable_date'] == '2025-04-01'
    assert report['result']['rows'][0]['signal_age_bars'] == 2


def test_unsupported_capability_is_rejected_instead_of_silently_ignored(scenario, monkeypatch):
    catalog = deepcopy(strategy_catalog())
    for row in catalog:
        row['capabilities'] = {'supports_signal_age_filter': False, 'supports_entry_delay': False}
    monkeypatch.setattr(service, 'strategy_catalog', lambda: catalog)
    with pytest.raises(TradeError, match='年龄'): prepare(scenario, signal_age_min=1)
    with pytest.raises(TradeError, match='延迟'): prepare(scenario, entry_delay_bars=2)


def test_api_preview_gate_history_restart_and_source_preservation(tmp_path):
    app = create_app(tmp_path, auto_rebuild=False)
    root = '/api/v1/research/signal-workspace'
    with started_client(app) as client:
        post = writer(client)
        dataset = data(post('/api/v1/market/datasets', {'symbol': '600000', 'bars': _bars(wulong=True), 'adjustment': 'none'}))
        source = {'source': {'kind': 'fixed', 'dataset_ids': [dataset['id']]}, 'scan': {'as_of_date': '2025-03-29', 'strict': True, 'strategies': [{'strategy_id': WULONG, 'params': {}}]}}
        preview = data(post(root + '/scan-preview', source))
        denied = post(root + '/scan-jobs', {**source, 'expected_preview_sha256': 'wrong'})
        assert denied.status_code == 409
        job = data(post(root + '/scan-jobs', {**source, 'expected_preview_sha256': preview['input_sha256']}))
        for _ in range(4):
            process_one_scan_chunk(app.state.db_factory, tmp_path)
            job = data(client.get('/api/v1/research/scan-jobs/' + job['id']))
            if job['state'] == 'succeeded': break
        assert job['state'] == 'succeeded'
        options = {'scan_id': job['scan_id'], 'as_of_date': '2025-03-29'}
        report = data(post(root + '/preview', options))
        assert data(client.get(root + '/reports')) == []
        saved = data(post(root + '/reports', {**options, 'expected_preview_sha256': report['id']}))
    restarted = create_app(tmp_path, auto_rebuild=False)
    with started_client(restarted) as client:
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        assert data(client.get(root + '/reports/' + saved['id'])) == saved
        deleted = client.delete(root + '/reports/' + saved['id'], headers={'X-CSRF-Token': token, 'Idempotency-Key': 'delete-signal-report'})
        assert data(deleted)['source_scan_and_runs_preserved']
        assert data(client.get('/api/v1/research/scans/' + job['scan_id']))['id'] == job['scan_id']
