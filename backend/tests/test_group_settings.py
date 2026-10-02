import json
import os
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from trade_app.api import settings_service as settings
from trade_app.market.calendar_service import get_calendar, plan_dates, save_calendar, calendar_audit
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError
from trade_app.trading.service import create_account
from trade_app.trading import simulation
from trade_app.trading.sim_models import SimWallet


@pytest.fixture
def db(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        yield tmp_path, factory
    finally:
        engine.dispose()


def reset(session, group, account=None):
    current = settings.get_group(session, group, account)
    preview = settings.preview(session, group, account, current['revision'])
    return settings.apply_preview(session, group, account, current['revision'], preview['preview_sha256'])


def test_read_and_default_preview_do_not_write_and_source_order_is_revisioned(db):
    _, factory = db
    with factory.begin() as session:
        original = settings.get_group(session, 'market_sources')
        assert original['revision'] == 0 and original['scope'] == 'global_data_directory'
        preview = settings.preview(session, 'market_sources', None, 0)
        assert preview['diff'] == [] and not list(session.scalars(select(AuditEvent)))
        custom = {'provider': 'auto', 'provider_order': ['akshare', 'baostock']}
        imported = settings.preview(session, 'market_sources', None, 0, custom)
        saved = settings.apply_preview(session, 'market_sources', None, 0, imported['preview_sha256'], value=custom)
        assert saved['value'] == custom and saved['revision'] == 1
        with pytest.raises(TradeError) as error:
            settings.apply_preview(session, 'market_sources', None, 0, preview['preview_sha256'])
        assert error.value.code == 'SETTINGS_REVISION_CONFLICT'
        assert settings.history(session, 'market_sources')[0]['operation'] == 'legacy_import'


def test_fee_reset_affects_only_selected_account_and_group(db):
    _, factory = db
    with factory.begin() as session:
        one = create_account(session, name='one')['id']
        two = create_account(session, name='two')['id']
        custom = {**settings.DEFAULT_FEE_CONFIG, 'commission_rate': '0.0008'}
        settings.save_group(session, 'fees', one, 1, custom)
        settings.save_group(session, 'fees', two, 1, {**custom, 'minimum_commission': '9.00'})
        before_two = settings.get_group(session, 'fees', two)
        before_targets = settings.get_group(session, 'targets', one)
        before_ai = settings.get_group(session, 'ai')
        preview = settings.preview(session, 'fees', one, 2)
        with pytest.raises(TradeError) as error:
            settings.apply_preview(session, 'fees', two, 2, preview['preview_sha256'])
        assert error.value.code == 'SETTINGS_PREVIEW_CHANGED'
        after = settings.apply_preview(session, 'fees', one, 2, preview['preview_sha256'])
        assert after['value'] == settings.DEFAULT_FEE_CONFIG and after['revision'] == 3
        assert settings.get_group(session, 'fees', two) == before_two
        assert settings.get_group(session, 'targets', one) == before_targets
        assert settings.get_group(session, 'ai') == before_ai
        assert all(row['after']['account_id'] == one for row in settings.history(session, 'fees', one))


def test_sim_fee_reset_preserves_slippage_cash_buffer_money_and_frozen_guard(db):
    _, factory = db
    with factory.begin() as session:
        config = {**simulation.DEFAULT_CONFIG, 'commission_rate': '0.0009', 'cash_buffer': '100.00', 'slippage_rate': '0.001'}
        account = simulation.create_sim_account(session, {'name': 'sim', 'initial_capital': '10000', 'start_date': '2025-01-01', 'config': config})
        wallet = session.get(SimWallet, account['id'])
        before_cash = wallet.cash_minor
        reset(session, 'fees', account['id'])
        new = json.loads(wallet.config_json)
        assert new['cash_buffer'] == '100.00' and new['slippage_rate'] == '0.001'
        assert wallet.cash_minor == before_cash and new['commission_rate'] == settings.DEFAULT_FEE_CONFIG['commission_rate']
        wallet.frozen = 1
        with pytest.raises(TradeError) as error:
            reset(session, 'fees', account['id'])
        assert error.value.code == 'SETTINGS_READONLY'


def test_calendar_reset_retains_revision_unknown_coverage_and_audit(db):
    _, factory = db
    with factory.begin() as session:
        current = save_calendar(session, {'expected_revision': 0, 'source': 'verified local schedule',
            'start_date': '2025-01-01', 'end_date': '2025-01-02',
            'days': [{'date': '2025-01-01', 'is_open': False}, {'date': '2025-01-02', 'is_open': True}]})
        assert plan_dates(session, '2025-01-01')['suggested_date'] == '2025-01-02'
        after = reset(session, 'calendar')
        assert after['revision'] == 2 and after['value'] == settings.CALENDAR_DEFAULTS
        assert get_calendar(session)['source'] is None
        assert plan_dates(session, '2025-01-01')['suggested_date'] is None
        assert plan_dates(session, '2025-01-01')['calendar']['start_date'] is None
        assert {item['snapshot']['revision'] for item in calendar_audit(session)} == {1, 2}
        with pytest.raises(TradeError) as error:
            settings.save_group(session, 'calendar', None, 0, {key: current[key] for key in settings.CALENDAR_DEFAULTS})
        assert error.value.code == 'SETTINGS_REVISION_CONFLICT'


def test_ai_reset_never_stores_key_values_or_changes_environment(db, monkeypatch):
    _, factory = db
    monkeypatch.setenv('TRADE_AI_TEXT_KEY', 'secret-value-never-in-output')
    with factory.begin() as session:
        value = deepcopy(settings.ai.DEFAULT_CONFIG)
        value['text'].update(base_url='https://example.com/v1', model='test-model')
        before = settings.save_group(session, 'ai', None, 0, value)
        assert before['credential_status']['text']
        preview = settings.preview(session, 'ai', None, 1)
        after = reset(session, 'ai')
        assert after['value'] == settings.ai.DEFAULT_CONFIG
        assert os.environ['TRADE_AI_TEXT_KEY'] == 'secret-value-never-in-output'
        assert 'secret-value-never-in-output' not in settings.encode([before, preview, after, settings.history(session, 'ai')])
        value['text']['api_key'] = 'secret-value-never-in-output'
        with pytest.raises(TradeError): settings.save_group(session, 'ai', None, 2, value)


@pytest.mark.parametrize('value', [
    {'provider': 'unknown', 'provider_order': ['baostock', 'akshare']},
    {'provider': 'auto', 'provider_order': ['akshare', 'akshare']},
    {'provider': 'auto', 'provider_order': ['akshare']},
    {'provider': 'auto', 'provider_order': ['akshare', 'baostock'], 'other': True},
])
def test_source_config_strict_validation(db, value):
    _, factory = db
    with factory.begin() as session, pytest.raises(TradeError):
        settings.save_group(session, 'market_sources', None, 0, value)


def test_scope_and_preview_hash_cannot_be_substituted(db):
    _, factory = db
    with factory.begin() as session:
        account = create_account(session, name='scope')['id']
        with pytest.raises(TradeError) as error: settings.get_group(session, 'ai', account)
        assert error.value.code == 'SETTINGS_SCOPE_MISMATCH'
        with pytest.raises(TradeError) as error: settings.get_group(session, 'fees')
        assert error.value.code == 'SETTINGS_ACCOUNT_REQUIRED'
        with pytest.raises(TradeError): settings.get_group(session, 'targets', 'missing')
        preview = settings.preview(session, 'ai', None, 0)
        with pytest.raises(TradeError) as error: settings.apply_preview(session, 'market_sources', None, 0, preview['preview_sha256'])
        assert error.value.code == 'SETTINGS_PREVIEW_CHANGED'
        with pytest.raises(TradeError): settings.preview(session, 'market_sources', None, False)


def test_group_settings_and_history_backup_roundtrip(tmp_path):
    path = tmp_path / 'original'
    engine, factory = open_database(path)
    with factory.begin() as session:
        saved = settings.save_group(session, 'market_sources', None, 0, {'provider': 'akshare', 'provider_order': ['akshare', 'baostock']})
        reset(session, 'calendar')
    engine.dispose()
    restored = tmp_path / 'restored'
    restore_to_new_directory(create_backup(path), restored)
    engine, factory = open_database(restored)
    try:
        with factory() as session:
            assert settings.get_group(session, 'market_sources') == saved
            assert len(settings.history(session, 'market_sources')) == 1
            assert get_calendar(session)['revision'] == 1 and not get_calendar(session)['days']
    finally:
        engine.dispose()


def test_routes_scope_csrf_idempotency_preview_is_read_only(tmp_path):
    from test_watch_pool import client_at
    from trade_app.api.settings_routes import router
    with client_at(tmp_path) as (app, client):
        if not any(getattr(route, 'path', '') == '/api/v1/settings/groups/{group}' for route in app.routes):
            app.router.routes[0:0] = router.routes
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        path = '/api/v1/settings/groups/market_sources'
        value = {'provider': 'auto', 'provider_order': ['akshare', 'baostock']}
        body = {'expected_revision': 0, 'value': value}
        assert client.put(path, json=body).status_code == 403
        preview = client.post(path + '/preview', json=body, headers=headers).json()['data']
        assert client.get(path).json()['data']['revision'] == 0
        body['preview_sha256'] = preview['preview_sha256']
        saved = client.post(path + '/import', json=body, headers=headers)
        assert saved.status_code == 200, saved.text
        assert client.post(path + '/import', json=body, headers=headers).json() == saved.json()
        stale = client.post(path + '/reset', json={'expected_revision': 0, 'preview_sha256': preview['preview_sha256']},
                            headers={**headers, 'Idempotency-Key': str(uuid4())})
        assert stale.status_code == 409
        assert len(client.get(path + '/audit').json()['data']) == 1


def test_online_api_uses_current_defaults_but_replay_keeps_original_result(tmp_path, monkeypatch):
    from test_watch_pool import client_at
    from trade_app.api import routes
    captured = []
    def prepare(name):
        def call(_session, _directory, body):
            captured.append((name, deepcopy(body)))
            return {'marker': name}
        return call
    for name in ('akshare', 'baostock'):
        monkeypatch.setattr(routes, 'prepare_' + name + '_sync', prepare(name))
        monkeypatch.setattr(routes, 'save_' + name + '_sync', lambda _session, _directory, value: value)
    with client_at(tmp_path) as (app, client):
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        with app.state.db_factory.begin() as session:
            settings.save_group(session, 'market_sources', None, 0, {'provider': 'auto', 'provider_order': ['akshare', 'baostock']})
        body = {'symbol': '600000', 'start_date': '2025-01-01', 'end_date': '2025-01-10'}
        first = client.post('/api/v1/market/online-sync', json=body, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()['data']['actual_provider'] == 'akshare'
        assert first.json()['data']['source_defaults']['revision'] == 1
        with app.state.db_factory.begin() as session:
            settings.save_group(session, 'market_sources', None, 1, {'provider': 'baostock', 'provider_order': ['baostock', 'akshare']})
        assert client.post('/api/v1/market/online-sync', json=body, headers=headers).json() == first.json()
        assert len(captured) == 1
        second = client.post('/api/v1/market/online-sync', json=body, headers={**headers, 'Idempotency-Key': str(uuid4())})
        assert second.json()['data']['actual_provider'] == 'baostock'
        explicit = client.post('/api/v1/market/online-sync', json={**body, 'provider': 'auto', 'provider_order': ['akshare', 'baostock']},
                               headers={**headers, 'Idempotency-Key': str(uuid4())})
        assert explicit.json()['data']['actual_provider'] == 'akshare'
        assert 'source_defaults' not in explicit.json()['data']


def test_batch_default_snapshot_replay_and_failed_retry_ignore_later_config(tmp_path):
    from test_watch_pool import client_at
    from trade_app.market.models import MarketSyncJob
    with client_at(tmp_path) as (app, client):
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        with app.state.db_factory.begin() as session:
            settings.save_group(session, 'market_sources', None, 0, {'provider': 'auto', 'provider_order': ['akshare', 'baostock']})
        body = {'symbols': ['600000'], 'start_date': '2025-01-01', 'end_date': '2025-01-10'}
        first = client.post('/api/v1/market/sync-jobs', json=body, headers=headers)
        assert first.status_code == 200, first.text
        job_id = first.json()['data']['id']
        with app.state.db_factory.begin() as session:
            row = session.get(MarketSyncJob, job_id)
            frozen = json.loads(row.request_json)
            assert frozen['provider_order'] == ['akshare', 'baostock'] and frozen['source_defaults']['revision'] == 1
            row.state = 'partial_failed'
            row.results_json = json.dumps([{'symbol': 'sh600000', 'state': 'failed', 'errors': []}])
            settings.save_group(session, 'market_sources', None, 1, {'provider': 'baostock', 'provider_order': ['baostock', 'akshare']})
        assert client.post('/api/v1/market/sync-jobs', json=body, headers=headers).json() == first.json()
        retried = client.post(f'/api/v1/market/sync-jobs/{job_id}/retry-failed', json={},
                             headers={**headers, 'Idempotency-Key': str(uuid4())})
        assert retried.status_code == 200, retried.text
        with app.state.db_factory() as session:
            new = json.loads(session.get(MarketSyncJob, retried.json()['data']['id']).request_json)
            assert new == frozen
