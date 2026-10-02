"""Catalog additions, separate from the immutable original 16-strategy archive."""
from copy import deepcopy
import hashlib
import json

from trade_app.research.classic_domain import CLASSIC_STRATEGY_IDS, DEFAULTS, SCHEMAS, VERSION


SOURCES = {
    'turtle': 'https://www.tradingblox.com/originalturtles/originalturtlerules.htm',
    'sma': 'https://mebfaber.com/timing-model/',
    'bollinger': 'https://www.bollingerbands.com/bollinger-band-rules',
}
DEFINITIONS = (
    ('Donchian 通道突破 · 收盘参考版', '通道突破趋势',
     '收盘突破此前55根最高价后观察买入，收盘跌破此前20根最低价观察退出。',
     '仅参考海龟System 2通道；不含原盘中突破、做空、N波动率仓位或金字塔加仓。',
     SOURCES['turtle'], 'DONCHIAN_PRIOR_HIGH_BREAKOUT', 'DONCHIAN_PRIOR_LOW_BREAKDOWN'),
    ('SMA 趋势开关 · 日线参考版', '均线趋势',
     '收盘高于200根SMA及可选缓冲则允许持多，低于SMA及反向缓冲则退出。',
     '日频单证券变体，不复刻Faber月末10月SMA、多资产配置或现金利息。',
     SOURCES['sma'], 'SMA_TREND_ABOVE', 'SMA_TREND_BELOW'),
    ('Bollinger 下轨重返 · 均值回归参考版', '波动带均值回归',
     '前收盘在20/2下轨外，本收盘回到带内且仍低于中轨时买入；达到中轨退出。',
     '自行定义的可测试组合，非Bollinger原完整系统；触及下轨本身不代表买点。',
     SOURCES['bollinger'], 'BOLLINGER_LOWER_REENTRY', 'BOLLINGER_MIDDLE_REACHED'),
)


def classic_catalog() -> list[dict]:
    source_sha = hashlib.sha256(json.dumps({'definitions': DEFINITIONS, 'defaults': DEFAULTS,
        'schemas': SCHEMAS}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    rows = []
    for strategy_id, (name, family, description, caveat, url, entry, exit_rule) in zip(CLASSIC_STRATEGY_IDS, DEFINITIONS):
        rows.append({'id': strategy_id, 'name': name, 'version': '1.0.0',
            'enabled_in_legacy': False, 'enabled_by_default': True, 'is_legacy_default': False,
            'origin': 'classic_reference', 'family': family,
            'capabilities': {'supports_matrix': False, 'supports_signal_age_filter': False,
                             'supports_entry_delay': True, 'supports_exit_signal': True,
                             'supports_full_signal_context': False},
            'params_schema': deepcopy(SCHEMAS[strategy_id]), 'default_params': dict(DEFAULTS[strategy_id]),
            'signal_params': dict(DEFAULTS[strategy_id]), 'pool_params': None, 'signal_top_n': 0,
            'description': description, 'status': 'classic_signal', 'calculation_version': VERSION,
            'source_sha256': source_sha, 'sources': [{'url': url, 'kind': 'author_reference'}],
            'playbook': {'intent': description, 'entry_events': [entry], 'exit_events': [exit_rule],
                         'execution': '已知日线收盘确认；之后可交易开盘执行，T+1及费用由执行器控制。'},
            'limitations': [caveat, '仅多头/空仓；默认参数不是收益承诺。',
                            '本地强度分仅用于同策略排序，不是胜率或跨策略统一质量分。',
                            '通用持有期/止损止盈等执行参数可能先于策略退出；请核对冻结配置。']})
    return rows
