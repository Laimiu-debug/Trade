"""Five-dragon cluster frozen-bar indicator from the legacy strategy plugin."""
from __future__ import annotations
import math
from typing import Any
from trade_app.research.trend_king_domain import CandlePoint

def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))
def _rolling_mean(values: list[float], window: int) -> list[float]:
    out = [math.nan] * len(values)
    if window <= 0 or len(values) < window:
        return out
    running = 0.0
    for idx, value in enumerate(values):
        running += float(value)
        if idx >= window:
            running -= float(values[idx - window])
        if idx >= window - 1:
            out[idx] = running / float(window)
    return out


def _safe_ratio(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if not math.isfinite(float(numerator)):
        return float(fallback)
    if not math.isfinite(float(denominator)) or abs(float(denominator)) <= 1e-9:
        return float(fallback)
    return float(numerator) / float(denominator)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return float(parsed)


def calculate_wulong_cluster_signal(candles: list[CandlePoint]) -> dict[str, Any]:
    periods = (5, 10, 20, 30, 60)
    trigger_date = str(candles[-1].time) if candles else ""
    base: dict[str, Any] = {
        "trigger_date": trigger_date,
        "has_data": False,
        "bullish_alignment": False,
        "close_above_all_mas": False,
        "rising_ma_count": 0,
        "current_spread_pct": 0.0,
        "convergence_spread_pct": math.nan,
        "pre_convergence_max_spread_pct": 0.0,
        "convergence_offset_days": -1,
        "spread_expansion_multiple": 0.0,
        "volume_ratio_20": 0.0,
        "breakout_ratio_pct": 0.0,
        "daily_return_pct": 0.0,
        "ma_values": {},
    }
    if len(candles) < max(periods) + 5:
        return base

    closes = [max(0.0, float(point.close)) for point in candles]
    highs = [max(0.0, float(point.high)) for point in candles]
    volumes = [max(0.0, float(point.volume)) for point in candles]
    ma_series = {period: _rolling_mean(closes, period) for period in periods}
    vol_ma20_series = _rolling_mean(volumes, 20)
    last_idx = len(candles) - 1

    current_ma_values: dict[str, float] = {}
    current_mas: list[float] = []
    for period in periods:
        current_value = ma_series[period][last_idx]
        if not math.isfinite(current_value):
            return base
        current_mas.append(float(current_value))
        current_ma_values[f"ma{period}"] = round(float(current_value), 6)

    current_close = max(0.01, closes[last_idx])
    current_spread_pct = _safe_ratio(max(current_mas) - min(current_mas), current_close)
    close_above_all_mas = current_close > max(current_mas)
    bullish_alignment = all(current_mas[idx] > current_mas[idx + 1] for idx in range(len(current_mas) - 1))

    rise_probe_offset = min(3, last_idx)
    rising_ma_count = 0
    if rise_probe_offset > 0:
        probe_idx = last_idx - rise_probe_offset
        for period in periods:
            previous_value = ma_series[period][probe_idx]
            current_value = ma_series[period][last_idx]
            if math.isfinite(previous_value) and math.isfinite(current_value) and current_value > previous_value:
                rising_ma_count += 1

    recent_high_start = max(0, last_idx - 20)
    previous_high = max(highs[recent_high_start:last_idx], default=highs[last_idx])
    breakout_ratio_pct = _safe_ratio(current_close - previous_high, max(0.01, previous_high))
    daily_return_pct = (
        _safe_ratio(current_close - closes[last_idx - 1], max(0.01, closes[last_idx - 1]))
        if last_idx > 0
        else 0.0
    )
    volume_ratio_20 = _safe_ratio(volumes[last_idx], vol_ma20_series[last_idx], 0.0)

    search_start = max(max(periods) - 1, last_idx - 12)
    convergence_idx = -1
    convergence_spread_pct = math.nan
    for idx in range(search_start, last_idx):
        mas = [ma_series[period][idx] for period in periods]
        if any(not math.isfinite(value) for value in mas):
            continue
        price = max(0.01, closes[idx])
        spread_pct = _safe_ratio(max(mas) - min(mas), price, math.inf)
        if not math.isfinite(convergence_spread_pct) or spread_pct < convergence_spread_pct:
            convergence_idx = idx
            convergence_spread_pct = spread_pct

    pre_convergence_max_spread_pct = 0.0
    if convergence_idx >= 0:
        pre_start = max(max(periods) - 1, convergence_idx - 15)
        for idx in range(pre_start, convergence_idx):
            mas = [ma_series[period][idx] for period in periods]
            if any(not math.isfinite(value) for value in mas):
                continue
            price = max(0.01, closes[idx])
            spread_pct = _safe_ratio(max(mas) - min(mas), price)
            pre_convergence_max_spread_pct = max(pre_convergence_max_spread_pct, spread_pct)

    spread_expansion_multiple = (
        _safe_ratio(current_spread_pct, max(convergence_spread_pct, 1e-6), 0.0)
        if convergence_idx >= 0 and math.isfinite(convergence_spread_pct)
        else 0.0
    )

    base.update(
        {
            "has_data": True,
            "bullish_alignment": bullish_alignment,
            "close_above_all_mas": close_above_all_mas,
            "rising_ma_count": rising_ma_count,
            "current_spread_pct": round(current_spread_pct, 6),
            "convergence_spread_pct": round(convergence_spread_pct, 6) if math.isfinite(convergence_spread_pct) else math.nan,
            "pre_convergence_max_spread_pct": round(pre_convergence_max_spread_pct, 6),
            "convergence_offset_days": last_idx - convergence_idx if convergence_idx >= 0 else -1,
            "spread_expansion_multiple": round(spread_expansion_multiple, 6),
            "volume_ratio_20": round(volume_ratio_20, 6),
            "breakout_ratio_pct": round(breakout_ratio_pct, 6),
            "daily_return_pct": round(daily_return_pct, 6),
            "ma_values": current_ma_values,
        }
    )
    return base


def evaluate_wulong_cluster_signal(indicator: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_params = params if isinstance(params, dict) else {}
    convergence_threshold_pct = _safe_float(raw_params.get("convergence_threshold_pct"), 0.012)
    pre_convergence_min_spread_pct = _safe_float(raw_params.get("pre_convergence_min_spread_pct"), 0.03)
    min_current_spread_pct = _safe_float(raw_params.get("min_current_spread_pct"), 0.015)
    min_spread_expansion_multiple = _safe_float(raw_params.get("min_spread_expansion_multiple"), 1.8)
    min_volume_ratio20 = _safe_float(raw_params.get("min_volume_ratio20"), 1.3)
    min_breakout_return_pct = _safe_float(raw_params.get("min_breakout_return_pct"), 0.008)
    max_convergence_age_days = max(1, _safe_int(raw_params.get("max_convergence_age_days"), 8))
    min_rising_ma_count = max(1, min(5, _safe_int(raw_params.get("min_rising_ma_count"), 4)))

    has_data = bool(indicator.get("has_data"))
    bullish_alignment = bool(indicator.get("bullish_alignment"))
    close_above_all_mas = bool(indicator.get("close_above_all_mas"))
    rising_ma_count = max(0, _safe_int(indicator.get("rising_ma_count"), 0))
    current_spread_pct = max(0.0, _safe_float(indicator.get("current_spread_pct"), 0.0))
    convergence_spread_pct = _safe_float(indicator.get("convergence_spread_pct"), math.nan)
    pre_convergence_max_spread_pct = max(0.0, _safe_float(indicator.get("pre_convergence_max_spread_pct"), 0.0))
    convergence_offset_days = _safe_int(indicator.get("convergence_offset_days"), -1)
    spread_expansion_multiple = max(0.0, _safe_float(indicator.get("spread_expansion_multiple"), 0.0))
    volume_ratio20 = max(0.0, _safe_float(indicator.get("volume_ratio_20"), 0.0))
    breakout_ratio_pct = _safe_float(indicator.get("breakout_ratio_pct"), 0.0)
    daily_return_pct = _safe_float(indicator.get("daily_return_pct"), 0.0)
    breakout_strength_pct = max(daily_return_pct, breakout_ratio_pct)

    convergence_found = has_data and convergence_offset_days >= 0 and math.isfinite(convergence_spread_pct)
    convergence_ok = convergence_found and convergence_spread_pct <= convergence_threshold_pct
    convergence_recent = convergence_found and convergence_offset_days <= max_convergence_age_days
    pre_dispersion_ok = pre_convergence_max_spread_pct >= pre_convergence_min_spread_pct
    expansion_ok = (
        current_spread_pct >= min_current_spread_pct
        and spread_expansion_multiple >= min_spread_expansion_multiple
    )
    volume_ok = volume_ratio20 >= min_volume_ratio20
    breakout_ok = breakout_strength_pct >= min_breakout_return_pct
    rising_ok = rising_ma_count >= min_rising_ma_count

    convergence_score = (
        _clamp_score((1.0 - (convergence_spread_pct / max(convergence_threshold_pct, 1e-6))) * 100.0)
        if convergence_ok
        else 0.0
    )
    expansion_score = _clamp_score(
        45.0 * _safe_ratio(current_spread_pct, max(min_current_spread_pct, 1e-6), 0.0)
        + 55.0 * _safe_ratio(spread_expansion_multiple, max(min_spread_expansion_multiple, 1e-6), 0.0)
    )
    trend_score = _clamp_score(
        (35.0 if bullish_alignment else 0.0)
        + (25.0 if close_above_all_mas else 0.0)
        + min(40.0, max(0.0, float(rising_ma_count)) / 5.0 * 40.0)
    )
    volume_score = _clamp_score(_safe_ratio(volume_ratio20, max(min_volume_ratio20, 1e-6), 0.0) * 100.0)
    breakout_score = _clamp_score(
        _safe_ratio(breakout_strength_pct, max(min_breakout_return_pct, 1e-6), 0.0) * 100.0
    )
    signal_score = _clamp_score(
        convergence_score * 0.18
        + expansion_score * 0.24
        + trend_score * 0.22
        + volume_score * 0.18
        + breakout_score * 0.18
    )
    health_score = _clamp_score(trend_score * 0.6 + convergence_score * 0.4)
    event_score = _clamp_score(expansion_score * 0.55 + breakout_score * 0.45)
    volatility_score = _clamp_score(volume_score)

    event_grade = "C"
    if signal_score >= 80.0:
        event_grade = "A"
    elif signal_score >= 65.0:
        event_grade = "B"

    return {
        "signal": bool(
            convergence_ok
            and convergence_recent
            and pre_dispersion_ok
            and expansion_ok
            and bullish_alignment
            and close_above_all_mas
            and rising_ok
            and volume_ok
            and breakout_ok
        ),
        "convergence_ok": convergence_ok,
        "convergence_recent": convergence_recent,
        "pre_dispersion_ok": pre_dispersion_ok,
        "expansion_ok": expansion_ok,
        "volume_ok": volume_ok,
        "breakout_ok": breakout_ok,
        "rising_ok": rising_ok,
        "signal_score": signal_score,
        "health_score": health_score,
        "event_score": event_score,
        "trend_score": trend_score,
        "structure_score": expansion_score,
        "phase_score": convergence_score,
        "volatility_score": volatility_score,
        "event_grade": event_grade,
        "breakout_strength_pct": breakout_strength_pct,
        "trigger_date": str(indicator.get("trigger_date") or "").strip(),
        "current_spread_pct": current_spread_pct,
        "convergence_spread_pct": convergence_spread_pct if math.isfinite(convergence_spread_pct) else 0.0,
        "pre_convergence_max_spread_pct": pre_convergence_max_spread_pct,
        "convergence_offset_days": convergence_offset_days,
        "spread_expansion_multiple": spread_expansion_multiple,
        "volume_ratio_20": volume_ratio20,
        "rising_ma_count": rising_ma_count,
        "bullish_alignment": bullish_alignment,
        "close_above_all_mas": close_above_all_mas,
        "ma_values": indicator.get("ma_values") if isinstance(indicator.get("ma_values"), dict) else {},
    }



