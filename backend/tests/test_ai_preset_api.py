"""HTTP contracts for explicit inference and reviewed parameter changes."""
import json
from uuid import uuid4

from trade_app.ai import service
from trade_app.platform.backup import restore_to_new_directory
from test_wyckoff_research_api import client_for, data, write


def configure(client):
    current = data(client.get('/api/v1/ai/config'))
    return data(write(client, '/ai/config', {
        'expected_revision': current['revision'],
        'text': {'base_url': 'http://127.0.0.1:8999/v1', 'model': 'local-test', 'secret_ref': ''},
        'vision': {'base_url': '', 'model': '', 'secret_ref': 'TRADE_AI_VISION_KEY'},
        'temperature': 0.3, 'max_tokens': 128, 'timeout_seconds': 10,
    }, 'PUT'))


def consume(client, run_id, suffix=''):
    response = write(client, f'/ai/runs/{run_id}/stream{suffix}', {})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[0]['type'] == 'started' and events[-1]['type'] == 'done'
    return events


def test_ai_routes_stream_scope_retry_usage_and_startup(tmp_path, monkeypatch):
    calls = []

    def provider(config, channel, messages, **_):
        calls.append((config, channel, messages))
        yield {'type': 'delta', 'content': '核对完成'}
        yield {'type': 'usage', 'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}
        yield {'type': 'end', 'finish_reason': 'stop'}

    monkeypatch.setattr(service, 'stream_chat', provider)
    with client_for(tmp_path) as client:
        config = configure(client)
        assert not calls
        account = data(write(client, '/accounts', {'name': 'AI 隔离账户'}))
        session = data(write(client, '/ai/sessions', {'title': '复盘核对', 'account_id': account['id']}))
        scope = '?account_id=' + account['id']
        path = '/ai/sessions/' + session['id']
        assert client.get('/api/v1' + path).status_code == 404
        prompt = {'message': '核对我的复盘', 'config_revision': config['revision'], 'context': {'account_date': '2026-01-01'}}
        preview = data(write(client, path + '/preview' + scope, prompt))
        prompt['expected_input_sha256'] = preview['input_sha256']
        key = str(uuid4())
        queued = data(write(client, path + '/runs' + scope, prompt, key=key))
        assert data(write(client, path + '/runs' + scope, prompt, key=key)) == queued
        assert not calls
        assert client.get('/api/v1/ai/runs/' + queued['id']).status_code == 404
        assert write(client, f'/ai/runs/{queued["id"]}/stream', {}).status_code == 404
        assert client.get(f'/api/v1/ai/runs/{queued["id"]}/stream' + scope).status_code in (404, 405)
        events = consume(client, queued['id'], scope)
        assert events[-1]['run']['status'] == 'completed'
        assert len(calls) == 1
        assert write(client, f'/ai/runs/{queued["id"]}/stream' + scope, {}).status_code == 409
        assert len(calls) == 1
        assert data(client.get('/api/v1/ai/usage' + scope))['total_tokens'] == 7
        assert data(client.get('/api/v1/ai/usage'))['call_count'] == 0
        assert data(client.get('/api/v1/ai/runs' + scope))[0]['output'] == '核对完成'
        assert len(data(client.get('/api/v1' + path + scope))['messages']) == 2
        # An unseen history change must invalidate an old prompt preview.
        assert write(client, path + '/runs' + scope, prompt).status_code == 409
        prompt.pop('expected_input_sha256')
        interrupted = data(write(client, path + '/runs' + scope, prompt))
        assert data(client.get('/api/v1/ai/config'))['revision'] == 1
    with client_for(tmp_path) as client:
        assert data(client.get('/api/v1/ai/runs/' + interrupted['id'] + scope))['status'] == 'interrupted'
        assert len(calls) == 1


def test_ai_csrf_unknown_fields_and_connection_test_are_explicit(tmp_path, monkeypatch):
    calls = []

    def provider(config, *_args, **_kwargs):
        calls.append(config)
        yield {'type': 'delta', 'content': 'OK'}
        yield {'type': 'end'}

    monkeypatch.setattr(service, 'stream_chat', provider)
    with client_for(tmp_path) as client:
        config = configure(client)
        assert write(client, '/ai/config', {'api_key': 'never-store-this'}, 'PUT').status_code == 422
        run = data(write(client, '/ai/test-connection', {'config_revision': config['revision'], 'channel': 'text'}))
        assert not calls and run['status'] == 'queued'
        response = client.post('/api/v1/ai/runs/' + run['id'] + '/stream', json={},
                               headers={'X-CSRF-Token': '', 'Idempotency-Key': str(uuid4())})
        assert response.status_code == 403 and not calls
        events = consume(client, run['id'])
        assert calls[0]['max_tokens'] == 8
        assert events[-1]['run']['usage']['total_tokens'] is None
        assert data(client.get('/api/v1/ai/usage'))['unknown_usage_count'] == 1
        queued = data(write(client, '/ai/test-connection', {'config_revision': config['revision']}))
        data(write(client, '/ai/runs/' + queued['id'] + '/cancel', {}))
        assert write(client, '/ai/runs/' + queued['id'] + '/stream', {}).status_code == 409
        assert len(calls) == 1


def test_preset_ai_proposal_explicit_apply_and_backup(tmp_path, monkeypatch):
    strategy = 'wyckoff_score_only'

    def provider(*_args, **_kwargs):
        yield {'type': 'delta', 'content': json.dumps({'strategy_id': strategy, 'params': {'min_score': '70'}})}
        yield {'type': 'end'}

    monkeypatch.setattr(service, 'stream_chat', provider)
    root = tmp_path / 'original'
    with client_for(root) as client:
        config = configure(client)
        # Lookup the actual catalog identifier; parameter proposals must use exact IDs.
        catalog = data(client.get('/api/v1/research/strategies'))
        strategy = next(item['id'] for item in catalog if 'score_only' in item['id'])
        preset = data(write(client, '/research/presets', {'name': '门槛研究', 'strategy_id': strategy,
                                                        'params': {'min_score': '60'}, 'favorite': True}))
        preset_path = '/research/presets/' + preset['id']
        share = data(client.get('/api/v1' + preset_path + '/export'))['share_code']
        preview = data(write(client, '/research/presets/preview-import', {'share_code': share}))
        assert preview['params'] == preset['params'] and not preview['saved']
        assert len(data(client.get('/api/v1/research/presets'))) == 1
        account = data(write(client, '/accounts', {'name': '参数来源账户'}))
        scope = '?account_id=' + account['id']
        session = data(write(client, '/ai/sessions', {'title': '参数建议', 'account_id': account['id']}))
        run = data(write(client, '/ai/sessions/' + session['id'] + '/runs' + scope,
                         {'message': '返回指定参数 JSON', 'config_revision': config['revision']}))
        source = {'preset_id': preset['id'], 'expected_revision': 1, 'source_ai_run_id': run['id'], 'account_id': account['id']}
        assert write(client, '/research/parameter-proposals', source).status_code == 409
        consume(client, run['id'], scope)
        assert data(client.get('/api/v1' + preset_path))['params']['min_score'] == '60'
        assert write(client, '/research/parameter-proposals', {**source, 'account_id': str(uuid4())}).status_code == 404
        proposal = data(write(client, '/research/parameter-proposals', source))
        assert proposal['status'] == 'pending'
        assert data(client.get('/api/v1' + preset_path))['revision'] == 1
        payload = {'expected_revision': 1, 'expected_proposal_hash': proposal['proposal_sha256']}
        apply_path = '/research/parameter-proposals/' + proposal['id'] + '/apply'
        result = data(write(client, apply_path, payload))
        assert result['preset']['params']['min_score'] == '70' and result['preset']['revision'] == 2
        assert data(write(client, apply_path, payload))['already_applied']
        assert len(data(client.get('/api/v1' + preset_path + '/history'))) == 2
        template = data(write(client, '/ai/templates', {'name': '核对格式', 'content': '明确列出缺失信息'}))
        backup = client.get('/api/v1/backups/export').content
    restored = tmp_path / 'restored'
    restore_to_new_directory(backup, restored)
    with client_for(restored) as client:
        assert data(client.get('/api/v1' + preset_path))['params']['min_score'] == '70'
        assert data(client.get('/api/v1/ai/runs/' + run['id'] + scope))['output']
        assert any(item['id'] == template['id'] for item in data(client.get('/api/v1/ai/templates')))
        assert data(client.get('/api/v1/research/parameter-proposals/' + proposal['id']))['status'] == 'applied'
