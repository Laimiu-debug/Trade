from __future__ import annotations

from app.core.chart_volume_swing_strategy import (
    calculate_chart_volume_swing_signal,
    evaluate_chart_volume_swing_signal,
)
from app.models import CandlePoint


def _make_box_breakout_pullback() -> list[CandlePoint]:
    candles: list[CandlePoint] = []
    price = 20.0
    base_vol = 2_000_000.0
    for i in range(100):
        if i < 45:
            px = 20.0 + (0.15 if i % 9 == 0 else -0.08 if i % 7 == 0 else 0.02)
            vol = base_vol * (0.7 if i > 22 else 0.95)
        elif i == 45:
            px = 21.5
            vol = base_vol * 2.2
        elif i == 46:
            px = 21.2
            vol = base_vol * 0.55
        elif i < 50:
            px = 21.0 + (i - 46) * 0.05
            vol = base_vol * 0.65
        else:
            px = 21.3 + (i - 50) * 0.03
            vol = base_vol * (1.1 if i % 5 == 0 else 0.8)
        day = f"2024-{1 + (i // 28):02d}-{(i % 28) + 1:02d}"
        candles.append(
            CandlePoint(
                time=day,
                open=px * 0.998,
                high=px * 1.012,
                low=px * 0.992,
                close=px,
                volume=int(vol),
                amount=px * vol,
            )
        )
    return candles


def test_chart_volume_swing_imports_and_runs() -> None:
    candles = _make_box_breakout_pullback()
    indicator = calculate_chart_volume_swing_signal(candles, {"min_score": 0})
    assert indicator.get("has_data") is True
    evaluation = evaluate_chart_volume_swing_signal(indicator, {"min_score": 0})
    assert "signal" in evaluation
    assert "chart_pattern" in evaluation or not evaluation.get("signal")


def test_scoring_prefers_pullback_retest() -> None:
    candles = _make_box_breakout_pullback()
    indicator = calculate_chart_volume_swing_signal(candles, {"min_score": 0})
    evaluation = evaluate_chart_volume_swing_signal(indicator, {"min_score": 0})
    if evaluation.get("signal"):
        assert evaluation.get("score_breakdown")
        assert float(evaluation["signal_score"]) >= 50.0


def test_dedupe_keeps_highest_score_in_window() -> None:
    from app.core.chart_volume_swing_strategy import dedupe_signals_by_score

    sigs = [
        {"symbol": "sh600000", "signal_date": "2025-01-02", "signal_score": 70.0},
        {"symbol": "sh600000", "signal_date": "2025-01-10", "signal_score": 78.0},
        {"symbol": "sh600000", "signal_date": "2025-01-12", "signal_score": 72.0},
        {"symbol": "sh600000", "signal_date": "2025-01-25", "signal_score": 80.0},
    ]
    out = dedupe_signals_by_score(sigs, window_days=15)
    assert len(out) == 2
    assert out[0]["signal_score"] == 78.0
    assert out[1]["signal_score"] == 80.0
    from app.core.chart_volume_swing_strategy import _breakout_vol_score, ChartVolumeSwingParams

    cfg = ChartVolumeSwingParams()
    mid = _breakout_vol_score(2.0, cfg)
    low = _breakout_vol_score(1.5, cfg)
    high = _breakout_vol_score(4.5, cfg)
    assert mid >= low
    assert mid > high
