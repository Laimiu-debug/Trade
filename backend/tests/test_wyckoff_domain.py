from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from app.core.signal_analyzer import SignalAnalyzer as LegacyAnalyzer
from app.models import CandlePoint as LegacyCandlePoint
from app.store import InMemoryStore
from trade_app.market.domain import eligible_bars
from trade_app.research.wyckoff_domain import (
    CALCULATION_VERSION, MINIMUM_BARS, SOURCE_PATH,
    calculate_snapshot, candidate_from_bars,
)


def event_rich_bars():
    """Deterministic decline, high-volume SC, AR, ST, breakout and LPS sequence."""
    closes = [12 - index * 2 / 59 for index in range(60)] + [
        9.3, 9.5, 9.7, 9.9, 10.1, 10.3, 10.5, 10.7, 10.5, 10.3,
        10.1, 9.9, 9.7, 9.5, 9.5, 9.6, 9.6, 9.6, 9.6, 9.6,
        9.2, 9.5, 10.2, 10.8, 11.2, 11.1, 11.2, 11.3, 11.4, 11.5,
    ]
    volume_by_index = {58: 1800, 60: 10000, 74: 800, 80: 2000,
                       82: 1600, 83: 1800, 84: 2200, 85: 600}
    bars = []
    day = date(2025, 1, 1)
    for index, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        open_price = closes[index - 1] if index else close
        bar = {'event_date': day.isoformat(), 'open': round(open_price, 4),
               'close': round(close, 4), 'high': round(max(open_price, close) + 0.15, 4),
               'low': round(min(open_price, close) - 0.15, 4),
               'volume': volume_by_index.get(index, 1000),
               'available_at': day.isoformat() + 'T08:00:00+00:00'}
        if index == 60:
            bar.update(low=9.0, high=10.2)
        elif index == 74:
            bar.update(low=9.0)
        elif index == 80:
            bar.update(low=8.8)
        bars.append(bar)
        day += timedelta(days=1)
    return bars


def _legacy_inputs(bars):
    candles = [LegacyCandlePoint(time=bar['event_date'], open=bar['open'], high=bar['high'],
                                low=bar['low'], close=bar['close'], volume=bar['volume'], amount=0)
               for bar in bars]
    store = SimpleNamespace(
        _ensure_candles=lambda _symbol: candles,
        _slice_candles_as_of=lambda points, _date: (points, None),
        _safe_mean=InMemoryStore._safe_mean, _hash_seed=lambda _symbol: 0,
        _resolve_symbol_name=lambda _symbol: '事件固定样本',
    )
    return InMemoryStore._build_row_from_candles(store, 'sh600000'), candles


def _assert_parity(bars, window_days=60, profile=None):
    row, candles = _legacy_inputs(bars)
    result = calculate_snapshot(bars, window_days=window_days, profile=profile)
    expected = LegacyAnalyzer.calculate_wyckoff_snapshot(
        row, candles, window_days, event_judgment_profile=profile)
    assert result['snapshot'] == expected
    assert result['candidate'] == {name: getattr(row, name) for name in ('ret40', 'retrace20', 'amplitude20')}
    assert result['event_age_days'] == LegacyAnalyzer._build_event_age_days(
        dates=[point.time for point in candles[-result['effective_window_days']:]],
        event_chain=expected['event_chain'])
    assert result['has_data'] is True
    assert result['source_path'] == SOURCE_PATH
    assert result['calculation_version'] == CALCULATION_VERSION
    assert result['source_date'] == bars[-1]['event_date']
    json.dumps(result, allow_nan=False)
    return result


@pytest.mark.parametrize('count,window_days', [(30, 60), (40, 60), (60, 20), (90, 60), (90, 120)])
def test_full_snapshot_matches_original_with_store_candidate_metrics(count, window_days):
    bars = event_rich_bars()[:count]
    before = deepcopy(bars)
    result = _assert_parity(bars, window_days)
    assert result['required_bars'] == MINIMUM_BARS
    assert result['observed_bars'] == count
    assert result['effective_window_days'] == max(20, min(count, window_days))
    assert bars == before


@pytest.mark.parametrize('count', [0, 1, 24, 25, 29])
def test_short_candidate_history_returns_explicit_insufficient_snapshot(count):
    bars = event_rich_bars()[:count]
    row, candles = _legacy_inputs(bars)
    assert row is None
    assert candidate_from_bars(bars) is None
    result = calculate_snapshot(bars)
    assert result['has_data'] is False
    assert result['candidate'] is None
    assert result['snapshot'] == LegacyAnalyzer._insufficient_data_snapshot(candles)
    assert result['observed_bars'] == count
    assert result['required_bars'] == 30
    assert result['effective_window_days'] == 0
    assert result['event_age_days'] == {}


def test_event_rich_fixture_keeps_dates_age_confirmation_and_quality_scores():
    bars = event_rich_bars()
    result = _assert_parity(bars)
    snapshot = result['snapshot']
    assert snapshot['events'] == ['PS', 'SC', 'AR', 'ST', 'TSO', 'SOS', 'JOC', 'LPS']
    assert snapshot['risk_events'] == ['PSY', 'BC']
    assert snapshot['event_dates']['SC'] == bars[60]['event_date']
    assert snapshot['event_dates']['JOC'] == bars[84]['event_date']
    assert snapshot['event_confirmation_map'] == {'SOS': 'confirmed', 'LPS': 'confirmed', 'JOC': 'confirmed'}
    assert result['event_age_days']['SC'] == 29
    assert result['event_age_days']['LPS'] == 4
    assert snapshot['entry_quality_score'] == 71.9
    assert snapshot['event_chain'] == sorted(snapshot['event_chain'], key=lambda event: event['date'])


def test_event_rules_and_weighted_profile_preserve_original_behavior_and_inputs():
    bars = event_rich_bars()
    baseline = _assert_parity(bars)
    profile = {'score_mode': 'dimension_weighted',
               'rule_values': [{'rule_key': 'enable_sos', 'value': False},
                               {'rule_key': 'enable_joc', 'value': 'false'}],
               'dimensions': [
                   {'dimension_id': 'risk', 'label': '低风险', 'metric_key': 'risk_score',
                    'enabled': True, 'weight': 3, 'invert': True},
                   {'dimension_id': 'context', 'label': '阶段', 'metric_key': 'phase_context_score',
                    'enabled': True, 'weight': 1, 'invert': False},
                   {'dimension_id': 'ignored', 'metric_key': 'event_strength_score',
                    'enabled': False, 'weight': 100},
               ]}
    original_profile = deepcopy(profile)
    customized = _assert_parity(bars, profile=profile)['snapshot']
    assert profile == original_profile
    assert 'SOS' not in customized['events'] and 'JOC' not in customized['events']
    assert customized['event_dimension_breakdown']
    assert [item['dimension_id'] for item in customized['event_dimension_breakdown']] == ['risk', 'context']
    assert customized['event_score'] != baseline['snapshot']['event_score']
    assert customized['entry_quality_score'] != baseline['snapshot']['entry_quality_score']


def test_future_bars_cannot_confirm_prefix_event_before_they_are_available():
    complete_bars = event_rich_bars()
    decision_at = complete_bars[84]['event_date'] + 'T09:00:00+00:00'
    prefix, quality = eligible_bars(complete_bars, decision_at, strict=True)
    assert len(prefix) == 85 and quality == []
    before = _assert_parity(prefix)
    assert before['snapshot']['event_confirmation_map']['JOC'] == 'pending'
    assert before['event_age_days']['JOC'] == 0
    assert before['snapshot']['trigger_date'] <= before['source_date']

    full = _assert_parity(complete_bars)
    assert full['snapshot']['event_confirmation_map']['JOC'] == 'confirmed'
    assert full['snapshot']['event_confirmation_map']['LPS'] == 'confirmed'
    assert full['snapshot']['entry_quality_score'] != before['snapshot']['entry_quality_score']
    assert calculate_snapshot(prefix) == before


def test_store_candidate_rounding_and_zero_volume_flat_bars_stay_finite():
    bars = event_rich_bars()[:40]
    for bar in bars:
        bar.update(open=10.0, high=10.0, low=10.0, close=10.0, volume=0)
    result = _assert_parity(bars)
    assert asdict(candidate_from_bars(bars)) == {'ret40': 0.0, 'retrace20': 0.0, 'amplitude20': 0.0}
    assert all(0 <= result['snapshot'][name] <= 100
               for name in ('entry_quality_score', 'risk_score', 'health_score', 'event_score'))
