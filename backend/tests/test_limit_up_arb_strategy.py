from app.core.limit_up_arb_strategy import (
    calculate_limit_up_arb_signal,
    evaluate_limit_up_arb_exit,
    evaluate_limit_up_arb_signal,
    resolve_limit_up_arb_exit_config,
)
from app.models import CandlePoint


def _candle(day: str, close: float, volume: float, *, open_: float | None = None, low: float | None = None) -> CandlePoint:
    open_price = close if open_ is None else open_
    low_price = low if low is not None else min(open_price, close) * 0.99
    return CandlePoint(
        time=day,
        open=open_price,
        high=max(open_price, close) * 1.01,
        low=low_price,
        close=close,
        volume=volume,
        amount=close * volume,
    )


def test_limit_up_arb_rejects_prev_limit_up_without_today_confirmation() -> None:
    candles = [
        _candle("2026-01-02", 10.0, 100_000),
        _candle("2026-01-03", 9.0, 120_000),
        _candle("2026-01-06", 9.9, 100_000, open_=9.0),
        _candle("2026-01-07", 11.0, 200_000, open_=9.95),
        _candle("2026-01-08", 11.05, 150_000, open_=11.0),
    ]
    indicator = calculate_limit_up_arb_signal(candles, symbol="sz000001")
    evaluation = evaluate_limit_up_arb_signal(indicator)
    assert indicator["prev_limit_up"] is True
    assert evaluation["signal"] is False


def test_limit_up_arb_signal_requires_prev_limit_up_volume_and_gain() -> None:
    candles = [
        _candle("2026-01-02", 10.0, 100_000),
        _candle("2026-01-03", 9.0, 120_000),
        _candle("2026-01-06", 9.9, 100_000, open_=9.0),
        _candle("2026-01-07", 11.0, 200_000, open_=9.95),
        _candle("2026-01-08", 11.5, 260_000, open_=11.0),
    ]
    indicator = calculate_limit_up_arb_signal(candles, symbol="sz000001")
    evaluation = evaluate_limit_up_arb_signal(indicator)
    assert evaluation["signal"] is True
    assert evaluation["volume_ratio_prev"] >= 1.2
    assert evaluation["day_gain"] >= 3.0


def test_limit_up_arb_exit_uses_backtest_style_take_profit() -> None:
    entry = 10.0
    next_day = _candle("2026-01-09", 10.35, 500_000, open_=10.2)
    exit_config = resolve_limit_up_arb_exit_config({"take_profit": 0.03, "stop_loss": 0.0})
    result = evaluate_limit_up_arb_exit(entry, next_day, exit_config=exit_config)
    assert result["hit_take_profit"] is True
    assert result["exit_reason"] == "take_profit"


def test_limit_up_arb_exit_falls_back_to_close_without_take_profit() -> None:
    entry = 10.0
    next_day = _candle("2026-01-09", 10.1, 500_000, open_=10.05)
    result = evaluate_limit_up_arb_exit(entry, next_day, exit_config={"take_profit": 0.0, "stop_loss": 0.0})
    assert result["exit_reason"] == "time_exit"
    assert result["exit_price"] == 10.1
