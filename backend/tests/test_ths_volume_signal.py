from __future__ import annotations

from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from app.models import CandlePoint


def _candle(day: str, close: float, volume: int) -> CandlePoint:
    return CandlePoint(
        time=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        amount=float(close) * float(volume),
    )


def test_indicator_detects_purple_to_yellow_flip() -> None:
    candles = [
        _candle("2026-01-02", 10.0, 100_000),
        _candle("2026-01-03", 11.0, 100_000),
        _candle("2026-01-06", 12.0, 100_000),
        _candle("2026-01-07", 11.0, 100_000),
        _candle("2026-01-08", 10.0, 100_000),
        _candle("2026-01-09", 11.0, 200_000),
    ]

    snapshot = calculate_ths_main_retail_signal(candles)

    assert snapshot["purple_to_yellow"] is True
    assert snapshot["golden_cross"] is False
    assert snapshot["trigger_date"] == "2026-01-09"


def test_indicator_detects_golden_cross_marker() -> None:
    candles = [
        _candle("2026-01-02", 10.0, 100_000),
        _candle("2026-01-03", 9.0, 400_000),
        _candle("2026-01-06", 8.0, 350_000),
        _candle("2026-01-07", 9.0, 500_000),
        _candle("2026-01-08", 10.0, 520_000),
    ]

    snapshot = calculate_ths_main_retail_signal(candles)

    assert snapshot["golden_cross"] is True
    assert snapshot["trigger_date"] == "2026-01-08"
    assert float(snapshot["main_force"]) > float(snapshot["retail_force"])


def test_indicator_rewards_main_force_explosion_and_main_vs_retail_gap() -> None:
    base_path = [
        ("2026-01-02", 10.0, 100_000),
        ("2026-01-03", 9.0, 220_000),
        ("2026-01-06", 8.8, 200_000),
        ("2026-01-07", 9.1, 180_000),
        ("2026-01-08", 9.4, 220_000),
    ]
    mild = calculate_ths_main_retail_signal([_candle(day, close, volume) for day, close, volume in base_path])
    explosive = calculate_ths_main_retail_signal(
        [_candle(day, close, volume) for day, close, volume in [*base_path, ("2026-01-09", 10.2, 2_400_000)]]
    )

    assert float(explosive["main_force_power_score"]) > float(mild["main_force_power_score"])
    assert float(explosive["force_gap_score"]) > float(mild["force_gap_score"])
    assert float(explosive["signal_score"]) > float(mild["signal_score"])
