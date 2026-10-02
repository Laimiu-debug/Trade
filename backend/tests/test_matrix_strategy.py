"""Parity and data-boundary tests for the frozen matrix candidate-pool plugin."""
from copy import deepcopy
from random import Random
from types import SimpleNamespace

import pytest

from app.core.strategy_plugins import MatrixSignalPlugin
from trade_app.platform.types import TradeError
from trade_app.research.matrix_domain import (
    MATRIX_PARAM_KEYS, evaluate_matrix_pool, matrix_param_schema, normalize_matrix_params,
)


def candidate(index=0, **overrides):
    return {
        'symbol': f'{600000 + index:06d}', 'dataset_id': f'{index + 1:064x}',
        'as_of_date': '2025-05-20', 'name': f'样本{index}', 'quality_flags': [],
        'ret40': 0.2, 'retrace20': 0.1, 'vol_slope20': 0.1,
        'price_vs_ma20': 0.01, 'up_down_volume_ratio': 1.5,
        'pullback_days': 2, 'ma10_above_ma20_days': 8, **overrides,
    }


@pytest.mark.parametrize('params', [{}, {
    'atr_ratio_max': '0.1', 'vol_ratio_max': '0.1', 'ret40_top_n': '50',
    'sideways_range_max': '0.05', 'near_ma20_max': '0.01', 'min_pool_score': '4',
    'breakout_vol_ratio': '3', 'max_pullback_days': '0',
}, {
    'atr_ratio_max': '2', 'vol_ratio_max': '2', 'ret40_top_n': '50',
    'sideways_range_max': '0.5', 'near_ma20_max': '0.2', 'min_pool_score': '1',
    'breakout_vol_ratio': '0.5', 'max_pullback_days': '20',
}])
def test_matrix_pool_and_ranks_match_legacy_plugin_for_frozen_pool(params):
    rng = Random(12409)
    inputs = [candidate(
        index, ret40=rng.uniform(-0.3, 0.6), retrace20=rng.uniform(0, 0.9),
        vol_slope20=rng.uniform(-0.3, 2.5), price_vs_ma20=rng.uniform(-0.2, 0.2),
        up_down_volume_ratio=rng.uniform(0.1, 4), pullback_days=rng.randrange(0, 25),
        ma10_above_ma20_days=rng.randrange(0, 41),
    ) for index in range(120)]
    original = deepcopy(inputs)
    legacy = MatrixSignalPlugin()
    legacy_rows = [SimpleNamespace(**row) for row in inputs]
    normalized = normalize_matrix_params(params)
    admitted = legacy.build_universe(candidates=legacy_rows, params=normalized, mode='strict')
    admitted_ids = {row.dataset_id for row in admitted}
    result = evaluate_matrix_pool(inputs, params)
    assert inputs == original
    expected_ranking = []
    for result_row, old in zip(result['rows'], legacy_rows):
        expected_signal = old.dataset_id in admitted_ids and legacy.generate_signals(row=old, snapshot={}, params=normalized)
        expected_score = legacy.rank_signals(signal=None, row=old, params=normalized, fallback_score=37)
        assert result_row['in_pool'] == (old.dataset_id in admitted_ids)
        assert result_row['signal'] == expected_signal
        assert result_row['score'] == expected_score
        if expected_signal:
            expected_ranking.append((old.dataset_id, expected_score))
        else:
            assert result_row['rank'] is None
            assert result_row['reasons']
    expected_ranking.sort(key=lambda row: row[1], reverse=True)
    assert result['ranking'] == [row[0] for row in expected_ranking]
    assert result['summary'] == {'input_count': 120, 'pool_count': len(admitted), 'signal_count': len(expected_ranking)}


def test_matrix_explains_distinct_pool_s3_and_ranking_s3_and_stable_ties():
    rows = [candidate(index, ret40=-0.1) for index in range(52)]
    result = evaluate_matrix_pool(rows, {'ret40_top_n': '50', 'min_pool_score': '4'})
    assert result['summary'] == {'input_count': 52, 'pool_count': 50, 'signal_count': 50}
    assert result['ranking'] == [row['dataset_id'] for row in rows[:50]]
    assert [row['ret40_rank'] for row in result['rows']] == list(range(1, 53))
    first, excluded = result['rows'][0], result['rows'][50]
    assert first['components']['s3'] is True
    assert first['components']['s3_rank'] is False
    assert first['pool_score'] == 4 and first['rank_pool_score'] == 3
    assert excluded['components']['s3'] is False
    assert excluded['signal'] is False and excluded['rank'] is None
    assert excluded['reasons'] == ['POOL_SCORE_BELOW_MIN']
    assert first['score'] == excluded['score']  # A high score cannot bypass admission.


def test_matrix_preserves_signed_s4_and_fixed_s6_threshold_with_explicit_rules():
    result = evaluate_matrix_pool([
        candidate(0, price_vs_ma20=-0.4, ret40=-0.1),
        candidate(1, price_vs_ma20=0.06, ret40=-0.1),
        candidate(2, price_vs_ma20=0.059, ret40=-0.1),
    ], {'near_ma20_max': '0.01'})
    first, boundary, near = result['rows']
    assert first['components']['s4'] is True
    assert first['components']['s6'] is False
    assert first['reasons'] == ['NO_ENTRY_TRIGGER']
    assert boundary['components']['s6'] is False  # Strict <, not <=.
    assert near['components']['s6'] is True  # S6 does not use near_ma20_max.
    assert '有符号' in result['component_rules']['s4']
    assert result['metric_label'] == 'legacy_matrix_plugin_proxy_not_vectorized_engine'


def test_matrix_threshold_equalities_and_empty_pool():
    result = evaluate_matrix_pool([candidate(
        retrace20=0.7, vol_slope20=0.6, price_vs_ma20=0.06,
        up_down_volume_ratio=1.2, pullback_days=3, ma10_above_ma20_days=5,
    )], {'min_pool_score': '1'})
    row = result['rows'][0]
    assert row['components'] == {'s1': False, 's2': False, 's3': True, 's3_rank': True,
                                  's4': False, 's5': True, 's6': False, 's7': True}
    assert row['signal'] is True
    empty = evaluate_matrix_pool([])
    assert empty['summary'] == {'input_count': 0, 'pool_count': 0, 'signal_count': 0}
    assert empty['ranking'] == [] and empty['rows'] == []


def test_matrix_param_schema_has_only_applied_keys_and_is_not_mutable():
    schema = matrix_param_schema()
    assert tuple(schema) == MATRIX_PARAM_KEYS
    for key, spec in schema.items():
        for boundary in ('minimum', 'maximum'):
            assert key in normalize_matrix_params({key: spec[boundary]})
    schema['ret40_top_n']['minimum'] = 0
    assert matrix_param_schema()['ret40_top_n']['minimum'] == 50
    assert normalize_matrix_params({'ret40_top_n': '50.0'})['ret40_top_n'] == '50'


@pytest.mark.parametrize('key,value', [
    ('ret40_top_n', '49'), ('ret40_top_n', '2001'), ('ret40_top_n', '50.5'),
    ('max_pullback_days', '-1'), ('min_pool_score', '5'), ('min_pool_score', True),
    ('atr_ratio_max', 'NaN'), ('atr_ratio_max', 'Infinity'), ('atr_ratio_max', None),
    ('vol_ratio_max', 'abc'), ('near_ma20_max', '0.001'), ('breakout_vol_ratio', '3.1'),
])
def test_matrix_rejects_invalid_applied_params(key, value):
    with pytest.raises(TradeError) as error:
        evaluate_matrix_pool([], {key: value})
    assert error.value.code == 'INVALID_STRATEGY_PARAM'


@pytest.mark.parametrize('key', ['min_score', 'min_event_count', 'require_sequence',
                                 'health_score_min', 'event_score_min', 'event_grade_min',
                                 'require_key_event_confirmation', 'unexpected'])
def test_matrix_rejects_unused_event_or_score_params(key):
    with pytest.raises(TradeError) as error:
        normalize_matrix_params({key: '0'})
    assert error.value.code == 'UNKNOWN_STRATEGY_PARAM'


@pytest.mark.parametrize('key,value', [
    ('ret40', float('nan')), ('retrace20', float('inf')), ('vol_slope20', None),
    ('price_vs_ma20', '-Infinity'), ('up_down_volume_ratio', True),
    ('pullback_days', -1), ('pullback_days', 1.5), ('ma10_above_ma20_days', 'invalid'),
])
def test_matrix_rejects_bad_metrics_before_they_can_change_top_n(key, value):
    with pytest.raises(TradeError) as error:
        evaluate_matrix_pool([candidate(0), candidate(1, **{key: value})])
    assert error.value.code == 'INVALID_MATRIX_CANDIDATE'


@pytest.mark.parametrize('duplicate,error_code', [
    ({'dataset_id': f'{1:064x}'}, 'DUPLICATE_DATASET'),
    ({'symbol': '600000'}, 'DUPLICATE_SYMBOL'),
    ({'symbol': 'SH600000'}, 'DUPLICATE_SYMBOL'),
    ({'symbol': '600000.SH'}, 'DUPLICATE_SYMBOL'),
    ({'symbol': 'sh600000 '}, 'DUPLICATE_SYMBOL'),
    ({'symbol': 'unexpected'}, 'INVALID_MATRIX_CANDIDATE'),
])
def test_matrix_rejects_duplicates_including_canonical_equity_aliases(duplicate, error_code):
    with pytest.raises(TradeError) as error:
        evaluate_matrix_pool([candidate(0), candidate(1, **duplicate)])
    assert error.value.code == error_code
