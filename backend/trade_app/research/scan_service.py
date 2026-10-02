"""Bounded synchronous strategy scans over explicit immutable daily datasets."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.scan_models import StrategyScanRun
from trade_app.research.service import create_run, strategy_catalog


CALCULATION_VERSION = 'frozen-single-strategy-scan-v1'
PROFILE_STRATEGIES = frozenset(('wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'))
MAX_SINGLE_EVALUATIONS = 100
MAX_RANGE_EVALUATIONS = 1000
MAX_RANGE_CALENDAR_DAYS = 90


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_encode(value).encode('utf-8')).hexdigest()


def _day(raw: object, field: str) -> str:
    try:
        result = date.fromisoformat(raw)
        if result.isoformat() != raw:
            raise ValueError(raw)
        return result.isoformat()
    except (ValueError, TypeError) as exc:
        raise TradeError('INVALID_SCAN_DATE', f'{field} 须为 YYYY-MM-DD') from exc


def _request(body: dict) -> dict:
    if not isinstance(body, dict) or set(body) - {'dataset_ids', 'strategies', 'as_of_date', 'date_from', 'date_to', 'strict', 'event_profile_id', 'event_profile_revision', 'signal_context'}:
        raise TradeError('INVALID_SCAN_REQUEST', '扫描请求包含未知字段')
    context = None
    if body.get('signal_context') is not None:
        from trade_app.research.signal_context import normalize_context
        context = normalize_context(body['signal_context'])
    dataset_ids = body.get('dataset_ids')
    if not isinstance(dataset_ids, list) or not 1 <= len(dataset_ids) <= 100:
        raise TradeError('INVALID_SCAN_DATASETS', '扫描需要 1 至 100 个冻结行情样本')
    if any(not isinstance(item, str) for item in dataset_ids) or len(set(dataset_ids)) != len(dataset_ids):
        raise TradeError('DUPLICATE_SCAN_DATASET', '冻结行情样本 ID 不能重复')
    raw_strategies = body.get('strategies')
    if not isinstance(raw_strategies, list) or not 1 <= len(raw_strategies) <= 10:
        raise TradeError('INVALID_SCAN_STRATEGIES', '扫描需要 1 至 10 个单股策略')
    catalog = {row['id']: row for row in strategy_catalog()}
    if context:
        from trade_app.research.signal_context import context_catalog
        catalog = {row['id']: row for row in context_catalog()}
    strategies = []
    seen = set()
    for raw in raw_strategies:
        if not isinstance(raw, dict) or set(raw) - {'strategy_id', 'params'} or not isinstance(raw.get('strategy_id'), str):
            raise TradeError('INVALID_SCAN_STRATEGIES', '策略必须包含 strategy_id 和参数对象')
        strategy_id = raw['strategy_id']
        if strategy_id in seen:
            raise TradeError('DUPLICATE_SCAN_STRATEGY', '同一扫描中的策略不能重复')
        seen.add(strategy_id)
        descriptor = catalog.get(strategy_id)
        if context and descriptor is None:
            raise TradeError('SIGNAL_CONTEXT_UNSUPPORTED', '该策略没有旧插件完整候选扫描适配，请使用单股观察或普通扫描入口', 409)
        if descriptor is None or descriptor.get('signal_params') is None:
            raise TradeError('STRATEGY_NOT_SINGLE_RUN', '该策略尚不支持冻结样本单股运行', 409)
        params = raw.get('params', {})
        if not isinstance(params, dict):
            raise TradeError('INVALID_SCAN_PARAMS', '每个策略的 params 须为对象')
        strategies.append({'strategy_id': strategy_id, 'params': dict(params)})
    strict = body.get('strict', True)
    if not isinstance(strict, bool):
        raise TradeError('INVALID_SCAN_STRICT', 'strict 须为 true 或 false')
    as_of_date = body.get('as_of_date')
    date_from, date_to = body.get('date_from'), body.get('date_to')
    if as_of_date is not None:
        if date_from is not None or date_to is not None:
            raise TradeError('INVALID_SCAN_DATE', '单日扫描与日期区间不能同时设置')
        as_of_date = _day(as_of_date, 'as_of_date')
        mode = 'single_date'
    else:
        date_from, date_to = _day(date_from, 'date_from'), _day(date_to, 'date_to')
        span = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days + 1
        if not 1 <= span <= MAX_RANGE_CALENDAR_DAYS:
            raise TradeError('INVALID_SCAN_RANGE', '扫描区间须按先后顺序且不超过 90 个自然日')
        mode = 'date_range'
    return {'dataset_ids': sorted(dataset_ids), 'strategies': sorted(strategies, key=lambda row: row['strategy_id']),
            'mode': mode, 'as_of_date': as_of_date, 'date_from': date_from, 'date_to': date_to,
            'strict': strict, **({'signal_context': context} if context else {})}


def _local_score(result: dict) -> tuple[float | None, str | None]:
    evaluation = result.get('evaluation') or {}
    for key, formula in (('local_score', evaluation.get('local_score_formula', 'strategy_local_score')),
                         ('signal_score', 'strategy_indicator_score')):
        value = evaluation.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value, formula
    return None, None


def _evidence(run: dict, dataset: dict, canonical_symbol: str, decision_date: str) -> dict:
    result = run['result']
    indicator = result.get('indicator') or {}
    evaluation = result.get('evaluation') or {}
    fresh_source = result.get('source_date') == decision_date
    signal = result.get('signal') is True and fresh_source
    quality = list(result.get('quality_flags') or [])
    if result.get('source_date') is not None and not fresh_source:
        quality.append('SOURCE_DATE_BEFORE_SCAN_DATE')
    score, score_kind = _local_score(result)
    primary = evaluation.get('primary_event') or indicator.get('signal')
    return {'run_id': run['id'], 'dataset_id': dataset['id'], 'symbol': canonical_symbol,
            'imported_symbol': dataset['symbol'], 'strategy_id': run['strategy_id'],
            'strategy_version': run['strategy_version'], 'decision_date': decision_date,
            'decision_at': run['decision_at'], 'source_date': result.get('source_date'),
            'status': result['status'], 'run_signal': result.get('signal'),
            'signal': signal, 'fresh_source': fresh_source,
            'draft_eligible': signal and result.get('draft_eligible') is not False,
            'shape_signal': result.get('shape_signal'),
            'primary_signal': primary if isinstance(primary, str) else None,
            'events': list(indicator.get('events') or []),
            'risk_events': list(indicator.get('risk_events') or []),
            'local_score': score, 'score_kind': score_kind,
            'quality_flags': sorted(set(quality)), 'code_sha256': result.get('code_sha256'),
            'calculation_version': result.get('calculation_version')}


def _aggregate(rows: list[dict], strategies: list[dict]) -> dict:
    hits_by_day = defaultdict(list)
    by_symbol = defaultdict(list)
    for row in rows:
        by_symbol[row['symbol']].append(row)
        if row['signal']:
            hits_by_day[(row['symbol'], row['decision_date'])].append(row)
    union = []
    intersection = []
    for (symbol, day), hits in sorted(hits_by_day.items(), key=lambda item: (item[0][1], item[0][0])):
        group = {'symbol': symbol, 'decision_date': day, 'matched_strategy_count': len(hits),
                 'strategy_ids': sorted(row['strategy_id'] for row in hits),
                 'run_ids': [row['run_id'] for row in hits]}
        union.append(group)
        if len(strategies) >= 2 and len(hits) == len(strategies):
            intersection.append(group)
    per_symbol = []
    for symbol, evidence in sorted(by_symbol.items()):
        appearances = []
        for strategy in strategies:
            selected = [row for row in evidence if row['strategy_id'] == strategy['strategy_id']]
            hits = [row for row in selected if row['signal']]
            appearances.append({'strategy_id': strategy['strategy_id'], 'evaluation_count': len(selected),
                                'signal_count': len(hits), 'signal_dates': [row['decision_date'] for row in hits],
                                'run_ids': [row['run_id'] for row in hits],
                                'scores': [{'date': row['decision_date'], 'value': row['local_score'],
                                            'kind': row['score_kind']} for row in hits]})
        per_symbol.append({'symbol': symbol, 'strategies': appearances})
    return {'union': union, 'intersection': intersection, 'per_symbol': per_symbol}


def create_scan(session: Session, data_dir: Path, body: dict) -> dict:
    """Create at most 100 single-day or 1000 range evaluations in one transaction."""
    request = _request(body)
    if request.get('signal_context'):
        raise TradeError('ASYNC_SCAN_REQUIRED', '完整信号上下文请通过后台扫描队列执行', 409)
    from trade_app.research.registry_service import require_enabled
    request['registry_admission'] = require_enabled(session, [row['strategy_id'] for row in request['strategies']])
    datasets = []
    seen_symbols = set()
    for dataset_id in request['dataset_ids']:
        dataset = get_dataset(session, data_dir, dataset_id)
        exchange, code = normalize_a_share_symbol(dataset['symbol'])
        canonical = exchange + code
        if canonical in seen_symbols:
            raise TradeError('DUPLICATE_SCAN_SYMBOL', '同一证券只能选择一个冻结行情样本，别名也视为重复')
        seen_symbols.add(canonical)
        datasets.append((canonical, dataset))
    datasets.sort(key=lambda pair: pair[0])
    planned = []
    for canonical, dataset in datasets:
        dates = ([request['as_of_date']] if request['mode'] == 'single_date' else
                 [bar['event_date'] for bar in dataset['bars']
                  if request['date_from'] <= bar['event_date'] <= request['date_to']])
        planned.extend((day, canonical, dataset) for day in dates)
    planned.sort(key=lambda item: (item[0], item[1]))
    evaluations = len(planned) * len(request['strategies'])
    limit = MAX_SINGLE_EVALUATIONS if request['mode'] == 'single_date' else MAX_RANGE_EVALUATIONS
    if evaluations == 0:
        raise TradeError('SCAN_RANGE_EMPTY', '所选区间没有样本交易日')
    if evaluations > limit:
        raise TradeError('SCAN_TOO_LARGE', f'本次扫描共 {evaluations} 次判断，当前上限为 {limit}')
    uses_profile = any(row['strategy_id'] in PROFILE_STRATEGIES for row in request['strategies'])
    if not uses_profile and (body.get('event_profile_id') is not None or body.get('event_profile_revision') is not None):
        raise TradeError('EVENT_PROFILE_NOT_APPLICABLE', '所选策略不使用维科夫事件模板')
    profile = (freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
               if uses_profile else None)
    request['event_profile'] = profile
    request['datasets'] = [
        {'id': dataset['id'], 'symbol': canonical, 'imported_symbol': dataset['symbol'],
         'provider': dataset['provider'], 'adjustment': dataset['adjustment'],
         'content_sha256': dataset['id']}
        for canonical, dataset in datasets]
    scan_code = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rows = []
    normalized_strategies = {}
    # A later invalid strategy parameter must not leave earlier child runs behind.
    # HTTP writes hold BEGIN IMMEDIATE; this savepoint also makes the operation
    # atomic for callers that catch a validation error inside their transaction.
    with session.begin_nested():
        for day, canonical, dataset in planned:
            decision = datetime.combine(date.fromisoformat(day), time(23, 59, 59),
                                        tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc).isoformat()
            for strategy in request['strategies']:
                child_body = {'dataset_id': dataset['id'], 'strategy_id': strategy['strategy_id'],
                              'decision_at': decision, 'strict': request['strict'], 'params': strategy['params']}
                if profile and strategy['strategy_id'] in PROFILE_STRATEGIES:
                    child_body.update(event_profile_id=profile['profile_id'], event_profile_revision=profile['revision'])
                run = create_run(session, data_dir, child_body)
                if profile and strategy['strategy_id'] in PROFILE_STRATEGIES and run['result'].get('event_profile') != profile:
                    raise TradeError('EVENT_PROFILE_CHANGED', '扫描期间事件模板发生变化，请重新运行', 409)
                rows.append(_evidence(run, dataset, canonical, day))
                normalized_strategies[run['strategy_id']] = {
                    'strategy_id': run['strategy_id'], 'params': run['params'],
                    'strategy_version': run['strategy_version'],
                    'calculation_version': run['result'].get('calculation_version')}
        request['strategies'] = [normalized_strategies[key] for key in sorted(normalized_strategies)]
        result = {'calculation_version': CALCULATION_VERSION, 'mode': request['mode'],
                  'decision_timezone': 'Asia/Shanghai', 'decision_time': '23:59:59',
                  'evaluation_count': len(rows), 'signal_count': sum(row['signal'] for row in rows),
                  'intersection_supported': len(request['strategies']) >= 2,
                  'rows': rows, 'run_ids': [row['run_id'] for row in rows],
                  **_aggregate(rows, request['strategies']),
                  'notes': ['只统计源日期等于扫描日期的触发，旧源日期结果仍保留研究证据',
                            '交集和并集按同一证券、同一扫描日期计算',
                            '分数为各策略自身指标，不能跨策略当作排名',
                            '当前为冻结样本同步扫描，未实现旧交叉验证的收益、回撤和组合回测']}
        code_sha256 = _digest({'scan_code_sha256': scan_code,
                               'child_code_sha256': sorted({row['code_sha256'] for row in rows if row['code_sha256']})})
        run_id = _digest({'request': request, 'result': result, 'code_sha256': code_sha256})
        existing = session.get(StrategyScanRun, run_id)
        if existing is None:
            existing = StrategyScanRun(id=run_id, request_json=_encode(request), result_json=_encode(result),
                                       code_sha256=code_sha256, created_at=utc_now())
            session.add(existing)
            session.flush()
        return _view(existing)


def _view(row: StrategyScanRun, detail: bool = True) -> dict:
    request, result = json.loads(row.request_json), json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at, 'code_sha256': row.code_sha256,
            'mode': request['mode'], 'as_of_date': request['as_of_date'],
            'date_from': request['date_from'], 'date_to': request['date_to'],
            'dataset_count': len(request['datasets']), 'strategy_count': len(request['strategies']),
            'evaluation_count': result['evaluation_count'], 'signal_count': result['signal_count'],
            'union_count': len(result['union']), 'intersection_count': len(result['intersection']),
            **({'request': request, 'result': result} if detail else {})}


def get_scan(session: Session, scan_id: str) -> dict:
    row = session.get(StrategyScanRun, scan_id)
    if row is None:
        raise TradeError('STRATEGY_SCAN_NOT_FOUND', '策略扫描记录不存在', 404)
    return _view(row)


def list_scans(session: Session) -> list[dict]:
    return [_view(row, False) for row in session.scalars(select(StrategyScanRun).order_by(
        StrategyScanRun.created_at.desc(), StrategyScanRun.id).limit(100))]


def delete_scan(session: Session, scan_id: str) -> dict:
    row = session.get(StrategyScanRun, scan_id)
    if row is None:
        raise TradeError('STRATEGY_SCAN_NOT_FOUND', '策略扫描记录不存在', 404)
    count = len(json.loads(row.result_json)['run_ids'])
    session.delete(row)
    session.flush()
    return {'id': scan_id, 'deleted': True, 'preserved_research_run_count': count}
