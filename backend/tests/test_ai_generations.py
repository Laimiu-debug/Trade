from copy import deepcopy
import io
import json

from PIL import Image
import pytest
from sqlalchemy import func, select, text

from trade_app.ai.config import DEFAULT_CONFIG, update_config
from trade_app.ai.generation_models import AIGeneration
from trade_app.ai.generation_service import (
    accept_generation, delete_generation, edit_generation, finalize_generation,
    get_generation, list_generation_audit, list_generations, prepare_generation,
    preview_generation, reject_generation, retract_generation,
)
from trade_app.ai.generation_schemas import strict_json
from trade_app.ai.models import AICall
from trade_app.ai.service import get_run, stream_run
from trade_app.analytics.service import process_one, projection_status
from trade_app.market.service import import_dataset
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.reviews.attachments import add_attachment, delete_attachment
from trade_app.reviews.periods import get_period, save_period
from trade_app.reviews.rounds import get_round_note
from trade_app.reviews.service import get_review, save_review
from trade_app.trading.models import Trade
from trade_app.trading.pending import confirm_pending_trade, list_pending_trades
from trade_app.trading.service import create_account, create_flow, create_trade, list_snapshots, save_snapshot


def _write(factory, operation):
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        return operation(session)


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        config = deepcopy(DEFAULT_CONFIG)
        config['text'] = {'base_url': 'http://localhost:9999/v1', 'model': 'text', 'secret_ref': ''}
        config['vision'] = {'base_url': 'http://localhost:9999/v1', 'model': 'vision', 'secret_ref': ''}
        _write(factory, lambda session: update_config(session, {'expected_revision': 0, **config}))
        account = _write(factory, lambda session: create_account(session, name='明确账户'))
        second = _write(factory, lambda session: create_account(session, name='无关账户'))
        output = io.BytesIO()
        Image.new('RGB', (8, 8), (20, 30, 40)).save(output, 'PNG')
        attachment = _write(factory, lambda session: add_attachment(session, tmp_path, account['id'],
            '2025-01-10', output.getvalue(), '成交截图.png'))
        yield factory, tmp_path, account['id'], second['id'], attachment
    finally:
        engine.dispose()


def _request(kind, target, **overrides):
    return {'kind': kind, 'target': target, 'config_revision': 1, 'notes': '', **overrides}


def _prepare(scenario, body, *, unbound=False):
    factory, directory, account_id, _other, _attachment = scenario
    scope = None if unbound else account_id
    with factory() as session:
        preview = preview_generation(session, directory, body, scope)
    return _write(factory, lambda session: prepare_generation(session, directory, {
        **body, 'expected_input_sha256': preview['input_sha256']}, scope))


def _complete(scenario, generation, output, captured=None):
    factory, directory, _account_id, _other, _attachment = scenario

    def provider(config, channel, messages, **_kwargs):
        if captured is not None:
            captured.append((channel, messages))
        yield {'type': 'delta', 'content': json.dumps(output, ensure_ascii=False) if not isinstance(output, str) else output}
        yield {'type': 'end', 'finish_reason': 'stop'}

    events = list(stream_run(factory, generation['run_id'], generation['account_id'],
                            provider_factory=provider, data_dir=directory))
    assert events[-1]['run']['status'] == 'completed'
    return _write(factory, lambda session: finalize_generation(session, generation['id'],
        {'expected_revision': 1}, generation['account_id']))


def _ocr_request(scenario, kind='ocr_trades'):
    return _request(kind, {'attachment_ids': [scenario[4]['id']]})


def _recognized_trade(**overrides):
    return {'trades': [{'trade_date': '2025-01-10', 'symbol': '600000', 'name': '示例',
                       'side': 'buy', 'quantity': 100, 'price': '10.20', 'fee': '5.00', **overrides}], 'warnings': []}


def test_image_bytes_are_only_sent_on_start_and_not_persisted(scenario):
    factory, _directory, account_id, _other, attachment = scenario
    generation = _prepare(scenario, _ocr_request(scenario))
    with factory() as session:
        call = session.get(AICall, generation['run_id'])
        assert call.status == 'queued' and 'base64' not in call.request_json
        assert session.scalar(select(func.count()).select_from(Trade)) == 0
    captured = []
    draft = _complete(scenario, generation, _recognized_trade(), captured)
    assert draft['status'] == 'draft' and draft['revision'] == 2
    assert captured[0][0] == 'vision'
    assert captured[0][1][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    _write(factory, lambda session: delete_attachment(session, account_id, attachment['id'], 1))
    with factory() as session:
        assert get_generation(session, draft['id'], account_id)['output'] == draft['output']
        assert 'base64' not in get_run(session, draft['run_id'], account_id)['request']['messages'][-1]['content']
        assert list_pending_trades(session, account_id) == []


def test_missing_or_wrong_scope_images_prevent_network_and_cross_account_reads(scenario):
    factory, directory, account_id, other, attachment = scenario
    body = _ocr_request(scenario)
    with factory() as session:
        with pytest.raises(TradeError):
            preview_generation(session, directory, body, None)
        with pytest.raises(TradeError):
            preview_generation(session, directory, body, other)
    generation = _prepare(scenario, body)
    _write(factory, lambda session: delete_attachment(session, account_id, attachment['id'], 1))
    events = list(stream_run(factory, generation['run_id'], account_id, data_dir=directory,
        provider_factory=lambda *_args, **_kwargs: pytest.fail('missing image must not call provider')))
    assert events[-1]['run']['error_code'] == 'ATTACHMENT_NOT_FOUND'
    with factory() as session:
        assert list_generations(session, other) == []
        with pytest.raises(TradeError):
            get_generation(session, generation['id'], other)
        with pytest.raises(TradeError):
            finalize_generation(session, generation['id'], {'expected_revision': 1}, account_id)


def test_ocr_corrections_enter_pending_only_and_cannot_retract_after_ledger_confirmation(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    draft = _complete(scenario, _prepare(scenario, _ocr_request(scenario)),
                      _recognized_trade(trade_date=None, side='hold', fee=None))
    with pytest.raises(TradeError) as incomplete:
        _write(factory, lambda session: accept_generation(session, draft['id'], {'expected_revision': 2}, account_id))
    assert incomplete.value.code == 'AI_OCR_NEEDS_REVIEW'
    edited = _write(factory, lambda session: edit_generation(session, draft['id'], {
        'expected_revision': 2, 'output': _recognized_trade()}, account_id))
    accepted = _write(factory, lambda session: accept_generation(session, draft['id'],
        {'expected_revision': edited['revision']}, account_id))
    pending = accepted['acceptance']['pending_trades'][0]
    assert pending['source'] == 'ai_ocr'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Trade)) == 0
        assert len(list_pending_trades(session, account_id)) == 1
    _write(factory, lambda session: confirm_pending_trade(session, account_id, pending['id'], 1, False))
    with pytest.raises(TradeError) as closed:
        _write(factory, lambda session: retract_generation(session, accepted['id'],
            {'expected_revision': accepted['revision']}, account_id))
    assert closed.value.code == 'AI_ACCEPTED_DATA_CHANGED'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Trade)) == 1


def test_ocr_pending_retract_and_invalid_json_history_are_durable(scenario):
    factory, directory, account_id, _other, _attachment = scenario
    invalid = _complete(scenario, _prepare(scenario, _ocr_request(scenario)), '```json\n{}\n```')
    assert invalid['status'] == 'invalid_output' and invalid['output'] is None
    assert invalid['validation_errors'][0]['code'] == 'AI_INVALID_JSON'
    fixed = _write(factory, lambda session: edit_generation(session, invalid['id'], {
        'expected_revision': 2, 'output': _recognized_trade()}, account_id))
    accepted = _write(factory, lambda session: accept_generation(session, fixed['id'], {'expected_revision': 3}, account_id))
    retracted = _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 4}, account_id))
    assert retracted['status'] == 'retracted'
    _write(factory, lambda session: delete_generation(session, accepted['id'], {'expected_revision': 5}, account_id))
    reopened_engine, reopened = open_database(directory)
    try:
        with reopened() as session:
            assert list_generations(session, account_id) == []
            assert list_pending_trades(session, account_id) == []
            assert [item['action'] for item in list_generation_audit(session, accepted['id'], account_id)] == [
                'prepare', 'finalize', 'edit', 'accept', 'retract', 'delete']
    finally:
        reopened_engine.dispose()


def test_asset_recognition_preserves_nulls_and_requires_explicit_snapshot_revision(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    _write(factory, lambda session: create_flow(session, account_id, {
        'flow_date': '2025-01-09', 'kind': 'initial', 'amount': '10000', 'note': ''}))
    output = {'snap_date': '2025-01-10', 'total_assets': None, 'available_cash': '8000',
              'positions': [{'symbol': '510300.SH', 'name': 'ETF', 'quantity': 500, 'market_value': '2000'}],
              'warnings': ['总资产待核对']}
    draft = _complete(scenario, _prepare(scenario, _ocr_request(scenario, 'ocr_assets')), output)
    assert draft['output']['total_assets'] is None and draft['output']['positions'][0]['symbol'] == '510300'
    with pytest.raises(TradeError):
        _write(factory, lambda session: accept_generation(session, draft['id'], {
            'expected_revision': 2, 'expected_target_revision': 0}, account_id))
    output['total_assets'] = '10000'
    edited = _write(factory, lambda session: edit_generation(session, draft['id'], {'expected_revision': 2, 'output': output}, account_id))
    accepted = _write(factory, lambda session: accept_generation(session, edited['id'], {
        'expected_revision': 3, 'expected_target_revision': 0}, account_id))
    with factory() as session:
        assert list_snapshots(session, account_id)[0]['total_assets'] == '10000.00'
    with pytest.raises(TradeError) as no_delete:
        _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 4}, account_id))
    assert no_delete.value.code == 'AI_RETRACT_UNSUPPORTED'


def test_existing_asset_snapshot_can_restore_previous_confirmed_version(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    _write(factory, lambda session: create_flow(session, account_id, {
        'flow_date': '2025-01-09', 'kind': 'initial', 'amount': '10000', 'note': ''}))
    _write(factory, lambda session: save_snapshot(session, account_id, {'snap_date': '2025-01-10',
        'total_assets': '9000', 'available_cash': '9000', 'positions': [], 'expected_revision': 0}))
    output = {'snap_date': '2025-01-10', 'total_assets': '10000', 'available_cash': '10000', 'positions': [], 'warnings': []}
    draft = _complete(scenario, _prepare(scenario, _ocr_request(scenario, 'ocr_assets')), output)
    assert draft['source']['destination_snapshot']['revision'] == 1
    accepted = _write(factory, lambda session: accept_generation(session, draft['id'], {
        'expected_revision': 2, 'expected_target_revision': 1}, account_id))
    _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 3}, account_id))
    with factory() as session:
        restored = list_snapshots(session, account_id)[0]
        assert restored['total_assets'] == '9000.00' and restored['revision'] == 3


def test_daily_draft_preserves_human_fields_and_retracts_only_unchanged_acceptance(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    _write(factory, lambda session: save_review(session, account_id, '2025-01-10', {
        'expected_revision': 0, 'title': '人工标题', 'overall_summary': '人工总结',
        'reflection': '人工反思', 'mistakes': '人工错误清单'}))
    body = _request('review_draft', {'type': 'daily', 'key': '2025-01-10', 'fields': ['overall_summary']})
    draft = _complete(scenario, _prepare(scenario, body), {'sections': {'overall_summary': 'AI 候选总结'}})
    with factory() as session:
        assert get_review(session, account_id, '2025-01-10')['overall_summary'] == '人工总结'
    accepted = _write(factory, lambda session: accept_generation(session, draft['id'], {
        'expected_revision': 2, 'expected_target_revision': 1}, account_id))
    with factory() as session:
        current = get_review(session, account_id, '2025-01-10')
        assert current['overall_summary'] == 'AI 候选总结' and current['mistakes'] == '人工错误清单'
        assert current['reflection'] == '人工反思'
    _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 3}, account_id))
    with factory() as session:
        restored = get_review(session, account_id, '2025-01-10')
        assert restored['overall_summary'] == '人工总结' and restored['revision'] == 3
    another = _complete(scenario, _prepare(scenario, body), {'sections': {'overall_summary': '第二次建议'}})
    adopted = _write(factory, lambda session: accept_generation(session, another['id'], {
        'expected_revision': 2, 'expected_target_revision': 3}, account_id))
    with factory() as session:
        latest = get_review(session, account_id, '2025-01-10')
    _write(factory, lambda session: save_review(session, account_id, '2025-01-10', {
        **latest, 'expected_revision': 4, 'overall_summary': '后续人工修订'}))
    with pytest.raises(TradeError):
        _write(factory, lambda session: retract_generation(session, adopted['id'], {'expected_revision': 3}, account_id))


@pytest.mark.parametrize('kind,key,field', [('weekly', '2025-W02', 'key_insight'),
                                         ('monthly', '2025-01', 'summary'),
                                         ('rehearsal', '2025-01-10', 'next_risk_plan')])
def test_period_and_rehearsal_drafts_require_explicit_acceptance(scenario, kind, key, field):
    factory, _directory, account_id, _other, _attachment = scenario
    body = _request('review_draft', {'type': kind, 'key': key, 'fields': [field]})
    draft = _complete(scenario, _prepare(scenario, body), {'sections': {field: '候选内容'}})
    accepted = _write(factory, lambda session: accept_generation(session, draft['id'], {
        'expected_revision': 2, 'expected_target_revision': 0}, account_id))
    assert accepted['acceptance']['target_revision'] == 1
    _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 3}, account_id))
    with factory() as session:
        current = get_review(session, account_id, key) if kind == 'rehearsal' else get_period(session, account_id, kind, key)
        assert current.get('sections', current).get(field, '') == ''


def test_source_revision_conflict_does_not_overwrite_review_or_create_half_result(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    body = _request('review_draft', {'type': 'daily', 'key': '2025-01-10', 'fields': ['reflection']})
    draft = _complete(scenario, _prepare(scenario, body), {'sections': {'reflection': '候选反思'}})
    _write(factory, lambda session: create_trade(session, account_id, {'trade_date': '2025-01-10',
        'symbol': '600000', 'side': 'buy', 'quantity': 100, 'price': '10', 'fee': '5'}))
    with pytest.raises(TradeError) as stale:
        _write(factory, lambda session: accept_generation(session, draft['id'], {
            'expected_revision': 2, 'expected_target_revision': 0}, account_id))
    assert stale.value.code == 'AI_SOURCE_CHANGED'
    with factory() as session:
        assert get_review(session, account_id, '2025-01-10') is None
        assert get_generation(session, draft['id'], account_id)['status'] == 'draft'


def test_round_generation_uses_current_round_and_versions_summary(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    for day, side, price in [('2025-01-09', 'buy', '10'), ('2025-01-10', 'sell', '11')]:
        _write(factory, lambda session: create_trade(session, account_id, {'trade_date': day,
            'symbol': '600000', 'side': side, 'quantity': 100, 'price': price, 'fee': '5'}))
    assert process_one(factory)
    with factory() as session:
        projection = projection_status(session, account_id)
        assert projection['status'] == 'fresh'
        round_id = projection['result']['rounds'][0]['id']
    draft = _complete(scenario, _prepare(scenario, _request('review_draft', {'type': 'round', 'key': round_id})),
                      {'sections': {'summary': '有明确来源的回合总结'}})
    _write(factory, lambda session: accept_generation(session, draft['id'], {
        'expected_revision': 2, 'expected_target_revision': 0}, account_id))
    with factory() as session:
        assert get_round_note(session, account_id, round_id)['summary'] == '有明确来源的回合总结'


def test_unbound_stock_analysis_keeps_original_fields_and_rejects_future_breakout(scenario):
    factory, directory, _account_id, _other, _attachment = scenario
    bars = [{'event_date': day, 'open': '10', 'high': '11', 'low': '9', 'close': '10', 'volume': 100,
             'available_at': available} for day, available in [
        ('2025-01-09', '2025-01-09T08:00:00Z'), ('2025-01-10', '2025-01-11T08:00:00Z')]]
    dataset = _write(factory, lambda session: import_dataset(session, directory, {
        'symbol': '600000', 'adjustment': 'none', 'bars': bars}))
    body = _request('stock_analysis', {'dataset_id': dataset['id'], 'decision_at': '2025-01-10T12:00:00Z'})
    generation = _prepare(scenario, body, unbound=True)
    assert generation['source']['breakout_candidates'] == ['2025-01-09']
    output = {'summary': '历史样本研究', 'conclusion': 'Unknown', 'confidence': 0.3,
              'breakout_date': '2025-01-10', 'trend_bull_type': None, 'theme_name': None, 'rise_reasons': []}
    invalid = _complete(scenario, generation, output)
    assert invalid['validation_errors'][0]['code'] == 'AI_INVALID_BREAKOUT_DATE'
    output['breakout_date'] = None
    fixed = _write(factory, lambda session: edit_generation(session, invalid['id'], {'expected_revision': 2, 'output': output}))
    accepted = _write(factory, lambda session: accept_generation(session, fixed['id'], {'expected_revision': 3}))
    assert accepted['output'] == output
    _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 4}))
    with factory() as session:
        assert len(list_generations(session)) == 1
        assert session.scalar(select(func.count()).select_from(Trade)) == 0


@pytest.mark.parametrize('raw', ['[' * 2000 + ']' * 2000, '{"a":1,"a":2}', '{"a":NaN}'], ids=['deep', 'duplicate', 'nan'])
def test_untrusted_structured_response_errors_are_bounded(raw):
    with pytest.raises(TradeError) as caught:
        strict_json(raw)
    assert caught.value.code == 'AI_INVALID_JSON'
