import pytest

from test_ai_generations import _complete, _prepare, _request, _write, scenario
from trade_app.ai.generation_service import accept_generation, get_generation, preview_generation, retract_generation
from trade_app.platform.types import TradeError
from trade_app.reviews.ai_scores import copy_ai_to_final
from trade_app.reviews.scores import DIMENSIONS, list_scores, save_scores
from trade_app.trading.service import create_trade


def _trades(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    result = []
    for symbol, side, price in [('600000', 'buy', '10'), ('600000', 'sell', '11'), ('600001', 'buy', '15')]:
        result.append(_write(factory, lambda session: create_trade(session, account_id, {
            'trade_date': '2025-01-10', 'symbol': symbol, 'side': side,
            'quantity': 100, 'price': price, 'fee': '5'})))
    return result


def _manual(scenario, scope, ids, *, revision=0, value=9):
    factory, _directory, account_id, _other, _attachment = scenario
    return _write(factory, lambda session: save_scores(session, account_id, '2025-01-10', {
        'scope': scope, 'trade_ids': ids, 'expected_revision': revision, 'comment': '人工整体点评',
        'scores': {dimension: {'final': value, 'comment': '人工依据'} for dimension in DIMENSIONS[scope]}}))


def _scoring(scenario, scope, ids=None):
    return _prepare(scenario, _request('review_scores', {'scope': scope, 'key': '2025-01-10', 'trade_ids': ids or []}))


def _output(generation, score=3):
    return {'summary': '本次选定范围的评分说明', 'subjects': [{
        'subject_id': target['subject_id'], 'scope': target['scope'], 'trade_ids': target['trade_ids'],
        'scores': {dimension: {'score': score, 'comment': 'AI 依据，行情证据有限'} for dimension in target['dimensions']},
        'comment': 'AI 目标点评'} for target in generation['source']['score_targets']]}


def _accept(scenario, generation):
    return _write(scenario[0], lambda session: accept_generation(session, generation['id'],
        {'expected_revision': generation['revision']}, scenario[2]))


def test_daily_ai_suggestions_preserve_manual_final_and_copy_is_separate(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    _trades(scenario)
    _manual(scenario, 'daily', [])
    generation = _scoring(scenario, 'daily')
    draft = _complete(scenario, generation, _output(generation, score=0))
    with factory() as session:
        initial = list_scores(session, account_id, '2025-01-10')[0]
        assert initial['scores']['position']['ai'] is None
    accepted = _accept(scenario, draft)
    sheet = accepted['acceptance']['sheets'][0]['after']
    assert set(sheet['scores']) == set(DIMENSIONS['daily'])
    assert all(item['ai'] == 0 and item['final'] == 9 and item['comment'] == '人工依据'
               for item in sheet['scores'].values())
    assert sheet['comment'] == '人工整体点评'
    copied = _write(factory, lambda session: copy_ai_to_final(session, account_id, sheet['id'], {
        'expected_revision': 2, 'dimensions': ['position']}))
    assert copied['scores']['position']['final'] == 0
    assert copied['scores']['position']['final_source'] == 'ai_accepted'
    assert copied['scores']['emotion']['final'] == 9
    with pytest.raises(TradeError):
        _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 3}, account_id))


def test_rescoring_keeps_manual_final_and_retract_restores_previous_suggestion(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    trades = _trades(scenario)
    _manual(scenario, 'trade', [trades[0]['id']])
    first = _scoring(scenario, 'trade', [trades[0]['id']])
    _accept(scenario, _complete(scenario, first, _output(first, 4)))
    second = _scoring(scenario, 'trade', [trades[0]['id']])
    accepted = _accept(scenario, _complete(scenario, second, _output(second, 7)))
    with factory() as session:
        sheet = list_scores(session, account_id, '2025-01-10')[0]
        assert sheet['scores']['timing']['ai'] == 7 and sheet['scores']['timing']['final'] == 9
    _write(factory, lambda session: retract_generation(session, accepted['id'], {'expected_revision': 3}, account_id))
    with factory() as session:
        restored = list_scores(session, account_id, '2025-01-10')[0]
        assert restored['scores']['timing']['ai'] == 4 and restored['scores']['timing']['final'] == 9


def test_trade_batch_checks_all_frozen_revisions_before_any_score_write(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    trades = _trades(scenario)
    ids = [trades[0]['id'], trades[1]['id']]
    generation = _scoring(scenario, 'batch', ids)
    assert len(generation['source']['score_targets']) == 2
    draft = _complete(scenario, generation, _output(generation))
    _manual(scenario, 'trade', [ids[1]])
    with factory.begin() as session:
        with pytest.raises(TradeError) as stale:
            accept_generation(session, draft['id'], {'expected_revision': 2}, account_id)
        assert stale.value.code == 'REVISION_CONFLICT'
        sheets = list_scores(session, account_id, '2025-01-10')
        assert len(sheets) == 1 and sheets[0]['scores']['timing']['ai'] is None
        assert get_generation(session, draft['id'], account_id)['status'] == 'draft'


def test_batch_and_t_group_targets_preserve_actual_ids_and_dimensions(scenario):
    factory, directory, account_id, _other, _attachment = scenario
    trades = _trades(scenario)
    batch = _scoring(scenario, 'batch', [item['id'] for item in trades])
    _accept(scenario, _complete(scenario, batch, _output(batch)))
    grouped = _scoring(scenario, 't_group', [trades[1]['id'], trades[0]['id']])
    assert len(grouped['source']['score_targets']) == 1
    assert grouped['source']['score_targets'][0]['subject_id'].startswith('group:')
    accepted = _accept(scenario, _complete(scenario, grouped, _output(grouped)))
    sheet = accepted['acceptance']['sheets'][0]['after']
    assert set(sheet['scores']) == {'timing', 'discipline', 'emotion'}
    with factory() as session:
        assert len(list_scores(session, account_id, '2025-01-10')) == 4
        with pytest.raises(TradeError):
            preview_generation(session, directory, _request('review_scores', {'scope': 't_group',
                'key': '2025-01-10', 'trade_ids': [trades[0]['id'], trades[2]['id']]}), account_id)


@pytest.mark.parametrize('mutation', ['bool', 'above_range', 'wrong_id', 'missing_dimension'])
def test_invalid_model_scores_never_create_sheets(scenario, mutation):
    factory, _directory, account_id, _other, _attachment = scenario
    generation = _scoring(scenario, 'daily')
    output = _output(generation)
    subject = output['subjects'][0]
    if mutation == 'bool':
        subject['scores']['position']['score'] = True
    elif mutation == 'above_range':
        subject['scores']['position']['score'] = 11
    elif mutation == 'wrong_id':
        subject['subject_id'] = 'unrelated'
    else:
        del subject['scores']['position']
    invalid = _complete(scenario, generation, output)
    assert invalid['status'] == 'invalid_output'
    with factory() as session:
        assert list_scores(session, account_id, '2025-01-10') == []


def test_cross_account_and_changed_source_block_copying_suggestions(scenario):
    factory, _directory, account_id, other, _attachment = scenario
    generation = _scoring(scenario, 'daily')
    accepted = _accept(scenario, _complete(scenario, generation, _output(generation)))
    sheet = accepted['acceptance']['sheets'][0]['after']
    body = {'expected_revision': 1, 'dimensions': ['position']}
    with pytest.raises(TradeError) as foreign:
        _write(factory, lambda session: copy_ai_to_final(session, other, sheet['id'], body))
    assert foreign.value.status == 404
    _trades(scenario)
    with pytest.raises(TradeError) as changed:
        _write(factory, lambda session: copy_ai_to_final(session, account_id, sheet['id'], body))
    assert changed.value.code == 'AI_SCORE_SOURCE_CHANGED'
    with factory() as session:
        assert list_scores(session, account_id, '2025-01-10')[0]['scores']['position']['final'] is None


def test_manual_score_edit_retains_ai_provenance(scenario):
    factory, _directory, account_id, _other, _attachment = scenario
    generation = _scoring(scenario, 'daily')
    accepted = _accept(scenario, _complete(scenario, generation, _output(generation)))
    sheet = accepted['acceptance']['sheets'][0]['after']
    _manual(scenario, 'daily', [], revision=sheet['revision'], value=8)
    with factory() as session:
        edited = list_scores(session, account_id, '2025-01-10')[0]
        assert edited['scores']['position']['ai_generation_id'] == accepted['id']
        assert edited['scores']['position']['final'] == 8
        assert edited['scores']['position']['ai'] == 3
