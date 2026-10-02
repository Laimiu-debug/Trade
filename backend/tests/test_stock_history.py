import json

from trade_app.research.b1_models import B1Run
from test_strategy_backtests import flat_bars
from test_wyckoff_research_api import client_for, data, write


def test_history_combines_funnel_b1_strategy_and_alias_versions_without_same_code_index(tmp_path):
    bars = flat_bars(280)
    with client_for(tmp_path) as client:
        stock = data(write(client, '/market/datasets', {'symbol': 'sz000001', 'bars': bars}))
        bars[-1]['volume'] += 1
        alias = data(write(client, '/market/datasets', {'symbol': '000001.SZ', 'bars': bars}))
        index = data(write(client, '/market/datasets', {'symbol': 'sh000001', 'bars': bars}))
        day = bars[-1]['event_date']
        for ds in (stock, alias, index):
            data(write(client, '/research/runs', {'dataset_id': ds['id'], 'strategy_id': 'relative_strength_breakout_v1',
                       'decision_at': day + 'T10:00:00+00:00', 'strict': True}))
        funnel = data(write(client, '/research/screener-runs', {'datasets': [{'dataset_id': stock['id'], 'float_shares': 100000000}], 'as_of_date': day}))
        b1 = data(write(client, '/research/b1-runs', {'dataset_ids': [alias['id'], index['id']], 'as_of_date': day}))
        response = data(client.get('/api/v1/research/stock-history?symbol=000001.SZ'))
        assert response['symbol'] == 'sz000001'
        assert {row['kind'] for row in response['items']} == {'strategy', 'screener', 'b1'}
        assert {row['dataset_id'] for row in response['items']} == {stock['id'], alias['id']}
        assert len(response['items']) == 4
        assert next(row for row in response['items'] if row['run_id'] == b1['id'])['outcome'] == 'insufficient'
        stage = next((stage for stage in ('step4', 'step3', 'step2', 'step1', 'input') if funnel['result']['pools'][stage]), 'excluded')
        assert next(row for row in response['items'] if row['run_id'] == funnel['id'])['outcome'] == stage
        assert all(row['created_at'] and row['observed_at'] for row in response['items'])
        assert 'bars' not in str(response) and 'result_json' not in str(response)
        bounded = data(client.get('/api/v1/research/stock-history?symbol=sz000001&limit=1'))
        assert len(bounded['items']) == 3
        assert client.get('/api/v1/research/stock-history?symbol=sz000001&limit=101').status_code == 422
        assert data(client.get('/api/v1/research/stock-history?symbol=bj920001'))['items'] == []


def test_b1_universe_object_dataset_references_and_no_hit_remain_distinct(tmp_path):
    with client_for(tmp_path) as client:
        sample = data(write(client, '/market/datasets', {'symbol': '600000', 'bars': flat_bars(5)}))
        with client.app.state.db_factory.begin() as session:
            for identifier, hits in [('hit', [{'dataset_id': sample['id'], 'symbol': '600000'}]), ('miss', [])]:
                session.add(B1Run(id=identifier, request_json=json.dumps({'datasets': [{'dataset_id': sample['id']}], 'source': 'tdx_universe'}),
                    result_json=json.dumps({'as_of_date': '2025-01-01', 'hits': hits, 'excluded': []}), code_sha256='f' * 64, created_at='2026-01-01T00:00:00Z'))
        response = data(client.get('/api/v1/research/stock-history?symbol=sh600000'))
        assert {row['run_id']: row['outcome'] for row in response['items']} == {'hit': 'hits', 'miss': 'no_hit'}
        assert all(row['observed_at'] != row['created_at'][:10] for row in response['items'])
