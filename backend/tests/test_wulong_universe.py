from copy import deepcopy
from datetime import date, timedelta
import json
from math import sin
from pathlib import Path
from types import SimpleNamespace

import pytest

import hashlib

from legacy_oracle import oracle
from trade_app.platform.types import TradeError
from trade_app.research.wulong_universe import (
    DEFAULT_UNIVERSE_PARAMS, MINIMUM_BARS, candidate_metrics_from_bars,
    evaluate_wulong_candidate, evaluate_wulong_universe, normalize_universe_params,
)


def _bars(count=90, flat=False):
    result = []
    for index in range(count):
        close = 10.0 if flat else round(10 + index * 0.08 + sin(index * 0.9) * 0.4, 4)
        result.append({'event_date': (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
                       'open': close - 0.1, 'high': close + 0.2, 'low': close - 0.2,
                       'close': close, 'volume': 1000 + index * 17})
    return result


def _legacy_model(bars):
    from app.models import CandlePoint
    from app.store import InMemoryStore
    candles = [CandlePoint(time=bar['event_date'], open=bar['open'], high=bar['high'],
                          low=bar['low'], close=bar['close'], volume=bar['volume'], amount=0)
               for bar in bars]
    stub = SimpleNamespace(
        _ensure_candles=lambda _symbol: candles,
        _slice_candles_as_of=lambda items, _as_of: (items, None),
        _safe_mean=InMemoryStore._safe_mean,
        _hash_seed=lambda _symbol: 0,
        _resolve_symbol_name=lambda _symbol: '固定对照样本',
    )
    return InMemoryStore._build_row_from_candles(stub, 'sh600000')


def _bars_key(bars):
    return hashlib.sha256(json.dumps(bars, sort_keys=True).encode()).hexdigest()[:24]


def _legacy_row(bars):
    """Original store candidate row (field dict) and whether the original plugin admitted it."""
    def compute():
        from app.core.strategy_plugins import WulongClusterPlugin
        row = _legacy_model(bars)
        admitted = WulongClusterPlugin().build_universe(candidates=[row], params=normalize_universe_params(), mode='strict')
        return {'row': row.model_dump(), 'admitted': bool(admitted)}
    return oracle('row:' + _bars_key(bars), compute)


@pytest.mark.parametrize('count,flat', [(30, False), (40, False), (90, False), (90, True)])
def test_metrics_match_legacy_store_path(count, flat):
    bars = _bars(count, flat)
    original = deepcopy(bars)
    metrics = candidate_metrics_from_bars(bars)
    old = _legacy_row(bars)
    assert metrics == {key: old['row'][key] for key in metrics}
    result = evaluate_wulong_universe(bars)
    assert result['passed'] is old['admitted']
    assert len(result['checks']) == 8
    assert bars == original
    if flat:
        assert metrics['ma10_above_ma20_days'] == metrics['ma5_above_ma10_days'] == 19


def test_registry_defaults_not_plugin_fallback_defaults():
    catalog_path = Path(__file__).parents[1] / 'trade_app/research/legacy_catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    # Frozen catalog stores descriptors under strategies.
    rows = catalog['strategies'] if isinstance(catalog, dict) else catalog
    row = next(item for item in rows if item['strategy_id'] == 'wulong_cluster_v1')
    for key, value in DEFAULT_UNIVERSE_PARAMS.items():
        expected = row['default_params'][key]
        assert value is expected if isinstance(value, bool) else float(value) == expected


def _boundary_metrics():
    return {'ret40': 0.06, 'retrace20': 0.18, 'up_down_volume_ratio': 1.08,
            'vol_slope20': 0, 'ma10_above_ma20_days': 4, 'ma5_above_ma10_days': 3,
            'has_upper_shadow_risk': False, 'has_blowoff_top': False}


@pytest.mark.parametrize('metric,value,failed_param', [
    ('ret40', 0.0599, 'min_ret40'), ('retrace20', 0.1801, 'max_retrace20'),
    ('up_down_volume_ratio', 1.0799, 'min_up_down_volume_ratio'),
    ('vol_slope20', -0.0001, 'min_vol_slope20'),
    ('ma10_above_ma20_days', 3, 'min_ma10_above_ma20_days'),
    ('ma5_above_ma10_days', 2, 'min_ma5_above_ma10_days'),
    ('has_upper_shadow_risk', True, 'allow_upper_shadow_risk'),
    ('has_blowoff_top', True, 'allow_blowoff_top'),
])
def test_all_eight_gates_match_legacy_inclusive_boundaries(metric, value, failed_param):
    metrics = _boundary_metrics()
    assert evaluate_wulong_candidate(metrics)['passed'] is True
    boundary = dict(metrics)
    metrics[metric] = value
    result = evaluate_wulong_candidate(metrics)

    def legacy_gates():
        from app.core.strategy_plugins import WulongClusterPlugin
        plugin = WulongClusterPlugin()
        return {'boundary': bool(plugin.build_universe(candidates=[SimpleNamespace(**boundary)],
                                                       params=normalize_universe_params(), mode='strict')),
                'changed': bool(plugin.build_universe(candidates=[SimpleNamespace(**metrics)],
                                                      params=result['params'], mode='strict'))}
    legacy = oracle(f'gate:{metric}', legacy_gates)
    assert legacy['boundary'] is True
    assert result['passed'] is False
    assert result['failed_conditions'] == [failed_param]
    assert len(result['reasons']) == 1
    assert legacy['changed'] is False


@pytest.mark.parametrize('param,risk', [('allow_upper_shadow_risk', 'has_upper_shadow_risk'),
                                     ('allow_blowoff_top', 'has_blowoff_top')])
def test_false_strings_do_not_enable_risks(param, risk):
    metrics = _boundary_metrics()
    metrics[risk] = True
    for value in (False, 'false', 'FALSE'):
        result = evaluate_wulong_candidate(metrics, {param: value})
        assert result['params'][param] is False
        assert result['passed'] is False
    for value in (True, 'true', 'TRUE'):
        assert evaluate_wulong_candidate(metrics, {param: value})['passed'] is True
    for value in ('yes', '0', 0, 1, None):
        with pytest.raises(TradeError):
            normalize_universe_params({param: value})


@pytest.mark.parametrize('raw', [
    {'min_ret40': '1.2001'}, {'max_retrace20': '0.009'},
    {'min_up_down_volume_ratio': '3.01'}, {'min_vol_slope20': '-0.5001'},
    {'min_ma10_above_ma20_days': '31'}, {'min_ma5_above_ma10_days': '2.5'},
    {'min_ret40': 'NaN'}, {'unknown_param': '1'},
])
def test_invalid_params_rejected_before_evaluation(raw):
    with pytest.raises(TradeError):
        evaluate_wulong_universe([], raw)


def test_minimum_samples_and_explicit_failed_conditions():
    for count in (0, MINIMUM_BARS - 1):
        result = evaluate_wulong_universe(_bars(count))
        assert result['passed'] is False
        assert result['status'] == 'insufficient_data'
        assert result['metrics'] is None
        assert result['required_bars'] == MINIMUM_BARS
        assert result['observed_bars'] == count
        assert result['failed_conditions'] == ['minimum_bars']
    assert evaluate_wulong_universe(_bars(MINIMUM_BARS))['status'] != 'insufficient_data'


def test_risk_metrics_preserve_strict_thresholds_and_store_volume_reference():
    bars = _bars(50, flat=True)
    for bar in bars:
        bar['volume'] = 1000
    bars[-15].update(open=10, volume=2500)
    bars[-1].update(open=10, high=11, low=9)
    exact = candidate_metrics_from_bars(bars)
    assert exact['has_blowoff_top'] is False
    assert exact['has_upper_shadow_risk'] is False
    bars[-15]['volume'] = 2501
    bars[-1]['high'] = 11.01
    metrics = candidate_metrics_from_bars(bars)
    assert metrics['has_blowoff_top'] is True
    assert metrics['has_upper_shadow_risk'] is True
    old = _legacy_row(bars)['row']
    assert metrics == {key: old[key] for key in metrics}


def test_zero_volume_remains_finite_and_matches_original():
    bars = _bars(50)
    for bar in bars:
        bar['volume'] = 0
    metrics = candidate_metrics_from_bars(bars)
    old = _legacy_row(bars)['row']
    assert metrics == {key: old[key] for key in metrics}
    json.dumps(evaluate_wulong_universe(bars), allow_nan=False)
