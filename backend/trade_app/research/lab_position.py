"""Structured fixed-sample Step1/static-versus-position diagnostics."""
from copy import deepcopy
from datetime import date
import math

from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError
from trade_app.research.lab_domain import signal_rows
from trade_app.research.portfolio_domain import digest
from trade_app.research.screener_domain import Step1, FunnelConfig, run_funnel
from trade_app.research.screener_metrics import build_candidate

VERSION = 'lab-frozen-step1-position-comparison-v1'


def normalize_step1(raw, datasets):
    if raw is None: return None
    if not isinstance(raw, dict) or set(raw) - {'parameters', 'float_shares', 'focus_symbols'}:
        raise TradeError('LAB_STEP1_INPUT', 'Step1诊断字段无效')
    parameters = raw.get('parameters', {})
    if not isinstance(parameters, dict) or set(parameters) - set(Step1.model_fields):
        raise TradeError('LAB_STEP1_INPUT', '仅接受实际Step1门槛参数')
    try:
        config = FunnelConfig(step1=Step1.model_validate(parameters)).step1.model_dump()
    except ValueError as exc: raise TradeError('LAB_STEP1_INPUT', 'Step1门槛超出范围') from exc
    identities = {row['symbol'] for row in datasets}
    sources, seen = [], set()
    items = raw.get('float_shares', [])
    if not isinstance(items, list) or len(items) > len(datasets): raise TradeError('LAB_STEP1_INPUT', '股本来源数量超过样本')
    for item in items:
        if not isinstance(item, dict) or set(item) != {'symbol', 'value', 'as_of_date'}: raise TradeError('LAB_STEP1_INPUT', '股本来源须含证券/股数/时点')
        symbol = ''.join(normalize_a_share_symbol(item['symbol']))
        value, day = item['value'], item['as_of_date']
        if symbol not in identities or symbol in seen: raise TradeError('LAB_STEP1_INPUT', '股本证券重复或不在样本内')
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1e15: raise TradeError('LAB_STEP1_INPUT', '股本须为正的有限股数')
        try:
            if day is not None and (type(day) is not str or date.fromisoformat(day).isoformat() != day): raise ValueError()
        except (ValueError, TypeError): raise TradeError('LAB_STEP1_INPUT', '股本时点无效') from None
        sources.append({'symbol': symbol, 'value': value, 'as_of_date': day}); seen.add(symbol)
    focus = raw.get('focus_symbols', [])
    if not isinstance(focus, list) or len(focus) > 64: raise TradeError('LAB_STEP1_INPUT', '关注证券列表无效')
    focus = [''.join(normalize_a_share_symbol(symbol)) for symbol in focus]
    if set(focus) - identities or len(set(focus)) != len(focus): raise TradeError('LAB_STEP1_INPUT', '关注证券须为不重复的样本证券')
    return {'parameters': config, 'float_shares': sorted(sources, key=lambda row: row['symbol']), 'focus_symbols': sorted(focus)}


def step1_signals(prefixes, context):
    # Preserve raw strategy hits outside today's Step1. The execution engine
    # applies its frozen/static or position-refreshed membership afterwards.
    rows = signal_rows(prefixes, context['strategy_id'], context['params'], {**context['filters'], 'daily_top': 2000})
    sources = {row['symbol']: row for row in context['step1']['float_shares']}
    candidates, missing = [], {}
    for symbol, bars in sorted(prefixes.items()):
        supplied = sources.get(symbol)
        value = supplied['value'] if supplied else None
        day = supplied['as_of_date'] if supplied else None
        if context['config']['execution_strict'] and day is None: value = None
        candidate = build_candidate({'id': digest(bars), 'symbol': symbol, 'bars': bars}, bars[-1]['event_date'], 40, value, day)
        if candidate is None: missing[symbol] = ['STEP1_REQUIRES_251_KNOWN_BARS']
        else: candidates.append(candidate)
    config = FunnelConfig(step1=Step1.model_validate(context['step1']['parameters']))
    funnel = run_funnel(candidates, config)
    members = {row['symbol'] for row in funnel['pools']['step1']}
    by_symbol = {row.symbol: row for row in candidates}
    for symbol, row in rows.items():
        candidate = by_symbol.get(symbol)
        reasons = missing.get(symbol, []) if candidate is None else funnel['rejections'].get(candidate.dataset_id, {}).get('reasons', []) if symbol not in members else []
        row['components']['step1'] = {'member': symbol in members, 'reasons': reasons,
            'metrics': candidate.model_dump() if candidate else None, 'float_shares': sources.get(symbol),
            'return_window_days': 40, 'rank_scope': 'fixed_research_sample'}
        row['in_pool'] = row['in_pool'] and symbol in members
        row['quality_flags'] += candidate.quality_flags if candidate else []
    return rows


def diagnostic_context(context, mode):
    value = deepcopy(context)
    value['config']['pool_roll'] = mode
    value['config']['entry_top_k'] = min(value['config']['entry_top_k'] or 2000, value['filters']['daily_top'])
    return value


def build_diagnostic(context, paths, plain_scan):
    """All arguments are persisted causal evidence, not a legacy console log."""
    as_of = plain_scan['as_of_date']
    evidence = {}
    for mode, records in paths.items():
        state = records[-1]['result']['checkpoint']
        all_trades = [row for record in records for row in record['result']['trades']]
        pools = [row for record in records for row in record['result']['pool_history']]
        decisions = [row for record in records for row in record['result']['decisions']]
        equity = [row for record in records for row in record['result']['equity']]
        planned = []
        for symbol, row in plain_scan['rows'].items():
            intent = state['pending'].get(symbol)
            refresh_due = mode == 'position' and state['refresh_due']
            in_pool = row['in_pool'] if refresh_due else symbol in state['pool']
            if in_pool and symbol not in state['positions'] and (intent or row['buy']):
                remaining = intent['remaining_bars'] if intent else context['config']['entry_delay_bars'] - 1
                planned.append({'symbol': symbol, 'source_date': intent['source_date'] if intent else row['source_date'],
                    'remaining_observed_bars': remaining, 'score': intent['score'] if intent else row['score'],
                    'status': 'conditional_next_observation' if remaining <= 0 else 'waiting_observed_bars',
                    'quantity': None, 'price': None, 'current_pool_member': symbol in state['pool'],
                    'requires_position_refresh': refresh_due})
        planned.sort(key=lambda row: (-row['score'], row['symbol']))
        evidence[mode] = {'final_cash': state['cash'], 'holdings': state['positions'], 'pending': state['pending'],
            'current_pool': state['pool'], 'refresh_due': state['refresh_due'], 'pool_refreshes': pools,
            'trades': all_trades, 'decisions': decisions, 'equity': equity, 'plan_signals': planned,
            'open_signals': [row for row in planned if row['remaining_observed_bars'] <= 0],
            'focused_trades': {symbol: [row for row in all_trades if row['symbol'] == symbol] for symbol in context['step1']['focus_symbols']}}
    comparisons = []
    for dataset in context['datasets']:
        symbol = dataset['symbol']; row = plain_scan['rows'].get(symbol)
        comparisons.append({'symbol': symbol, 'asof_step1_member': bool(row and row['in_pool']),
            'asof_strategy_signal': bool(row and row['buy']), 'asof_eligible_plain_signal': bool(row and row['buy'] and row['in_pool']),
            'step1': row['components']['step1'] if row else None,
            'static_pool_member': symbol in evidence['static']['current_pool'], 'position_pool_member': symbol in evidence['position']['current_pool'],
            'static_held_quantity': evidence['static']['holdings'].get(symbol, {}).get('quantity', 0),
            'position_held_quantity': evidence['position']['holdings'].get(symbol, {}).get('quantity', 0),
            'actual_trades_asof': {mode: [trade for trade in body['trades'] if trade['date'] == as_of and trade['symbol'] == symbol] for mode, body in evidence.items()},
            'execution_decisions_asof': {mode: [item for item in body['decisions'] if item['date'] == as_of and item['symbol'] == symbol] for mode, body in evidence.items()}})
    return {'version': VERSION, 'as_of_date': as_of, 'decision_at': plain_scan['decision_at'], 'step1_inputs': context['step1'],
        'plain_scan': plain_scan, 'paths': evidence, 'comparison': comparisons,
        'scope': 'fixed_sample_step1_static_vs_position', 'limitations': [
            '使用明确冻结的行情和股本来源日期，只在固定样本内按新Step1公式排名，不声称旧RUN_ID历史全市场成员等价',
            'Step1至少需要251根当时可见日线；缺股本/未来股本/严格未知股本日期明确排除，不伪造换手率',
            '静态路径在首个执行日冻结池；持仓路径在实际卖出后的下一观察日刷新，两者使用相同策略门槛',
            '当日日终信号不能解释成当日开盘成交；plan/open仅是下次观察的条件候选，价格、数量未知',
            'daily_top作为到期执行候选前K，原脚本60/55混合阈值不作为同口径比较；旧独立槽和未来去重不复刻']}
