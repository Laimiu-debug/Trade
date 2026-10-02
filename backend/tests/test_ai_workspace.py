from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text

from trade_app.ai.config import DEFAULT_CONFIG, get_config, normalize_config, update_config
from trade_app.ai.contexts import freeze_context, system_templates
from trade_app.ai.models import AICall, AIConfig, AIPromptRevision
from trade_app.ai.provider import ProviderFailure, stream_chat
from trade_app.ai.service import (
    assert_run_startable, cancel_run, create_session, create_template, delete_session,
    delete_template, get_run, get_session, list_runs, list_sessions, list_templates,
    prepare_connection_test, prepare_run, preview_prompt, recover_interrupted_runs,
    stream_run, update_template, usage_summary,
)
from trade_app.market.service import import_dataset
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.trading.service import create_account, create_trade
from trade_app.trading.simulation import create_sim_account


def _write(factory, operation):
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        return operation(session)


def _settings():
    body = deepcopy(DEFAULT_CONFIG)
    body['text'] = {'base_url': 'https://models.example.invalid/v1', 'model': 'text-example',
                    'secret_ref': 'TRADE_AI_TEST_KEY'}
    body['vision'] = {'base_url': 'http://localhost:9999/v1', 'model': 'vision-local', 'secret_ref': ''}
    return body


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADE_AI_TEST_KEY', 'test-secret-that-must-never-be-persisted')
    engine, factory = open_database(tmp_path)
    try:
        _write(factory, lambda session: update_config(session, {'expected_revision': 0, **_settings()}))
        chat = _write(factory, lambda session: create_session(session, {'title': '策略讨论'}))
        yield factory, tmp_path, chat
    finally:
        engine.dispose()


def _request(**overrides):
    return {'message': '解释所选样本', 'config_revision': 1, 'channel': 'text',
            'context': {'manual_text': '这是用户选择的样本说明'}, **overrides}


def _prepare(scenario, **overrides):
    factory, directory, chat = scenario
    return _write(factory, lambda session: prepare_run(session, directory, chat['id'], _request(**overrides)))


def _provider(_config, _channel, _messages, *, should_cancel):
    yield {'type': 'delta', 'content': '第一段'}
    yield {'type': 'delta', 'content': '，第二段。'}
    yield {'type': 'usage', 'usage': {'prompt_tokens': 7, 'completion_tokens': 8, 'total_tokens': 15}}
    yield {'type': 'end', 'finish_reason': 'stop'}


class Chunks(httpx.SyncByteStream):
    def __init__(self, pieces):
        self.pieces = pieces

    def __iter__(self):
        yield from self.pieces


def _http_provider(handler):
    return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs)


def test_config_versions_credentials_and_url_validation_are_safe(scenario):
    factory, _directory, _chat = scenario
    with factory() as session:
        config = get_config(session)
        assert config['revision'] == 1
        assert config['text']['secret_configured'] is True
        assert config['vision']['secret_configured'] is False
        assert 'test-secret' not in json.dumps(config)
    changed = _settings()
    changed['text']['model'] = 'new-model'
    _write(factory, lambda session: update_config(session, {'expected_revision': 1, **changed}))
    with pytest.raises(TradeError) as stale:
        _write(factory, lambda session: update_config(session, {'expected_revision': 1, **changed}))
    assert stale.value.code == 'AI_CONFIG_CONFLICT'
    with factory() as session:
        versions = list(session.scalars(select(AIConfig).order_by(AIConfig.revision)))
        assert len(versions) == 2 and 'text-example' in versions[0].config_json
        assert all('test-secret' not in item.config_json for item in versions)
    for url in ('https://user:password@example.com/v1', 'file:///tmp/key', 'https://example.com/#token',
                'https://example.com/?key=secret', 'http://example.com/v1', 'http://localhost:bad/v1'):
        invalid = _settings()
        invalid['text']['base_url'] = url
        with pytest.raises(TradeError):
            normalize_config(invalid)
    for extra in ({'api_key': 'actual-key'}, {'secret_ref': 'AWS_SECRET_ACCESS_KEY'}):
        invalid = _settings()
        invalid['text'].update(extra)
        with pytest.raises(TradeError):
            normalize_config(invalid)


def test_provider_sse_segmentation_unicode_multiline_usage_and_secret_header(scenario):
    config = {**_settings(), 'revision': 1}
    content = (': heartbeat\r\n\r\ndata: {"choices":[{"delta":{"content":"你好"}}]}\r\n\r\n'
               'data: {"choices": [\n'
               'data: {"delta":{"content":"，世界"},"finish_reason":"stop"}]}\n\n'
               'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":3,"total_tokens":14}}\n\n'
               'data: [DONE]\n\n').encode()
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              stream=Chunks([content[index:index + 1] for index in range(len(content))]))

    events = list(stream_chat(config, 'text', [{'role': 'user', 'content': 'hello'}],
                              client_factory=_http_provider(handler)))
    assert ''.join(event['content'] for event in events if event['type'] == 'delta') == '你好，世界'
    assert events[-2]['usage']['total_tokens'] == 14 and events[-1]['type'] == 'end'
    assert captured[0].headers['Authorization'] == 'Bearer test-secret-that-must-never-be-persisted'
    assert str(captured[0].url).endswith('/v1/chat/completions')


@pytest.mark.parametrize('problem,expected', [('timeout', 'AI_TIMEOUT'), ('http', 'AI_AUTH_ERROR'),
                                            ('badjson', 'AI_INVALID_RESPONSE'), ('truncated', 'AI_STREAM_INTERRUPTED')])
def test_provider_failure_codes_do_not_expose_response_or_exception_secrets(scenario, problem, expected, caplog):
    def handler(request):
        if problem == 'timeout':
            raise httpx.ReadTimeout('test-secret-that-must-never-be-persisted', request=request)
        if problem == 'http':
            return httpx.Response(401, text='test-secret-that-must-never-be-persisted')
        if problem == 'badjson':
            return httpx.Response(200, content=b'data: test-secret-that-must-never-be-persisted\n\n')
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    with pytest.raises(ProviderFailure) as caught:
        list(stream_chat(_settings(), 'text', [{'role': 'user', 'content': 'hello'}],
                         client_factory=_http_provider(handler)))
    assert caught.value.code == expected
    assert 'test-secret' not in str(caught.value) + caplog.text


def test_preview_is_frozen_and_stream_chunks_survive_restart(scenario):
    factory, directory, chat = scenario
    with factory() as session:
        preview = preview_prompt(session, directory, chat['id'], _request())
    run = _prepare(scenario, expected_input_sha256=preview['input_sha256'])
    changed = _settings()
    changed['text']['model'] = 'new-model'
    _write(factory, lambda session: update_config(session, {'expected_revision': 1, **changed}))
    captured = []

    def provider(config, channel, messages, **kwargs):
        captured.append((config, channel, messages))
        yield from _provider(config, channel, messages, **kwargs)

    iterator = stream_run(factory, run['id'], provider_factory=provider)
    assert next(iterator)['type'] == 'started'
    assert next(iterator)['content'] == '第一段'
    with factory() as session:
        assert get_run(session, run['id'])['output'] == '第一段'
    events = list(iterator)
    assert events[-1]['run']['status'] == 'completed'
    assert captured[0][0]['text']['model'] == 'text-example'
    assert captured[0][2] == preview['messages']
    reopened_engine, reopened = open_database(directory)
    try:
        with reopened() as session:
            detail = get_session(session, chat['id'])
            assert [item['role'] for item in detail['messages']] == ['user', 'assistant']
            assert detail['messages'][-1]['content'] == '第一段，第二段。'
            assert usage_summary(session)['total_tokens'] == 15
            assert 'test-secret' not in json.dumps(get_run(session, run['id']))
    finally:
        reopened_engine.dispose()


def test_cancel_disconnect_duplicate_start_and_recovery_preserve_partial_output(scenario):
    factory, _directory, chat = scenario
    first = _prepare(scenario)
    with pytest.raises(TradeError) as busy:
        _prepare(scenario)
    assert busy.value.code == 'AI_SESSION_BUSY'
    iterator = stream_run(factory, first['id'], provider_factory=_provider)
    next(iterator)
    next(iterator)
    with factory() as session:
        with pytest.raises(TradeError):
            assert_run_startable(session, first['id'])
    _write(factory, lambda session: cancel_run(session, first['id']))
    assert list(iterator)[-1]['run']['status'] == 'cancelled'
    second = _prepare(scenario)
    disconnected = stream_run(factory, second['id'], provider_factory=_provider)
    next(disconnected)
    next(disconnected)
    disconnected.close()
    with factory() as session:
        row = get_run(session, second['id'])
        assert row['status'] == 'interrupted' and row['output'] == '第一段'
        assert row['error_code'] == 'AI_CLIENT_DISCONNECTED'
    third = _prepare(scenario)
    assert recover_interrupted_runs(factory) == 1
    with factory() as session:
        assert get_run(session, third['id'])['error_code'] == 'AI_PROCESS_INTERRUPTED'
    fourth = _prepare(scenario)
    _write(factory, lambda session: cancel_run(session, fourth['id']))
    with pytest.raises(TradeError) as duplicate:
        list(stream_run(factory, fourth['id'], provider_factory=_provider))
    assert duplicate.value.code == 'AI_RUN_ALREADY_STARTED'


def test_missing_key_and_provider_errors_are_durable_unknown_usage(scenario, monkeypatch):
    factory, _directory, _chat = scenario
    monkeypatch.delenv('TRADE_AI_TEST_KEY')
    monkeypatch.setattr('trade_app.ai.provider.httpx.Client', lambda **_kwargs: pytest.fail('must not open network'))
    run = _prepare(scenario)
    final = list(stream_run(factory, run['id']))[-1]['run']
    assert final['status'] == 'failed' and final['error_code'] == 'AI_SECRET_MISSING'
    assert final['usage']['total_tokens'] is None
    failed = _prepare(scenario)

    def broken(*_args, **_kwargs):
        yield {'type': 'delta', 'content': '已收到的片段'}
        raise RuntimeError('test-secret-that-must-never-be-persisted')

    final = list(stream_run(factory, failed['id'], provider_factory=broken))[-1]['run']
    assert final['output'] == '已收到的片段' and final['error_code'] == 'AI_INTERNAL_ERROR'
    with factory() as session:
        assert usage_summary(session)['unknown_usage_count'] == 2
        assert usage_summary(session)['total_tokens'] is None
        assert all('test-secret' not in json.dumps(item) for item in list_runs(session))


def test_session_scope_delete_and_connection_test_are_explicit(scenario):
    factory, _directory, chat = scenario
    first = _write(factory, lambda session: create_account(session, name='账户 A'))
    second = _write(factory, lambda session: create_account(session, name='账户 B'))
    scoped = _write(factory, lambda session: create_session(session, {'title': '私有范围', 'account_id': first['id']}))
    with factory() as session:
        assert [item['id'] for item in list_sessions(session)] == [chat['id']]
        assert [item['id'] for item in list_sessions(session, first['id'])] == [scoped['id']]
        for scope in (None, second['id']):
            with pytest.raises(TradeError) as denied:
                get_session(session, scoped['id'], scope)
            assert denied.value.status == 404
    test = _write(factory, lambda session: prepare_connection_test(session, {'channel': 'vision', 'config_revision': 1}))
    assert test['status'] == 'queued' and test['kind'] == 'connection_test'
    seen = []

    def probe(config, channel, messages, **kwargs):
        seen.append((config['max_tokens'], channel, messages))
        yield from _provider(config, channel, messages, **kwargs)

    list(stream_run(factory, test['id'], provider_factory=probe))
    assert seen == [(8, 'vision', [{'role': 'user', 'content': 'Reply OK.'}])]
    _write(factory, lambda session: delete_session(session, scoped['id'], {'expected_revision': 1}, first['id']))
    with factory() as session:
        assert list_sessions(session, first['id']) == []
        with pytest.raises(TradeError):
            get_session(session, scoped['id'], first['id'])


def test_templates_original_playbooks_versions_and_preview_conflict(scenario):
    factory, directory, chat = scenario
    originals = system_templates()
    assert len(originals) == 16 and all(item['readonly'] for item in originals)
    catalog = json.loads((Path(__file__).parents[1] / 'trade_app/research/legacy_catalog.json').read_text(encoding='utf-8'))
    assert json.loads(originals[0]['content'])['playbook'] == catalog['strategies'][0]['playbook']
    custom = _write(factory, lambda session: create_template(session, {'name': '检查风险', 'content': '请列出不确定项。'}))
    request = _request(template_id=custom['id'], template_revision=1)
    with factory() as session:
        preview = preview_prompt(session, directory, chat['id'], request)
        with pytest.raises(TradeError):
            delete_template(session, originals[0]['id'], {'expected_revision': 1})
    _write(factory, lambda session: update_template(session, custom['id'], {
        'expected_revision': 1, 'name': '检查风险', 'content': '请列出来源和不确定项。'}))
    with pytest.raises(TradeError):
        _write(factory, lambda session: prepare_run(session, directory, chat['id'], {
            **request, 'expected_input_sha256': preview['input_sha256']}))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AICall)) == 0
        assert session.scalar(select(func.count()).select_from(AIPromptRevision)) == 2
        assert len(list_templates(session)) == 17
    _write(factory, lambda session: delete_template(session, custom['id'], {'expected_revision': 2}))


def test_context_selects_only_explicit_account_date_and_eligible_market_bars(scenario):
    factory, directory, _chat = scenario
    first = _write(factory, lambda session: create_account(session, name='账户 A'))
    second = _write(factory, lambda session: create_account(session, name='账户 B'))
    for account, day, symbol in ((first, '2025-01-09', '600000'), (first, '2025-01-20', '600002'),
                                 (second, '2025-01-09', '600001')):
        _write(factory, lambda session: create_trade(session, account['id'], {
            'trade_date': day, 'symbol': symbol, 'side': 'buy', 'quantity': 100, 'price': '10.00', 'fee': '5.00'}))
    bars = [{'event_date': '2025-01-09', 'open': '10', 'high': '11', 'low': '9', 'close': '10',
             'volume': 100, 'available_at': '2025-01-09T08:00:00Z'},
            {'event_date': '2025-01-10', 'open': '1000', 'high': '1001', 'low': '999', 'close': '1000',
             'volume': 100, 'available_at': '2025-01-12T08:00:00Z'}]
    dataset = _write(factory, lambda session: import_dataset(session, directory, {
        'symbol': '600000', 'bars': bars, 'adjustment': 'none'}))
    with factory() as session:
        context = freeze_context(session, directory, {'dataset_ids': [dataset['id']],
            'decision_at': '2025-01-10T18:00:00+08:00', 'account_date': '2025-01-10'}, account_id=first['id'])
        assert len(context['datasets'][0]['bars']) == 1
        assert [item['symbol'] for item in context['account']['facts']['trades']] == ['600000']
        serialized = json.dumps(context)
        assert second['id'] not in serialized and '600002' not in serialized
        assert freeze_context(session, directory, {}, account_id=None)['account'] is None
        with pytest.raises(TradeError):
            freeze_context(session, directory, {'account_date': '2025-01-10'}, account_id=None)
        with pytest.raises(TradeError):
            freeze_context(session, directory, {'dataset_ids': [dataset['id']],
                           'decision_at': '2025-01-10T12:00:00'}, account_id=None)


def test_account_revision_preview_conflict_and_sim_historical_positions_rejected(scenario):
    factory, directory, _chat = scenario
    real = _write(factory, lambda session: create_account(session, name='可变账户'))
    chat = _write(factory, lambda session: create_session(session, {'title': '账户复盘', 'account_id': real['id']}))
    body = _request(context={'account_date': '2025-01-10'})
    with factory() as session:
        preview = preview_prompt(session, directory, chat['id'], body, real['id'])
    _write(factory, lambda session: create_trade(session, real['id'], {
        'trade_date': '2025-01-10', 'symbol': '600000', 'side': 'buy', 'quantity': 100, 'price': '10', 'fee': '5'}))
    with pytest.raises(TradeError) as changed:
        _write(factory, lambda session: prepare_run(session, directory, chat['id'], {
            **body, 'expected_input_sha256': preview['input_sha256']}, real['id']))
    assert changed.value.code == 'AI_CONTEXT_CHANGED'
    sim = _write(factory, lambda session: create_sim_account(session, {
        'name': '模拟', 'initial_capital': '100000', 'start_date': '2025-01-10'}))
    with factory() as session:
        with pytest.raises(TradeError) as historical:
            freeze_context(session, directory, {'account_date': '2025-01-09'}, account_id=sim['id'])
        assert historical.value.code == 'AI_SIM_DATE_MISMATCH'
        context = freeze_context(session, directory, {'account_date': '2025-01-10'}, account_id=sim['id'])
        assert context['account']['facts']['cash'] == '100000.00'


def test_network_concurrency_slots_and_delete_busy_session(scenario):
    factory, _directory, chat = scenario
    first = _prepare(scenario)
    second = _write(factory, lambda session: prepare_connection_test(session, {'config_revision': 1}))
    third = _write(factory, lambda session: prepare_connection_test(session, {'config_revision': 1}))
    with pytest.raises(TradeError) as busy:
        _write(factory, lambda session: delete_session(session, chat['id'], {'expected_revision': 2}))
    assert busy.value.code == 'AI_SESSION_BUSY'
    one = stream_run(factory, first['id'], provider_factory=_provider)
    two = stream_run(factory, second['id'], provider_factory=_provider)
    next(one)
    next(one)
    next(two)
    next(two)
    try:
        final = list(stream_run(factory, third['id'], provider_factory=_provider))[-1]['run']
        assert final['error_code'] == 'AI_CONCURRENCY_LIMIT'
    finally:
        one.close()
        two.close()
