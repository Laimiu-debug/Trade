import asyncio
from contextlib import contextmanager
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trade_app.market.service import import_dataset
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research.screener_service import prepare_screener_run, save_screener_run
from trade_app.research import watch_pool_service as pool


def seed(session, path, symbols=('600000',)):
    bars = []
    for index in range(260):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        close = 10 + index / 100
        bars.append({'event_date': day, 'open': f'{close:.4f}', 'high': f'{close + .2:.4f}',
                     'low': f'{close - .2:.4f}', 'close': f'{close:.4f}', 'volume': 100000,
                     'amount': '2000000', 'available_at': day + 'T08:00:00+00:00'})
    ids = [import_dataset(session, path, {'symbol': symbol, 'adjustment': 'none', 'bars': bars})['id'] for symbol in symbols]
    body = {'datasets': [{'dataset_id': value, 'float_shares': 20000000,
                           'float_shares_as_of_date': '2025-01-01'} for value in ids],
            'as_of_date': bars[-1]['event_date'], 'return_window_days': 40, 'config': {}}
    run = save_screener_run(session, prepare_screener_run(session, path, body))
    return run['result']['pools']['input']


@pytest.fixture
def db(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        yield tmp_path, factory
    finally:
        engine.dispose()


def test_defaults_read_preview_is_non_mutating_and_save_is_explicit(db):
    path, factory = db
    with factory.begin() as session:
        member = seed(session, path)[0]
        before = pool.get_pool(session)
        assert before['revision'] == 0 and before['state']['manual'] == []
        preview = pool.prepare_state(session, path, {'manual': [member], 'order': ['600000']})
        assert pool.get_pool(session) == before and pool.list_audit(session) == []
        saved = pool.save_pool(session, preview, 0, action='legacy_import')
        assert saved['revision'] == 1 and saved['state']['order'] == ['sh600000']
        stored = saved['state']['manual'][0]
        assert stored['source_run_id'] and stored['source_code_sha256']
        assert stored['dataset_id'] == member['dataset_id'] and stored['ret40'] == member['ret40']
        assert pool.list_audit(session)[0]['action'] == 'legacy_import'
    with factory() as session:
        assert pool.get_pool(session) == saved


def test_stale_revision_and_alias_duplicate_cannot_overwrite(db):
    path, factory = db
    with factory.begin() as session:
        member = seed(session, path)[0]
        suffix = {**member, 'symbol': '600000.SH'}
        prepared = pool.prepare_state(session, path, {'manual': [suffix]})
        pool.save_pool(session, prepared, 0)
        with pytest.raises(TradeError) as error:
            pool.save_pool(session, prepared, 0)
        assert error.value.code == 'WATCH_POOL_VERSION_CONFLICT'
        with pytest.raises(TradeError) as error:
            pool.prepare_state(session, path, {'manual': [member], 'automatic': [suffix]})
        assert error.value.code == 'DUPLICATE_WATCH_SYMBOL'
        assert pool.get_pool(session)['revision'] == 1


def test_same_code_index_and_stock_stay_distinct_and_wrong_exchange_rejected(db):
    path, factory = db
    with factory.begin() as session:
        index, stock, beijing = seed(session, path, ('sh000001', '000001', '920001'))
        result = pool.prepare_state(session, path, {'manual': [index, stock, {**beijing, 'symbol': '920001.BJ'}],
                                                  'order': ['000001.SZ', '000001.SH', '920001']})
        assert result['state']['order'] == ['sz000001', 'sh000001', 'bj920001']
        with pytest.raises(TradeError) as error:
            pool.prepare_state(session, path, {'manual': [{**index, 'symbol': '000001'}]})
        assert error.value.code == 'WATCH_SYMBOL_MISMATCH'


@pytest.mark.parametrize('changes,code', [
    ({'ret40': 99}, 'WATCH_EVIDENCE_NOT_FOUND'),
    ({'as_of_date': '2020-01-01'}, 'WATCH_DATE_MISMATCH'),
    ({'source_run_id': 'missing'}, 'WATCH_EVIDENCE_NOT_FOUND'),
    ({'symbol': '000001'}, 'WATCH_SYMBOL_MISMATCH'),
    ({'dataset_id': []}, 'INVALID_WATCH_MEMBER'),
])
def test_client_cannot_fabricate_metrics_dates_or_source(db, changes, code):
    path, factory = db
    with factory.begin() as session:
        member = seed(session, path)[0]
        with pytest.raises(TradeError) as error:
            pool.prepare_state(session, path, {'manual': [{**member, **changes}]})
        assert error.value.code == code
        assert pool.get_pool(session)['revision'] == 0


@pytest.mark.parametrize('config', [{'top_n': 0}, {'top_n': 501}, {'enabled': 'true'},
                                  {'ret40_min': 2, 'ret40_max': 1}, {'amount20_min': float('nan')},
                                  {'trend_classes': ['A', 'A']}, {'unknown': 1}])
def test_invalid_config_is_rejected_without_clamping(db, config):
    path, factory = db
    with factory() as session, pytest.raises(TradeError) as error:
        pool.prepare_state(session, path, {'config': config})
    assert error.value.code == 'INVALID_WATCH_POOL'


def test_frozen_contents_corruption_is_rejected(db):
    path, factory = db
    with factory.begin() as session:
        member = seed(session, path)[0]
        (path / 'market' / (member['dataset_id'] + '.json')).write_text('{}')
        with pytest.raises(TradeError) as error:
            pool.prepare_state(session, path, {'manual': [member]})
        assert error.value.code == 'MARKET_DATA_CORRUPT'


def test_pool_provenance_order_config_audit_backup_roundtrip(tmp_path):
    path = tmp_path / 'original'
    engine, factory = open_database(path)
    with factory.begin() as session:
        members = seed(session, path, ('600000', '000001'))
        raw = {'manual': members[:1], 'automatic': members[1:], 'order': ['000001', '600000'],
               'config': {'top_n': 12, 'source_mode': 'strategy'}}
        saved = pool.save_pool(session, pool.prepare_state(session, path, raw), 0)
    engine.dispose()
    restored = tmp_path / 'restored'
    restore_to_new_directory(create_backup(path), restored)
    engine, factory = open_database(restored)
    try:
        with factory() as session:
            assert pool.get_pool(session) == saved
            assert pool.list_audit(session)[0]['snapshot'] == saved
            assert pool.prepare_state(session, restored, saved['state'])['state'] == saved['state']
    finally:
        engine.dispose()


@contextmanager
def client_at(path):
    from trade_app.main import create_app
    from trade_app.api.watch_pool_routes import router
    app = create_app(path, auto_rebuild=False)
    if not any(getattr(route, 'path', '') == '/api/v1/research/watch-pool' for route in app.routes):
        app.router.routes[0:0] = router.routes
    with asyncio.Runner() as runner:
        lifespan = app.router.lifespan_context(app)
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app) as client:
                yield app, client
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


def test_api_preview_revision_idempotency_history_and_csrf(tmp_path):
    with client_at(tmp_path) as (app, client):
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        with app.state.db_factory.begin() as session:
            member = seed(session, tmp_path)[0]
        body = {'state': {'manual': [member]}}
        assert client.post('/api/v1/research/watch-pool/preview', json=body, headers=headers).status_code == 200
        assert client.get('/api/v1/research/watch-pool').json()['data']['revision'] == 0
        body['expected_revision'] = 0
        assert client.put('/api/v1/research/watch-pool', json=body).status_code == 403
        first = client.post('/api/v1/research/watch-pool/import', json=body, headers=headers)
        assert first.status_code == 200, first.text
        assert client.post('/api/v1/research/watch-pool/import', json=body, headers=headers).json() == first.json()
        headers['Idempotency-Key'] = str(uuid4())
        conflict = client.put('/api/v1/research/watch-pool', json=body, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()['error']['code'] == 'WATCH_POOL_VERSION_CONFLICT'
        assert len(client.get('/api/v1/research/watch-pool/audit').json()['data']) == 1
