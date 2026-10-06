"""Pure immutable bar validation and point-in-time availability rules."""
from __future__ import annotations

from datetime import date, datetime, timezone
from trade_app.platform.types import TradeError, decimal_text, decimal_value, price_text, price_units


def normalize_bars(bars: list[dict]) -> list[dict]:
    if not 2 <= len(bars) <= 2000:
        raise TradeError('INVALID_BAR_COUNT', '行情样本需要 2 至 2000 个交易日')
    normalized: list[dict] = []
    previous = ''
    for bar in bars:
        day = str(bar['event_date'])
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError(day)
        except ValueError as exc:
            raise TradeError('INVALID_BAR_DATE', '行情日期必须是 YYYY-MM-DD') from exc
        if day <= previous:
            raise TradeError('BAR_ORDER', '行情日期必须严格递增且不可重复')
        previous = day
        prices = {key: price_units(bar[key]) for key in ('open', 'high', 'low', 'close')}
        if prices['low'] > min(prices['open'], prices['close']) or prices['high'] < max(prices['open'], prices['close']) or prices['low'] > prices['high']:
            raise TradeError('INVALID_OHLC', f'{day} 的开高低收关系无效')
        volume = int(bar['volume'])
        if volume < 0:
            raise TradeError('INVALID_VOLUME', '成交量不能小于零')
        available = bar.get('available_at')
        if available is not None:
            try:
                moment = datetime.fromisoformat(str(available).replace('Z', '+00:00'))
                if moment.tzinfo is None:
                    raise ValueError(available)
                available = moment.astimezone(timezone.utc).isoformat()
                if moment.astimezone(timezone.utc).date() < date.fromisoformat(day):
                    raise ValueError(available)
            except ValueError as exc:
                raise TradeError('INVALID_AVAILABLE_AT', '可得时间需要时区') from exc
        amount = bar.get('amount')
        if amount is not None:
            parsed_amount = decimal_value(amount, '成交额')
            if parsed_amount < 0 or parsed_amount > 1_000_000_000_000_000:
                raise TradeError('INVALID_AMOUNT', '成交额须为非负且不超过上限')
            amount = decimal_text(parsed_amount, 2)
        result = {'event_date': day, 'open': price_text(prices['open']),
                           'high': price_text(prices['high']), 'low': price_text(prices['low']),
                           'close': price_text(prices['close']), 'volume': volume,
                           'available_at': available}
        if amount is not None:
            result['amount'] = amount
        normalized.append(result)
    return normalized


def eligible_bars(bars: list[dict], decision_at: str, strict: bool) -> tuple[list[dict], list[str]]:
    try:
        cutoff = datetime.fromisoformat(decision_at.replace('Z', '+00:00'))
        if cutoff.tzinfo is None:
            raise ValueError(decision_at)
        cutoff = cutoff.astimezone(timezone.utc)
    except ValueError as exc:
        raise TradeError('INVALID_DECISION_TIME', '决策时间需要时区') from exc
    eligible: list[dict] = []
    quality: set[str] = set()
    for bar in bars:
        available = bar.get('available_at')
        if available is None:
            # Historical daily bars without a verified timestamp are assumed
            # available at Shanghai day end only in non-strict research mode.
            event_end = datetime.fromisoformat(bar['event_date'] + 'T23:59:59.999999+08:00')
            if event_end > cutoff:
                continue
            quality.add('historical_availability_unknown')
            if strict:
                continue
        elif datetime.fromisoformat(available) > cutoff or date.fromisoformat(bar['event_date']) > cutoff.date():
            continue
        eligible.append(bar)
    return eligible, sorted(quality)
