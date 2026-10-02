"""The five executable parameters of the frozen B1 multi-period scanner.

Legacy generic event/score settings are retained as catalog metadata, but the
scanner never used them. They must not be accepted as executable presets.
"""
from decimal import Decimal

from trade_app.platform.types import TradeError, decimal_value

B1_ID = 'b1_mtf_v1'
VERSION = 'legacy-b1-scanner-params-v1'
DEFAULTS = {'vol_ratio': .8, 'chg_limit': 3.0, 'amp_limit_10cm': 5.0,
            'amp_limit_20cm': 8.0, 'kdj_j_upper': 50.0}
SCHEMA = {
    'vol_ratio': {'type': 'number', 'title': '当日量 / 20 日均量上限', 'minimum': .1, 'maximum': 1.5, 'default': .8},
    'chg_limit': {'type': 'number', 'title': '日涨跌幅绝对值上限 (%)', 'minimum': .5, 'maximum': 10, 'default': 3},
    'amp_limit_10cm': {'type': 'number', 'title': '10cm 振幅上限 (%)', 'minimum': 1, 'maximum': 15, 'default': 5},
    'amp_limit_20cm': {'type': 'number', 'title': '20cm 振幅上限 (%)', 'minimum': 1, 'maximum': 20, 'default': 8},
    'kdj_j_upper': {'type': 'number', 'title': '前一日 KDJ J 上限', 'minimum': 10, 'maximum': 90, 'default': 50},
}
READONLY_FIELDS = {'min_score', 'min_event_count', 'require_sequence', 'health_score_min',
    'event_score_min', 'event_grade_min', 'require_key_event_confirmation', 'min_daily_bars', 'min_total_bars'}


def normalize_b1_params(raw: dict) -> dict[str, float]:
    if not isinstance(raw, dict) or len(raw) > 32:
        raise TradeError('INVALID_B1_PARAMS', 'B1 参数须为对象')
    unsupported = set(raw) - set(DEFAULTS)
    if unsupported:
        code = 'READONLY_STRATEGY_PARAM' if unsupported & READONLY_FIELDS else 'UNKNOWN_STRATEGY_PARAM'
        raise TradeError(code, 'B1 扫描只使用量比、涨跌幅、两类振幅、KDJ 五项参数；其他字段不能作为有效参数提交')
    result = {}
    for key, default in DEFAULTS.items():
        value = raw.get(key, default)
        if type(value) not in (int, float, str) or isinstance(value, str) and len(value) > 256:
            raise TradeError('INVALID_B1_PARAMS', f'{key} 须为有限数值')
        number = decimal_value(value, key)
        spec = SCHEMA[key]
        if not Decimal(str(spec['minimum'])) <= number <= Decimal(str(spec['maximum'])):
            raise TradeError('INVALID_B1_PARAMS', f"{key} 须在 {spec['minimum']} 至 {spec['maximum']} 之间")
        result[key] = float(number)
    return result
