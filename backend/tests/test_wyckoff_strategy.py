"""Compare snapshot gates to the original scanner and prevent unsafe coercion."""
from copy import deepcopy

import pytest

from legacy_oracle import oracle
from trade_app.platform.types import TradeError
from trade_app.research.wyckoff_strategy import (
    WYCKOFF_STRATEGY_IDS, evaluate_wyckoff_strategy, normalize_wyckoff_params, wyckoff_param_schema,
)


def snapshot(**overrides):
    return {'events': ['SOS'], 'risk_events': [], 'event_dates': {'SOS': '2025-05-20'},
            'event_chain': [{'event': 'SOS', 'date': '2025-05-20', 'category': 'accumulation'}],
            'sequence_ok': True, 'entry_quality_score': 82.0, 'health_score': 71.0,
            'event_score': 65.0, 'event_grade': 'B', 'event_confirmation_map': {'SOS': 'confirmed'},
            'trigger_date': '2025-05-20', 'signal': 'SOS', 'phase': '吸筹D',
            'phase_hint': '测试信号', 'structure_hhh': 'HH|HL|HC', **overrides}


def row_fields():
    return dict(
        symbol='sh600000', name='测试标的', latest_price=10, day_change=0.1, day_change_pct=0.01,
        score=80, ret40=0.25, turnover20=0.08, amount20=8e8, amplitude20=0.05, retrace20=0.03,
        pullback_days=3, ma10_above_ma20_days=8, ma5_above_ma10_days=6, price_vs_ma20=0.06,
        vol_slope20=0.1, up_down_volume_ratio=1.4, pullback_volume_ratio=0.7,
        has_blowoff_top=False, has_divergence_5d=False, has_upper_shadow_risk=False,
        ai_confidence=0.7, theme_stage='发酵中', trend_class='A', stage='Mid', labels=[],
        reject_reasons=[], degraded=False,
    )


def row():
    """The candidate row as the original ScreenerResult model dumped it."""
    def dump():
        from app.models import ScreenerResult
        return ScreenerResult(**row_fields()).model_dump()
    return oracle('candidate_row', dump)


@pytest.mark.parametrize('strategy_id', WYCKOFF_STRATEGY_IDS)
def test_frozen_gates_match_original_store_scan_for_valid_snapshots(strategy_id, monkeypatch):
    candidate = row()
    variations = [
        {}, {'entry_quality_score': 59.99}, {'entry_quality_score': 60},
        {'health_score': 54.99}, {'health_score': 55}, {'event_score': 54.99}, {'event_score': 55},
        {'event_grade': 'C'}, {'event_grade': 'A'}, {'event_confirmation_map': {}},
        {'event_confirmation_map': {'SOS': 'pending'}}, {'event_confirmation_map': {'SOS': 'failed'}},
        {'event_confirmation_map': {'SOS': ' CONFIRMED '}},
        {'events': [], 'risk_events': [], 'event_dates': {}, 'event_chain': []},
        {'events': [], 'risk_events': ['SOW'], 'signal': 'SOW', 'event_chain': [], 'event_dates': {}},
        {'event_chain': [None, {}, {'event': 'SOS'}, {'event': 'SOS', 'date': '2025-05-20'}]},
        {'sequence_ok': False}, {'event_score': 0}, {'event_score': None},
        {'health_score': None},
    ]

    def legacy_scan(source):
        from app.core.strategy_plugins import ScoreOnlyRankPlugin, WyckoffTrendPlugin
        from app.models import ScreenerResult
        from app.store import store
        legacy_row = ScreenerResult(**row_fields())
        monkeypatch.setattr(store, '_resolve_signal_candidates', lambda **_: ([legacy_row], None, None, '2025-05-20'))
        monkeypatch.setattr(store, '_compute_signal_age_days', lambda **_: (0, '2025-05-20'))
        monkeypatch.setattr(store, '_save_signals_runtime_cache', lambda *_, **__: None)
        monkeypatch.setattr(store, '_is_signals_disk_cache_enabled', lambda: False)
        monkeypatch.setattr(store, '_calc_wyckoff_snapshot', lambda *_, **__: deepcopy(source))
        old = store.get_signals(mode='full_market', strategy_id=strategy_id, as_of_date='2025-05-20', refresh=True)
        if not old.items:
            return None
        item = old.items[0]
        plugin = ScoreOnlyRankPlugin() if strategy_id == 'score_only_rank_v1' else WyckoffTrendPlugin(strategy_id)
        return {'entry_quality_score': item.entry_quality_score, 'health_score': item.health_score,
                'event_score': item.event_score, 'event_count': item.wy_event_count,
                'event_chain': item.wy_event_chain,
                'local_score': plugin.rank_signals(signal=item, row=legacy_row, params={},
                                                   fallback_score=item.health_score * 0.45 + item.event_score * 0.55)}

    for index, variation in enumerate(variations):
        source = snapshot(**variation)
        old = oracle(f'{strategy_id}:{index}', lambda: legacy_scan(source))
        original = deepcopy(source)
        new = evaluate_wyckoff_strategy(strategy_id, source, {}, candidate)
        assert source == original
        assert new['signal'] == (old is not None), (strategy_id, variation, new)
        if old is not None:
            assert new['entry_quality_score'] == old['entry_quality_score']
            assert new['health_score'] == old['health_score']
            assert new['event_score'] == old['event_score']
            assert new['event_count'] == old['event_count']
            assert new['event_chain'] == old['event_chain']
            assert new['local_score'] == old['local_score']


def test_strategy_effective_defaults_are_scan_defaults_not_backtest_or_bare_plugin_defaults():
    v1 = normalize_wyckoff_params('wyckoff_trend_v1', {})
    v2 = normalize_wyckoff_params('wyckoff_trend_v2', {})
    score = normalize_wyckoff_params('score_only_rank_v1', {})
    assert v1 == {'window_days': '60', 'min_score': '60', 'min_event_count': '1',
                  'require_sequence': False, 'health_score_min': '0', 'event_score_min': '0',
                  'event_grade_min': 'C', 'require_key_event_confirmation': False}
    assert v2['health_score_min'] == v2['event_score_min'] == '55'
    assert v2['event_grade_min'] == 'B' and v2['require_key_event_confirmation'] is True
    assert v2['min_score'] == '60' and v2['min_event_count'] == '1'
    assert score['min_score'] == '60' and score['min_event_count'] == '0'
    assert score['window_days'] == '60'


def test_zero_health_is_preserved_instead_of_legacy_fallback_to_entry_quality():
    source = snapshot(health_score=0, entry_quality_score=95, event_score=95)
    result = evaluate_wyckoff_strategy('wyckoff_trend_v2', source, {})
    assert result['signal'] is False
    assert result['reasons'] == ['HEALTH_SCORE_BELOW_MIN']
    assert result['health_score'] == 0
    assert result['local_score'] == 95 * 0.55
    assert result['quality_flags'] == ['EXPLICIT_ZERO_HEALTH_PRESERVED']


def test_score_only_uses_event_snapshot_quality_and_separates_candidate_proxy_score():
    low_quality = evaluate_wyckoff_strategy('score_only_rank_v1', snapshot(entry_quality_score=59), {},
                                           {'score': 99, 'quality_flags': ['FLOAT_SHARES_NOT_FOUND']})
    assert low_quality['candidate_score'] == 99
    assert low_quality['entry_quality_score'] == low_quality['local_score'] == 59
    assert low_quality['signal'] is False
    assert low_quality['candidate_quality_flags'] == ['FLOAT_SHARES_NOT_FOUND']
    no_events = evaluate_wyckoff_strategy('score_only_rank_v1', snapshot(
        events=[], risk_events=[], event_dates={}, event_chain=[], signal='', entry_quality_score=60), {}, {'score': 0})
    assert no_events['signal'] is True and no_events['candidate_score'] == 0
    assert no_events['positive_primary_event'] is False
    assert no_events['local_score_formula'] == 'entry_quality_score'


def test_event_count_uses_chain_then_dates_then_event_and_risk_arrays():
    source = snapshot(events=['SOS', 'Spring'], risk_events=['SOW'], event_chain=[
        {'event': 'SOS', 'date': '2025-05-19'}, {'event': 'SOS', 'date': '2025-05-20'}, None, {}])
    result = evaluate_wyckoff_strategy('wyckoff_trend_v1', source, {'min_event_count': '3'})
    assert result['event_count'] == 2 and result['signal'] is False
    source['event_chain'] = []
    source['event_dates'] = {' SOS ': '2025-05-20', 'SOW': '2025-05-19'}
    result = evaluate_wyckoff_strategy('wyckoff_trend_v1', source, {})
    assert result['event_count'] == 2
    assert result['event_chain'][0]['category'] == 'distributionRisk'
    source['event_dates'] = {}
    result = evaluate_wyckoff_strategy('wyckoff_trend_v1', source, {'min_event_count': '3'})
    assert result['event_count'] == 3 and result['signal'] is True


def test_primary_key_confirmation_does_not_use_global_or_another_events_status():
    source = snapshot(confirmation_status='confirmed', event_confirmation_map={'Spring': 'confirmed'})
    result = evaluate_wyckoff_strategy('wyckoff_trend_v2', source, {})
    assert result['reasons'] == ['PRIMARY_KEY_EVENT_UNCONFIRMED']
    source.update(signal='SOW', risk_events=['SOW'])
    result = evaluate_wyckoff_strategy('wyckoff_trend_v2', source, {})
    assert result['signal'] is True  # Observation gate; never label this a buy signal.
    assert result['primary_event'] == 'SOW' and result['risk_events'] == ['SOW']
    assert result['positive_primary_event'] is False
    assert result['checks'][-2]['actual'] == 'not_applicable'


def test_explicit_sequence_false_is_not_truthy_string_and_all_failures_are_explained():
    params = normalize_wyckoff_params('wyckoff_trend_v2', {'require_sequence': 'false'})
    assert params['require_sequence'] is False
    result = evaluate_wyckoff_strategy('wyckoff_trend_v2', snapshot(
        sequence_ok=False, entry_quality_score=59, health_score=54, event_score=54,
        event_grade='C', event_confirmation_map={}), {'require_sequence': 'true', 'min_event_count': '2'})
    assert result['reasons'] == ['EVENT_COUNT_BELOW_MIN', 'EVENT_SEQUENCE_REQUIRED', 'ENTRY_QUALITY_BELOW_MIN',
                                 'HEALTH_SCORE_BELOW_MIN', 'EVENT_SCORE_BELOW_MIN', 'EVENT_GRADE_BELOW_MIN',
                                 'PRIMARY_KEY_EVENT_UNCONFIRMED']


@pytest.mark.parametrize('raw', ['', 'not-a-date', '2025-02-30', '20250520'])
def test_invalid_trigger_dates_cannot_be_replaced_with_today(raw):
    result = evaluate_wyckoff_strategy('score_only_rank_v1', snapshot(trigger_date=raw), {})
    assert result['signal'] is False and result['trigger_date'] is None
    assert result['reasons'] == ['INVALID_TRIGGER_DATE']


@pytest.mark.parametrize('key,value', [
    ('window_days', '19'), ('window_days', '241'), ('window_days', '60.5'),
    ('min_event_count', '-1'), ('min_event_count', '13'), ('min_event_count', '1.5'),
    ('min_score', 'NaN'), ('health_score_min', 'Infinity'), ('event_score_min', '-1'),
    ('min_score', True), ('require_sequence', 'maybe'), ('require_key_event_confirmation', 1),
    ('event_grade_min', 'D'),
])
def test_invalid_params_are_rejected_instead_of_clamped_or_silently_ignored(key, value):
    with pytest.raises(TradeError) as error:
        normalize_wyckoff_params('wyckoff_trend_v2', {key: value})
    assert error.value.code == 'INVALID_STRATEGY_PARAM'


@pytest.mark.parametrize('key', ['matrix_event_semantic_version', 'rank_weight_health', 'rank_weight_event',
                                 'signal_age_min', 'entry_delay_days', 'unknown'])
def test_unimplemented_matrix_ranking_and_execution_params_are_rejected(key):
    with pytest.raises(TradeError) as error:
        normalize_wyckoff_params('wyckoff_trend_v2', {key: '0'})
    assert error.value.code == 'UNKNOWN_STRATEGY_PARAM'


@pytest.mark.parametrize('field,value', [
    ('entry_quality_score', float('nan')), ('entry_quality_score', None), ('health_score', float('inf')),
    ('event_score', -1), ('health_score', 101), ('entry_quality_score', True), ('sequence_ok', 'false'),
])
def test_invalid_snapshot_numbers_and_sequence_cannot_bypass_gates(field, value):
    with pytest.raises(TradeError) as error:
        evaluate_wyckoff_strategy('wyckoff_trend_v1', snapshot(**{field: value}), {})
    assert error.value.code == 'INVALID_WYCKOFF_SNAPSHOT'


def test_schema_copies_and_valid_inclusive_boundaries():
    schema = wyckoff_param_schema('wyckoff_trend_v2')
    for key, spec in schema.items():
        for bound in ('minimum', 'maximum'):
            if bound in spec:
                assert key in normalize_wyckoff_params('wyckoff_trend_v2', {key: spec[bound]})
    schema['window_days']['minimum'] = 1
    assert wyckoff_param_schema('wyckoff_trend_v2')['window_days']['minimum'] == 20
    equal = evaluate_wyckoff_strategy('wyckoff_trend_v2', snapshot(
        entry_quality_score=60, health_score=55, event_score=55, event_grade='B'), {})
    assert equal['signal'] is True
    assert equal['positive_primary_event'] is True
