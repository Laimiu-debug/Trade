from test_wyckoff_research_api import client_for, data, dataset, write


def test_event_store_tasks_preserve_checkpoint_resume_routes(tmp_path):
    with client_for(tmp_path) as client:
        sample = dataset(client)
        body = {'dataset_ids': [sample['id']], 'start_date': sample['last_date'],
                'end_date': sample['last_date'], 'window_days': [60], 'strict': True}
        preview = data(write(client, '/research/event-store/preview', body))
        job = data(write(client, '/research/event-store/jobs', {
            **body, 'expected_preview_sha256': preview['input_sha256']}))
        def row():
            return next(item for item in data(client.get('/api/v1/tasks'))['items'] if item['id'] == job['id'])
        task = row()
        assert task['kind'] == 'event_store' and task['page'] == 'events'
        assert task['progress'] == {'done': 0, 'total': 1}
        assert set(task['actions']) == {'cancel'}
        assert not {'request_json', 'versions', 'bars', 'result_json'} & task.keys()
        data(write(client, task['actions']['cancel'], {}))
        assert row()['state'] == 'cancelled' and set(row()['actions']) == {'resume'}
        data(write(client, row()['actions']['resume'], {}))
        assert row()['state'] == 'queued'


def test_walk_forward_tasks_expose_checkpoint_controls_without_frozen_payloads(tmp_path):
    from test_walk_forward import create
    with client_for(tmp_path) as client:
        job, _, _ = create(client, tmp_path)
        def row():
            return next(item for item in data(client.get('/api/v1/tasks'))['items'] if item['id'] == job['id'])
        task = row()
        assert task['kind'] == 'walk_forward' and task['pause_supported']
        assert task['progress'] == {'done': 0, 'total': 6}
        assert set(task['actions']) == {'pause', 'cancel'}
        assert not {'plan', 'input_json', 'summary_json', 'result_json', 'bars'} & task.keys()
        data(write(client, task['actions']['pause'], {}))
        task = row()
        assert task['state'] == 'paused' and set(task['actions']) == {'resume', 'cancel'}
        data(write(client, task['actions']['resume'], {}))
        data(write(client, row()['actions']['cancel'], {}))
        task = row()
        assert task['state'] == 'cancelled' and set(task['actions']) == {'retry'}
        assert task['progress']['done'] == task['progress']['total']
        data(write(client, task['actions']['retry'], {}))
        assert row()['state'] == 'queued'


def test_task_summary_filters_account_and_preserves_real_cancel_routes(tmp_path):
    with client_for(tmp_path) as client:
        account = data(write(client, '/accounts', {'name': '任务范围'}))
        other = data(write(client, '/accounts', {'name': '其他范围'}))
        sample = dataset(client)
        catalog = data(client.get('/api/v1/research/strategies'))
        strategy = next(item['id'] for item in catalog if item['signal_params'] is not None)
        backtest = data(write(client, '/backtests', {'dataset_id': sample['id'], 'strategy_id': strategy}))
        sync = data(write(client, '/market/sync-jobs', {'symbols': ['sh600000'], 'start_date': '2026-01-01', 'end_date': '2026-02-01'}))
        session = data(write(client, '/ai/sessions', {'title': '内部文本', 'account_id': account['id']}))
        run = data(write(client, '/ai/sessions/' + session['id'] + '/runs?account_id=' + account['id'],
                         {'message': '不出现在任务列表中的私有正文', 'config_revision': 0}))
        result = data(client.get('/api/v1/tasks?account_id=' + account['id']))
        assert {backtest['id'], sync['id'], run['id']} <= {item['id'] for item in result['items']}
        assert '不出现在任务列表中的私有正文' not in str(result)
        assert all(not item['pause_supported'] for item in result['items'])
        assert run['id'] not in {item['id'] for item in data(client.get('/api/v1/tasks'))['items']}
        assert run['id'] not in {item['id'] for item in data(client.get('/api/v1/tasks?account_id=' + other['id']))['items']}
        target = next(item for item in result['items'] if item['id'] == backtest['id'])
        data(write(client, target['actions']['cancel'], {}))
        refreshed = data(client.get('/api/v1/tasks?account_id=' + account['id']))
        target = next(item for item in refreshed['items'] if item['id'] == backtest['id'])
        assert target['state'] == 'cancelled' and 'cancel' not in target['actions'] and 'retry' in target['actions']
        assert client.get('/api/v1/tasks?account_id=does-not-exist').status_code == 404
