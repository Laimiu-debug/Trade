"""Pure first-strategy adapter matching the production K-line candidate path."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from trade_app.platform.types import TradeError, decimal_value


STRATEGY_ID = 'relative_strength_breakout_v1'
STRATEGY_VERSION = '1.0.0-alpha'
CALCULATION_VERSION = 'legacy-store-candidate-v2'
DEFAULT_PARAMS = {'min_ret40': '0.12', 'max_retrace20': '0.22',
                  'min_up_down_volume_ratio': '1.15', 'min_vol_slope20': '0.02',
                  'min_ai_confidence': '0'}
PARAM_BOUNDS = {'min_ret40': ('0', '1.5'), 'max_retrace20': ('0.01', '0.6'),
                'min_up_down_volume_ratio': ('0.8', '3'),
                'min_vol_slope20': ('-0.5', '0.5'), 'min_ai_confidence': ('0', '1')}


@dataclass(frozen=True)
class Candidate:
    symbol: str
    date: str
    ret40: float
    retrace20: float
    up_down_volume_ratio: float
    vol_slope20: float
    pullback_volume_ratio: float
    ai_confidence: float


def normalize_params(raw: dict[str, str]) -> dict[str, str]:
    if set(raw) - set(DEFAULT_PARAMS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '包含不支持的策略参数')
    params = dict(DEFAULT_PARAMS)
    for key, value in raw.items():
        parsed = decimal_value(value, key)
        lower, upper = PARAM_BOUNDS[key]
        if parsed < decimal_value(lower, key) or parsed > decimal_value(upper, key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        params[key] = format(parsed, 'f')
    return params


def candidate_from_bars(symbol: str, bars: list[dict]) -> Candidate | None:
    if len(bars) < 30:
        return None
    closes = [float(bar['close']) for bar in bars]
    highs = [float(bar['high']) for bar in bars]
    volumes = [max(0, int(bar['volume'])) for bar in bars]
    latest = closes[-1]
    look40 = min(40, len(closes) - 1)
    start_close = closes[len(closes) - look40]
    ret40 = (latest - start_close) / max(start_close, 0.01)
    high20 = max(highs[-20:])
    retrace20 = max(0.0, (high20 - latest) / max(high20, 0.01))
    avg_vol10 = mean(volumes[-10:])
    avg_prev10 = mean(volumes[-20:-10])
    vol_slope20 = (avg_vol10 - avg_prev10) / max(avg_prev10, 1.0)
    up_volumes = [volumes[index] for index in range(1, len(closes)) if closes[index] >= closes[index - 1]]
    down_volumes = [volumes[index] for index in range(1, len(closes)) if closes[index] < closes[index - 1]]
    ratio = (mean(up_volumes) if up_volumes else 0.0) / max(mean(down_volumes) if down_volumes else 0.0, 1.0)
    pb_closes = closes[-20:]
    peak_offset = max(range(len(pb_closes)), key=lambda index: pb_closes[index])
    pullback_days = len(pb_closes) - 1 - peak_offset
    recent_pullback = volumes[-pullback_days:] if pullback_days > 0 else []
    pullback_ratio = (mean(recent_pullback) / max(mean(volumes[-5:]), 1.0)
                      if recent_pullback else 0.85)
    ai_confidence = max(0.35, min(0.95,
        0.50 + ret40 * 0.30 + (ratio - 1.0) * 0.10 - max(0.0, pullback_ratio - 0.8) * 0.20))
    return Candidate(symbol=symbol, date=bars[-1]['event_date'],
                     ret40=round(ret40, 4), retrace20=round(retrace20, 4),
                     up_down_volume_ratio=round(ratio, 4), vol_slope20=round(vol_slope20, 4),
                     pullback_volume_ratio=round(pullback_ratio, 4),
                     ai_confidence=round(ai_confidence, 2))


def relative_strength_signal(candidate: Candidate, params: dict[str, str]) -> bool:
    # Exact predicates of old RelativeStrengthBreakoutPlugin.build_universe and generate_signals.
    return (candidate.ret40 >= float(params['min_ret40'])
            and candidate.retrace20 <= float(params['max_retrace20'])
            and candidate.up_down_volume_ratio >= float(params['min_up_down_volume_ratio'])
            and candidate.vol_slope20 >= float(params['min_vol_slope20'])
            and candidate.ai_confidence >= float(params['min_ai_confidence']))
