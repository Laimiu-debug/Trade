"""Immutable signal reports and explicit, bounded source-to-scan preparation."""
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.domain import eligible_bars
from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.scan_job_models import StrategyScanJob
from trade_app.research.scan_job_service import prepare_scan_job, enqueue_scan_job
from trade_app.research.scan_job_worker import digest, encode
from trade_app.research.scan_service import get_scan
from trade_app.research.screener_service import get_screener_run, list_screener_runs
from trade_app.research.tdx_universe_models import TdxUniverseItem, TdxUniverseJob
from trade_app.research.service import get_run, strategy_catalog
from trade_app.research.signal_workspace_domain import VERSION, local_day, range_performance, signal_row
from trade_app.research.signal_workspace_models import SignalWorkspaceReport, SignalWorkspaceSource


def _day(value):
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError(value)
        return value
    except (ValueError, TypeError) as exc:
        raise TradeError('INVALID_SIGNAL_DATE', '日期须为 YYYY-MM-DD') from exc


def _code():
    root = Path(__file__).parent
    return hashlib.sha256(b''.join((root / name).read_bytes() for name in (
        'signal_workspace_service.py', 'signal_workspace_domain.py', 'legacy_catalog.json',
        'wulong_universe.py', 'signal_context.py', 'signal_context_formatters.py', 'screener_metrics.py'))).hexdigest()


def _board(symbol):
    if symbol.startswith('bj'):
        return 'beijing'
    if symbol.startswith(('sh688', 'sh689')):
        return 'star'
    if symbol.startswith(('sz300', 'sz301')):
        return 'chinext'
    return 'main'


def list_sources(session: Session) -> dict:
    from trade_app.research.signal_context import context_catalog
    jobs = []
    for row in session.scalars(select(TdxUniverseJob).where(TdxUniverseJob.state == 'succeeded').order_by(TdxUniverseJob.created_at.desc()).limit(30)):
        request = json.loads(row.request_json)
        jobs.append({'id': row.id, 'as_of_date': request.get('as_of_date'),
                     'kind': request.get('kind', 'funnel'), 'total_count': row.total_count,
                     'error_count': row.error_count})
    return {'trend_pools': list_screener_runs(session), 'tdx_jobs': jobs, 'signal_context_strategies': context_catalog(),
            'limits': {'datasets': 100, 'single_evaluations': 100, 'range_evaluations': 1000},
            'notes': ['通达信来源是当前本地文件集合，历史市场成分和退市覆盖未经核验',
                      '完整本地集合超过单次预算时须显式选择子集，不会静默截断']}


def resolve_source(session: Session, source: dict) -> dict:
    if not isinstance(source, dict) or set(source) - {'kind', 'dataset_ids', 'screener_run_id', 'step', 'tdx_job_id'}:
        raise TradeError('INVALID_SIGNAL_SOURCE', '信号来源包含不支持的字段')
    kind = source.get('kind')
    selected = source.get('dataset_ids')
    if selected is not None and (not isinstance(selected, list) or len(selected) > 100 or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in selected) or len(selected) != len(set(selected))):
        raise TradeError('INVALID_SIGNAL_SOURCE', '选择样本须为最多 100 个不重复的有效 ID')
    for key in ('screener_run_id', 'tdx_job_id'):
        if source.get(key) is not None and (not isinstance(source[key], str) or not 1 <= len(source[key]) <= 128):
            raise TradeError('INVALID_SIGNAL_SOURCE', '来源记录 ID 无效')
    reference = None
    as_of = None
    candidates = []
    if kind == 'fixed':
        if not isinstance(selected, list) or not selected:
            raise TradeError('INVALID_SIGNAL_SOURCE', '请选择冻结行情样本')
        candidates = selected
    elif kind == 'trend_pool':
        run = get_screener_run(session, source.get('screener_run_id'))
        step = source.get('step', 'step4')
        if step not in ('input', 'step1', 'step2', 'step3', 'step4'):
            raise TradeError('INVALID_SIGNAL_POOL_STEP', '筛选阶段无效')
        candidates = [row['dataset_id'] for row in run['result']['pools'].get(step, [])]
        as_of = run['as_of_date']
        reference = {'screener_run_id': run['id'], 'step': step, 'content_sha256': digest(run)}
    elif kind == 'tdx':
        job = session.get(TdxUniverseJob, source.get('tdx_job_id'))
        if not job or job.state != 'succeeded':
            raise TradeError('SIGNAL_SOURCE_NOT_READY', '请选择已完成的通达信任务', 409)
        candidates = [row.dataset_id for row in session.scalars(select(TdxUniverseItem).where(
            TdxUniverseItem.job_id == job.id, TdxUniverseItem.dataset_id.is_not(None)).order_by(TdxUniverseItem.ordinal))]
        as_of = json.loads(job.request_json).get('as_of_date')
        reference = {'tdx_job_id': job.id, 'request_sha256': digest(json.loads(job.request_json)),
                     'error_count': job.error_count, 'total_count': job.total_count,
                     'historical_membership_verified': False}
    else:
        raise TradeError('INVALID_SIGNAL_SOURCE', '来源须为固定样本、趋势池或通达信任务')
    candidates = list(dict.fromkeys(candidates))
    if selected is not None:
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected) or len(selected) != len(set(selected)):
            raise TradeError('INVALID_SIGNAL_SOURCE', '样本 ID 须为不重复列表')
        if not set(selected) <= set(candidates):
            raise TradeError('SIGNAL_SOURCE_MEMBERSHIP_CHANGED', '选择的样本不属于该来源', 409)
        chosen = selected
    else:
        chosen = candidates
    return {'kind': kind, 'source_date': as_of, 'reference': reference, 'available_dataset_ids': candidates,
            'dataset_ids': sorted(chosen), 'available_count': len(candidates),
            'selected_count': len(chosen), 'is_subset': len(chosen) < len(candidates),
            'historical_membership_verified': False}


def prepare_workspace_scan(session: Session, data_dir: Path, body: dict) -> dict:
    if set(body) - {'source', 'scan'} or not isinstance(body.get('scan'), dict):
        raise TradeError('INVALID_SIGNAL_REQUEST', '请求需要来源和扫描配置')
    source = resolve_source(session, body['source'])
    if not 1 <= len(source['dataset_ids']) <= 100:
        raise TradeError('SIGNAL_SOURCE_BUDGET', '单次最多选择 100 个样本，请显式缩小集合；未自动截断')
    scan = dict(body['scan'])
    if set(scan) - {'strategies', 'as_of_date', 'date_from', 'date_to', 'strict', 'event_profile_id', 'event_profile_revision', 'signal_context'}:
        raise TradeError('INVALID_SIGNAL_REQUEST', '扫描配置包含不支持的字段，样本应由所选来源提供')
    start = _day(scan.get('as_of_date') or scan.get('date_from'))
    if source['source_date'] and source['source_date'] > start:
        raise TradeError('SIGNAL_POOL_FROM_FUTURE', '候选池来源日期晚于扫描开始日，不能用于此前判断')
    prepared = prepare_scan_job(session, data_dir, {**scan, 'dataset_ids': source['dataset_ids']})
    return {'source': source, 'source_input': body['source'], 'scan': prepared}


def save_workspace_scan(session: Session, prepared: dict) -> dict:
    if resolve_source(session, prepared['source_input']) != prepared['source']:
        raise TradeError('SIGNAL_SOURCE_CHANGED', '候选来源在预览后发生变化', 409)
    job = enqueue_scan_job(session, prepared['scan'])
    existing = session.get(SignalWorkspaceSource, job['id'])
    # Same immutable scan can have multiple honest source descriptions. Preserve
    # the first; reports still disclose the exact frozen dataset IDs in all cases.
    if existing is None:
        session.add(SignalWorkspaceSource(job_id=job['id'], source_json=encode(prepared['source']), created_at=utc_now()))
        session.flush()
    return {**job, 'source': prepared['source']}


def _options(body: dict) -> dict:
    allowed = {'scan_id', 'as_of_date', 'signal_age_min', 'signal_age_max', 'entry_delay_bars',
               'timeliness', 'strategy_ids', 'markets', 'boards', 'min_rank_score', 'min_overlap'}
    if set(body) - allowed:
        raise TradeError('INVALID_SIGNAL_OPTIONS', '包含不支持的信号筛选字段')
    if not isinstance(body.get('scan_id'), str) or not 1 <= len(body['scan_id']) <= 128:
        raise TradeError('INVALID_SIGNAL_OPTIONS', '请选择有效扫描记录')
    age_min, age_max = body.get('signal_age_min', 0), body.get('signal_age_max')
    delay, overlap = body.get('entry_delay_bars', 1), body.get('min_overlap', 1)
    for number, maximum in ((age_min, 2000), (age_max if age_max is not None else age_min, 2000), (delay, 5), (overlap, 10)):
        if type(number) is not int or number < 0 or number > maximum:
            raise TradeError('INVALID_SIGNAL_OPTIONS', '年龄、延迟或共振数量超出允许范围')
    if delay < 1 or overlap < 1 or age_max is not None and age_max < age_min:
        raise TradeError('INVALID_SIGNAL_OPTIONS', '延迟须为 1 至 5；年龄上下限须有序')
    timeliness = body.get('timeliness', 'all')
    if timeliness not in ('all', 'active', 'expiring', 'expired'):
        raise TradeError('INVALID_SIGNAL_OPTIONS', '时效状态无效')
    minimum = body.get('min_rank_score')
    if minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not 0 <= minimum <= 100):
        raise TradeError('INVALID_SIGNAL_OPTIONS', '排名分下限须为 0 至 100')
    choices = {}
    for key, allowed_values in (('markets', {'sh', 'sz', 'bj'}), ('boards', {'main', 'star', 'chinext', 'beijing'}), ('strategy_ids', None)):
        value = body.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value) or len(value) != len(set(value)) or (allowed_values and not set(value) <= allowed_values):
            raise TradeError('INVALID_SIGNAL_OPTIONS', f'{key} 选项无效')
        choices[key] = sorted(value)
    return {'scan_id': body.get('scan_id'), 'as_of_date': _day(body.get('as_of_date')),
            'signal_age_min': age_min, 'signal_age_max': age_max, 'entry_delay_bars': delay,
            'timeliness': timeliness, 'min_rank_score': minimum, 'min_overlap': overlap, **choices}


def prepare_report(session: Session, data_dir: Path, body: dict) -> dict:
    request = _options(body)
    scan = get_scan(session, request['scan_id'])
    scan_end = scan['as_of_date'] or scan['date_to']
    if not 0 <= (date.fromisoformat(request['as_of_date']) - date.fromisoformat(scan_end)).days <= 366:
        raise TradeError('SIGNAL_REPORT_DATE', '报告截至日须在扫描结束日及其后 366 日内')
    catalog = {row['id']: row for row in strategy_catalog()}
    strategies = {row['strategy_id'] for row in scan['request']['strategies']}
    if not set(request['strategy_ids']) <= strategies:
        raise TradeError('SIGNAL_STRATEGY_NOT_IN_SCAN', '策略不在该次扫描中')
    for strategy_id in request['strategy_ids'] or strategies:
        capability = catalog[strategy_id]['capabilities']
        if (request['signal_age_min'] or request['signal_age_max'] is not None) and not capability['supports_signal_age_filter']:
            raise TradeError('SIGNAL_AGE_UNSUPPORTED', '所选策略不支持信号年龄过滤')
        if request['entry_delay_bars'] != 1 and not capability['supports_entry_delay']:
            raise TradeError('SIGNAL_DELAY_UNSUPPORTED', '所选策略不支持延迟入场')
    datasets = {row['id']: get_dataset(session, data_dir, row['id']) for row in scan['request']['datasets']}
    cutoff = request['as_of_date'] + 'T23:59:59+08:00'
    rows, hashes = [], {}
    for evidence in scan['result']['rows']:
        run = get_run(session, evidence['run_id'])
        if (run['dataset_id'] != evidence['dataset_id'] or run['decision_at'] != evidence['decision_at']
                or run['result'].get('code_sha256') != evidence.get('code_sha256')):
            raise TradeError('SIGNAL_EVIDENCE_CHANGED', '原扫描与子运行证据不一致', 409)
        hashes[run['id']] = digest(run)
        dataset = datasets[run['dataset_id']]
        old_bars, _ = eligible_bars(dataset['bars'], run['decision_at'], run['strict'])
        report_bars, _ = eligible_bars(dataset['bars'], cutoff, run['strict'])
        old_bars = [bar for bar in old_bars if bar['event_date'] <= local_day(run['decision_at'])]
        report_bars = [bar for bar in report_bars if bar['event_date'] <= request['as_of_date']]
        row = signal_row(run, old_bars, report_bars, request['as_of_date'], catalog[run['strategy_id']], request['entry_delay_bars'])
        market, digits = normalize_a_share_symbol(dataset['symbol'])
        row.update(symbol=market + digits, market=market, board=_board(market + digits))
        reasons = []
        if not row['signal']: reasons.append('no_fresh_signal')
        if request['strategy_ids'] and row['strategy_id'] not in request['strategy_ids']: reasons.append('strategy_filter')
        if request['markets'] and market not in request['markets']: reasons.append('market_filter')
        if request['boards'] and row['board'] not in request['boards']: reasons.append('board_filter')
        if (request['signal_age_min'] or request['signal_age_max'] is not None) and (row['signal_age_bars'] is None or row['signal_age_bars'] < request['signal_age_min'] or request['signal_age_max'] is not None and row['signal_age_bars'] > request['signal_age_max']): reasons.append('age_filter')
        if request['timeliness'] != 'all' and row['timeliness'] != request['timeliness']: reasons.append('timeliness_filter')
        if request['min_rank_score'] is not None and (row['rank_score'] is None or row['rank_score'] < request['min_rank_score']): reasons.append('rank_score_filter')
        row.update(excluded_reasons=reasons, rank=None)
        rows.append(row)
    groups = defaultdict(list)
    for row in rows:
        if not row['excluded_reasons']: groups[(row['strategy_id'], row['decision_date'])].append(row)
    for (strategy_id, _), group in groups.items():
        group.sort(key=lambda row: row['symbol'])
        group.sort(key=lambda row: (row['rank_score'] if row['rank_score'] is not None else -1, row['entry_quality_score'] or 0, row['priority'], row['event_count'], row['trigger_date'] or ''), reverse=True)
        cap = catalog[strategy_id]['signal_top_n']
        for position, row in enumerate(group, 1):
            row['rank'] = position if row['rank_score'] is not None else None
            if cap and position > cap: row['excluded_reasons'].append('strategy_top_n')
    # Cross-strategy overlap is over the whole interval, with explicit same-day
    # overlap kept separately. This restores the old range-union semantics.
    by_symbol = defaultdict(list)
    for row in rows:
        if not row['excluded_reasons']: by_symbol[row['symbol']].append(row)
    appearances = []
    start = scan['as_of_date'] or scan['date_from']
    for symbol, evidence in sorted(by_symbol.items()):
        hits = sorted({row['strategy_id'] for row in evidence})
        if len(hits) < request['min_overlap']:
            for row in evidence: row['excluded_reasons'].append('range_overlap_filter')
            continue
        dataset = datasets[evidence[0]['dataset_id']]
        bars, quality = eligible_bars(dataset['bars'], (scan['as_of_date'] or scan['date_to']) + 'T23:59:59+08:00', scan['request']['strict'])
        appearances.append({'symbol': symbol, 'dataset_id': dataset['id'], 'overlap_count': len(hits),
            'strategy_ids': hits, 'signal_dates': sorted({row['decision_date'] for row in evidence}),
            'strategies': [{'strategy_id': sid, 'appearance_count': sum(row['strategy_id'] == sid for row in evidence),
                           'dates': sorted({row['decision_date'] for row in evidence if row['strategy_id'] == sid}),
                           'scores': [{'date': row['decision_date'], 'value': row['rank_score'], 'run_id': row['run_id']} for row in evidence if row['strategy_id'] == sid]} for sid in hits],
            'range_performance': {**range_performance(bars, start, scan_end), 'quality_flags': quality}})
    source = {'kind': 'saved_scan', 'dataset_ids': sorted(datasets), 'historical_membership_verified': False}
    jobs = list(session.scalars(select(StrategyScanJob).where(StrategyScanJob.scan_id == scan['id'])))
    for job in jobs:
        saved = session.get(SignalWorkspaceSource, job.id)
        if saved:
            source = json.loads(saved.source_json)
            break
    by_day = defaultdict(list)
    for row in rows:
        if not row['excluded_reasons']: by_day[(row['symbol'], row['decision_date'])].append(row)
    wanted = set(request['strategy_ids']) or strategies
    same_day = [{'symbol': symbol, 'decision_date': day, 'strategy_ids': sorted({row['strategy_id'] for row in group}), 'run_ids': [row['run_id'] for row in group]} for (symbol, day), group in sorted(by_day.items()) if len(wanted) >= 2 and {row['strategy_id'] for row in group} == wanted]
    result = {'version': VERSION, 'code_sha256': _code(), 'scan_sha256': digest(scan),
        'research_run_hashes': hashes, 'source': source, 'as_of_date': request['as_of_date'],
        'scan_request': scan['request'], 'scan_code_sha256': scan['code_sha256'],
        'rows': rows, 'signal_count': sum(not row['excluded_reasons'] for row in rows),
        'per_symbol': appearances, 'same_day_intersection': same_day,
        'strategies': [{'id': sid, 'name': catalog[sid]['name'], 'capabilities': catalog[sid]['capabilities'], 'signal_top_n': catalog[sid]['signal_top_n']} for sid in sorted(strategies)],
        'notes': ['信号年龄按已观察交易 K 线计数；Active/Expiring 按报告截至日和旧版触发日加 2 自然日计算',
                  '确认时间是保存证据的观察上界，不以图形回标日冒充首次可知日；入场日期在确认/决策日之后',
                  '入场日期是历史已观察日的开盘口径说明，不承诺真实成交；待买草稿仍需账户校验',
                  '排名只比较同一策略同一决策日的所选样本；完整扫描冻结候选口径、通用事件门槛与排名权重，旧研究观察记录保持原能力',
                  '区间收益是首末有效收盘价格变化，回撤只读区间内收盘；不含交易成本，不是组合策略收益',
                  '历史股票池成员覆盖未经核验；市场/板块过滤只按证券代码分类']}
    if len(encode(result).encode()) > 8 * 1024 * 1024:
        raise TradeError('SIGNAL_REPORT_TOO_LARGE', '信号报告超过 8 MiB 上限')
    return {'id': digest({'request': request, 'result': result}), 'request': request, 'result': result}


def save_report(session: Session, prepared: dict) -> dict:
    scan = get_scan(session, prepared['request']['scan_id'])
    if digest(scan) != prepared['result']['scan_sha256']:
        raise TradeError('SIGNAL_EVIDENCE_CHANGED', '扫描结果在预览后发生变化', 409)
    existing = session.get(SignalWorkspaceReport, prepared['id'])
    if existing is None:
        existing = SignalWorkspaceReport(id=prepared['id'], scan_id=scan['id'], request_json=encode(prepared['request']),
            result_json=encode(prepared['result']), created_at=utc_now())
        session.add(existing)
        session.flush()
    return _view(existing)


def _view(row, detail=True):
    request, result = json.loads(row.request_json), json.loads(row.result_json)
    if digest({'request': request, 'result': result}) != row.id:
        raise TradeError('SIGNAL_REPORT_CORRUPT', '信号报告内容摘要不一致', 409)
    return {'id': row.id, 'scan_id': row.scan_id, 'created_at': row.created_at,
            'as_of_date': request['as_of_date'], 'signal_count': result['signal_count'],
            'symbol_count': len(result['per_symbol']), **({'request': request, 'result': result} if detail else {})}


def get_report(session, report_id):
    row = session.get(SignalWorkspaceReport, report_id)
    if row is None:
        raise TradeError('SIGNAL_REPORT_NOT_FOUND', '信号报告不存在', 404)
    return _view(row)


def list_reports(session):
    return [_view(row, False) for row in session.scalars(select(SignalWorkspaceReport).order_by(SignalWorkspaceReport.created_at.desc()).limit(100))]


def delete_report(session, report_id):
    get_report(session, report_id)
    session.delete(session.get(SignalWorkspaceReport, report_id))
    session.flush()
    return {'id': report_id, 'deleted': True, 'source_scan_and_runs_preserved': True}
