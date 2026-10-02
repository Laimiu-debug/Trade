"""Explicit legacy signal-scanner path, distinct from a single-stock observation.

All inputs are frozen and already point-in-time eligible. Candidate metric path,
event profile, universe admission, generic gates and ranking remain inspectable.
"""
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from trade_app.platform.types import TradeError, decimal_value
from trade_app.research.domain import candidate_from_bars as relative_candidate, relative_strength_signal
from trade_app.research.wulong_universe import candidate_metrics_from_bars, evaluate_wulong_candidate, UNIVERSE_PARAM_KEYS
from trade_app.research.screener_metrics import build_candidate
from trade_app.research.wyckoff_domain import candidate_from_bars as event_candidate, SignalAnalyzer
from trade_app.research.wyckoff_strategy import evaluate_wyckoff_strategy, WYCKOFF_STRATEGY_IDS
from trade_app.research.signal_context_formatters import SignalContextBuilder
from trade_app.research.runtime import evaluate_strategy, TREND_KING_IDS, THS_TRIGGERS, _json_safe
from trade_app.research.trend_king_domain import CandlePoint
from trade_app.research.b1_domain import B1Params, check_b1
from trade_app.research.b1_params import SCHEMA as B1_SCHEMA, normalize_b1_params

VERSION = 'legacy-full-signal-context-frozen-candidates-v1'
GATE_KEYS = ('min_score', 'min_event_count', 'require_sequence', 'health_score_min', 'event_score_min', 'event_grade_min', 'require_key_event_confirmation')
RANK_KEYS = ('rank_weight_health', 'rank_weight_event', 'rank_weight_strength', 'rank_weight_volume', 'rank_weight_structure')
GATE_DEFAULTS = {'min_score': 60, 'min_event_count': 1, 'require_sequence': False, 'health_score_min': 0,
                 'event_score_min': 0, 'event_grade_min': 'C', 'require_key_event_confirmation': False}
GATE_SCHEMA = {key: {'type': 'boolean' if type(value) is bool else 'enum' if isinstance(value, str) else 'integer' if key == 'min_event_count' else 'number',
                     'minimum': 0, 'maximum': 12 if key == 'min_event_count' else 100, 'options': ['A','B','C'], 'default': value, 'title': key}
               for key, value in GATE_DEFAULTS.items()}


def normalize_context(raw):
    if not isinstance(raw, dict) or set(raw) - {'candidate_path', 'window_days', 'universe_mode'}:
        raise TradeError('INVALID_SIGNAL_CONTEXT', '完整扫描上下文仅接受候选口径、预筛模式和事件窗口')
    path, window = raw.get('candidate_path', 'legacy_store'), raw.get('window_days', 60)
    mode = raw.get('universe_mode','strategy_filtered')
    if path not in ('legacy_store', 'legacy_tdx') or type(window) is not int or not 20 <= window <= 240:
        raise TradeError('INVALID_SIGNAL_CONTEXT', '候选口径须为旧单股/TDX，事件窗口须为20–240整数')
    if mode not in ('strategy_filtered','full_market'):
        raise TradeError('INVALID_SIGNAL_CONTEXT','预筛模式须为策略预筛或跳过预筛')
    return {'candidate_path': path, 'window_days': window, 'universe_mode':mode}


@lru_cache(maxsize=1)
def context_catalog():
    from trade_app.research.service import strategy_catalog
    result = []
    legacy_ids = {row['strategy_id'] for row in json.loads(Path(__file__).with_name('legacy_catalog.json').read_text(encoding='utf-8'))['strategies']}
    for row in strategy_catalog():
        # This adapter implements the frozen legacy plugins, not every future
        # single-stock strategy. New entries need their own explicit adapter.
        if row['id'] not in legacy_ids or row.get('supports_full_signal_context') is False or row.get('capabilities', {}).get('supports_full_signal_context') is False:
            continue
        base = row['signal_params'] or row.get('pool_params') or {}
        if row['id'] == 'b1_mtf_v1': base = normalize_b1_params({})
        keys = set(base) | set(GATE_KEYS)
        if row['id'] == 'relative_strength_breakout_v1': keys |= set(RANK_KEYS)
        keys.discard('window_days')
        schema = {key: dict(row['params_schema'].get(key) or B1_SCHEMA.get(key) or GATE_SCHEMA[key]) for key in sorted(keys)}
        defaults = {key: row['default_params'].get(key, base.get(key, GATE_DEFAULTS.get(key))) for key in keys}
        for key in keys:
            if key in GATE_SCHEMA:
                schema[key] = {**GATE_SCHEMA[key], **schema[key]}
            schema[key]['default'] = defaults[key]
            schema[key].pop('readonly', None); schema[key].pop('readonly_reason', None)
        result.append({'id': row['id'], 'name': row['name'], 'version': row['version'], 'signal_params': defaults,
                       'params_schema': schema, 'capabilities': row['capabilities'], 'signal_top_n': row['signal_top_n']})
    return result


def normalize_context_params(strategy_id, raw):
    row = next((row for row in context_catalog() if row['id'] == strategy_id), None)
    if row is None:
        raise TradeError('SIGNAL_CONTEXT_UNSUPPORTED', '该策略没有旧插件完整候选扫描适配，请使用单股观察或普通扫描入口', 409)
    if not isinstance(raw, dict) or set(raw) - set(row['params_schema']):
        raise TradeError('UNKNOWN_SIGNAL_CONTEXT_PARAM', '完整扫描包含未知或未使用的参数')
    result = {}
    for key, spec in row['params_schema'].items():
        value = raw.get(key, spec['default'])
        if spec['type'] == 'boolean':
            if type(value) is bool: result[key] = value
            elif isinstance(value, str) and value.lower() in ('true','false'): result[key] = value.lower() == 'true'
            else: raise TradeError('INVALID_STRATEGY_PARAM', key+'须为布尔值')
        elif spec['type'] == 'enum':
            if value not in spec['options']: raise TradeError('INVALID_STRATEGY_PARAM', key+'选项无效')
            result[key] = value
        else:
            if isinstance(value, bool): raise TradeError('INVALID_STRATEGY_PARAM', key+'须为数值')
            number = decimal_value(value, key)
            if not decimal_value(spec['minimum'],key) <= number <= decimal_value(spec['maximum'],key) or spec['type']=='integer' and number != int(number):
                raise TradeError('INVALID_STRATEGY_PARAM', key+'超出范围或不是整数')
            result[key] = str(int(number)) if spec['type']=='integer' else format(number,'f')
    return result


def describe_context_strategy(strategy_id):
    row = next((row for row in context_catalog() if row['id'] == strategy_id), None)
    if not row: raise TradeError('UNKNOWN_STRATEGY', '完整扫描策略不存在')
    # All direct calculation dependencies, including formatter copies and both
    # candidate formula paths, participate in the worker execution identity.
    root = Path(__file__).parent
    files = sorted(path for path in root.glob('*.py') if path.name.endswith('_domain.py'))
    files += [root / name for name in ('signal_context.py','signal_context_formatters.py','signal_workspace_domain.py',
        'service.py','runtime.py','domain.py','wulong_universe.py','screener_metrics.py','wyckoff_strategy.py',
        'b1_params.py','event_profiles.py','event_profile_catalog.json','legacy_catalog.json')]
    files += [root.parent / name for name in ('market/domain.py', 'market/symbols.py', 'platform/symbols.py', 'platform/types.py')]
    return {'strategy_id': strategy_id, 'strategy_version': row['version'], 'calculation_version': VERSION,
            'code_sha256': hashlib.sha256(b''.join(path.read_bytes() for path in files)).hexdigest()}


def candidate(bars, dataset, context, day):
    if context['candidate_path'] == 'legacy_tdx':
        result = build_candidate({**dataset, 'bars': bars}, day, 40)
        return result.model_dump(mode='json') if result else None
    shared = candidate_metrics_from_bars(bars)
    if shared is None: return None
    relative = asdict(relative_candidate(dataset['symbol'],bars)); event = asdict(event_candidate(bars))
    closes = [float(bar['close']) for bar in bars]
    ma20 = sum(closes[-20:])/20
    return {**shared, **relative, **event, 'symbol': dataset['symbol'], 'dataset_id': dataset['id'], 'as_of_date': bars[-1]['event_date'],
            'pullback_days': 19-max(range(20),key=lambda index: closes[-20:][index]),
            'price_vs_ma20': round((closes[-1]-ma20)/max(ma20,.01),4),
            'quality_flags': ['FLOAT_SHARES_NOT_USED_BY_SIGNAL_CONTEXT']}


def evaluate_context(strategy_id, *, symbol, bars, params, profile, context, frozen_candidate, pool_evaluation=None, quality=None):
    from trade_app.research.signal_workspace_domain import rank_score
    from trade_app.research.service import strategy_catalog, normalize_strategy_params
    quality = list(quality or [])
    source = bars[-1]['event_date'] if bars else None
    enough = frozen_candidate is not None
    result = {'status': 'computed' if enough else 'insufficient_data', 'signal': False, 'draft_eligible': False,
              'source_date': source, 'candidate': frozen_candidate, 'quality_flags': quality,
              'score': None, 'executable_date': None, 'event_profile': profile, 'signal_context': context,
              'candidate_metric_source': context['candidate_path'], 'universe': None}
    if not enough:
        result['quality_flags'].append('SIGNAL_CANDIDATE_HISTORY_INSUFFICIENT')
        result['required_bars'] = 251 if context['candidate_path']=='legacy_tdx' else 30
        return result
    quality += list(frozen_candidate.get('quality_flags') or [])
    skip_universe = context.get('universe_mode','strategy_filtered')=='full_market'
    candles = [CandlePoint(time=bar['event_date'],open=float(bar['open']),high=float(bar['high']),low=float(bar['low']),
        close=float(bar['close']),volume=bar['volume'],amount=float(bar.get('amount') or 0)) for bar in bars]
    snapshot = SignalAnalyzer.calculate_wyckoff_snapshot(SimpleNamespace(**frozen_candidate), candles, context['window_days'], event_judgment_profile=profile['snapshot'])
    admitted = True; plugin_signal = True
    if strategy_id == 'relative_strength_breakout_v1':
        passed = relative_strength_signal(SimpleNamespace(**frozen_candidate),params)
        admitted = skip_universe or passed
        # The original full_market mode skips build_universe's five gates;
        # generate_signals still applies these three price/volume conditions.
        plugin_signal = (frozen_candidate['ret40']>=float(params['min_ret40'])
            and frozen_candidate['retrace20']<=float(params['max_retrace20'])
            and frozen_candidate['up_down_volume_ratio']>=float(params['min_up_down_volume_ratio']))
        result['universe'] = {'passed': passed, 'applied':not skip_universe,'metrics': frozen_candidate, 'source_path': context['candidate_path']}
    elif strategy_id == 'matrix_signal_v1':
        if pool_evaluation is None: raise TradeError('SIGNAL_MATRIX_POOL_REQUIRED','矩阵完整扫描需要同日冻结候选池')
        admitted = skip_universe or pool_evaluation['in_pool']
        plugin_signal = pool_evaluation['components']['s5'] or pool_evaluation['components']['s6']
        result['universe'] = {**pool_evaluation,'applied':not skip_universe}
    elif strategy_id == 'b1_mtf_v1':
        b1_params = B1Params(**normalize_b1_params({key:params[key] for key in B1_SCHEMA}))
        source_bars = [{'date':bar['event_date'], **{key:float(bar[key]) for key in ('open','high','low','close')},'volume':bar['volume']} for bar in bars]
        hit = check_b1(symbol[-6:],source_bars,b1_params)
        indicator = {'has_data':len(bars)>=250,'signal':hit is not None, **(hit or {})}
        snapshot['b1_mtf_signal'] = indicator; plugin_signal = hit is not None
    elif strategy_id not in WYCKOFF_STRATEGY_IDS:
        base = next(row['signal_params'] for row in strategy_catalog() if row['id']==strategy_id)
        raw = evaluate_strategy(strategy_id,symbol=symbol,bars=bars,
            params=normalize_strategy_params(strategy_id,{key:params[key] for key in base}),quality=quality)
        names = {'wulong_cluster_v1':'wulong_cluster_signal','emotion_limit_up_v1':'emotion_limit_up_signal',
                 'ths_force_rhythm_v1':'force_rhythm_signal','limit_up_arb_v1':'limit_up_arb_signal'}
        name = 'trend_king_signal' if strategy_id in TREND_KING_IDS else 'ths_main_retail_signal' if strategy_id in THS_TRIGGERS else names[strategy_id]
        snapshot[name] = raw.get('indicator') or {}
        plugin_signal = raw.get('shape_signal',raw.get('signal')) is True
        quality += [flag for flag in raw.get('quality_flags',[]) if not flag.startswith('candidate_universe_')]
        if strategy_id == 'wulong_cluster_v1':
            universe = evaluate_wulong_candidate(frozen_candidate,{key:params[key] for key in UNIVERSE_PARAM_KEYS})
            admitted = skip_universe or universe['passed']
            result['universe'] = {**universe,'applied':not skip_universe,'source_path':context['candidate_path']}
    custom = SignalContextBuilder()._build_strategy_signal_context(strategy_id,snapshot,params)
    if custom is not None:
        name, trigger = custom['signal_name'],custom['trigger_date']
        snapshot.update(custom, signal=name, events=[name], risk_events=[], event_dates={name:trigger},
            event_chain=[{'event':name,'date':trigger,'category':'custom'}],event_confirmation_map={name:'confirmed'})
    gates = evaluate_wyckoff_strategy('wyckoff_trend_v1',snapshot,
        {'window_days': context['window_days'], **{key:params[key] for key in GATE_KEYS}})
    gates['limitations'] = ['此处为七项通用事件门槛；完整候选预筛和排名分别保存在 universe/ranking',
        '事件确认仅在冻结决策时点前的行情中判定，回标发生日不代表当日即可得知']
    signal = admitted and plugin_signal and gates['signal']
    quality += gates['quality_flags']
    if not admitted: quality.append('SIGNAL_CANDIDATE_UNIVERSE_REJECTED')
    rank = rank_score(strategy_id, {'indicator': snapshot,'evaluation':gates}, frozen_candidate, params=params, metric_source=context['candidate_path'])
    draft_eligible = signal and (strategy_id not in WYCKOFF_STRATEGY_IDS or gates['positive_primary_event'])
    if strategy_id=='wulong_cluster_v1' and skip_universe:
        draft_eligible = False
        result['draft_block_reason'] = '此扫描明确跳过五龙入池预筛；可观察信号，创建模拟草稿需通过完整入池核对的运行'
    result.update(signal=signal, shape_signal=plugin_signal, draft_eligible=draft_eligible,
                  indicator=_json_safe(snapshot),evaluation=gates,ranking=rank,quality_flags=sorted(set(quality)),
                  candidate_universe_filter_run=not skip_universe)
    return result
