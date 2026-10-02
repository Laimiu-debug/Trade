from copy import deepcopy
from datetime import date, timedelta
import json
import time

import pytest
from sqlalchemy import delete, select

from trade_app.market.service import import_dataset
from trade_app.platform.db import open_database
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError
from trade_app.research import sim_equity_service as service
from trade_app.research.sim_equity_domain import calculate_equity, quote_schedule
from trade_app.research.models import ResearchRun
from trade_app.research.sim_equity_models import SimEquityDraftEvidence, SimEquityReport
from trade_app.trading.sim_models import SimWallet
from trade_app.trading.simulation import create_sim_account, create_order, fill_order, settle


def bars():
    return [{'event_date': f'2025-01-0{index}', 'open': str(close), 'high': str(close),
             'low': str(close), 'close': str(close), 'volume': 1000,
             'available_at': f'2025-01-0{index}T08:00:00Z'}
            for index, close in enumerate((10, 11, 9, 13, 9999), 1)]


def trade(session, account_id, day, side, quantity, price, symbol='600000.SH'):
    settle(session, account_id, day)
    order = create_order(session, account_id, {'symbol': symbol, 'side': side, 'quantity': quantity,
        'limit_price': price, 'signal_date': day, 'submit_date': day})
    return fill_order(session, account_id, order['id'], {'expected_revision': order['revision'],
        'fill_date': day, 'fill_price': price})


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    with factory.begin() as session:
        account = create_sim_account(session, {'name': '曲线验证', 'initial_capital': '10000', 'start_date': '2025-01-01'})
        trade(session, account['id'], '2025-01-02', 'buy', 100, '10')
        settle(session, account['id'], '2025-01-04')
        dataset = import_dataset(session, tmp_path, {'symbol': 'sh600000', 'adjustment': 'none', 'bars': bars()})
    yield factory, tmp_path, account['id'], dataset['id']
    engine.dispose()


def prepare(scenario, **overrides):
    factory, path, account_id, dataset_id = scenario
    with factory() as session:
        return service.prepare_report(session, path, account_id, {'date_from': '2025-01-01',
            'date_to': '2025-01-04', 'dataset_ids': [dataset_id], 'strict': True, **overrides})


def save(scenario, prepared):
    with scenario[0].begin() as session:
        return service.save_report(session, scenario[2], prepared)


def test_actual_fills_replay_cash_fees_marked_assets_drawdown_and_future_exclusion(scenario):
    report = prepare(scenario)
    result = report['result']
    rows = {row['date']: row for row in result['points']}
    assert rows['2025-01-01']['total_assets'] == '10000.00'
    assert rows['2025-01-02']['cash'] == '8994.99'
    assert rows['2025-01-02']['total_assets'] == '10094.99'
    assert rows['2025-01-02']['asset_index'] == '1.00949900'
    assert rows['2025-01-03']['total_assets'] == '9894.99'
    assert float(rows['2025-01-03']['drawdown_pct']) == pytest.approx(1.9812)
    assert rows['2025-01-04']['total_assets'] == '10294.99'
    assert rows['2025-01-04']['cumulative_fees'] == '5.01'
    assert result['monthly'][0]['denominator_assets'] == '10000.00'
    assert result['monthly'][0]['return_pct'] == '2.9499'
    assert result['monthly'][0]['is_partial_month']
    assert '2025-01-05' not in rows
    assert report['request']['account']['fills'][0]['gross_minor'] == 100000


def test_sell_replay_uses_actual_fees_not_new_settings(scenario):
    factory, path, account_id, _dataset_id = scenario
    with factory.begin() as session:
        trade(session, account_id, '2025-01-04', 'sell', 100, '12')
        wallet = session.get(SimWallet, account_id)
        config = json.loads(wallet.config_json)
        config['minimum_commission'] = '99'
        wallet.config_json = json.dumps(config)
        wallet.config_version += 1
        wallet.revision += 1
    report = prepare(scenario)
    assert report['result']['points'][-1]['cash'] == '10188.78'
    assert report['result']['points'][-1]['total_assets'] == '10188.78'
    assert report['result']['points'][-1]['positions'] == []
    assert report['result']['points'][-1]['cumulative_fees'] == '11.22'


def test_missing_and_stale_quotes_are_not_zero_or_full_drawdown(scenario):
    report = prepare(scenario, dataset_ids=[])
    points = report['result']['points']
    assert points[-1]['total_assets'] is None and points[-1]['cash'] == '8994.99'
    assert points[-1]['positions'][0]['close'] is None
    assert report['result']['summary']['max_drawdown_pct'] is None
    assert report['result']['monthly'][0]['return_pct'] is None
    saved = save(scenario, report)
    with scenario[0]() as session:
        with pytest.raises(TradeError, match='缺价'): service.size_from_assets(session, scenario[2], {'report_id': saved['id'], 'percent': '10', 'limit_price': '10'})
    factory, path, account_id, _dataset_id = scenario
    with factory.begin() as session:
        short = import_dataset(session, path, {'symbol': '600000', 'adjustment': 'none', 'bars': bars()[:2]})
    stale = prepare(scenario, dataset_ids=[short['id']])
    assert stale['result']['points'][-1]['quality'] == 'stale_quotes'
    assert stale['result']['points'][-1]['positions'][0]['quote_date'] == '2025-01-02'


def test_late_availability_rolls_shanghai_date_and_never_replaces_newer_quote(scenario):
    factory, path, _account_id, _dataset_id = scenario
    changed = bars()
    changed[1]['available_at'] = '2025-01-03T18:00:00Z'  # Jan 4 Shanghai
    changed[3]['available_at'] = '2025-01-05T08:00:00Z'
    with factory.begin() as session:
        dataset = import_dataset(session, path, {'symbol': '600000', 'adjustment': 'none', 'bars': changed})
    report = prepare(scenario, dataset_ids=[dataset['id']])
    rows = {row['date']: row for row in report['result']['points']}
    assert rows['2025-01-02']['positions'][0]['quote_date'] == '2025-01-01'
    assert rows['2025-01-04']['positions'][0]['quote_date'] == '2025-01-03'
    assert rows['2025-01-04']['total_assets'] == '9894.99'


def test_aliases_match_but_index_and_stock_exchanges_are_isolated(scenario):
    factory, path, _account_id, dataset_id = scenario
    with factory.begin() as session:
        alias = import_dataset(session, path, {'symbol': '600000', 'adjustment': 'none', 'bars': bars()})
        index = import_dataset(session, path, {'symbol': 'sh000001', 'adjustment': 'none', 'bars': bars()})
    with pytest.raises(TradeError, match='别名'): prepare(scenario, dataset_ids=[dataset_id, alias['id']])
    with pytest.raises(TradeError, match='不属于'): prepare(scenario, dataset_ids=[index['id']])
    assert prepare(scenario, dataset_ids=[alias['id']])['result']['points'][-1]['total_assets'] == '10294.99'


def test_subset_range_opens_from_prior_day_and_does_not_count_initial_again(scenario):
    report = prepare(scenario, date_from='2025-01-03')
    result = report['result']
    assert result['opening_baseline']['date'] == '2025-01-02'
    assert result['opening_baseline']['total_assets'] == '10094.99'
    assert result['monthly'][0]['denominator_assets'] == '10094.99'
    assert float(result['monthly'][0]['return_pct']) == pytest.approx(1.9812)


def test_report_immutability_wallet_conflict_and_account_isolation(scenario):
    factory, path, account_id, _dataset_id = scenario
    report = prepare(scenario)
    saved = save(scenario, report)
    with factory.begin() as session:
        other = create_sim_account(session, {'name': '另一个账户', 'initial_capital': '20000', 'start_date': '2025-01-01'})
        settle(session, account_id, '2025-01-05')
    with factory.begin() as session:
        with pytest.raises(TradeError, match='变化'): service.save_report(session, account_id, report)
        with pytest.raises(TradeError, match='没有此'): service.get_report(session, other['id'], saved['id'])
        assert service.get_report(session, account_id, saved['id']) == saved
        assert service.list_reports(session, other['id']) == []
        service.delete_report(session, account_id, saved['id'])
        assert service.list_reports(session, account_id) == []


def test_initial_date_missing_or_ledger_mismatch_is_error_not_empty(scenario):
    factory, _path, account_id, _dataset_id = scenario
    with factory.begin() as session:
        session.get(SimWallet, account_id).cash_minor += 1
    with pytest.raises(TradeError, match='不一致'): prepare(scenario)
    with factory.begin() as session:
        session.get(SimWallet, account_id).cash_minor -= 1
        session.execute(delete(AuditEvent).where(AuditEvent.account_id == account_id, AuditEvent.operation == 'create', AuditEvent.entity_type == 'sim_account'))
    with pytest.raises(TradeError, match='初始日期'): prepare(scenario)


def test_asset_percent_size_and_atomic_draft_record_exact_denominator(scenario):
    factory, _path, account_id, dataset_id = scenario
    saved = save(scenario, prepare(scenario))
    with factory.begin() as session:
        # A well-described saved observation; sizing does not execute a strategy.
        session.add(ResearchRun(id='positive-run', dataset_id=dataset_id, strategy_id='relative_strength_breakout_v1',
            strategy_version='1', decision_at='2025-01-04T15:00:00Z', strict=1, params_json='{}',
            result_json=json.dumps({'status': 'computed', 'signal': True, 'draft_eligible': True,
                'candidate': {'symbol': '600000.SH'}, 'source_date': '2025-01-04'}), created_at='2025-01-04T15:00:00Z'))
    body = {'report_id': saved['id'], 'percent': '20', 'limit_price': '10'}
    with factory() as session: quote = service.size_from_assets(session, account_id, body)
    assert quote['denominator_assets'] == '10294.99'
    assert quote['requested_budget'] == '2058.99' and quote['quantity'] == 200
    assert quote['required_cash'] == '2005.02' and quote['mode'] == 'asset_percent'
    with factory.begin() as session:
        before = session.get(SimWallet, account_id).cash_minor
        created = service.create_asset_sized_draft(session, account_id, {**body, 'source_run_id': 'positive-run', 'expected_quote_sha256': quote['quote_sha256']})
        assert created['draft']['quantity'] == 200
        assert session.get(SimWallet, account_id).cash_minor == before
        evidence = session.get(SimEquityDraftEvidence, created['evidence_id'])
        assert json.loads(evidence.evidence_json)['quote']['denominator_assets'] == '10294.99'
        with pytest.raises(TradeError, match='变化'):
            service.create_asset_sized_draft(session, account_id, {**body, 'percent': '30', 'source_run_id': 'positive-run', 'expected_quote_sha256': quote['quote_sha256']})
    with factory.begin() as session:
        create_order(session, account_id, {'symbol': '600000.SH', 'side': 'buy', 'quantity': 100, 'limit_price': '10', 'signal_date': '2025-01-04', 'submit_date': '2025-01-04'})
    with factory() as session:
        with pytest.raises(TradeError, match='变化'): service.size_from_assets(session, account_id, body)


def test_maximum_bar_budget_preprocess_is_bounded_without_day_by_bar_scan():
    days = [(date(2024, 1, 1) + timedelta(days=index)).isoformat() for index in range(900)]
    datasets = [{'id': str(index), 'symbol_key': f'sh{600000 + index}', 'bars': [
        {'event_date': day, 'close': '10', 'available_at': day + 'T08:00:00Z'} for day in days]} for index in range(64)]
    fills = [{'symbol_key': row['symbol_key'], 'side': 'buy', 'quantity': 100, 'gross_minor': 100000,
        'commission_minor': 500, 'stamp_minor': 0, 'transfer_minor': 1, 'fill_date': days[0]} for row in datasets]
    frozen = {'date_from': days[400], 'date_to': days[765], 'initial_date': days[0],
        'initial_minor': 100_000_000, 'fills': fills, 'strict': True}
    started = time.perf_counter()
    result = calculate_equity(frozen, datasets)
    elapsed = time.perf_counter() - started
    assert len(result['points']) == 366 and len(result['points'][-1]['positions']) == 64
    assert result['summary']['missing_observation_count'] == 0
    assert elapsed < 5, f'57,600-bar bounded pure replay unexpectedly took {elapsed:.3f}s'


def test_unknown_availability_strict_and_diagnostic_quotes_cannot_size(scenario):
    factory, path, account_id, _dataset_id = scenario
    unknown = bars()
    for row in unknown: row['available_at'] = None
    with factory.begin() as session:
        dataset = import_dataset(session, path, {'symbol': '600000', 'adjustment': 'none', 'bars': unknown})
    strict = prepare(scenario, dataset_ids=[dataset['id']])
    assert strict['result']['summary']['ending_assets'] is None
    loose = save(scenario, prepare(scenario, dataset_ids=[dataset['id']], strict=False))
    assert loose['result']['points'][-1]['quality'] == 'availability_unknown'
    assert loose['result']['summary']['ending_assets'] == '10294.99'
    with factory() as session:
        with pytest.raises(TradeError, match='可得时间未知'):
            service.size_from_assets(session, account_id, {'report_id': loose['id'], 'percent': '20', 'limit_price': '10'})


@pytest.mark.parametrize('override', [
    {'date_from': '2023-01-01'}, {'date_to': '2025-01-05'}, {'strict': 1},
    {'dataset_ids': [['wrong']]}, {'dataset_ids': ['same'] * 65}, {'unexpected': True},
])
def test_report_bounds_are_explicit_errors(scenario, override):
    with pytest.raises(TradeError): prepare(scenario, **override)


def test_month_boundary_uses_previous_endpoint_and_preserves_partial_month():
    frozen = {'date_from': '2025-01-30', 'date_to': '2025-02-02', 'initial_date': '2025-01-30',
        'initial_minor': 1000000, 'fills': [{'symbol_key': 'sh600000', 'side': 'buy', 'quantity': 100,
            'gross_minor': 100000, 'commission_minor': 500, 'stamp_minor': 0, 'transfer_minor': 1, 'fill_date': '2025-01-30'}], 'strict': True}
    dataset = {'id': 'monthly', 'symbol_key': 'sh600000', 'bars': [
        {'event_date': day, 'close': close, 'available_at': day + 'T08:00:00Z'}
        for day, close in [('2025-01-30', '10'), ('2025-01-31', '11'), ('2025-02-02', '12')]]}
    result = calculate_equity(frozen, [dataset])
    jan, feb = result['monthly']
    assert jan['denominator_assets'] == '10000.00' and jan['ending_assets'] == '10094.99'
    assert feb['start_date'] == '2025-01-31' and feb['denominator_assets'] == '10094.99'
    assert feb['ending_assets'] == '10194.99'
    assert float(feb['return_pct']) == pytest.approx(.9906)
    assert jan['is_partial_month'] and feb['is_partial_month']


def test_api_preview_is_readonly_save_revision_idempotent_and_survives_restart(scenario):
    from trade_app.main import create_app
    from test_trade_rebuild import started_client
    factory, path, account_id, dataset_id = scenario
    root = f'/api/v1/sim-accounts/{account_id}/equity-reports'
    body = {'date_from': '2025-01-01', 'date_to': '2025-01-04', 'dataset_ids': [dataset_id], 'strict': True}
    with started_client(create_app(path, auto_rebuild=False)) as client:
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': 'save-equity'}
        assert client.get(root + '/inputs').json()['data']['fill_count'] == 1
        assert client.get(root).json()['data'] == []
        preview = client.post(root + '/preview', json=body, headers=headers)
        assert preview.status_code == 200, preview.text
        prepared = preview.json()['data']
        assert client.get(root).json()['data'] == []
        stale = client.post(root, json={**body, 'expected_input_sha256': 'wrong'}, headers=headers)
        assert stale.status_code == 409
        payload = {**body, 'expected_input_sha256': prepared['input_sha256']}
        first = client.post(root, json=payload, headers=headers)
        assert first.status_code == 200, first.text
        saved = first.json()['data']
        assert client.post(root, json=payload, headers=headers).json()['data'] == saved
        assert len(client.get(root).json()['data']) == 1
        invalid = client.post(root + '/size', json={'report_id': saved['id'], 'percent': '10'}, headers=headers)
        assert invalid.status_code == 400
        invalid = client.post(root + '/drafts', json={'report_id': saved['id'], 'percent': '10', 'limit_price': '10',
            'source_run_id': [], 'expected_quote_sha256': 'a' * 64}, headers={**headers, 'Idempotency-Key': 'bad-draft'})
        assert invalid.status_code == 400
    with started_client(create_app(path, auto_rebuild=False)) as client:
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        assert client.get(root + '/' + saved['id']).json()['data'] == saved
        response = client.delete(root + '/' + saved['id'], headers={'X-CSRF-Token': token, 'Idempotency-Key': 'delete-equity'})
        assert response.status_code == 200, response.text
        assert client.get(root).json()['data'] == []
        with factory() as session:
            assert service.inputs(session, account_id)['fill_count'] == 1
