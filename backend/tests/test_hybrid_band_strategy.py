from __future__ import annotations

from app.core.hybrid_band_strategy import (
    calculate_hybrid_band_signal,
    evaluate_hybrid_band_signal,
    resolve_hybrid_band_params,
)
from app.models import CandlePoint


def _make_trend_candles() -> list[CandlePoint]:
    candles: list[CandlePoint] = []
    price = 10.0
    vol = 1_000_000.0
    for i in range(80):
        if i < 40:
            price += 0.02
            v = vol * 0.9
        elif i == 40:
            price += 0.35
            v = vol * 2.5
        elif i < 45:
            price -= 0.03
            v = vol * 0.6
        else:
            price += 0.04
            v = vol * 1.1
        day = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        candles.append(
            CandlePoint(
                time=day,
                open=price * 0.998,
                high=price * 1.01,
                low=price * 0.992,
                close=price,
                volume=int(v),
                amount=price * v,
            )
        )
    return candles


def test_hybrid_band_calculate_and_evaluate() -> None:
    candles = _make_trend_candles()
    indicator = calculate_hybrid_band_signal(candles, {"min_hybrid_score": 0, "min_score": 0})
    assert indicator.get("has_data") is True
    assert "hybrid_score" in indicator
    evaluation = evaluate_hybrid_band_signal(indicator, {"min_hybrid_score": 0})
    assert "signal" in evaluation
    assert float(evaluation.get("signal_score") or 0) >= 0.0


def test_resolve_hybrid_band_params_merges_chart_defaults() -> None:
    cfg = resolve_hybrid_band_params({"min_hybrid_score": 85.0, "breakout_vol_ratio": 1.8})
    assert cfg["min_hybrid_score"] == 85.0
    assert cfg["breakout_vol_ratio"] == 1.8
