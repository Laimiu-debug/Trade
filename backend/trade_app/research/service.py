"""Persist deterministic research inputs and outcomes against frozen market data."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.domain import eligible_bars
from trade_app.market.service import get_dataset
from trade_app.platform.types import TradeError, decimal_value, utc_now
from trade_app.research.domain import (
    CALCULATION_VERSION, DEFAULT_PARAMS, STRATEGY_ID, STRATEGY_VERSION,
    normalize_params,
)
from trade_app.research.models import ResearchRun
from trade_app.research.wulong_universe import DEFAULT_UNIVERSE_PARAMS, UNIVERSE_PARAM_KEYS, normalize_universe_params
from trade_app.research.wyckoff_strategy import WYCKOFF_STRATEGY_IDS, normalize_wyckoff_params, wyckoff_param_schema
from trade_app.research.classic_catalog import classic_catalog
from trade_app.research.classic_domain import CLASSIC_STRATEGY_IDS, VERSION as CLASSIC_VERSION, normalize_classic_params
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES, evaluate_strategy

WYCKOFF_VERSION = 'legacy-wyckoff-frozen-observation-v2'


TREND_KING_MODES = {'trend_king_v1': 'all', 'trend_king_limitup_v1': 'a',
                    'trend_king_rally_v1': 'b', 'trend_king_pullback_v1': 'c'}
TREND_KING_VERSION = 'legacy-trend-king-frozen-v1'
TREND_KING_PARAMS = {'min_day_gain_a': '9.5', 'min_hist': '30',
                     'min_gain_3d_b': '7', 'min_gain_5d_b': '10',
                     'max_vol_ratio_c': '1.5', 'min_hist_c': '50',
                     'min_prev_gain_c': '3'}
TREND_KING_BOUNDS = {'min_day_gain_a': ('5', '20'), 'min_hist': ('10', '100'),
                     'min_gain_3d_b': ('3', '20'), 'min_gain_5d_b': ('5', '30'),
                     'max_vol_ratio_c': ('0.5', '3'), 'min_hist_c': ('20', '100'),
                     'min_prev_gain_c': ('0', '10')}
LIMIT_UP_ARB_ID = 'limit_up_arb_v1'
LIMIT_UP_ARB_VERSION = 'legacy-limit-up-arb-frozen-v1'
LIMIT_UP_ARB_PARAMS = {'min_day_gain': '3', 'min_volume_ratio_prev': '1.2',
                       'min_volume_ratio_ma5': '0', 'sector_rank_weight': '0.08'}
THS_TRIGGERS = {'ths_main_force_flip_v1': 'purple_to_yellow',
                'ths_main_force_golden_cross_v1': 'golden_cross'}
THS_VERSION = 'legacy-ths-volume-frozen-v1'
EMOTION_ID = 'emotion_limit_up_v1'
EMOTION_VERSION = 'legacy-emotion-limit-up-frozen-v1'
EMOTION_PARAMS = {'min_day_gain': '9.5', 'lookback_days': '5', 'min_score': '0'}
WULONG_ID = 'wulong_cluster_v1'
WULONG_VERSION = 'legacy-wulong-frozen-with-universe-v2'
WULONG_PARAMS = {'convergence_threshold_pct': '0.04',
                 'pre_convergence_min_spread_pct': '0.02',
                 'min_current_spread_pct': '0.01',
                 'min_spread_expansion_multiple': '1.6',
                 'min_volume_ratio20': '1.2',
                 'min_breakout_return_pct': '0.006',
                 'max_convergence_age_days': '10',
                 'min_rising_ma_count': '3'}
RHYTHM_ID = 'ths_force_rhythm_v1'
RHYTHM_VERSION = 'legacy-force-rhythm-frozen-v1'
RHYTHM_PARAM_KEYS = ('min_cycle_count', 'max_cycle_cv', 'max_amplitude_cv',
                     'min_autocorr', 'min_wave_swing_ratio', 'trough_percentile_max',
                     'peak_percentile_min', 'retail_high_percentile_min',
                     'require_trough_turn', 'block_buy_on_retail_sell',
                     'peak_reject_percentile_min', 'flip_trough_percentile_min',
                     'flip_trough_percentile_max', 'trough_turn_percentile_min',
                     'trough_turn_percentile_max', 'deep_trough_percentile_max',
                     'deep_trough_streak_target', 'flip_follow_bars_ago',
                     'flip_follow_max_trough_percentile', 'flip_follow_min_trough_percentile',
                     'retail_block_min_trough_percentile')


def normalize_rhythm_params(raw: dict[str, str]) -> dict:
    row = next(item for item in strategy_catalog() if item['id'] == RHYTHM_ID)
    if set(raw) - set(RHYTHM_PARAM_KEYS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '主散节奏波包含不支持的参数')
    result = {}
    for key in RHYTHM_PARAM_KEYS:
        spec = row['params_schema'][key]
        value = raw.get(key, row['default_params'][key])
        if spec['type'] == 'boolean':
            if isinstance(value, bool):
                result[key] = value
            elif str(value).lower() in ('true', 'false'):
                result[key] = str(value).lower() == 'true'
            else:
                raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为 true 或 false')
            continue
        number = decimal_value(value, key)
        if number < decimal_value(str(spec['minimum']), key) or number > decimal_value(str(spec['maximum']), key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        if spec['type'] == 'integer' and number != int(number):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为整数')
        result[key] = format(number, 'f')
    return result


def normalize_wulong_params(raw: dict[str, str]) -> dict:
    if set(raw) - set(WULONG_PARAMS) - set(UNIVERSE_PARAM_KEYS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '五龙聚首包含不支持的参数')
    schema = next(row['params_schema'] for row in strategy_catalog() if row['id'] == WULONG_ID)
    result = dict(WULONG_PARAMS)
    for key, value in raw.items():
        if key in UNIVERSE_PARAM_KEYS:
            continue
        number = decimal_value(value, key)
        item = schema[key]
        if number < decimal_value(str(item['minimum']), key) or number > decimal_value(str(item['maximum']), key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        if item['type'] == 'integer' and number != int(number):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 须为整数')
        result[key] = format(number, 'f')
    return {**result, **normalize_universe_params({key: value for key, value in raw.items() if key in UNIVERSE_PARAM_KEYS})}


def normalize_emotion_params(raw: dict[str, str]) -> dict[str, str]:
    if set(raw) - set(EMOTION_PARAMS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '情绪涨停包含不支持的参数')
    bounds = {'min_day_gain': ('5', '20'), 'lookback_days': ('3', '10'),
              'min_score': ('0', '100')}
    result = dict(EMOTION_PARAMS)
    for key, value in raw.items():
        number = decimal_value(value, key)
        lower, upper = bounds[key]
        if number < decimal_value(lower, key) or number > decimal_value(upper, key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        if key == 'lookback_days' and number != int(number):
            raise TradeError('INVALID_STRATEGY_PARAM', '回溯窗口须为整数')
        result[key] = format(number, 'f')
    return result


def normalize_trend_king_params(raw: dict[str, str], strategy_id: str) -> dict[str, str]:
    if set(raw) - set(TREND_KING_PARAMS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '趋势为王包含不支持的参数')
    result = dict(TREND_KING_PARAMS)
    for key, value in raw.items():
        number = decimal_value(value, key)
        lower, upper = TREND_KING_BOUNDS[key]
        if number < decimal_value(lower, key) or number > decimal_value(upper, key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        result[key] = format(number, 'f')
    result['mode'] = TREND_KING_MODES[strategy_id]
    return result


def normalize_limit_up_arb_params(raw: dict[str, str]) -> dict[str, str]:
    if set(raw) - set(LIMIT_UP_ARB_PARAMS):
        raise TradeError('UNKNOWN_STRATEGY_PARAM', '涨停套利包含不支持的参数')
    bounds = {'min_day_gain': (1, 20), 'min_volume_ratio_prev': (1, 5),
              'min_volume_ratio_ma5': (0, 5), 'sector_rank_weight': (0, 0.3)}
    result = dict(LIMIT_UP_ARB_PARAMS)
    for key, value in raw.items():
        number = decimal_value(value, key)
        lower, upper = bounds[key]
        if number < decimal_value(str(lower), key) or number > decimal_value(str(upper), key):
            raise TradeError('INVALID_STRATEGY_PARAM', f'{key} 超出支持范围')
        result[key] = format(number, 'f')
    return result


@lru_cache(maxsize=1)
def strategy_catalog() -> list[dict]:
    from trade_app.research.matrix_domain import MATRIX_ID, MATRIX_VERSION, normalize_matrix_params
    snapshot = json.loads((Path(__file__).with_name('legacy_catalog.json')).read_text(encoding='utf-8'))
    result = []
    for legacy in snapshot['strategies']:
        partial = legacy['strategy_id'] == STRATEGY_ID
        b1_screener = legacy['strategy_id'] == 'b1_mtf_v1'
        trend_king = legacy['strategy_id'] in TREND_KING_MODES
        limit_up_arb = legacy['strategy_id'] == LIMIT_UP_ARB_ID
        ths_signal = legacy['strategy_id'] in THS_TRIGGERS
        emotion_signal = legacy['strategy_id'] == EMOTION_ID
        wulong_signal = legacy['strategy_id'] == WULONG_ID
        rhythm_signal = legacy['strategy_id'] == RHYTHM_ID
        matrix_pool = legacy['strategy_id'] == MATRIX_ID
        wyckoff_signal = legacy['strategy_id'] in WYCKOFF_STRATEGY_IDS
        rhythm_params = ({key: str(legacy['default_params'][key]).lower()
                          for key in RHYTHM_PARAM_KEYS} if rhythm_signal else None)
        result.append({
            'id': legacy['strategy_id'], 'name': legacy['name'],
            'version': legacy['version'], 'enabled_in_legacy': legacy['enabled'],
            'is_legacy_default': legacy['is_default'],
            'capabilities': legacy['capabilities'], 'params_schema': legacy['params_schema'],
            'default_params': legacy['default_params'], 'signal_params': DEFAULT_PARAMS if partial else TREND_KING_PARAMS if trend_king else LIMIT_UP_ARB_PARAMS if limit_up_arb else {} if ths_signal else EMOTION_PARAMS if emotion_signal else {**WULONG_PARAMS, **{key: str(value).lower() for key, value in DEFAULT_UNIVERSE_PARAMS.items()}} if wulong_signal else rhythm_params,
            'pool_params': normalize_matrix_params({}) if matrix_pool else None,
            'signal_top_n': legacy['signal_top_n'], 'description': legacy['description'],
            'playbook': legacy['playbook'], 'source_sha256': snapshot['source_sha256'],
            'status': 'partial_signal' if partial else 'b1_screener' if b1_screener else 'trend_king_signal' if trend_king else 'limit_up_arb_signal' if limit_up_arb else 'ths_signal' if ths_signal else 'emotion_signal' if emotion_signal else 'wulong_signal' if wulong_signal else 'rhythm_signal' if rhythm_signal else 'matrix_pool' if matrix_pool else 'not_migrated',
            'calculation_version': CALCULATION_VERSION if partial else TREND_KING_VERSION if trend_king else LIMIT_UP_ARB_VERSION if limit_up_arb else THS_VERSION if ths_signal else EMOTION_VERSION if emotion_signal else WULONG_VERSION if wulong_signal else RHYTHM_VERSION if rhythm_signal else MATRIX_VERSION if matrix_pool else None,
            'limitations': ['仅支持固定行情单股观察信号', '评分、完整策略事件和回测尚待对照']
                           if partial else ['B1 冻结样本或通达信全市场扫描可用', '完整策略事件与回测尚待迁移']
                           if b1_screener else ['单股冻结日线信号可用；无板块排名，模式评分仅为指标分', '完整策略事件、排名与回测尚待迁移']
                           if trend_king else ['单股冻结日线信号可用；板块排名不可得', '旧版入场、出场与回测尚待迁移']
                           if limit_up_arb else ['单股冻结日线主力紫转黄或金叉触发可用', '候选排名、事件与回测尚待迁移']
                           if ths_signal else ['单股冻结日线涨停与主力量能共振信号可用', '次日倍量确认、候选排名与回测尚待迁移']
                           if emotion_signal else ['五龙形态与旧单股候选入池过滤可用', '全市场候选口径、完整排名与回测尚待迁移']
                           if wulong_signal else ['单股冻结日线主散节奏波信号可用', '候选排名、出场执行和旧回测尚待迁移']
                           if rhythm_signal else ['冻结筛选输入池上的旧矩阵插件近似信号可用', '向量化矩阵引擎、事件质量与出场回测尚待迁移']
                           if matrix_pool else ['执行逻辑尚未迁移，不能在新应用运行'],
        })
        if b1_screener:
            from trade_app.research.b1_params import DEFAULTS as B1_DEFAULTS, SCHEMA as B1_SCHEMA, VERSION as B1_VERSION
            result[-1].update(scanner_params=B1_DEFAULTS, calculation_version=B1_VERSION,
                params_schema={**{key: {**value, 'readonly': True, 'readonly_reason': 'B1扫描未使用的旧通用事件/评分字段'} for key, value in legacy['params_schema'].items() if key not in B1_SCHEMA}, **B1_SCHEMA})
        if wyckoff_signal:
            result[-1].update({
                'params_schema': wyckoff_param_schema(legacy['strategy_id']),
                'signal_params': {key: str(value).lower() if isinstance(value, bool) else str(value)
                                  for key, value in normalize_wyckoff_params(legacy['strategy_id'], {}).items()},
                'status': 'wyckoff_signal', 'calculation_version': WYCKOFF_VERSION,
                'limitations': ['冻结日线上的事件链、阶段、质量评分及观察门槛可用',
                                '单股质量分不是跨股排名；仅正向主事件可创建买入草稿；旧版出场回测待迁移'],
            })
    return result + classic_catalog()


def normalize_strategy_params(strategy_id: str, raw: dict) -> dict:
    if strategy_id not in SINGLE_SYMBOL_STRATEGIES:
        raise TradeError('STRATEGY_NOT_MIGRATED', '该策略尚不支持单股研究运行', 409)
    if strategy_id in CLASSIC_STRATEGY_IDS:
        return normalize_classic_params(strategy_id, raw)
    if strategy_id in THS_TRIGGERS:
        if raw:
            raise TradeError('UNKNOWN_STRATEGY_PARAM', '天下无双单股触发器不接受筛选排名参数')
        return {}
    if strategy_id == STRATEGY_ID:
        return normalize_params(raw)
    if strategy_id in TREND_KING_MODES:
        return normalize_trend_king_params(raw, strategy_id)
    if strategy_id == LIMIT_UP_ARB_ID:
        return normalize_limit_up_arb_params(raw)
    if strategy_id == EMOTION_ID:
        return normalize_emotion_params(raw)
    if strategy_id == WULONG_ID:
        return normalize_wulong_params(raw)
    if strategy_id == RHYTHM_ID:
        return normalize_rhythm_params(raw)
    return normalize_wyckoff_params(strategy_id, raw)


def run_data(row: ResearchRun) -> dict:
    return {'id': row.id, 'dataset_id': row.dataset_id, 'strategy_id': row.strategy_id,
            'strategy_version': row.strategy_version, 'decision_at': row.decision_at,
            'strict': bool(row.strict), 'params': json.loads(row.params_json),
            'result': json.loads(row.result_json), 'created_at': row.created_at}


def describe_strategy(strategy_id: str) -> dict:
    """Freeze version and executable code identity without database access."""
    if strategy_id not in SINGLE_SYMBOL_STRATEGIES:
        raise TradeError('STRATEGY_NOT_MIGRATED', '该策略尚不支持单股研究运行', 409)
    strategy_version = STRATEGY_VERSION if strategy_id == STRATEGY_ID else next(
        item['version'] for item in strategy_catalog() if item['id'] == strategy_id)
    calculation_version = (CLASSIC_VERSION if strategy_id in CLASSIC_STRATEGY_IDS else
                           CALCULATION_VERSION if strategy_id == STRATEGY_ID else
                           TREND_KING_VERSION if strategy_id in TREND_KING_MODES else
                           LIMIT_UP_ARB_VERSION if strategy_id == LIMIT_UP_ARB_ID else
                           EMOTION_VERSION if strategy_id == EMOTION_ID else
                           WULONG_VERSION if strategy_id == WULONG_ID else
                           RHYTHM_VERSION if strategy_id == RHYTHM_ID else
                           WYCKOFF_VERSION if strategy_id in WYCKOFF_STRATEGY_IDS else THS_VERSION)
    code_bytes: bytes
    if strategy_id in CLASSIC_STRATEGY_IDS:
        code_bytes = (Path(__file__).with_name('classic_domain.py').read_bytes()
                      + Path(__file__).with_name('classic_catalog.py').read_bytes())
    elif strategy_id == STRATEGY_ID:
        code_bytes = Path(__file__).with_name('domain.py').read_bytes()
    else:
        code_file = Path(__file__).with_name(
            'trend_king_domain.py' if strategy_id in TREND_KING_MODES else
            'limit_up_arb_domain.py' if strategy_id == LIMIT_UP_ARB_ID else
            'emotion_limit_up_domain.py' if strategy_id == EMOTION_ID else
            'wulong_domain.py' if strategy_id == WULONG_ID else
            'force_rhythm_domain.py' if strategy_id == RHYTHM_ID else
            'wyckoff_domain.py' if strategy_id in WYCKOFF_STRATEGY_IDS else 'ths_volume_domain.py')
        code_bytes = code_file.read_bytes()
        if strategy_id == EMOTION_ID:
            code_bytes += Path(__file__).with_name('ths_volume_domain.py').read_bytes()
        if strategy_id == WULONG_ID:
            code_bytes += Path(__file__).with_name('wulong_universe.py').read_bytes()
        if strategy_id == LIMIT_UP_ARB_ID:
            code_bytes += ((Path(__file__).parent.parent / 'market' / 'symbols.py').read_bytes() +
                           (Path(__file__).parent.parent / 'platform' / 'symbols.py').read_bytes())
        if strategy_id in WYCKOFF_STRATEGY_IDS:
            for dependency in ('wyckoff_strategy.py', 'event_profiles.py', 'event_profile_catalog.json'):
                code_bytes += Path(__file__).with_name(dependency).read_bytes()
    code_sha256 = hashlib.sha256(
        code_bytes + Path(__file__).read_bytes() + Path(__file__).with_name('runtime.py').read_bytes() +
        (Path(__file__).parent.parent / 'market' / 'domain.py').read_bytes()).hexdigest()
    return {'strategy_id': strategy_id, 'strategy_version': strategy_version,
            'calculation_version': calculation_version, 'code_sha256': code_sha256}


def create_run(session: Session, data_dir: Path, body: dict) -> dict:
    strategy_id = body['strategy_id']
    params = normalize_strategy_params(strategy_id, body.get('params') or {})
    from trade_app.research.registry_service import require_enabled
    require_enabled(session, [strategy_id])
    dataset = get_dataset(session, data_dir, body['dataset_id'])
    event_profile = None
    if strategy_id in WYCKOFF_STRATEGY_IDS:
        event_profile = freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
    elif body.get('event_profile_id') is not None or body.get('event_profile_revision') is not None:
        raise TradeError('EVENT_PROFILE_UNSUPPORTED', '该策略不使用事件模板，请移除模板参数')
    try:
        decision = datetime.fromisoformat(body['decision_at'].replace('Z', '+00:00'))
        if decision.tzinfo is None:
            raise ValueError(body['decision_at'])
        decision_at = decision.astimezone(timezone.utc).isoformat()
    except ValueError as exc:
        raise TradeError('INVALID_DECISION_TIME', '决策时间需要时区') from exc
    descriptor = describe_strategy(strategy_id)
    strategy_version = descriptor['strategy_version']
    calculation_version = descriptor['calculation_version']
    code_sha256 = descriptor['code_sha256']
    identity = {'dataset_id': dataset['id'], 'strategy_id': strategy_id,
                'strategy_version': strategy_version, 'calculation_version': calculation_version,
                'decision_at': decision_at, 'strict': body['strict'], 'params': params}
    if code_sha256:
        identity['code_sha256'] = code_sha256
    if event_profile:
        identity['event_profile'] = event_profile
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    run_id = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    existing = session.get(ResearchRun, run_id)
    if existing is not None:
        return run_data(existing)
    bars, quality = eligible_bars(dataset['bars'], decision_at, body['strict'])
    result = evaluate_strategy(strategy_id, symbol=dataset['symbol'], bars=bars, params=params,
                               event_profile=event_profile, quality=quality)
    result.update(code_sha256=code_sha256, calculation_version=calculation_version)
    row = ResearchRun(id=run_id, dataset_id=dataset['id'], strategy_id=strategy_id,
                      strategy_version=strategy_version, decision_at=decision_at,
                      strict=int(body['strict']), params_json=json.dumps(params, sort_keys=True),
                      result_json=json.dumps(result, ensure_ascii=False, sort_keys=True),
                      created_at=utc_now())
    session.add(row)
    session.flush()
    return run_data(row)


def list_runs(session: Session) -> list[dict]:
    return [run_data(row) for row in session.scalars(select(ResearchRun).order_by(
        ResearchRun.created_at.desc(), ResearchRun.id).limit(100))]


def get_run(session: Session, run_id: str) -> dict:
    row = session.get(ResearchRun, run_id)
    if row is None:
        raise TradeError('RESEARCH_RUN_NOT_FOUND', '研究运行不存在', 404)
    return run_data(row)
