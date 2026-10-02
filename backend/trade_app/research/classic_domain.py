"""Three explicit long/flat reference rules over an already eligible daily prefix.

There is no provider, clock, account or order state here. Callers filter historical
availability and execute confirmed signals at a later open. These are deliberately
named adaptations, not reproductions of the authors' complete trading systems.
"""
from decimal import Decimal, localcontext

from trade_app.platform.types import TradeError, decimal_value


VERSION = 'classic-confirmed-close-v1'
DONCHIAN = 'classic_donchian_breakout_v1'
SMA = 'classic_sma_trend_v1'
BOLLINGER = 'classic_bollinger_reentry_v1'
CLASSIC_STRATEGY_IDS = (DONCHIAN, SMA, BOLLINGER)
DEFAULTS = {
    DONCHIAN: {'entry_period': '55', 'exit_period': '20'},
    SMA: {'period': '200', 'buffer_pct': '0'},
    BOLLINGER: {'period': '20', 'stddev_multiplier': '2'},
}
SCHEMAS = {
    DONCHIAN: {
        'entry_period': {'type': 'integer', 'title': '突破通道周期', 'minimum': 5, 'maximum': 500, 'default': 55},
        'exit_period': {'type': 'integer', 'title': '退出通道周期', 'minimum': 2, 'maximum': 250, 'default': 20},
    },
    SMA: {
        'period': {'type': 'integer', 'title': '趋势均线周期', 'minimum': 2, 'maximum': 500, 'default': 200},
        'buffer_pct': {'type': 'number', 'title': '均线双向缓冲比例（0.01=1%）', 'minimum': 0, 'maximum': 0.2, 'default': 0},
    },
    BOLLINGER: {
        'period': {'type': 'integer', 'title': '布林带周期', 'minimum': 5, 'maximum': 250, 'default': 20},
        'stddev_multiplier': {'type': 'number', 'title': '总体标准差倍数', 'minimum': 0.5, 'maximum': 4, 'default': 2},
    },
}


def normalize_classic_params(strategy_id: str, raw: dict) -> dict[str, str]:
    if strategy_id not in CLASSIC_STRATEGY_IDS:
        raise TradeError('UNKNOWN_CLASSIC_STRATEGY', '未知经典参考策略')
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS[strategy_id]):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '包含经典策略不支持的参数')
    values = {**DEFAULTS[strategy_id], **raw}
    result = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 必须是数值')
        number = decimal_value(value, key)
        schema = SCHEMAS[strategy_id][key]
        if (number < Decimal(str(schema['minimum'])) or number > Decimal(str(schema['maximum']))
                or schema['type'] == 'integer' and number != number.to_integral_value()):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出范围或不是整数')
        result[key] = str(int(number)) if schema['type'] == 'integer' else format(number.normalize(), 'f')
    if strategy_id == DONCHIAN and int(result['exit_period']) >= int(result['entry_period']):
        raise TradeError('INVALID_STRATEGY_PARAM', '退出通道周期必须小于突破通道周期')
    return result


def required_bars(strategy_id: str, params: dict) -> int:
    if strategy_id == DONCHIAN:
        return max(int(params['entry_period']), int(params['exit_period'])) + 1
    return int(params['period']) + (1 if strategy_id == BOLLINGER else 0)


def _mean(values):
    return sum(values, Decimal(0)) / len(values)


def _band(values, multiplier):
    middle = _mean(values)
    variance = _mean([(value - middle) ** 2 for value in values])
    deviation = variance.sqrt()
    return middle, middle - multiplier * deviation, middle + multiplier * deviation, deviation


def evaluate_classic(strategy_id: str, bars: list[dict], params: dict) -> dict:
    """Return exact threshold evidence and separate entry/exit decisions.

    Donchian excludes the signal bar from both extreme windows. SMA and bands
    include the current, already closed bar. Equal SMA/channel thresholds produce
    no cross; a Bollinger return exactly to the middle is an exit, never a buy.
    """
    params = normalize_classic_params(strategy_id, params)
    minimum = required_bars(strategy_id, params)
    source_date = bars[-1]['event_date'] if bars else None
    base = {'available': len(bars) >= minimum, 'observed_bars': len(bars), 'required_bars': minimum,
            'source_date': source_date, 'signal': False, 'exit_signal': False,
            'entry_reason': None, 'exit_reason': None, 'local_score': None,
            'local_score_formula': None, 'metrics': {}, 'reasons': [], 'version': VERSION}
    if len(bars) < minimum:
        return {**base, 'reasons': ['INSUFFICIENT_CLASSIC_HISTORY']}
    # Decimal arithmetic makes threshold equality deterministic across execution
    # adapters. Historical validation/normalization belongs to the market domain.
    with localcontext() as arithmetic:
        arithmetic.prec = 40
        tail = bars[-minimum:]
        closes = [Decimal(str(bar['close'])) for bar in tail]
        close = closes[-1]
        liquid = int(tail[-1]['volume']) > 0
        metrics = {'close': close, 'volume': int(tail[-1]['volume'])}
        if strategy_id == DONCHIAN:
            entry = max(Decimal(str(bar['high'])) for bar in bars[-int(params['entry_period'])-1:-1])
            stop = min(Decimal(str(bar['low'])) for bar in bars[-int(params['exit_period'])-1:-1])
            buy, sell = close > entry, close < stop
            entry_reason, exit_reason = 'DONCHIAN_PRIOR_HIGH_BREAKOUT', 'DONCHIAN_PRIOR_LOW_BREAKDOWN'
            strength = (close / entry - 1) * 100
            formula = 'clamp(100 * (close / prior_entry_high - 1), 0, 100)'
            metrics.update(prior_entry_high=entry, prior_exit_low=stop,
                           entry_window_start=bars[-int(params['entry_period'])-1]['event_date'],
                           exit_window_start=bars[-int(params['exit_period'])-1]['event_date'],
                           channel_end=bars[-2]['event_date'])
        elif strategy_id == SMA:
            middle = _mean(closes)
            buffer = Decimal(params['buffer_pct'])
            entry, stop = middle * (1 + buffer), middle * (1 - buffer)
            buy, sell = close > entry, close < stop
            entry_reason, exit_reason = 'SMA_TREND_ABOVE', 'SMA_TREND_BELOW'
            strength = (close / entry - 1) * 100
            formula = 'clamp(100 * (close / entry_threshold - 1), 0, 100)'
            metrics.update(sma=middle, entry_threshold=entry, exit_threshold=stop)
        else:
            n, multiplier = int(params['period']), Decimal(params['stddev_multiplier'])
            middle, lower, upper, deviation = _band(closes[-n:], multiplier)
            prev_middle, prev_lower, prev_upper, prev_deviation = _band(closes[-n-1:-1], multiplier)
            buy = closes[-2] < prev_lower and close >= lower and close < middle and deviation > 0
            sell = close >= middle
            entry_reason, exit_reason = 'BOLLINGER_LOWER_REENTRY', 'BOLLINGER_MIDDLE_REACHED'
            strength = (close - lower) / (middle - lower) * 100 if middle > lower else Decimal(0)
            formula = 'clamp(100 * (close - lower) / (middle - lower), 0, 100); zero-width=0'
            metrics.update(middle=middle, lower=lower, upper=upper, population_stddev=deviation,
                           previous_close=closes[-2], previous_middle=prev_middle,
                           previous_lower=prev_lower, previous_upper=prev_upper,
                           previous_population_stddev=prev_deviation)
        score = float(max(Decimal(0), min(Decimal(100), strength)))
        base.update(signal=bool(buy and liquid), exit_signal=bool(sell and liquid),
                    entry_reason=entry_reason if buy and liquid else None,
                    exit_reason=exit_reason if sell and liquid else None,
                    local_score=score, local_score_formula=formula,
                    metrics={key: format(value, 'f') if isinstance(value, Decimal) else value for key, value in metrics.items()},
                    reasons=[entry_reason] if buy and liquid else [exit_reason] if sell and liquid else
                            ['NO_CURRENT_VOLUME'] if not liquid else ['NO_CLASSIC_TRIGGER'])
    return base
