"""Simulation validates default buy lots at every order and draft entry point."""
import json

import pytest

from trade_app.research.draft_models import SimOrderDraft
from trade_app.research.models import ResearchRun
from trade_app.trading.sim_models import SimOrder
from test_portfolio import context
from test_wyckoff_research_api import client_for, data, dataset, write


def sim_root(client):
    account = data(write(client, '/sim-accounts', {
        'name': '整手买入', 'initial_capital': '10000', 'start_date': '2025-01-01'}))
    return '/sim-accounts/' + account['id']


def buy_body(quantity):
    return {'symbol': '600000', 'side': 'buy', 'quantity': quantity, 'limit_price': '10',
            'signal_date': '2025-01-01', 'submit_date': '2025-01-01'}


@pytest.mark.parametrize('quantity', [1, 99, 101, 150])
def test_invalid_buy_lot_rejects_without_reserving_cash(tmp_path, quantity):
    with client_for(tmp_path) as client:
        root = sim_root(client)
        before = data(client.get('/api/v1' + root + '/portfolio'))
        rejected = write(client, root + '/orders', buy_body(quantity))
        assert rejected.status_code == 400
        assert rejected.json()['error']['code'] == 'SIM_INVALID_LOT_SIZE'
        assert data(client.get('/api/v1' + root + '/portfolio')) == before
        assert data(client.get('/api/v1' + root + '/orders')) == []


def test_invalid_old_pending_buy_cannot_fill_and_odd_sell_remains_valid(tmp_path):
    with client_for(tmp_path) as client:
        root = sim_root(client)
        order = data(write(client, root + '/orders', buy_body(100)))
        with client.app.state.db_factory.begin() as session:
            session.get(SimOrder, order['id']).quantity = 1
        before = data(client.get('/api/v1' + root + '/portfolio'))
        rejected = write(client, root + '/orders/' + order['id'] + '/fill', {
            'expected_revision': 1, 'fill_date': '2025-01-01', 'fill_price': '10'})
        assert rejected.status_code == 400
        assert rejected.json()['error']['code'] == 'SIM_INVALID_LOT_SIZE'
        assert data(client.get('/api/v1' + root + '/portfolio')) == before
        with client.app.state.db_factory.begin() as session:
            session.get(SimOrder, order['id']).quantity = 100
        data(write(client, root + '/orders/' + order['id'] + '/fill', {
            'expected_revision': 1, 'fill_date': '2025-01-01', 'fill_price': '10'}))
        data(write(client, root + '/settle', {'to_date': '2025-01-02'}))
        sell = data(write(client, root + '/orders', {
            'symbol': '600000', 'side': 'sell', 'quantity': 1, 'limit_price': '10',
            'signal_date': '2025-01-02', 'submit_date': '2025-01-02'}))
        filled = data(write(client, root + '/orders/' + sell['id'] + '/fill', {
            'expected_revision': 1, 'fill_date': '2025-01-02', 'fill_price': '10'}))
        assert filled['portfolio']['positions'][0]['quantity'] == 99


def test_draft_create_edit_preview_and_batch_cannot_bypass_buy_lots(tmp_path):
    with client_for(tmp_path) as client:
        root = sim_root(client)
        ds = dataset(client, context(count=2, start=0)['datasets'][0]['bars'])
        run_id = 'a' * 64
        with client.app.state.db_factory.begin() as session:
            session.add(ResearchRun(id=run_id, dataset_id=ds['id'],
                strategy_id='relative_strength_breakout_v1', strategy_version='test',
                decision_at='2025-01-01T15:00:00+00:00', strict=0, params_json='{}',
                result_json=json.dumps({'status': 'computed', 'signal': True, 'draft_eligible': True,
                    'source_date': '2025-01-01', 'candidate': {'symbol': '600000'}}),
                created_at='2025-01-01T15:00:00+00:00'))
        body = {'source_run_id': run_id, 'quantity': 101, 'limit_price': '10'}
        assert write(client, root + '/drafts', body).status_code == 400
        assert data(client.get('/api/v1' + root + '/drafts')) == []
        draft = data(write(client, root + '/drafts', {**body, 'quantity': 100}))
        path = root + '/drafts/' + draft['id']
        assert write(client, path, {'expected_revision': 1, 'quantity': 101, 'limit_price': '10'}, 'PUT').status_code == 400
        preview = data(client.get('/api/v1' + path + '/preview'))
        assert preview['can_submit']
        with client.app.state.db_factory.begin() as session:
            session.get(SimOrderDraft, draft['id']).quantity = 101
        assert client.get('/api/v1' + path + '/preview').status_code == 400
        assert client.get('/api/v1' + root + '/drafts/preview-batch', params={
            'draft_id': draft['id']}).status_code == 400
        submission = f"/submit?expected_revision=1&expected_wallet_revision={preview['wallet_revision']}&expected_config_version={preview['config_version']}"
        assert write(client, path + submission, {}).status_code == 400
        batch = {'drafts': [{'id': draft['id'], 'expected_revision': 1}],
                 'expected_wallet_revision': preview['wallet_revision'],
                 'expected_config_version': preview['config_version']}
        assert write(client, root + '/drafts/submit-batch', batch).status_code == 400
        assert data(client.get('/api/v1' + root + '/orders')) == []
        repaired = data(write(client, path, {
            'expected_revision': 1, 'quantity': 100, 'limit_price': '10'}, 'PUT'))
        assert repaired['revision'] == 2
        batch['drafts'][0]['expected_revision'] = 2
        assert len(data(write(client, root + '/drafts/submit-batch', batch))['orders']) == 1
