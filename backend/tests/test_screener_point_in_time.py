from datetime import date, timedelta

import pytest

from trade_app.research.screener_metrics import build_candidate


def _bars(count):
    start = date(2025, 1, 1)
    rows = []
    for index in range(count):
        day = (start + timedelta(days=index)).isoformat()
        close = 10 + index / 100
        rows.append({'event_date': day, 'open': str(close), 'high': str(close + .3),
                     'low': str(close - .3), 'close': str(close),
                     'volume': 100_000 + index * 100, 'amount': '2000000',
                     'available_at': day + 'T07:00:00+00:00'})
    return rows


def _dataset(rows, key='a'):
    return {'id': key * 64, 'symbol': '600000', 'bar_count': len(rows), 'bars': rows}


def _candidate(dataset, as_of):
    return build_candidate(dataset, as_of, 40, float_shares=20_000_000,
                           float_shares_as_of_date='2025-01-01')


@pytest.mark.parametrize('eligible_count', [41, 250, 251])
def test_future_file_extent_never_changes_historical_pool_eligibility(eligible_count):
    complete = _bars(400)
    historical = complete[:eligible_count]
    cutoff = historical[-1]['event_date']
    known = _candidate(_dataset(historical), cutoff)
    extended = _candidate(_dataset(complete, 'b'), cutoff)
    if eligible_count <= 250:
        assert known is None
        assert extended is None
    else:
        assert known is not None and extended is not None
        assert known.model_dump(exclude={'dataset_id'}) == extended.model_dump(exclude={'dataset_id'})


def test_unavailable_251st_bar_cannot_supply_history_eligibility():
    rows = _bars(251)
    cutoff = rows[-1]['event_date']
    # Shanghai's next midnight is 16:00 UTC. Equality is not yet available.
    rows[-1]['available_at'] = cutoff + 'T16:00:00+00:00'
    assert _candidate(_dataset(rows), cutoff) is None
    assert _candidate(_dataset(rows[:-1], 'b'), cutoff) is None


def test_delayed_latest_bar_is_excluded_from_metrics_and_source_date():
    rows = _bars(252)
    cutoff = rows[-1]['event_date']
    historical = rows[:-1]
    rows[-1] = {**rows[-1], 'open': '1000', 'high': '2000', 'low': '1',
                'close': '1500', 'volume': 1_000_000_000,
                'available_at': cutoff + 'T16:00:00+00:00'}
    delayed = _candidate(_dataset(rows), cutoff)
    known = _candidate(_dataset(historical, 'b'), cutoff)
    assert delayed is not None and known is not None
    assert delayed.model_dump(exclude={'dataset_id', 'quality_flags'}) == known.model_dump(
        exclude={'dataset_id', 'quality_flags'})
    assert delayed.as_of_date == historical[-1]['event_date']
    assert 'BARS_AFTER_DECISION_EXCLUDED' in delayed.quality_flags
    assert 'LATEST_BAR_BEFORE_AS_OF_DATE' in delayed.quality_flags
    assert 'LATEST_BAR_BEFORE_AS_OF_DATE' in known.quality_flags
    # A missing requested-day bar can be a holiday; this alone is not degradation.
    assert known.degraded is False and delayed.degraded is False


def test_same_day_complete_history_has_no_source_date_gap_flag():
    rows = _bars(251)
    candidate = _candidate(_dataset(rows), rows[-1]['event_date'])
    assert candidate is not None
    assert 'LATEST_BAR_BEFORE_AS_OF_DATE' not in candidate.quality_flags
