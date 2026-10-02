"""Matrix pool provenance, persistence, and observation-to-draft API coverage."""
import asyncio
import hashlib
import json
from contextlib import contextmanager
from datetime import date, timedelta

from fastapi.testclient import TestClient

from trade_app.main import create_app
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.types import utc_now
from trade_app.research.screener_models import ScreenerRun


@contextmanager
def started_client(app):
    lifespan = app.router.lifespan_context(app)
    with asyncio.Runner() as runner:
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app) as client:
                yield client
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


def writer(client):
    token = client.get('/api/v1/session').json()['data']['csrf_token']
    serial = 0

    def post(path, body, key=None):
        nonlocal serial
        serial += 1
        return client.post(path, json=body, headers={
            'X-CSRF-Token': token, 'Idempotency-Key': key or f'matrix-test-{serial}',
        })
    return post


def data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def bars(*, declining=False, delayed=False):
    result = []
    for index in range(260):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        close = 200 - index * 0.5 if declining else 10 + index * 0.04
        result.append({'event_date': day, 'open': f'{close - 0.1:.4f}',
                       'high': f'{close + 0.4:.4f}', 'low': f'{close - 0.4:.4f}',
                       'close': f'{close:.4f}', 'volume': 1_000_000 + index * 1_000,
                       'amount': f'{close * (1_000_000 + index * 1_000):.2f}',
                       'available_at': f'{day}T07:00:00+00:00'})
    if delayed:
        result[-1]['available_at'] = f'{date.fromisoformat(result[-1]["event_date"]) + timedelta(days=1)}T07:00:00+00:00'
        # If a known future bar leaks, it changes the latest return dramatically.
        for key in ('open', 'high', 'low', 'close'):
            result[-1][key] = '1000.0000'
    return result


def seeded_candidate(index, **overrides):
    return {'symbol': f'{600000 + index:06d}', 'dataset_id': f'{index + 1:064x}',
            'as_of_date': '2025-05-20', 'name': str(index), 'quality_flags': [],
            'ret40': -0.1 - index * 0.001, 'retrace20': 0.1, 'vol_slope20': 0.1,
            'price_vs_ma20': 0.01, 'up_down_volume_ratio': 1.5,
            'pullback_days': 2, 'ma10_above_ma20_days': 8, **overrides}


def seed_source(app, candidates, *, return_window=40):
    snapshot = {'as_of_date': '2025-05-20', 'return_window_days': return_window,
                'pools': {'input': candidates}, 'quality_flags': sorted({
                    flag for row in candidates for flag in row['quality_flags']}),
                'summary': {'input': len(candidates)}}
    contents = json.dumps(snapshot, sort_keys=True)
    source_id = hashlib.sha256(contents.encode()).hexdigest()
    with app.state.db_factory.begin() as session:
        session.add(ScreenerRun(id=source_id, request_json=json.dumps({
            'as_of_date': snapshot['as_of_date'], 'return_window_days': return_window}),
            result_json=contents, code_sha256='f' * 64, created_at=utc_now()))
    return source_id


def test_matrix_real_frozen_source_persistence_promotion_draft_and_backup(tmp_path):
    original_dir = tmp_path / 'original'
    app = create_app(original_dir, auto_rebuild=False)
    with started_client(app) as client:
        post = writer(client)
        increasing, falling, delayed = bars(), bars(declining=True), bars(delayed=True)
        ids = []
        for symbol, points in [('600000', increasing), ('000001', falling), ('600001', delayed)]:
            ids.append(data(post('/api/v1/market/datasets', {'symbol': symbol, 'bars': points}))['id'])
        as_of = increasing[-1]['event_date']
        source = data(post('/api/v1/research/screener-runs', {
            'datasets': [{'dataset_id': item} for item in ids],
            'as_of_date': as_of, 'return_window_days': 40, 'config': {},
        }))
        source_rows = source['result']['pools']['input']
        future_filtered = next(row for row in source_rows if row['dataset_id'] == ids[2])
        assert future_filtered['as_of_date'] == delayed[-2]['event_date']
        assert 'BARS_AFTER_DECISION_EXCLUDED' in future_filtered['quality_flags']
        assert future_filtered['ret40'] < 0.2
        payload = {'source_run_id': source['id'], 'strict': True}
        run = data(post('/api/v1/research/matrix-runs', payload, 'matrix-create'))
        assert data(post('/api/v1/research/matrix-runs', payload, 'matrix-create')) == run
        assert data(post('/api/v1/research/matrix-runs', payload)) == run
        assert run['strategy_id'] == 'matrix_signal_v1'
        assert len(run['result']['source_sha256']) == 64
        assert run['result']['source_code_sha256'] == source['code_sha256']
        assert run['result']['summary'] == {
            'input_count': 2, 'pool_count': 2, 'signal_count': 1,
            'source_count': 3, 'excluded_count': 1,
        }
        assert run['result']['excluded'] == [{
            'symbol': '600001', 'dataset_id': ids[2], 'reasons': ['CANDIDATE_DATE_MISMATCH'],
        }]
        assert run['result']['ranking'] == [ids[0]]
        assert data(client.get('/api/v1/research/matrix-runs/' + run['id'])) == run
        history = data(client.get('/api/v1/research/matrix-runs'))
        assert len(history) == 1 and history[0]['id'] == run['id']
        assert 'result' not in history[0]
        promote = f"/api/v1/research/matrix-runs/{run['id']}/signals/"
        signal = data(post(promote + ids[0], {}))
        assert data(post(promote + ids[0], {})) == signal
        assert signal['strategy_id'] == 'matrix_signal_v1'
        assert signal['strict'] is True
        assert signal['result']['draft_eligible'] is True
        assert signal['result']['candidate']['matrix_run_id'] == run['id']
        assert signal['result']['source_date'] == as_of
        assert signal['result']['evaluation'] == run['result']['rows'][0]
        for rejected_id in ids[1:]:
            rejected = post(promote + rejected_id, {})
            assert rejected.status_code == 409
            assert rejected.json()['error']['code'] == 'MATRIX_SIGNAL_NOT_FOUND'
        account = data(post('/api/v1/sim-accounts', {
            'name': '矩阵测试账户', 'initial_capital': '10000', 'start_date': as_of,
        }))
        draft_root = f"/api/v1/sim-accounts/{account['id']}/drafts"
        draft = data(post(draft_root, {
            'source_run_id': signal['id'], 'quantity': 100, 'limit_price': increasing[-1]['close'],
        }))
        assert draft['symbol'] == '600000' and draft['signal_date'] == as_of
        assert draft['status'] == 'draft' and draft['source_run_id'] == signal['id']

    backup_bytes = create_backup(original_dir)
    restored_dir = tmp_path / 'restored'
    restore_to_new_directory(backup_bytes, restored_dir)
    for path in (original_dir, restored_dir):
        with started_client(create_app(path, auto_rebuild=False)) as client:
            writer(client)
            assert data(client.get('/api/v1/research/matrix-runs/' + run['id'])) == run
            assert data(client.get('/api/v1/research/runs/' + signal['id'])) == signal
            assert data(client.get(draft_root)) == [draft]


def test_matrix_strict_unknown_and_stale_rows_are_removed_before_top_n(tmp_path):
    app = create_app(tmp_path, auto_rebuild=False)
    with started_client(app) as client:
        post = writer(client)
        candidates = [seeded_candidate(index) for index in range(53)]
        candidates[0]['quality_flags'] = ['HISTORICAL_AVAILABLE_AT_UNKNOWN']
        candidates[52].update(as_of_date='2025-05-19', ret40=1.0)
        source_id = seed_source(app, candidates)
        payload = {'source_run_id': source_id, 'params': {'ret40_top_n': '50', 'min_pool_score': '4'}}
        strict = data(post('/api/v1/research/matrix-runs', {**payload, 'strict': True}))
        loose = data(post('/api/v1/research/matrix-runs', {**payload, 'strict': False}))
        assert strict['id'] != loose['id']
        assert strict['result']['summary'] == {
            'source_count': 53, 'excluded_count': 2, 'input_count': 51,
            'pool_count': 50, 'signal_count': 50,
        }
        assert loose['result']['summary'] == {
            'source_count': 53, 'excluded_count': 1, 'input_count': 52,
            'pool_count': 50, 'signal_count': 50,
        }
        strict_rows = {row['dataset_id']: row for row in strict['result']['rows']}
        loose_rows = {row['dataset_id']: row for row in loose['result']['rows']}
        newly_admitted = candidates[50]['dataset_id']
        assert strict_rows[newly_admitted]['components']['s3'] is True
        assert strict_rows[newly_admitted]['ret40_rank'] == 50
        assert strict_rows[newly_admitted]['signal'] is True
        assert loose_rows[newly_admitted]['components']['s3'] is False
        assert loose_rows[newly_admitted]['signal'] is False
        assert strict['result']['ranking'] == [row['dataset_id'] for row in candidates[1:51]]
        assert strict['result']['excluded'][0]['reasons'] == ['HISTORICAL_AVAILABLE_AT_UNKNOWN']
        # Numerically equivalent parameter strings produce the same frozen identity.
        canonical = data(post('/api/v1/research/matrix-runs', {
            **payload, 'strict': True, 'params': {'ret40_top_n': '50.0', 'min_pool_score': '4.00'},
        }))
        assert canonical['id'] == strict['id']
        assert len(data(client.get('/api/v1/research/matrix-runs'))) == 2


def test_matrix_source_window_missing_source_and_unused_params_reject_without_saved_run(tmp_path):
    app = create_app(tmp_path, auto_rebuild=False)
    with started_client(app) as client:
        post = writer(client)
        source_id = seed_source(app, [seeded_candidate(0)], return_window=20)
        rejected = post('/api/v1/research/matrix-runs', {'source_run_id': source_id})
        assert rejected.status_code == 409
        assert rejected.json()['error']['code'] == 'MATRIX_RETURN_WINDOW_REQUIRED'
        missing = post('/api/v1/research/matrix-runs', {'source_run_id': '0' * 64})
        assert missing.status_code == 404
        assert missing.json()['error']['code'] == 'SCREENER_RUN_NOT_FOUND'
        valid_id = seed_source(app, [seeded_candidate(0)])
        unused = post('/api/v1/research/matrix-runs', {
            'source_run_id': valid_id, 'params': {'min_score': '0'},
        })
        assert unused.status_code == 400
        assert unused.json()['error']['code'] == 'UNKNOWN_STRATEGY_PARAM'
        assert data(client.get('/api/v1/research/matrix-runs')) == []
        assert client.get('/api/v1/research/matrix-runs/' + '0' * 64).status_code == 404


def test_matrix_all_unknown_strict_pool_stays_empty_and_cannot_promote(tmp_path):
    app = create_app(tmp_path, auto_rebuild=False)
    with started_client(app) as client:
        post = writer(client)
        candidate = seeded_candidate(0, quality_flags=['HISTORICAL_AVAILABLE_AT_UNKNOWN'])
        source_id = seed_source(app, [candidate])
        run = data(post('/api/v1/research/matrix-runs', {'source_run_id': source_id, 'strict': True}))
        assert run['result']['rows'] == [] and run['result']['ranking'] == []
        assert run['result']['summary'] == {
            'source_count': 1, 'excluded_count': 1, 'input_count': 0, 'pool_count': 0, 'signal_count': 0,
        }
        promote = post(f"/api/v1/research/matrix-runs/{run['id']}/signals/{candidate['dataset_id']}", {})
        assert promote.status_code == 409
        assert promote.json()['error']['code'] == 'MATRIX_SIGNAL_NOT_FOUND'
