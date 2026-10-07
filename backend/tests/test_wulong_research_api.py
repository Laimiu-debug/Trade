"""Exercise Wulong's complete single-stock draft gate using a real shape trigger."""
import asyncio
from datetime import date, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from legacy_oracle import oracle, plain
from trade_app.main import create_app
from trade_app.research.models import ResearchRun


def _post(client, path, body):
    return client.post(path, json=body, headers={'Idempotency-Key': str(uuid4())})


def _data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def _trigger_bars():
    # An established rise, 25-day consolidation, then three expanding up days.
    # Early pullbacks ensure both up- and down-volume samples are represented.
    closes = [round(8 + index * 2 / 59 - (0.1 if index % 7 == 3 else 0), 4)
              for index in range(60)] + [10] * 25 + [10.3, 10.6, 10.9]
    bars = []
    for index, close in enumerate(closes):
        day = date(2025, 1, 1) + timedelta(days=index)
        volume = (3000 if index == len(closes) - 1 else
                  1200 if index == 0 or close >= closes[index - 1] else 1000)
        bars.append({'event_date': day.isoformat(), 'open': f'{close - 0.05:.4f}',
                     'high': f'{close + 0.1:.4f}', 'low': f'{close - 0.1:.4f}',
                     'close': f'{close:.4f}', 'volume': volume,
                     'available_at': day.isoformat() + 'T08:00:00+00:00'})
    return bars


@pytest.fixture
def scenario(tmp_path):
    app = create_app(tmp_path, auto_rebuild=False)
    lifespan = app.router.lifespan_context(app)
    with asyncio.Runner() as runner:
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app) as client:
                client.headers['X-CSRF-Token'] = client.get('/api/v1/session').json()['data']['csrf_token']
                bars = _trigger_bars()
                # Add a dramatic later bar: neither indicator nor candidate metrics
                # may use it before the frozen decision time.
                future_bar = {'event_date': '2025-03-30', 'open': '10.9000',
                              'high': '15.0000', 'low': '10.0000', 'close': '14.0000',
                              'volume': 1000000, 'available_at': '2025-03-30T08:00:00+00:00'}
                dataset = _data(_post(client, '/api/v1/market/datasets', {
                    'symbol': '600000', 'bars': bars + [future_bar], 'adjustment': 'none'}))
                account = _data(_post(client, '/api/v1/sim-accounts', {
                    'name': '五龙完整入池测试', 'initial_capital': '10000', 'start_date': '2025-03-29'}))
                body = {'dataset_id': dataset['id'], 'strategy_id': 'wulong_cluster_v1',
                        'decision_at': '2025-03-29T09:00:00+00:00', 'strict': True, 'params': {}}
                yield client, bars, dataset, f"/api/v1/sim-accounts/{account['id']}", body
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


def _assert_real_shape_parity(client, bars, run):
    descriptor = next(row for row in _data(client.get('/api/v1/research/strategies'))
                      if row['id'] == 'wulong_cluster_v1')

    def legacy():
        from app.core.strategy_plugins import (
            WulongClusterPlugin, calculate_wulong_cluster_signal, evaluate_wulong_cluster_signal,
        )
        from app.models import CandlePoint
        points = [CandlePoint(time=bar['event_date'], open=float(bar['open']), high=float(bar['high']),
                             low=float(bar['low']), close=float(bar['close']), volume=bar['volume'], amount=0)
                  for bar in bars]
        indicator = calculate_wulong_cluster_signal(points)
        pool = WulongClusterPlugin().build_universe(
            candidates=[SimpleNamespace(**run['result']['universe']['metrics'])], params=run['params'], mode='strict')
        return {'indicator': indicator, 'evaluation': evaluate_wulong_cluster_signal(indicator, descriptor['default_params']),
                'pool': bool(pool)}
    case = json.dumps([run['result']['universe']['metrics'], run['params']], sort_keys=True)
    expected = oracle(f'real_shape:{case}', legacy)
    assert expected['evaluation']['signal'] is True
    assert plain(run['result']['indicator']) == expected['indicator']
    assert plain(run['result']['evaluation']) == expected['evaluation']
    assert run['result']['shape_signal'] is True
    assert expected['pool'] is run['result']['universe']['passed']


def test_real_wulong_shape_and_candidate_create_draft_with_default_params(scenario):
    client, bars, _dataset, root, body = scenario
    run = _data(_post(client, '/api/v1/research/runs', body))
    _assert_real_shape_parity(client, bars, run)
    result = run['result']
    assert result['status'] == 'computed'
    assert result['signal'] is True and result['draft_eligible'] is True
    assert result['candidate_universe_filter_run'] is True
    assert result['universe']['passed'] is True
    assert result['universe']['observed_bars'] == 88
    assert result['universe']['source_path'] == 'store._build_row_from_candles'
    assert result['universe']['metrics']['ret40'] == 0.1322
    assert result['universe']['metrics']['up_down_volume_ratio'] == 1.2231
    assert result['source_date'] == '2025-03-29'
    draft = _data(_post(client, root + '/drafts', {
        'source_run_id': run['id'], 'quantity': 100, 'limit_price': '10.9'}))
    assert draft['source_run_id'] == run['id'] and draft['status'] == 'draft'
    assert _data(client.get(root + f"/drafts/{draft['id']}/preview"))['can_submit'] is True
    assert _data(client.get('/api/v1/research/runs/' + run['id'])) == run


def test_shape_trigger_with_failed_candidate_gate_is_not_an_actionable_signal(scenario):
    client, bars, _dataset, root, body = scenario
    run = _data(_post(client, '/api/v1/research/runs', {
        **body, 'params': {'min_ret40': '0.2'}}))
    _assert_real_shape_parity(client, bars, run)
    result = run['result']
    assert result['shape_signal'] is True
    assert result['signal'] is False and result['draft_eligible'] is False
    assert result['candidate_universe_filter_run'] is True
    assert result['universe']['passed'] is False
    assert result['universe']['failed_conditions'] == ['min_ret40']
    assert 'candidate_universe_rejected' in result['quality_flags']
    rejected = _post(client, root + '/drafts', {
        'source_run_id': run['id'], 'quantity': 100, 'limit_price': '10.9'})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['error']['code'] == 'SIGNAL_NOT_ACTIONABLE'
    assert _data(client.get(root + '/drafts')) == []
    assert _data(client.get(root + '/orders')) == []


def test_old_saved_raw_wulong_signal_without_candidate_result_still_rejected(scenario):
    client, _bars, dataset, root, body = scenario
    run = _data(_post(client, '/api/v1/research/runs', body))
    old_result = dict(run['result'])
    # Persist the old record format, in which signal only meant a shape trigger.
    # Absence of a new field must never grant candidate eligibility by default.
    for key in ('shape_signal', 'universe', 'candidate_universe_filter_run', 'draft_eligible'):
        old_result.pop(key, None)
    assert old_result['signal'] is True
    old_run_id = 'f' * 64
    with client.app.state.db_factory.begin() as session:
        session.add(ResearchRun(
            id=old_run_id, dataset_id=dataset['id'], strategy_id='wulong_cluster_v1',
            strategy_version='2.0.0-alpha', decision_at=body['decision_at'], strict=1,
            params_json='{}', result_json=json.dumps(old_result), created_at='2025-03-29T09:00:01Z'))
    rejected = _post(client, root + '/drafts', {
        'source_run_id': old_run_id, 'quantity': 100, 'limit_price': '10.9'})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['error']['code'] == 'SIGNAL_NOT_ACTIONABLE'
    assert _data(client.get(root + '/drafts')) == []
    assert _data(client.get(root + '/orders')) == []
