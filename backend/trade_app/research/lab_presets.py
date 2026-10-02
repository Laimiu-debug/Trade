"""Audited adaptations of the four original morning pipelines, never aliases."""
from copy import deepcopy
from trade_app.platform.types import TradeError
from trade_app.research.portfolio_domain import digest

VERSION = 'legacy-morning-four-causal-band-exits-v2'
_ROWS = [
    ('hybrid85_fast_3slot', 85, 2e8, .05, .80, 500, False, 'pullback', .05, .18, 12, .08, 4, .02, .08, 3, 3),
    ('hybrid_dual_30', 75, 1e8, .05, 1., 500, False, 'pullback', .08, .30, 20, .12, 4, .03, .12, 3, 2),
    ('hybrid88_ths_2slot', 82, 2e8, .08, .60, 300, True, 'pullback', .06, .28, 20, .10, 4, .03, .10, 2, 2),
    ('hybrid_compound', 80, 1e8, .05, .80, 500, False, 'any', .05, .30, 20, .08, 3, .015, .08, 3, 3),
]
DIFFERENCES = [
    'MA10跟踪/时间止损/破箱收盘退出改为观察完成后的下一开盘；保本只能由先前已可得高点激活，不能在同根K线先用high抬止损再用low成交',
    '原cache按事后去重信号排名并可复制trend分支额外加5；新因果前缀按固定证券样本排名，一证券只保留一个信号',
    '新共享现金/T+1/费用/滑点/持仓估值替代旧独立槽收益相乘；1/slots为明确的新资产占比规则，非槽账户复刻',
    '旧滚动20日窗口每5日重叠且可使用预计算未来退出；新波段统计使用连续真实资金曲线的非重叠完整窗口',
    '筛选只在所提供固定样本内，不恢复旧本机pickle或历史全市场成员',
]


def preset_catalog():
    values = []
    for name, score, amount, rmin, rmax, top, ths, sources, stop, take, hold, trail, time_days, time_gain, breakeven, daily, slots in _ROWS:
        original = {'name': name, 'filter': {'min_hybrid': score, 'min_amount20': amount, 'ret40_min': rmin,
            'ret40_max': rmax, 'ret40_top_n': top, 'require_ths': ths, 'require_rhythm': False, 'sources': sources},
            'exit': {'stop_loss': stop, 'take_profit': take, 'max_hold': hold, 'trail_activate': trail,
                'time_stop_days': time_days, 'time_stop_min_gain': time_gain, 'breakeven_trigger': breakeven},
            'daily_top': daily, 'max_slots': slots}
        overlay = {'strategy_id': 'hybrid_band_v1', 'params': {'min_hybrid_score': score},
            'filters': {'min_amount20': str(amount), 'ret40_min': str(rmin), 'ret40_max': str(rmax), 'ret40_top_n': top,
                'require_ths': ths, 'require_rhythm': False, 'sources': sources, 'daily_top': daily, 'rank_bonus': True},
            'config': {'stop_loss_pct': str(stop), 'take_profit_pct': str(take), 'max_holding_bars': hold,
                'max_positions': slots, 'position_pct': str(1 / slots), 'entry_delay_bars': 1,
                'trailing_stop_pct': '0', 'daily_weak_clear': False, 'entry_top_k': 0, 'pool_roll': 'daily',
                'trail_activate': str(trail), 'time_stop_days': time_days, 'time_stop_min_gain': str(time_gain),
                'breakeven_trigger': str(breakeven), 'structural_box_stop': True, 'ma20_box_break': True}}
        record = {'id': name, 'version': VERSION, 'exact_legacy_equivalence': False, 'original': original,
            'executable_overlay': overlay, 'unsupported_legacy_fields': {}, 'differences': DIFFERENCES}
        values.append({**record, 'preset_sha256': digest(record)})
    return {'version': VERSION, 'source': 'final-trade/backend/scripts/morning_band_report.py', 'presets': values}


def adopt_preset(raw, preset_id, *, accept_adapted=False):
    from trade_app.research.lab_context import normalize_input
    selected = next((row for row in preset_catalog()['presets'] if row['id'] == preset_id), None)
    if selected is None: raise TradeError('LAB_PRESET_NOT_FOUND', '未知晨报预设')
    if not accept_adapted:
        raise TradeError('LAB_PRESET_NOT_EQUIVALENT', '四套预设使用因果退出修正；请先查看presets，再以--accept-adapted显式采用新口径')
    normalized = normalize_input(raw)
    # Preserve sample and user fee assumptions; replace signal/filter/variant
    # settings completely so stale manual overrides cannot masquerade as preset.
    overlay = deepcopy(selected['executable_overlay'])
    value = {**normalized, **overlay, 'config': {**normalized['config'], **overlay['config']}, 'variants': [{}],
        'preset_adoption': {'id': preset_id, 'version': VERSION, 'preset_sha256': selected['preset_sha256'],
            'accepted_adapted': True, 'exact_legacy_equivalence': False}}
    return normalize_input(value)


def validate_adoption(raw):
    if raw is None: return None
    if not isinstance(raw, dict) or set(raw) != {'id', 'version', 'preset_sha256', 'accepted_adapted', 'exact_legacy_equivalence'}:
        raise TradeError('INVALID_LAB_PRESET_EVIDENCE', '预设采用证据字段无效')
    selected = next((row for row in preset_catalog()['presets'] if row['id'] == raw['id']), None)
    if (selected is None or raw['version'] != VERSION or raw['preset_sha256'] != selected['preset_sha256']
            or raw['accepted_adapted'] is not True or raw['exact_legacy_equivalence'] is not False):
        raise TradeError('INVALID_LAB_PRESET_EVIDENCE', '预设版本或差异确认摘要无效')
    return deepcopy(raw)
