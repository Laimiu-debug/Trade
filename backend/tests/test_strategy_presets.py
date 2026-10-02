"""Versioned parameter-only sharing and explicit, stale-safe proposal apply."""
import base64
import hashlib
import json
import zipfile
from io import BytesIO

import pytest

from trade_app.ai.models import AICall
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research import preset_service as service
from trade_app.research.preset_models import StrategyParameterProposal, StrategyPreset


@pytest.fixture
def session(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as current:
            yield current
    finally:
        engine.dispose()


def save(session, **kwargs):
    return service.save_preset(session, {'name': '观察参数', 'strategy_id': 'relative_strength_breakout_v1',
                                         'params': {'min_ret40': '0.2'}, **kwargs})


def code_for(payload=None, raw=None):
    raw = raw if raw is not None else json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':'), allow_nan=False).encode()
    return service.SHARE_PREFIX + '.' + base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' + hashlib.sha256(raw).hexdigest()


def share_payload():
    return {'format_version': 1, 'strategy_id': 'relative_strength_breakout_v1',
            'strategy_version': '1.0.0-alpha', 'name': '导入参数', 'params': {'min_ret40': '0.3'}}


def proposal(session, preset, **kwargs):
    return service.create_parameter_proposal(session, {'preset_id': preset['id'],
        'expected_revision': preset['revision'], 'params': {'min_ret40': '0.35'}, **kwargs})


def apply(session, preview, **kwargs):
    return service.apply_parameter_proposal(session, preview['id'], {'expected_revision': preview['base_revision'],
        'expected_proposal_hash': preview['proposal_sha256'], **kwargs})


@pytest.mark.parametrize('strategy_id', service.SUPPORTED_PRESET_STRATEGIES)
def test_all_fifteen_supported_strategy_presets_roundtrip_through_current_normalizer(session, strategy_id):
    preset = save(session, strategy_id=strategy_id, params={})
    assert preset['compatible'] is True
    assert all(isinstance(value, str) for value in preset['params'].values())
    assert 'mode' not in preset['params']
    exported = service.export_preset(session, preset['id'])
    imported = service.preview_import(session, {'share_code': exported['share_code'], 'current_params': preset['params']})
    assert imported['params'] == preset['params']
    assert imported['diff'] == [] and imported['saved'] is False
    assert len(service.list_presets(session)) == 1
    assert preset['schema_sha256'] and preset['catalog_sha256'] and preset['contract_sha256']


def test_boolean_api_values_normalize_and_unknown_derived_or_unsupported_params_fail(session):
    preset = save(session, strategy_id='wulong_cluster_v1', params={'allow_upper_shadow_risk': True})
    assert preset['params']['allow_upper_shadow_risk'] == 'true'
    assert service.normalize_preset_params('wulong_cluster_v1', {'allow_blowoff_top': 'false'})['allow_blowoff_top'] == 'false'
    for strategy, params in [('trend_king_v1', {'mode': 'limitup'}), ('matrix_signal_v1', {'event_score_min': 20}),
                             ('ths_main_force_flip_v1', {'top_n': 3})]:
        with pytest.raises(TradeError) as failure:
            save(session, strategy_id=strategy, params=params)
        assert failure.value.code == 'UNKNOWN_STRATEGY_PARAM'
    with pytest.raises(TradeError) as failure:
        save(session, strategy_id='unimplemented_strategy_v1', params={})
    assert failure.value.code == 'STRATEGY_PRESET_UNSUPPORTED'
    for params in ({'min_ret40': True}, {'min_ret40': []}, {'min_ret40': 'NaN'}, {'min_ret40': 2}):
        with pytest.raises(TradeError):
            save(session, params=params)


def test_named_favorite_updates_conflicts_and_deletion_keep_immutable_history(session):
    first = save(session)
    second = save(session, name='收藏参数', favorite=True)
    assert [item['id'] for item in service.list_presets(session, favorites_only=True)] == [second['id']]
    assert service.list_presets(session)[0]['id'] == second['id']
    updated = service.save_preset(session, {'name': '修改名称', 'strategy_id': first['strategy_id'],
        'params': {'min_ret40': '0.4'}, 'favorite': True, 'expected_revision': 1}, first['id'])
    assert updated['revision'] == 2 and updated['favorite']
    with pytest.raises(TradeError) as failure:
        service.delete_preset(session, first['id'], {'expected_revision': 1})
    assert failure.value.code == 'PRESET_VERSION_CONFLICT'
    deleted = service.delete_preset(session, first['id'], {'expected_revision': 2})
    assert deleted['revision'] == 3
    with pytest.raises(TradeError) as failure:
        service.get_preset(session, first['id'])
    assert failure.value.code == 'STRATEGY_PRESET_NOT_FOUND'
    history = service.preset_history(session, first['id'])
    assert [item['action'] for item in history] == ['delete', 'update', 'create']
    assert history[-1]['snapshot']['name'] == '观察参数'
    assert history[-1]['snapshot']['params']['min_ret40'] == '0.2'
    assert history[0]['snapshot']['deleted'] is True


def test_share_export_whitelists_parameter_metadata_and_import_preview_does_not_save(session):
    preset = save(session)
    row = session.get(StrategyPreset, preset['id'])
    stored = json.loads(row.snapshot_json)
    stored.update({'event_profile': {'secret': 'profile-private'}, 'account_id': 'private-account',
                   'bars': [{'private': True}], 'api_key': 'should-not-share'})
    row.snapshot_json = json.dumps(stored)
    exported = service.export_preset(session, preset['id'])
    raw = base64.urlsafe_b64decode(exported['share_code'].split('.')[1] + '==')
    assert set(json.loads(raw)) == service.SHARE_KEYS
    assert b'private' not in raw and b'should-not-share' not in raw
    assert exported['event_profile_included'] is False
    imported = service.preview_import(session, {'share_code': code_for(share_payload()),
                                               'current_params': {'min_ret40': '0.20'}})
    assert imported['diff'] == [{'parameter': 'min_ret40', 'before': '0.2', 'after': '0.3'}]
    assert imported['saved'] is False and len(service.list_presets(session)) == 1
    saved = service.save_preset(session, {key: imported[key] for key in ('name', 'strategy_id', 'params')})
    assert saved['id'] != preset['id'] and saved['params']['min_ret40'] == '0.3'


@pytest.mark.parametrize('change,code', [({'strategy_version': '99.0'}, 'PRESET_STRATEGY_VERSION'),
    ({'format_version': 2}, 'PRESET_SHARE_VERSION'), ({'account_id': 'secret'}, 'INVALID_PRESET_FIELDS'),
    ({'event_profile': {}}, 'INVALID_PRESET_FIELDS'), ({'params': {'readonly': 1}}, 'UNKNOWN_STRATEGY_PARAM')])
def test_share_import_rejects_version_and_field_conflicts(session, change, code):
    with pytest.raises(TradeError) as failure:
        service.preview_import(session, {'share_code': code_for({**share_payload(), **change})})
    assert failure.value.code == code
    assert service.list_presets(session) == []


def test_share_checksum_size_compressed_nested_duplicate_and_noncanonical_json_rejected(session, monkeypatch):
    valid = code_for(share_payload())
    with pytest.raises(TradeError) as failure:
        service.preview_import(session, {'share_code': valid[:-64] + '0' * 64})
    assert failure.value.code == 'PRESET_SHARE_CHECKSUM'
    zipped = BytesIO()
    with zipfile.ZipFile(zipped, 'w') as archive:
        archive.writestr('payload.json', json.dumps(share_payload()))
    raw_cases = [zipped.getvalue(), b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":' + b'[' * 12 + b'0' + b']' * 12 + b'}',
                 json.dumps(share_payload(), indent=2).encode()]
    for raw in raw_cases:
        with pytest.raises(TradeError) as failure:
            service.preview_import(session, {'share_code': code_for(raw=raw)})
        assert failure.value.code in ('INVALID_PRESET_JSON', 'INVALID_PRESET_SHARE')
    monkeypatch.setattr(base64, 'b64decode', lambda *_, **__: pytest.fail('Do not decode oversized input'))
    with pytest.raises(TradeError) as failure:
        service.preview_import(session, {'share_code': 'a' * (64 * 1024 + 1)})
    assert failure.value.code == 'PRESET_SHARE_TOO_LARGE'


def test_proposal_is_audited_patch_and_requires_explicit_hash_then_apply_is_idempotent(session):
    preset = save(session, params={'min_ret40': '0.2', 'max_retrace20': '0.4'})
    preview = proposal(session, preset)
    assert preview['status'] == 'pending'
    assert service.get_preset(session, preset['id'])['revision'] == 1
    assert preview['params']['max_retrace20'] == '0.4'
    assert preview['submitted_params'] == {'min_ret40': '0.35'}
    assert preview['diff'] == [{'parameter': 'min_ret40', 'before': '0.2', 'after': '0.35'}]
    with pytest.raises(TradeError) as failure:
        apply(session, preview, expected_proposal_hash='wrong')
    assert failure.value.code == 'PARAMETER_PROPOSAL_CONFLICT'
    applied = apply(session, preview)
    assert applied['preset']['revision'] == 2 and applied['already_applied'] is False
    assert applied['preset']['params']['min_ret40'] == '0.35'
    repeated = apply(session, preview)
    assert repeated['already_applied'] is True and repeated['preset']['revision'] == 2
    history = service.preset_history(session, preset['id'])
    assert len(history) == 2 and history[0]['action'] == 'apply_proposal'
    assert history[0]['snapshot']['proposal_id'] == preview['id']


@pytest.mark.parametrize('conflict', ['revision', 'base', 'contract', 'snapshot'])
def test_stale_or_corrupted_proposal_cannot_mutate_preset(session, monkeypatch, conflict):
    preset = save(session)
    preview = proposal(session, preset)
    row = session.get(StrategyPreset, preset['id'])
    if conflict == 'revision':
        service.save_preset(session, {'name': '并行编辑', 'strategy_id': preset['strategy_id'],
            'params': preset['params'], 'expected_revision': 1}, preset['id'])
    elif conflict == 'base':
        stored = json.loads(row.snapshot_json)
        stored['params']['min_ret40'] = '0.7'
        row.snapshot_json = json.dumps(stored)
    elif conflict == 'snapshot':
        proposal_row = session.get(StrategyParameterProposal, preview['id'])
        stored = json.loads(proposal_row.snapshot_json)
        stored['params']['min_ret40'] = '0.8'
        proposal_row.snapshot_json = json.dumps(stored)
    else:
        original = service._metadata
        monkeypatch.setattr(service, '_metadata', lambda strategy: {**original(strategy), 'contract_sha256': 'changed'})
    before = row.snapshot_json
    with pytest.raises(TradeError) as failure:
        apply(session, preview)
    assert failure.value.code in ('PRESET_VERSION_CONFLICT', 'PARAMETER_PROPOSAL_CONFLICT', 'PRESET_CONTRACT_CHANGED')
    assert row.snapshot_json == before
    assert service.get_parameter_proposal(session, preview['id'])['status'] == 'pending'


def ai_call(session, **kwargs):
    now = utc_now()
    values = {'id': new_id(), 'session_id': None, 'account_id': 'account-a', 'kind': 'chat', 'channel': 'text',
        'status': 'completed', 'config_revision': 1, 'config_json': '{"secret":"not-for-presets"}',
        'request_json': '{}', 'context_json': '{"position":"private"}', 'input_sha256': 'a' * 64,
        'output': json.dumps({'strategy_id': 'relative_strength_breakout_v1', 'params': {'min_ret40': '0.4'}}),
        'usage_json': '{}', 'error_code': None, 'cancel_requested': 0, 'created_at': now, 'updated_at': now,
        'started_at': now, 'finished_at': now, **kwargs}
    row = AICall(**values)
    session.add(row)
    session.flush()
    return row


def ai_source(call):
    return {key: getattr(call, key) for key in ('id', 'account_id', 'status', 'kind', 'output')}


def test_ai_proposal_reads_only_completed_saved_chat_json_in_explicit_account_scope(session):
    preset = save(session)
    call = ai_call(session)
    body = {'preset_id': preset['id'], 'expected_revision': 1, 'source_ai_run_id': call.id, 'account_id': 'account-a'}
    preview = service.create_parameter_proposal(session, body, ai_source=ai_source(call))
    assert preview['params']['min_ret40'] == '0.4'
    assert preview['source'] == {'kind': 'ai', 'source_ai_run_id': call.id, 'account_id': 'account-a',
                                  'output_sha256': hashlib.sha256(call.output.encode()).hexdigest()}
    assert 'private' not in json.dumps(preview) and 'not-for-presets' not in json.dumps(preview)
    assert service.get_preset(session, preset['id'])['params']['min_ret40'] == '0.2'
    with pytest.raises(TradeError) as failure:
        service.create_parameter_proposal(session, {**body, 'account_id': 'account-b'}, ai_source=ai_source(call))
    assert failure.value.code == 'AI_CALL_NOT_FOUND'
    with pytest.raises(TradeError) as failure:
        service.create_parameter_proposal(session, {**body, 'params': {'min_ret40': '0.9'}}, ai_source=ai_source(call))
    assert failure.value.code == 'INVALID_PARAMETER_PROPOSAL'
    applied = apply(session, preview)
    assert applied['preset']['params']['min_ret40'] == '0.4'


@pytest.mark.parametrize('changes,error', [({'status': 'running'}, 'AI_PROPOSAL_NOT_READY'),
    ({'kind': 'connection_test'}, 'AI_PROPOSAL_NOT_READY'), ({'output': '```json\n{}\n```'}, 'INVALID_PRESET_JSON'),
    ({'output': '{"strategy_id":"matrix_signal_v1","params":{}}'}, 'PRESET_STRATEGY_CONFLICT'),
    ({'output': '{"strategy_id":"relative_strength_breakout_v1","params":{},"secret":"x"}'}, 'INVALID_PRESET_FIELDS')])
def test_unfinished_unscoped_or_unstructured_ai_output_is_not_a_parameter_proposal(session, changes, error):
    preset = save(session)
    call = ai_call(session, **changes)
    with pytest.raises(TradeError) as failure:
        service.create_parameter_proposal(session, {'preset_id': preset['id'], 'expected_revision': 1,
            'source_ai_run_id': call.id, 'account_id': 'account-a'}, ai_source=ai_source(call))
    assert failure.value.code == error
    assert service.get_preset(session, preset['id'])['revision'] == 1
