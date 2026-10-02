import asyncio
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from trade_app.main import create_app
from trade_app.research.event_profiles import get_system_profile
from trade_app.research.wyckoff_domain import calculate_snapshot
from test_wyckoff_domain import event_rich_bars


@contextmanager
def client_for(path):
    app = create_app(path, auto_rebuild=False)
    lifespan = app.router.lifespan_context(app)
    with asyncio.Runner() as runner:
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app) as client:
                client.headers['X-CSRF-Token'] = client.get('/api/v1/session').json()['data']['csrf_token']
                yield client
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


def write(client, path, body, method='POST', key=None):
    return client.request(method, '/api/v1' + path, json=body,
                          headers={'Idempotency-Key': key or str(uuid4())})


def data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def dataset(client, bars=None):
    values = deepcopy(bars if bars is not None else event_rich_bars())
    for row in values:
        for key in ('open', 'close', 'high', 'low'):
            row[key] = str(row[key])
    return data(write(client, '/market/datasets', {'symbol': 'sh600000', 'bars': values}))


def custom_payload(name='自定义事件模板'):
    profile = get_system_profile()
    return {key: name if key == 'name' else value for key, value in profile.items()
            if key not in ('profile_id', 'is_system', 'updated_at')}


def test_profile_crud_readonly_conflict_apply_delete_history_and_restart(tmp_path):
    with client_for(tmp_path) as client:
        catalog = data(client.get('/api/v1/research/event-profiles'))
        assert len(catalog['profiles']) == 3
        system_id = catalog['active_profile_id']
        body = {'profile': custom_payload()}
        key = str(uuid4())
        profile = data(write(client, '/research/event-profiles', body, key=key))
        assert data(write(client, '/research/event-profiles', body, key=key)) == profile
        path = '/research/event-profiles/' + profile['profile_id']
        assert profile['revision'] == 1 and not profile['is_system']
        assert write(client, '/research/event-profiles/' + system_id,
                     {'expected_revision': 1, **body}, 'PUT').status_code == 409
        selected = data(write(client, path + '/apply', {'expected_revision': 1, 'expected_active_revision': 0}))
        assert selected['active_revision'] == 1
        assert write(client, path + '/apply', {'expected_revision': 1, 'expected_active_revision': 0}).status_code == 409
        changed = data(write(client, path, {'expected_revision': 1, 'profile': custom_payload('已改名')}, 'PUT'))
        assert changed['revision'] == 2 and changed['sha256'] == profile['sha256']
        assert write(client, path, {'expected_revision': 1, **body}, 'PUT').status_code == 409
        invalid = custom_payload()
        invalid['rule_values'].append({'key': 'unknown_rule', 'value': 1})
        assert write(client, path, {'expected_revision': 2, 'profile': invalid}, 'PUT').status_code == 400
        assert write(client, path, {'expected_revision': 2, 'expected_active_revision': 0}, 'DELETE').status_code == 409
        restored = data(client.get('/api/v1/research/event-profiles'))
        assert next(row for row in restored['profiles'] if row['profile_id'] == profile['profile_id'])['revision'] == 2
        data(write(client, path, {'expected_revision': 2, 'expected_active_revision': 1}, 'DELETE'))
        history = data(client.get('/api/v1' + path + '/history'))
        assert [row['action'] for row in history] == ['delete', 'update', 'apply', 'create']
    with client_for(tmp_path) as client:
        final = data(client.get('/api/v1/research/event-profiles'))
        assert final['active_profile_id'] == system_id and final['active_revision'] == 2
        assert len(final['profiles']) == 3
        assert len(data(client.get('/api/v1' + path + '/history'))) == 4


@pytest.mark.parametrize('strategy_id', ['wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'])
def test_event_snapshot_is_point_in_time_and_inspectable(strategy_id, tmp_path):
    bars = event_rich_bars()
    with client_for(tmp_path) as client:
        ds = dataset(client)
        cutoff = bars[84]['event_date']
        body = {'dataset_id': ds['id'], 'strategy_id': strategy_id,
                'decision_at': cutoff + 'T09:00:00+00:00', 'strict': True}
        run = data(write(client, '/research/runs', body))
        expected = calculate_snapshot(bars[:85], profile=get_system_profile())
        assert run['result']['indicator'] == expected['snapshot']
        assert run['result']['event_age_days'] == expected['event_age_days']
        assert run['result']['indicator']['event_confirmation_map']['JOC'] == 'pending'
        assert run['result']['observed_bars'] == 85
        assert data(write(client, '/research/runs', body))['id'] == run['id']
        later = data(write(client, '/research/runs', {**body, 'decision_at': bars[-1]['event_date'] + 'T09:00:00+00:00'}))
        assert later['id'] != run['id']
        assert later['result']['indicator']['event_confirmation_map']['JOC'] == 'confirmed'
        assert data(client.get('/api/v1/research/runs/' + run['id'])) == run
        assert later['result']['score'] is None
        assert later['result']['evaluation']['entry_quality_score'] == 71.9
        assert len(later['result']['evaluation']['checks']) >= 7


def test_profile_revision_frozen_in_identity_and_old_result_survives_deletion(tmp_path):
    with client_for(tmp_path) as client:
        ds = dataset(client)
        p = data(write(client, '/research/event-profiles', {'profile': custom_payload()}))
        path = '/research/event-profiles/' + p['profile_id']
        body = {'dataset_id': ds['id'], 'strategy_id': 'wyckoff_trend_v1',
                'decision_at': '2025-06-01T09:00:00+00:00', 'event_profile_id': p['profile_id'],
                'event_profile_revision': 1}
        first = data(write(client, '/research/runs', body))
        data(write(client, path, {'expected_revision': 1, 'profile': custom_payload('第二版本')}, 'PUT'))
        assert write(client, '/research/runs', body).status_code == 409
        second = data(write(client, '/research/runs', {**body, 'event_profile_revision': 2}))
        assert first['id'] != second['id']
        assert first['result']['event_profile']['sha256'] == second['result']['event_profile']['sha256']
        data(write(client, path, {'expected_revision': 2, 'expected_active_revision': 0}, 'DELETE'))
        assert data(client.get('/api/v1/research/runs/' + first['id'])) == first
        assert write(client, '/research/runs', {**body, 'event_profile_revision': 2}).status_code == 404
        assert write(client, '/research/runs', {**body, 'strategy_id': 'wulong_cluster_v1'}).status_code == 400
    with client_for(tmp_path) as client:
        assert data(client.get('/api/v1/research/runs/' + first['id'])) == first


def test_passed_observation_without_positive_primary_event_cannot_be_bought(tmp_path):
    bars = event_rich_bars()[:62]
    with client_for(tmp_path) as client:
        ds = dataset(client, bars)
        run = data(write(client, '/research/runs', {'dataset_id': ds['id'],
            'strategy_id': 'wyckoff_trend_v1', 'decision_at': bars[-1]['event_date'] + 'T09:00:00+00:00',
            'params': {'min_score': '0', 'min_event_count': '0'}}))
        assert run['result']['signal'] is True
        assert run['result']['draft_eligible'] is False
        account = data(write(client, '/sim-accounts', {'name': '观察不下单', 'initial_capital': '10000',
                                                       'start_date': bars[-1]['event_date']}))
        response = write(client, '/sim-accounts/' + account['id'] + '/drafts',
                         {'source_run_id': run['id'], 'quantity': 100, 'limit_price': '10'})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'SIGNAL_NOT_ACTIONABLE'
