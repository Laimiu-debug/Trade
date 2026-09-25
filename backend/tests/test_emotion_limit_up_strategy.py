from app.core.emotion_limit_up_strategy import (
    _day_golden_cross,
    _day_purple_to_yellow,
    calculate_emotion_limit_up_signal,
    evaluate_emotion_limit_up_signal,
    evaluate_next_day_entry,
)
from app.models import CandlePoint


def _candle(
    day: str,
    close: float,
    volume: float,
    *,
    open_: float | None = None,
    low: float | None = None,
) -> CandlePoint:
    open_price = close if open_ is None else open_
    low_price = close if low is None else low
    return CandlePoint(
        time=day,
        open=open_price,
        high=max(open_price, close) * 1.01,
        low=low_price,
        close=close,
        volume=volume,
        amount=float(close) * float(volume),
    )


def test_signal_helpers_detect_cross_and_flip() -> None:
    main_force = [0.0, 1.0, 1.2, 1.0, 0.9, 1.3]
    retail_force = [1.0, 1.2, 1.4, 1.3, 1.2, 1.0]
    assert _day_purple_to_yellow(main_force, 5) is True
    assert _day_golden_cross(main_force, retail_force, 5) is True


def _build_limit_up_series() -> list[CandlePoint]:
    candles = [
        _candle("2026-01-02", 10.0, 100_000),
        _candle("2026-01-03", 9.0, 400_000),
        _candle("2026-01-06", 8.0, 350_000),
        _candle("2026-01-07", 9.0, 500_000),
        _candle("2026-01-08", 10.0, 520_000),
        _candle("2026-01-09", 10.1, 300_000),
        _candle("2026-01-10", 10.2, 280_000),
        _candle("2026-01-11", 10.3, 260_000),
        _candle("2026-01-12", 10.4, 240_000),
        _candle("2026-01-13", 10.5, 220_000),
        _candle("2026-01-14", 10.6, 200_000),
        _candle("2026-01-15", 10.7, 180_000),
        _candle("2026-01-16", 10.8, 160_000),
        _candle("2026-01-17", 10.9, 140_000),
    ]
    prev_close = candles[-1].close
    candles.append(_candle("2026-01-20", prev_close * 1.102, 250_000, open_=prev_close, low=prev_close * 1.04))
    return candles


def test_emotion_limit_up_detects_limit_up_with_recent_signal() -> None:
    candles = _build_limit_up_series()
    indicator = calculate_emotion_limit_up_signal(candles)
    evaluation = evaluate_emotion_limit_up_signal(indicator)
    assert indicator["has_data"] is True
    assert indicator["limit_up"] is True
    assert indicator["has_purple_to_yellow"] or indicator["has_golden_cross"]
    assert evaluation["signal"] is True
    assert float(indicator["signal_score"]) > 0.0


def test_emotion_limit_up_rejects_without_limit_up() -> None:
    candles = [_candle(f"2026-02-{index + 1:02d}", 10.0 + index * 0.05, 100_000) for index in range(20)]
    indicator = calculate_emotion_limit_up_signal(candles)
    evaluation = evaluate_emotion_limit_up_signal(indicator)
    assert evaluation["signal"] is False


def test_next_day_entry_matrix() -> None:
    limit_day = _candle("2026-03-01", 11.0, 200_000, open_=10.0, low=10.5)
    good_next = _candle("2026-03-02", 11.2, 450_000, open_=10.9, low=10.8)
    bad_next = _candle("2026-03-02", 10.2, 100_000, open_=10.8, low=10.0)

    good_eval = evaluate_next_day_entry(limit_day, good_next)
    bad_eval = evaluate_next_day_entry(limit_day, bad_next)

    assert good_eval["entry_allowed"] is True
    assert good_eval["action"] == "尾盘买入"
    assert bad_eval["entry_allowed"] is False
    assert bad_eval["action"] == "放弃"
