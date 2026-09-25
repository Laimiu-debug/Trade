from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.signal_analyzer import SignalAnalyzer
from app.models import CandlePoint, ScreenerResult


def _build_row(symbol: str = "sz300750") -> ScreenerResult:
    return ScreenerResult(
        symbol=symbol,
        name="test",
        latest_price=10.0,
        day_change=0.1,
        day_change_pct=0.01,
        score=80,
        ret40=0.25,
        turnover20=0.08,
        amount20=8e8,
        amplitude20=0.05,
        retrace20=0.03,
        pullback_days=3,
        ma10_above_ma20_days=8,
        ma5_above_ma10_days=6,
        price_vs_ma20=0.06,
        vol_slope20=0.1,
        up_down_volume_ratio=1.4,
        pullback_volume_ratio=0.7,
        has_blowoff_top=False,
        has_divergence_5d=False,
        has_upper_shadow_risk=False,
        ai_confidence=0.7,
        theme_stage="发酵中",
        trend_class="A",
        stage="Mid",
        labels=[],
        reject_reasons=[],
        degraded=False,
        degraded_reason=None,
    )


def _build_candles(start: str = "2025-01-02", count: int = 90) -> list[CandlePoint]:
    out: list[CandlePoint] = []
    cursor = datetime.strptime(start, "%Y-%m-%d")
    price = 10.0
    while len(out) < count:
        if cursor.weekday() >= 5:
            cursor += timedelta(days=1)
            continue
        open_px = price
        close_px = open_px * 1.004
        high_px = close_px * 1.01
        low_px = open_px * 0.99
        out.append(
            CandlePoint(
                time=cursor.strftime("%Y-%m-%d"),
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=120000,
                amount=close_px * 120000,
            )
        )
        cursor += timedelta(days=1)
        price = close_px
    return out


def test_signal_analyzer_outputs_m2_m3_scores() -> None:
    row = _build_row()
    candles = _build_candles()
    snapshot = SignalAnalyzer.calculate_wyckoff_snapshot(row, candles, 60)

    assert 0.0 <= float(snapshot["health_score"]) <= 100.0
    assert 0.0 <= float(snapshot["slope_stability"]) <= 100.0
    assert 0.0 <= float(snapshot["volatility_stability"]) <= 100.0
    assert 0.0 <= float(snapshot["pullback_quality"]) <= 100.0

    assert 0.0 <= float(snapshot["event_score"]) <= 100.0
    assert snapshot["event_grade"] in {"A", "B", "C"}
    assert 0.0 <= float(snapshot["event_background_score"]) <= 100.0
    assert 0.0 <= float(snapshot["event_position_score"]) <= 100.0
    assert 0.0 <= float(snapshot["event_vol_price_score"]) <= 100.0
    assert 0.0 <= float(snapshot["event_confirmation_score"]) <= 100.0
    assert 0.0 <= float(snapshot["candle_quality_score"]) <= 100.0
    assert 0.0 <= float(snapshot["cost_center_shift_score"]) <= 100.0
    assert 0.0 <= float(snapshot["weekly_context_score"]) <= 100.0
    assert 0.85 <= float(snapshot["weekly_context_multiplier"]) <= 1.15
    assert 0.0 <= float(snapshot["event_recency_score"]) <= 100.0
    assert 0.0 <= float(snapshot["phase_context_score"]) <= 100.0
    assert 0.0 <= float(snapshot["risk_score"]) <= 100.0
    assert snapshot["confirmation_status"] in {"confirmed", "partial", "unconfirmed", "risk_blocked"}
    assert isinstance(snapshot["event_confirmation_map"], dict)
    assert isinstance(snapshot["event_grade_map"], dict)


def test_phase_context_soft_gate_penalizes_isolated_breakout_signal() -> None:
    isolated = SignalAnalyzer._calculate_phase_context_score(
        events=["SOS"],
        risk_events=[],
        event_age_days={"SOS": 1},
    )
    contextual = SignalAnalyzer._calculate_phase_context_score(
        events=["SC", "AR", "ST", "SOS"],
        risk_events=[],
        event_age_days={"SC": 8, "AR": 6, "ST": 4, "SOS": 1},
    )

    assert contextual > isolated


def test_event_decay_reduces_stale_event_contribution() -> None:
    row = _build_row()
    common_kwargs = {
        "phase": "吸筹D",
        "events": ["SOS"],
        "risk_events": [],
        "structure_hhh": "HH|HL|HC",
        "row": row,
        "ret20": 0.12,
        "ret10": 0.08,
        "tr_pos": 0.7,
        "sequence_ok": True,
        "health_metrics": {"health_score": 80.0, "slope_stability": 75.0, "volatility_stability": 72.0, "pullback_quality": 78.0},
    }
    fresh = SignalAnalyzer._calculate_scores(
        **common_kwargs,
        event_age_days={"SOS": 1},
    )
    stale = SignalAnalyzer._calculate_scores(
        **common_kwargs,
        event_age_days={"SOS": 30},
    )

    assert float(fresh["event_recency_score"]) > float(stale["event_recency_score"])
    assert float(fresh["event_strength_score"]) > float(stale["event_strength_score"])


def test_weekday_adjusted_volume_baseline_handles_holiday_gap() -> None:
    dates = [
        "2025-01-02",  # Thu
        "2025-01-03",  # Fri
        "2025-01-06",  # Mon
        "2025-01-07",  # Tue
        "2025-01-08",  # Wed
        "2025-01-09",  # Thu
        "2025-01-10",  # Fri
        "2025-01-13",  # Mon
        "2025-01-14",  # Tue
        "2025-01-15",  # Wed
        "2025-01-24",  # Fri (long holiday gap before this)
    ]
    volumes = [100_000, 98_000, 102_000, 99_000, 101_000, 97_000, 100_500, 103_000, 99_500, 100_200, 160_000]

    base = SignalAnalyzer._weekday_adjusted_volume_baseline(
        volumes=volumes,
        dates=dates,
        idx=10,
        window=8,
    )
    ratio = SignalAnalyzer._volume_ratio_with_calendar_adjustment(
        volumes=volumes,
        dates=dates,
        idx=10,
        window=8,
    )

    assert base > 100_000
    assert ratio < 1.6


def test_candle_quality_prefers_confirmed_breakout_bar() -> None:
    opens = [10.0, 10.0]
    highs = [11.2, 10.5]
    lows = [9.9, 9.8]
    closes = [11.0, 9.95]

    strong_breakout = SignalAnalyzer._event_candle_quality_score(
        event_name="SOS",
        idx=0,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
    )
    weak_bar = SignalAnalyzer._event_candle_quality_score(
        event_name="SOS",
        idx=1,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
    )

    assert strong_breakout > weak_bar


def test_cost_center_shift_score_penalizes_price_only_push() -> None:
    row = _build_row()
    stable_price = [10.0 + 0.05 * idx for idx in range(25)]
    stable_volume = [100_000 + (idx % 3 - 1) * 600 for idx in range(25)]
    inflated_price = [
        10.0 + 0.04 * idx if idx < 18 else (10.0 + 0.04 * 17) + (idx - 17) * 0.24
        for idx in range(25)
    ]
    inflated_volume = [150_000 if idx < 18 else 24_000 for idx in range(25)]

    stable_score = SignalAnalyzer._calculate_cost_center_shift_score(
        closes=stable_price,
        volumes=stable_volume,
        row=row,
    )
    inflated_score = SignalAnalyzer._calculate_cost_center_shift_score(
        closes=inflated_price,
        volumes=inflated_volume,
        row=row,
    )

    assert stable_score > inflated_score


def test_weekly_context_metrics_prefers_higher_weekly_trend() -> None:
    dates = _build_candles(start="2025-01-02", count=70)
    down_candles = [bar.model_copy() for bar in dates]
    up_candles = [bar.model_copy() for bar in dates]

    for idx, candle in enumerate(down_candles):
        down_factor = 1.0 - idx * 0.0022
        down_open = max(5.0, 12.0 * down_factor)
        down_close = down_open * 0.998
        candle.open = down_open
        candle.close = down_close
        candle.high = max(down_open, down_close) * 1.005
        candle.low = min(down_open, down_close) * 0.995
        candle.volume = int(95_000 + idx * 200)

    for idx, candle in enumerate(up_candles):
        up_factor = 1.0 + idx * 0.0026
        up_open = 10.0 * up_factor
        up_close = up_open * 1.003
        candle.open = up_open
        candle.close = up_close
        candle.high = max(up_open, up_close) * 1.006
        candle.low = min(up_open, up_close) * 0.995
        candle.volume = int(100_000 + idx * 350)

    down_score, down_multiplier = SignalAnalyzer._calculate_weekly_context_metrics(
        dates=[item.time for item in down_candles],
        opens=[item.open for item in down_candles],
        highs=[item.high for item in down_candles],
        lows=[item.low for item in down_candles],
        closes=[item.close for item in down_candles],
        volumes=[item.volume for item in down_candles],
    )
    up_score, up_multiplier = SignalAnalyzer._calculate_weekly_context_metrics(
        dates=[item.time for item in up_candles],
        opens=[item.open for item in up_candles],
        highs=[item.high for item in up_candles],
        lows=[item.low for item in up_candles],
        closes=[item.close for item in up_candles],
        volumes=[item.volume for item in up_candles],
    )

    assert up_score > down_score
    assert up_multiplier > down_multiplier


def test_event_confirmation_map_marks_sos_confirmed() -> None:
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
    opens = [10.0, 10.1, 10.4, 10.6, 10.7]
    highs = [10.2, 10.3, 10.8, 10.9, 11.0]
    lows = [9.8, 10.0, 10.2, 10.5, 10.6]
    closes = [10.1, 10.2, 10.7, 10.75, 10.8]
    volumes = [100_000, 110_000, 140_000, 120_000, 118_000]

    confirmation = SignalAnalyzer._evaluate_event_confirmation_map(
        event_dates={"SOS": dates[2]},
        dates=dates,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )
    assert confirmation.get("SOS") == "confirmed"


def test_event_confirmation_map_marks_failed_and_pending_cases() -> None:
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
    opens = [10.0, 9.9, 9.8, 9.7, 9.75]
    highs = [10.2, 10.0, 9.95, 9.9, 9.92]
    lows = [9.7, 9.6, 9.4, 9.35, 9.5]
    closes = [9.9, 9.7, 9.8, 9.6, 9.7]
    volumes = [120_000, 118_000, 130_000, 115_000, 112_000]

    confirmation = SignalAnalyzer._evaluate_event_confirmation_map(
        event_dates={"Spring": dates[2], "JOC": dates[-1]},
        dates=dates,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )
    assert confirmation.get("Spring") == "failed"
    assert confirmation.get("JOC") == "pending"
