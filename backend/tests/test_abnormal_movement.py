from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.abnormal_movement import (
    TRIGGER_THRESHOLD_10,
    WARN_THRESHOLD_10,
    WINDOW_30,
    _max_daily_dev_room,
    _resolve_bars_needed,
    _window_cum_deviation,
    analyze_symbol_abnormal_events,
    resolve_benchmark_key,
    resolve_benchmark_symbol,
    resolve_index_close_for_symbol,
)


def test_resolve_benchmark_for_gem() -> None:
    assert resolve_benchmark_key("sz300750") == "gem"
    symbol, _name = resolve_benchmark_symbol("sz300750")
    assert symbol == "sz399102"


def test_meets_trigger_for_199_97() -> None:
    from app.core.abnormal_movement import TRIGGER_THRESHOLD_30, _meets_trigger

    assert _meets_trigger(199.97, TRIGGER_THRESHOLD_30)
    assert not _meets_trigger(199.94, TRIGGER_THRESHOLD_30)


def test_resolve_index_close_fallback() -> None:
    maps = {"sh_main": {"2026-01-02": 3000.0}, "gem": {}}
    assert resolve_index_close_for_symbol(maps, "sz002552")["2026-01-02"] == 3000.0


def test_max_dev_room_for_10d_window() -> None:
    deviations = [10.0] * 10
    room = _max_daily_dev_room(deviations, 9, 10, TRIGGER_THRESHOLD_10)
    assert room == 10.0


def test_window_cum_deviation() -> None:
    bars = [
        {"date": "2025-01-01", "close": 10.0},
        {"date": "2025-01-02", "close": 11.0},
        {"date": "2025-01-03", "close": 12.1},
    ]
    index = {"2025-01-01": 1000.0, "2025-01-02": 1000.0, "2025-01-03": 1000.0}
    dev = _window_cum_deviation(bars, index, 2, 2)
    assert dev is not None
    assert dev > 20.0


def test_resolve_bars_needed_caps_range() -> None:
    dates = [f"2026-01-{day:02d}" for day in range(1, 61)]
    needed = _resolve_bars_needed(date_from="2026-01-01", date_to="2026-03-01", trading_dates=dates)
    assert WINDOW_30 + 60 <= needed <= 900


def test_snapshot_mode_skips_historical_episodes() -> None:
    bars: list[dict[str, object]] = []
    index_close: dict[str, float] = {}
    close = 10.0
    index_level = 1000.0
    start = date(2025, 1, 2)
    for i in range(80):
        day = (start + timedelta(days=i)).isoformat()
        bars.append(
            {
                "date": day,
                "open": close,
                "high": close * 1.08,
                "low": close * 0.98,
                "close": close,
                "volume": 1000,
                "amount": 1e7,
            }
        )
        index_close[day] = index_level
        if i > 0:
            close *= 1.08 if i <= 14 else 1.0
            index_level *= 1.0005

    date_to = (start + timedelta(days=79)).isoformat()
    full = analyze_symbol_abnormal_events(
        symbol="sz300002",
        name="测试",
        bars=bars,
        index_close_by_date=index_close,
        date_from="2025-02-01",
        date_to=date_to,
        include_warnings=True,
        snapshot_only=False,
    )
    snap = analyze_symbol_abnormal_events(
        symbol="sz300002",
        name="测试",
        bars=bars,
        index_close_by_date=index_close,
        date_from="2025-02-01",
        date_to=date_to,
        include_warnings=True,
        snapshot_only=True,
    )
    assert len(snap) <= len(full)


def test_analyze_symbol_trigger_episode() -> None:
    bars: list[dict[str, object]] = []
    index_close: dict[str, float] = {}
    close = 10.0
    index_level = 1000.0
    start = date(2025, 1, 2)
    for i in range(40):
        day = (start + timedelta(days=i)).isoformat()
        bars.append(
            {
                "date": day,
                "open": close,
                "high": close * 1.12,
                "low": close * 0.98,
                "close": close,
                "volume": 1000,
                "amount": 1e7,
            }
        )
        index_close[day] = index_level
        if i > 0:
            close *= 1.12 if i <= 12 else 1.02
            index_level *= 1.001
        else:
            close = 10.0

    events = analyze_symbol_abnormal_events(
        symbol="sz300001",
        name="测试",
        bars=bars,
        index_close_by_date=index_close,
        date_from="2025-01-02",
        date_to=(start + timedelta(days=39)).isoformat(),
        include_warnings=True,
    )
    assert events
    assert any(item.entry_date for item in events)
    assert any(item.daily_limits for item in events)
