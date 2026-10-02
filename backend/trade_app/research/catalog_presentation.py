"""Current navigation metadata, kept separate from frozen legacy calculations.

Families group related formulas without aliasing IDs, merging scores or changing
persisted runs. Execution paths are supplied from actual runner allow-lists.
"""
from copy import deepcopy


FAMILIES = {
    'wyckoff_trend_v1': ('wyckoff', '维科夫事件', 'V1 事件门槛'),
    'wyckoff_trend_v2': ('wyckoff', '维科夫事件', 'V2 健康与事件门槛'),
    'score_only_rank_v1': ('wyckoff', '维科夫事件', '入场质量评分'),
    'relative_strength_breakout_v1': ('relative_strength', '相对强弱', '价格与量能突破'),
    'ths_main_force_flip_v1': ('ths_volume', '主力量能', '紫转黄'),
    'ths_main_force_golden_cross_v1': ('ths_volume', '主力量能', '主力金叉'),
    'ths_force_rhythm_v1': ('ths_rhythm', '主散节奏', '周期波形'),
    'wulong_cluster_v1': ('moving_average_cluster', '均线聚合', '五龙聚首'),
    'matrix_signal_v1': ('matrix_plugin', '矩阵候选插件', '候选池代理公式'),
    'b1_mtf_v1': ('multi_timeframe', '多周期共振', 'B1'),
    'trend_king_v1': ('trend_king', '趋势为王', '综合 A / B / C'),
    'trend_king_limitup_v1': ('trend_king', '趋势为王', 'A 涨停形态'),
    'trend_king_rally_v1': ('trend_king', '趋势为王', 'B 连续上涨'),
    'trend_king_pullback_v1': ('trend_king', '趋势为王', 'C 回调形态'),
    'emotion_limit_up_v1': ('limit_up', '涨停与情绪', '情绪共振'),
    'limit_up_arb_v1': ('limit_up', '涨停与情绪', '涨停后放量'),
    'classic_donchian_breakout_v1': ('classic_trend', '经典趋势规则', 'Donchian 通道'),
    'classic_sma_trend_v1': ('classic_trend', '经典趋势规则', '长期均线趋势'),
    'classic_bollinger_reentry_v1': ('classic_reversion', '经典均值回归规则', '布林带重返'),
}


def present_strategy(item, *, single_ids, context_ids):
    """Return a detached descriptor; never mutate the memoized source catalog."""
    row = deepcopy(item)
    identity = row['id']
    family_id, family_name, variant = FAMILIES.get(identity, ('other', '其他策略', row['name']))
    single = identity in single_ids
    full = identity in context_ids
    native = 'b1_scanner' if identity == 'b1_mtf_v1' else 'matrix_pool' if identity == 'matrix_signal_v1' else None
    paths = (['single_observation', 'single_backtest', 'traditional_portfolio'] if single else [])
    if full:
        paths.append('full_signal_context')
    if native:
        paths.append(native)
    origin = row.get('origin', 'final_trade' if identity in FAMILIES and not identity.startswith('classic_') else 'custom')
    legacy_limits = row.get('limitations', [])
    if origin == 'final_trade':
        limits = ['完整候选扫描须明确选择 store 或 TDX 口径，两者同名指标不能混用；分数只在对应策略与口径内解释。']
        if single:
            limits.append('单股回测使用冻结持有期与风险规则；传统组合另含风险事件退出，其他指标卖出提示须以执行器记录为准。')
        if identity.startswith('trend_king_') or identity in ('limit_up_arb_v1', 'emotion_limit_up_v1'):
            limits.append('缺少同期板块排名时保留原公式的中性输入并标注缺失，不声称真实板块排名。')
        if identity.startswith('trend_king_'):
            limits.append('min_hist / min_hist_c 为历史最大涨幅阈值（百分比），不是历史天数。')
            limits.append('旧公式 C 须先满足 A；综合模式按 A 或 B 触发。模式间可能重叠，不能视为独立投票。')
        if identity == 'matrix_signal_v1':
            limits.append('本 ID 为旧 MatrixSignalPlugin 的候选代理口径；原始 S1–S9 与 aligned 事件矩阵在独立组合引擎运行。')
        if identity == 'b1_mtf_v1':
            limits.append('原生 B1 扫描使用五项指标参数；旧通用事件门槛仅在完整候选扫描入口生效。')
        if identity in ('wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'):
            limits.append('观察通过仍须核对正向主事件才能创建买入草稿；事件发生日不等于当时已确认。')
    else:
        limits = list(legacy_limits)
    if not paths:
        limits.append('当前没有已登记的执行入口，保留目录资料供查阅。')
    semantics = {
        'entry': '冻结时点的已知日线信号；回测在满足执行约束的后续开盘执行' if single else '冻结候选扫描；命中结果由用户明确选择后再创建模拟草稿',
        'exit': '策略退出信号与冻结风险配置共同决定，退出信号于后续开盘执行' if origin == 'classic_reference' and single else '单股按冻结持有期与风险规则；传统组合另含风险事件退出，专用引擎分别记录退出规则' if single else '此扫描入口不执行持仓出场',
        'ranking': '完整候选扫描保存原插件排名及口径；单股指标分不是跨策略统一排名' if full else '没有旧插件完整候选排名，保留本策略信号与指标证据',
    }
    row.update(family_id=family_id, family_name=family_name, variant_name=variant,
        origin=origin, execution_paths=paths, availability='available' if paths else 'unavailable',
        execution_entry=native or ('single_symbol' if single else 'unavailable'),
        execution_semantics=semantics, supports_full_signal_context=full,
        legacy_status=item.get('status') if origin == 'final_trade' else None,
        legacy_limitations=deepcopy(legacy_limits) if origin == 'final_trade' else [],
        limitations=limits,
        current_capabilities={'single_observation': single, 'single_backtest': single,
            'traditional_portfolio': single, 'full_signal_context': full,
            'b1_scanner': native == 'b1_scanner', 'matrix_pool': native == 'matrix_pool'})
    return row
