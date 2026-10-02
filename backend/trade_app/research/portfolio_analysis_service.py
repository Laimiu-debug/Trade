"""Durable post-hoc analysis, independent of portfolio execution revisions."""
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import load_only
from trade_app.platform.compute_process import ComputeBudget, ComputeProcessError, run_json_process
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.portfolio_analysis_domain import VERSION
from trade_app.research.portfolio_analysis_models import PortfolioAnalysis
from trade_app.research.portfolio_domain import canonical, digest, initial_checkpoint
from trade_app.research.portfolio_models import PortfolioRun, PortfolioChunk
from trade_app.research.portfolio_service import get_portfolio, code_sha256 as execution_code_sha256

BUDGET = ComputeBudget(version='portfolio-analysis-v1', timeout_seconds=120,
    input_bytes=32 * 1024 * 1024, output_bytes=16 * 1024 * 1024)


def code_sha256():
    base = Path(__file__).resolve().parents[1]
    return digest({'execution': execution_code_sha256(), 'analysis': {name: digest((base / name).read_text(encoding='utf-8')) for name in (
        'research/portfolio_analysis_domain.py', 'research/portfolio_analysis_worker.py',
        'research/portfolio_plan_domain.py',
        'research/portfolio_analysis_service.py', 'market/domain.py', 'platform/compute_process.py', 'platform/runtime.py')}})


def _options(body):
    values = {key: body.get(key, default) for key, default in (('seed', 20260926), ('iterations', 400), ('block_size', 5))}
    for key, lo, hi in (('seed', 0, 2**32 - 1), ('iterations', 100, 2000), ('block_size', 1, 60)):
        if type(values[key]) is not int or not lo <= values[key] <= hi:
            raise TradeError('INVALID_ANALYSIS_OPTIONS', f'{key} 须为 {lo} 至 {hi} 的整数')
    return values


def _data(row):
    return {key: getattr(row, key) for key in ('id', 'source_run_id', 'source_result_sha256', 'name', 'version',
        'code_sha256', 'input_sha256', 'result_sha256', 'error', 'created_at', 'updated_at')} | {
        'state': 'cancelling' if row.cancel_requested else row.state,
        'options': json.loads(row.options_json), 'compute_metrics': json.loads(row.metrics_json),
        'capabilities': {'cancel': row.state in ('queued', 'running') and not row.cancel_requested,
            'resume': row.state == 'paused', 'retry': row.state in ('failed', 'cancelled'),
            'delete': row.state not in ('queued', 'running')}}


def _row(session, run_id):
    row = session.get(PortfolioAnalysis, run_id)
    if row is None or row.deleted:
        raise TradeError('PORTFOLIO_ANALYSIS_NOT_FOUND', '组合分析不存在或已删除', 404)
    return row


def list_analyses(session, source_run_id=None):
    query = select(PortfolioAnalysis).options(load_only(*[getattr(PortfolioAnalysis, key)
        for key in PortfolioAnalysis.__table__.columns.keys() if key not in ('input_json', 'result_json')]))
    query = query.where(PortfolioAnalysis.deleted == 0)
    if source_run_id: query = query.where(PortfolioAnalysis.source_run_id == source_run_id)
    return [_data(row) for row in session.scalars(query.order_by(PortfolioAnalysis.created_at.desc(), PortfolioAnalysis.id).limit(100))]


def get_analysis(session, run_id):
    row = _row(session, run_id)
    data = _data(row)
    if row.result_json is not None:
        value = json.loads(row.result_json)
        if digest(value) != row.result_sha256:
            raise TradeError('PORTFOLIO_ANALYSIS_CORRUPT', '分析结果摘要校验失败', 409)
        data['result'] = value
    return data


def create_analysis(session, body):
    if not isinstance(body, dict) or set(body) - {'source_run_id', 'name', 'seed', 'iterations', 'block_size', 'plan_date'}:
        raise TradeError('INVALID_ANALYSIS_REQUEST', '存在未知分析字段')
    name = body.get('name', '组合高级分析').strip()
    if not 1 <= len(name) <= 80:
        raise TradeError('INVALID_ANALYSIS_REQUEST', '名称须为1至80字')
    options = _options(body)
    source = get_portfolio(session, body.get('source_run_id'), full=True)
    if source['state'] != 'succeeded':
        raise TradeError('PORTFOLIO_NOT_READY', '只分析已完成的组合证据', 409)
    if (session.scalar(select(func.count()).select_from(PortfolioAnalysis).where(PortfolioAnalysis.deleted == 0,
            PortfolioAnalysis.state.in_(('queued', 'running', 'paused')))) or 0) >= 5:
        raise TradeError('PORTFOLIO_ANALYSIS_QUEUE_FULL', '最多保留5项未结束的组合分析', 429)
    context = json.loads(session.get(PortfolioRun, source['id']).input_json)
    # Pool explanations are already available in the source; diagnostics require
    # only aggregate equity, actual fills and rejected entry counts.
    result = {key: source['result'][key] for key in ('initial_capital', 'quality_flags', 'buy_count', 'trades', 'equity', 'decisions')}
    plan_date = body.get('plan_date') or context['calendar'][-1]
    if plan_date not in context['calendar']:
        raise TradeError('INVALID_ANALYSIS_DATE', '计划日期须为原组合中已有估值的日期')
    options['plan_date'] = plan_date
    plan = {'status': 'not_generated', 'reason': '源执行代码已变化，保留旧证据，仅生成描述统计，不使用新算法重算旧计划', 'as_of_date': plan_date}
    if context['code_sha256'] == execution_code_sha256():
        cursor = context['calendar'].index(plan_date) + 1
        chunk = session.scalar(select(PortfolioChunk).where(PortfolioChunk.run_id == source['id'], PortfolioChunk.end_cursor <= cursor)
            .order_by(PortfolioChunk.end_cursor.desc()).limit(1))
        checkpoint = json.loads(chunk.result_json)['checkpoint'] if chunk else initial_checkpoint(context)
        plan = {'status': 'ready', 'as_of_date': plan_date, 'checkpoint': checkpoint, 'replay_days': cursor - checkpoint['cursor']}
    frozen = {'context': context, 'plan': plan, 'result': result, 'source_result_sha256': source['result_sha256'],
        'options': options, 'budget': BUDGET.to_dict()}
    encoded = canonical(frozen)
    if len(encoded.encode('utf-8')) > BUDGET.input_bytes - 1024:
        raise TradeError('PORTFOLIO_ANALYSIS_INPUT_LIMIT', '完整分析输入超过32MiB，请缩小原组合区间；不会截断数据')
    now = utc_now()
    row = PortfolioAnalysis(id=new_id(), source_run_id=source['id'], source_result_sha256=source['result_sha256'], name=name,
        version=VERSION, code_sha256=code_sha256(), input_json=encoded, input_sha256=digest(frozen),
        options_json=canonical(options), state='queued', metrics_json='{}', cancel_requested=0, deleted=0,
        created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _data(row)


def control_analysis(session, run_id, action):
    row = _row(session, run_id)
    if action in ('retry', 'resume') and row.state in (('failed', 'cancelled') if action == 'retry' else ('paused',)):
        if row.code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '分析算法已更新，请创建新分析，旧产物保留', 409)
        row.state, row.error, row.cancel_requested, row.attempt_id = 'queued', None, 0, None
    elif action == 'cancel' and row.state in ('queued', 'running'):
        if row.state == 'running': row.cancel_requested = 1
        else: row.state = 'cancelled'
    elif action == 'delete' and row.state not in ('queued', 'running'):
        row.deleted = 1
    else:
        raise TradeError('PORTFOLIO_ANALYSIS_STATE_CONFLICT', '当前状态不能执行此操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(row)


def recover_interrupted_analyses(factory):
    with factory.begin() as session:
        for row in session.scalars(select(PortfolioAnalysis).where(PortfolioAnalysis.state.in_(('running', 'queued')))):
            row.state = 'cancelled' if row.cancel_requested else 'paused'
            row.error, row.attempt_id, row.cancel_requested = 'RECOVERED_AFTER_RESTART', None, 0
            row.updated_at = utc_now()


def process_one_analysis(factory, data_dir=None, *, should_stop=None):
    if should_stop and should_stop(): return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(PortfolioAnalysis.id).where(PortfolioAnalysis.state == 'running').limit(1)): return False
        row = session.scalar(select(PortfolioAnalysis).where(PortfolioAnalysis.deleted == 0, PortfolioAnalysis.state == 'queued')
            .order_by(PortfolioAnalysis.created_at, PortfolioAnalysis.id).limit(1))
        if row is None: return False
        run_id, attempt = row.id, new_id()
        frozen, input_sha, code = json.loads(row.input_json), row.input_sha256, row.code_sha256
        row.state, row.attempt_id, row.updated_at = 'running', attempt, utc_now()
    def stop_reason():
        if should_stop and should_stop(): return 'shutdown'
        with factory() as session:
            current = session.get(PortfolioAnalysis, run_id)
            if current is None or current.cancel_requested or current.state != 'running' or current.attempt_id != attempt:
                return 'cancelled'
        return None
    value, failure, metrics = None, None, {}
    try:
        if code != code_sha256(): raise TradeError('CODE_VERSION_CHANGED', '组合分析算法已变化', 409)
        if digest(frozen) != input_sha: raise TradeError('PORTFOLIO_ANALYSIS_CORRUPT', '冻结分析输入校验失败', 409)
        computed = run_json_process('trade_app.research.portfolio_analysis_worker', {'attempt_id': attempt, 'input': frozen},
            budget=ComputeBudget(**frozen['budget']), stop_reason=stop_reason)
        metrics = computed.metrics
        if not computed.value.get('ok'):
            error = computed.value.get('error') or {}
            raise TradeError(error.get('code', 'COMPUTE_FAILED'), error.get('message', '组合分析失败'))
        value = computed.value.get('result')
        if computed.value.get('attempt_id') != attempt or not isinstance(value, dict) or value.get('version') != VERSION:
            raise TradeError('COMPUTE_INVALID_RESULT', '组合分析计算身份或版本不符')
        canonical(value)
        if code != code_sha256(): raise TradeError('CODE_VERSION_CHANGED', '计算期间分析算法已变化', 409)
    except (TradeError, ComputeProcessError) as exc:
        failure, metrics = exc.code, getattr(exc, 'metrics', metrics)
    except Exception:
        failure = 'COMPUTE_FAILED'
    with factory.begin() as session:
        row = session.get(PortfolioAnalysis, run_id)
        if row is None or row.state != 'running' or row.attempt_id != attempt: return True
        if row.cancel_requested or failure == 'COMPUTE_CANCELLED':
            row.state, row.error = 'cancelled', 'COMPUTE_CANCELLED'
        elif failure:
            row.state, row.error = ('paused' if failure == 'COMPUTE_SHUTDOWN' else 'failed'), failure
        else:
            row.state, row.error = 'succeeded', None
            row.result_json, row.result_sha256 = canonical(value), digest(value)
        row.metrics_json, row.cancel_requested, row.attempt_id = canonical(metrics), 0, None
        row.updated_at = utc_now()
    return True
