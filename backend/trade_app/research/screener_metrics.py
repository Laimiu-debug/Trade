"""Port of final-trade tdx_loader screener metrics over frozen daily bars."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from trade_app.research.screener_domain import ScreenerCandidate


def _mean(values: list[float] | list[int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _ma(values: list[float], index: int, period: int) -> float | None:
    return _mean(values[index - period + 1:index + 1]) if index + 1 >= period else None


def build_candidate(dataset: dict, as_of_date: str, return_window_days: int,
                    float_shares: float | None = None,
                    float_shares_as_of_date: str | None = None) -> ScreenerCandidate | None:
    cutoff = datetime.combine(date.fromisoformat(as_of_date) + timedelta(days=1), time.min,
                              tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    bars = [bar for bar in dataset['bars'] if bar['event_date'] <= as_of_date and
            (bar.get('available_at') is None or
             datetime.fromisoformat(bar['available_at']) < cutoff)]
    excluded_future = any(bar['event_date'] <= as_of_date and bar.get('available_at') is not None and
                          datetime.fromisoformat(bar['available_at']) >= cutoff
                          for bar in dataset['bars'])
    # Eligibility must use the same point-in-time history as the indicators.
    # A future file extension cannot supply listing/history eligibility today.
    if len(bars) < max(return_window_days + 1, 40) or len(bars) <= 250:
        return None
    closes = [float(bar['close']) for bar in bars]
    highs = [float(bar['high']) for bar in bars]
    lows = [float(bar['low']) for bar in bars]
    opens = [float(bar['open']) for bar in bars]
    volumes = [int(bar['volume']) for bar in bars]
    amounts = [float(bar['amount']) if bar.get('amount') is not None else None for bar in bars]
    latest, previous = closes[-1], closes[-2]
    window = min(return_window_days, len(closes) - 1)
    ret_window = latest / max(closes[-window], 0.01) - 1
    high20, low20, close20 = highs[-20:], lows[-20:], closes[-20:]
    volume20, amount20 = volumes[-20:], amounts[-20:]
    quality = []
    if excluded_future:
        quality.append('BARS_AFTER_DECISION_EXCLUDED')
    if bars[-1]['event_date'] != as_of_date:
        # Informational: without a trading calendar this may simply be a holiday.
        quality.append('LATEST_BAR_BEFORE_AS_OF_DATE')
    if any(bar.get('available_at') is None for bar in bars):
        quality.append('HISTORICAL_AVAILABLE_AT_UNKNOWN')
    if float_shares is not None and float_shares_as_of_date and float_shares_as_of_date > as_of_date:
        turnover20 = None
        quality.append('FLOAT_SHARES_FROM_FUTURE')
    elif float_shares is None or float_shares <= 0:
        turnover20 = None
        quality.append('FLOAT_SHARES_NOT_FOUND')
    else:
        turnover20 = _mean([max(0, volume) / float_shares for volume in volume20])
        if not float_shares_as_of_date:
            quality.append('FLOAT_SHARES_AS_OF_UNKNOWN')
    if any(value is None for value in amount20):
        mean_amount = None
        quality.append('AMOUNT_NOT_FOUND')
    else:
        mean_amount = _mean(amount20)
    amplitude20 = _mean([(high - low) / max(close, 0.01)
                         for high, low, close in zip(high20, low20, close20)])
    retrace20 = (max(high20) - latest) / max(max(high20), 0.01)
    ma20 = _mean(closes[-20:])
    price_vs_ma20 = (latest - ma20) / max(ma20, 0.01)
    ma10_above_ma20_days = 0
    ma5_above_ma10_days = 0
    for index in range(max(0, len(closes) - 20), len(closes)):
        ma10, ma20_at, ma5 = _ma(closes, index, 10), _ma(closes, index, 20), _ma(closes, index, 5)
        if ma10 is not None and ma20_at is not None and ma10 > ma20_at:
            ma10_above_ma20_days += 1
        if ma5 is not None and ma10 is not None and ma5 > ma10:
            ma5_above_ma10_days += 1
    vol_slope20 = (volume20[-1] - volume20[0]) / max(volume20[0], 1)
    up_volumes, down_volumes = [], []
    for index in range(max(1, len(closes) - 20), len(closes)):
        (up_volumes if closes[index] >= closes[index - 1] else down_volumes).append(volumes[index])
    mean_up, mean_down = _mean(up_volumes), _mean(down_volumes)
    up_down_volume_ratio = mean_up / max(mean_down, 1)
    pullback_volume_ratio = mean_down / max(_mean(volume20), 1) if down_volumes else 0.6
    limit_up_days = sum(1 for index in range(max(1, len(closes) - 20), len(closes))
                        if (closes[index] - closes[index - 1]) / max(closes[index - 1], 0.01) >= 0.095)
    trend_class = ('B' if limit_up_days >= 2 else 'A_B' if ret_window >= 0.5
                   else 'A' if ret_window > 0 else 'Unknown')
    stage = 'Early' if ret_window < 0.3 else 'Mid' if ret_window <= 0.8 else 'Late'
    theme_stage = ('发酵中' if ret_window < 0.3 else
                   '高潮' if ret_window < 0.8 and up_down_volume_ratio >= 1 else '退潮')
    avg_volume20 = _mean(volume20)
    has_blowoff_top = any(volumes[index] > avg_volume20 * 2.5 and closes[index] <= opens[index]
                          for index in range(len(closes) - 20, len(closes)))
    has_divergence_5d = (closes[-1] > closes[-6] and _mean(volumes[-5:]) < _mean(volumes[-10:-5]) * 0.9)
    has_upper_shadow_risk = any(
        highs[index] - lows[index] > 0 and
        (highs[index] - max(opens[index], closes[index])) / (highs[index] - lows[index]) > 0.5
        and closes[index] <= opens[index]
        for index in range(len(closes) - 5, len(closes)))
    score = int(round(max(0, min(100, 45 + ret_window * 90 + up_down_volume_ratio * 8
                                 - pullback_volume_ratio * 15
                                 + max(0, (0.08 - abs(price_vs_ma20)) * 200)))))
    confidence = round(max(0.35, min(0.95, 0.5 + ret_window * 0.3
                                     + (up_down_volume_ratio - 1) * 0.1
                                     - max(0, pullback_volume_ratio - 0.8) * 0.2)), 2)
    return ScreenerCandidate(
        symbol=dataset['symbol'], dataset_id=dataset['id'], as_of_date=bars[-1]['event_date'],
        name=dataset.get('name', ''), score=score, ret40=round(ret_window, 4),
        turnover20=round(turnover20, 4) if turnover20 is not None else None,
        amount20=mean_amount, amplitude20=round(amplitude20, 4),
        retrace20=round(retrace20, 4),
        pullback_days=len(closes[-20:]) - 1 - max(range(20), key=lambda idx: closes[-20:][idx]),
        ma10_above_ma20_days=ma10_above_ma20_days,
        ma5_above_ma10_days=ma5_above_ma10_days,
        price_vs_ma20=round(price_vs_ma20, 4), vol_slope20=round(vol_slope20, 4),
        up_down_volume_ratio=round(up_down_volume_ratio, 4),
        pullback_volume_ratio=round(pullback_volume_ratio, 4),
        has_blowoff_top=has_blowoff_top, has_divergence_5d=has_divergence_5d,
        has_upper_shadow_risk=has_upper_shadow_risk, ai_confidence=confidence,
        theme_stage=theme_stage, trend_class=trend_class,
        degraded=turnover20 is None or mean_amount is None, quality_flags=quality)
