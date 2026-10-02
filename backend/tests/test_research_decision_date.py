"""A signal's source bar date cannot override its later research decision date."""
import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_app.main import create_app
from trade_app.research.draft_models import SimOrderDraft
from trade_app.research.models import ResearchRun


@contextmanager
def _client(data_dir):
    app = create_app(data_dir, auto_rebuild=False)
    lifespan = app.router.lifespan_context(app)
    with asyncio.Runner() as runner:
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app) as client:
                client.headers['X-CSRF-Token'] = client.get('/api/v1/session').json()['data']['csrf_token']
                yield client
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


def _post(client, path, body=None):
    return client.post(path, json=body or {}, headers={'Idempotency-Key': str(uuid4())})


def _data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def _dataset(client):
    bars = []
    for index in range(45):
        day = date(2025, 1, 8) - timedelta(days=44 - index)
        close = 10 + index * 0.2
        bars.append({'event_date': day.isoformat(), 'open': f'{close - 0.1:.4f}',
                     'high': f'{close + 0.5:.4f}', 'low': f'{close - 0.5:.4f}',
                     'close': f'{close:.4f}', 'volume': 1000 + index * 30,
                     'available_at': datetime.combine(day + timedelta(days=1),
                         datetime.min.time(), timezone.utc).isoformat()})
    # The Jan 8 bar arrives late. It cannot be used by the Jan 10 12:00Z run,
    # while the 18:00Z run can use it only after Shanghai's date has rolled over.
    bars[-1]['available_at'] = '2025-01-10T17:00:00+00:00'
    return _data(_post(client, '/api/v1/market/datasets', {
        'symbol': '600000', 'bars': bars, 'adjustment': 'none'}))


def _run(client, dataset, decision_at):
    result = _data(_post(client, '/api/v1/research/runs', {
        'dataset_id': dataset['id'], 'strategy_id': 'relative_strength_breakout_v1',
        'decision_at': decision_at, 'strict': True,
        'params': {'min_vol_slope20': '0'}}))
    assert result['result']['status'] == 'computed'
    assert result['result']['signal'] is True
    return result


def _account(client, name='决策时间核对'):
    row = _data(_post(client, '/api/v1/sim-accounts', {
        'name': name, 'initial_capital': '10000', 'start_date': '2025-01-10'}))
    return row['id'], f"/api/v1/sim-accounts/{row['id']}"


def _draft(client, root, run):
    return _data(_post(client, root + '/drafts', {
        'source_run_id': run['id'], 'quantity': 100, 'limit_price': '20'}))


def _submit(client, root, draft, preview):
    query = (f"expected_revision={draft['revision']}"
             f"&expected_wallet_revision={preview['wallet_revision']}"
             f"&expected_config_version={preview['config_version']}")
    return _post(client, root + f"/drafts/{draft['id']}/submit?{query}")


def _batch_body(drafts, preview):
    return {'drafts': [{'id': draft['id'], 'expected_revision': draft['revision']} for draft in drafts],
            'expected_wallet_revision': preview['wallet_revision'],
            'expected_config_version': preview['config_version']}


def test_delayed_research_signal_waits_until_shanghai_decision_day(tmp_path):
    with _client(tmp_path) as client:
        dataset = _dataset(client)
        run = _run(client, dataset, '2025-01-10T18:00:00Z')
        assert run['result']['source_date'] == '2025-01-08'
        _account_id, root = _account(client)
        draft = _draft(client, root, run)
        preview = _data(client.get(root + f"/drafts/{draft['id']}/preview"))
        assert preview['signal_date'] == '2025-01-08'
        assert preview['decision_date'] == '2025-01-11'
        assert preview['submit_date'] == '2025-01-10'
        assert preview['cash_gap'] == '0.00'
        assert preview['eligible_date'] is False and preview['can_submit'] is False
        rejected = _submit(client, root, draft, preview)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()['error']['code'] == 'SIGNAL_DECISION_IN_FUTURE'
        assert _data(client.get(root + '/orders')) == []
        assert _data(client.get(root + '/drafts')) == [draft]
        unchanged = _data(client.get(root + f"/drafts/{draft['id']}/preview"))
        assert unchanged['wallet_revision'] == preview['wallet_revision']

        _data(_post(client, root + '/settle', {'to_date': '2025-01-11'}))
        ready = _data(client.get(root + f"/drafts/{draft['id']}/preview"))
        assert ready['decision_date'] == ready['submit_date'] == '2025-01-11'
        assert ready['eligible_date'] is True and ready['can_submit'] is True
        submitted = _data(_submit(client, root, draft, ready))
        assert submitted['draft']['status'] == 'submitted'
        assert submitted['order']['submit_date'] == '2025-01-11'
        assert len(_data(client.get(root + '/orders'))) == 1


def test_mixed_batch_cannot_submit_future_decision_or_foreign_account_drafts(tmp_path):
    with _client(tmp_path) as client:
        dataset = _dataset(client)
        early = _run(client, dataset, '2025-01-10T12:00:00Z')
        later = _run(client, dataset, '2025-01-10T18:00:00Z')
        assert early['result']['source_date'] == '2025-01-07'
        assert later['result']['source_date'] == '2025-01-08'
        _account_id, root = _account(client)
        _foreign_id, foreign_root = _account(client, '隔离账户')
        drafts = [_draft(client, root, run) for run in (early, later)]
        query = [('draft_id', draft['id']) for draft in drafts]
        preview = _data(client.get(root + '/drafts/preview-batch', params=query))
        assert [row['can_submit'] for row in preview['drafts']] == [True, False]
        assert preview['can_submit'] is False and preview['cash_gap'] == '0.00'
        rejected = _post(client, root + '/drafts/submit-batch', _batch_body(drafts, preview))
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()['error']['code'] == 'BATCH_PREVIEW_NOT_READY'
        assert _data(client.get(root + '/orders')) == []
        assert all(draft['status'] == 'draft' and draft['revision'] == 1
                   for draft in _data(client.get(root + '/drafts')))

        # Foreign account access remains denied before any preview/submit data is returned.
        assert client.get(foreign_root + f"/drafts/{drafts[0]['id']}/preview").status_code == 404
        assert client.get(foreign_root + '/drafts/preview-batch', params=query).status_code == 404
        assert _submit(client, foreign_root, drafts[0], preview).status_code == 404
        foreign_submit = _post(client, foreign_root + '/drafts/submit-batch', _batch_body(drafts, preview))
        assert foreign_submit.status_code == 404, foreign_submit.text
        assert _data(client.get(foreign_root + '/orders')) == []

        _data(_post(client, root + '/settle', {'to_date': '2025-01-11'}))
        ready = _data(client.get(root + '/drafts/preview-batch', params=query))
        assert ready['can_submit'] is True
        accepted = _data(_post(client, root + '/drafts/submit-batch', _batch_body(drafts, ready)))
        assert len(accepted['orders']) == 2
        assert all(order['submit_date'] == '2025-01-11' for order in accepted['orders'])
        assert len(_data(client.get(root + '/orders'))) == 2
        assert _data(client.get(foreign_root + '/orders')) == []


def test_old_saved_draft_rechecks_source_decision_date_after_restart(tmp_path):
    with _client(tmp_path) as client:
        dataset = _dataset(client)
        account_id, root = _account(client, '旧草稿')
        # Seed the pre-fix persisted shape: the result has no draft_eligible or
        # decision_date field, and the draft stores only its old source date.
        # The source ResearchRun.decision_at was already part of the old schema.
        with client.app.state.db_factory.begin() as session:
            session.add(ResearchRun(
                id='old-decision-run', dataset_id=dataset['id'],
                strategy_id='relative_strength_breakout_v1', strategy_version='1.0.0-alpha',
                decision_at='2025-01-10T18:00:00Z', strict=1, params_json='{}',
                result_json=json.dumps({'status': 'computed', 'signal': True,
                    'source_date': '2025-01-08', 'candidate': {'symbol': '600000'}}),
                created_at='2025-01-10T18:00:01Z'))
            session.flush()
            session.add(SimOrderDraft(
                id='old-saved-draft', account_id=account_id, source_run_id='old-decision-run',
                symbol='600000', signal_date='2025-01-08', quantity=100,
                limit_price_units=200000, status='draft', order_id=None, revision=1,
                created_at='2025-01-10T18:00:02Z', updated_at='2025-01-10T18:00:02Z'))

    with _client(tmp_path) as client:
        draft = _data(client.get(root + '/drafts'))[0]
        preview = _data(client.get(root + '/drafts/old-saved-draft/preview'))
        assert preview['decision_date'] == '2025-01-11'
        assert preview['signal_date'] == '2025-01-08'
        assert preview['can_submit'] is False
        assert _submit(client, root, draft, preview).status_code == 409
        assert _data(client.get(root + '/orders')) == []
        _data(_post(client, root + '/settle', {'to_date': '2025-01-11'}))
        ready = _data(client.get(root + '/drafts/old-saved-draft/preview'))
        assert ready['can_submit'] is True
        assert _data(_submit(client, root, draft, ready))['draft']['status'] == 'submitted'
