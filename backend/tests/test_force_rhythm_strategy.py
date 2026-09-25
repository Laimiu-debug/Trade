from __future__ import annotations

from app.core.force_rhythm_strategy import (
    calculate_force_rhythm_signal,
    detect_rhythm_wave_pattern,
    evaluate_force_rhythm_exit,
    evaluate_force_rhythm_signal,
)
from app.models import CandlePoint


def _candle(day: str, close: float, volume: int, *, open_price: float | None = None) -> CandlePoint:
    open_value = float(open_price if open_price is not None else close)
    return CandlePoint(
        time=day,
        open=open_value,
        high=max(open_value, close) * 1.01,
        low=min(open_value, close) * 0.99,
        close=close,
        volume=volume,
        amount=float(close) * float(volume),
    )


def _build_rhythmic_wave_candles(*, cycle_length: int = 14, cycles: int = 6) -> list[CandlePoint]:
    candles: list[CandlePoint] = []
    price = 20.0
    day_index = 1
    for cycle in range(cycles):
        for phase in range(cycle_length):
            if phase < cycle_length // 2:
                next_price = price + 0.35
                volume = 280_000 + phase * 12_000
            else:
                next_price = price - 0.22
                volume = 90_000 + phase * 3_000
            day = f"2026-01-{day_index:02d}"
            candles.append(_candle(day, next_price, volume, open_price=price))
            price = next_price
            day_index += 1

    turn_day = f"2026-01-{day_index:02d}"
    turn_price = price + 0.45
    candles.append(_candle(turn_day, turn_price, 320_000, open_price=price))
    return candles


def _base_buy_indicator(**overrides: object) -> dict[str, object]:
    indicator: dict[str, object] = {
        "has_data": True,
        "cycle_count": 4,
        "cycle_cv": 0.25,
        "amplitude_cv": 0.30,
        "autocorr": 0.20,
        "wave_swing_ratio": 0.30,
        "trough_turn": True,
        "trough_percentile": 0.22,
        "prev_main_force_state": "falling",
        "main_force_state": "rising",
        "deep_trough_streak": 0,
        "recent_flip_bars_ago": -1,
        "recent_flip_trough_percentile": 0.5,
        "retail_percentile": 0.35,
        "main_force": 12.0,
        "retail_force": 8.0,
        "prev_retail_force": 7.5,
        "retail_force_state": "flat",
        "signal_score": 10.0,
        "rhythm_regularity_score": 5.0,
        "main_force_power_score": 70.0,
        "trigger_date": "2026-03-01",
    }
    indicator.update(overrides)
    return indicator


def test_regular_wave_forms_pattern_and_triggers_at_trough_turn() -> None:
    candles = _build_rhythmic_wave_candles()
    indicator = calculate_force_rhythm_signal(candles)
    evaluation = evaluate_force_rhythm_signal(indicator)

    assert indicator["has_data"] is True
    assert indicator["rhythm_pattern_formed"] is True
    assert int(indicator["cycle_count"]) >= 3
    assert indicator["trough_turn"] is True
    if indicator["retail_sell_signal"]:
        assert evaluation["signal"] is False
    else:
        assert evaluation["signal"] is True


def test_irregular_noise_does_not_form_pattern_or_trigger() -> None:
    candles: list[CandlePoint] = []
    price = 15.0
    for index in range(1, 81):
        drift = ((-1) ** index) * 0.05 + (index % 7) * 0.01
        volume = 100_000 + (index * 17_000) % 90_000
        price = max(5.0, price + drift)
        candles.append(_candle(f"2026-02-{index:02d}", price, volume))

    indicator = calculate_force_rhythm_signal(candles)
    evaluation = evaluate_force_rhythm_signal(indicator)

    assert indicator["rhythm_pattern_formed"] is False
    assert evaluation["signal"] is False


def test_signal_does_not_use_score_threshold() -> None:
    indicator = _base_buy_indicator(
        signal_score=10.0,
        rhythm_regularity_score=5.0,
        trough_percentile=0.48,
        trough_turn=False,
    )
    pattern = detect_rhythm_wave_pattern(indicator)
    evaluation = evaluate_force_rhythm_signal(indicator)

    assert pattern["rhythm_pattern_formed"] is True
    assert evaluation["signal"] is True
    assert evaluation["trigger_reason"] == "flip_band"


def test_retail_sell_blocks_buy_when_enabled() -> None:
    indicator = _base_buy_indicator(
        retail_force=20.0,
        main_force=10.0,
        retail_force_state="rising",
        retail_percentile=0.72,
        trough_percentile=0.48,
        trough_turn=False,
    )
    evaluation = evaluate_force_rhythm_signal(indicator, {"block_buy_on_retail_sell": True})

    assert evaluation["retail_sell_signal"] is True
    assert evaluation["signal"] is False


def test_retail_sell_triggers_exit() -> None:
    indicator = _base_buy_indicator(
        retail_force=20.0,
        main_force=10.0,
        retail_force_state="rising",
        retail_percentile=0.72,
    )
    exit_eval = evaluate_force_rhythm_exit(indicator)

    assert exit_eval["exit_signal"] is True
    assert "散户卖出" in exit_eval["exit_reason"]


def test_main_peak_triggers_exit() -> None:
    indicator = _base_buy_indicator(
        trough_percentile=0.78,
        main_force_state="falling",
    )
    exit_eval = evaluate_force_rhythm_exit(indicator)

    assert exit_eval["exit_signal"] is True
    assert "主力波峰" in exit_eval["exit_reason"]


def test_registry_plugin_uses_pattern_not_score() -> None:
    from app.core.strategy_registry import StrategyRegistry
    from app.models import ScreenerResult

    registry = StrategyRegistry()
    screener_row = ScreenerResult(
        symbol="sz301326",
        name="唯科科技",
        latest_price=20.0,
        day_change=0.1,
        day_change_pct=0.01,
        score=75,
        ret40=0.12,
        turnover20=0.08,
        amount20=8e8,
        amplitude20=0.05,
        retrace20=0.08,
        pullback_days=2,
        ma10_above_ma20_days=5,
        ma5_above_ma10_days=4,
        price_vs_ma20=0.02,
        vol_slope20=0.01,
        up_down_volume_ratio=1.2,
        pullback_volume_ratio=0.8,
        has_blowoff_top=False,
        has_divergence_5d=False,
        has_upper_shadow_risk=False,
        ai_confidence=0.0,
        theme_stage="发酵中",
        trend_class="A",
        stage="Mid",
        labels=[],
        reject_reasons=[],
        degraded=False,
        degraded_reason=None,
    )
    snapshot = {
        "force_rhythm_signal": {
            "has_data": True,
            "rhythm_pattern_formed": True,
            "trough_turn": True,
            "cycle_count": 4,
            "cycle_cv": 0.25,
            "amplitude_cv": 0.30,
            "autocorr": 0.20,
            "wave_swing_ratio": 0.30,
            "trough_percentile": 0.48,
            "prev_main_force_state": "falling",
            "main_force": 12.0,
            "retail_force": 8.0,
            "prev_retail_force": 7.5,
            "main_force_state": "rising",
            "retail_force_state": "flat",
            "retail_percentile": 0.35,
            "deep_trough_streak": 0,
            "recent_flip_bars_ago": -1,
            "recent_flip_trough_percentile": 0.5,
            "signal_score": 5.0,
            "rhythm_regularity_score": 5.0,
            "main_force_power_score": 70.0,
            "trigger_date": "2026-03-01",
        }
    }
    assert registry.generate_signals(
        strategy_id="ths_force_rhythm_v1",
        row=screener_row,
        snapshot=snapshot,
        params={},
    )
