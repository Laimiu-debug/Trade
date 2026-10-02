"""One shared portfolio quantum per experiment turn; no additional executor."""
from copy import deepcopy
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import load_only

from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research import portfolio_service as portfolio
from trade_app.research.portfolio_domain import canonical, digest
from trade_app.research.portfolio_models import PortfolioRun
from trade_app.research.portfolio_experiment_models import PortfolioExperiment, PortfolioExperimentPoint
from trade_app.research.portfolio_experiment_domain import VERSION, candidate_context, portfolio_metrics, sample_plan, score_points, supported_schema

MAX_INPUT_BYTES = 128 * 1024 * 1024
BUDGET = {'elapsed_ms': 2 * 60 * 60 * 1000, 'result_bytes': 128 * 1024 * 1024}


def code_sha256():
    folder = Path(__file__).parent
    return digest({'portfolio': portfolio.code_sha256(), 'experiment': [digest((folder / name).read_text(encoding='utf-8'))
        for name in ('portfolio_experiment_domain.py', 'portfolio_experiment_service.py')]})


def _source(session, run_id):
    row = session.get(PortfolioRun, run_id)
    if row is None or row.deleted or row.state != 'succeeded':
        raise TradeError('PORTFOLIO_SOURCE_REQUIRED', '请先完成一份固定样本组合回测', 409)
    if row.code_sha256 != portfolio.code_sha256():
        raise TradeError('CODE_VERSION_CHANGED', '来源组合算法已更新，请先以相同样本重算', 409)
    portfolio.get_portfolio(session, run_id, full=True)
    return json.loads(row.input_json)


def _plan(session, body):
    source = _source(session, body['base_run_id'])
    sampling = sample_plan(source, body)
    # The immutable dataset body dominates size; reject an oversized plan
    # before repeatedly serializing large inputs inside a create transaction.
    immutable_bytes = len(canonical({key: value for key, value in source.items() if key not in ('params', 'config')}).encode())
    if immutable_bytes * sampling['actual_points'] > MAX_INPUT_BYTES:
        raise TradeError('PORTFOLIO_EXPERIMENT_INPUT_LIMIT', '全部候选冻结输入合计超过128MiB，请减少点数或样本范围')
    input_bytes = sum(len(canonical(candidate_context(source, axis)).encode()) for axis in sampling['samples'])
    if input_bytes > MAX_INPUT_BYTES:
        raise TradeError('PORTFOLIO_EXPERIMENT_INPUT_LIMIT', '全部候选冻结输入合计超过128MiB，请减少点数或样本范围')
    return {'version': VERSION, 'source_run_id': body['base_run_id'], 'source': source, 'plan': sampling,
            'code_sha256': code_sha256(), 'budget': BUDGET, 'frozen_child_input_bytes': input_bytes}


def _preview(value):
    return {'preview_sha256': digest(value), 'plan': value['plan'], 'version': value['version'],
        'source': portfolio._context_metadata(value['source']), 'budget': value['budget'],
        'frozen_child_input_bytes': value['frozen_child_input_bytes'],
        'notes': ['固定研究样本内参数比较；未执行样本外验证', '每轮只运行一个候选的5个日期，暂停保留成功检查点',
                  '部分减仓按完整持仓周期统计；分钟成交未实现']}


def preview_experiment(session, body):
    return _preview(_plan(session, body))


def create_experiment(session, body):
    name = str(body.get('name', '')).strip()
    if not 1 <= len(name) <= 80:
        raise TradeError('INVALID_EXPERIMENT_NAME', '名称须为1至80字')
    value = _plan(session, body)
    if value['source']['mode'] == 'traditional_runtime14':
        from trade_app.research.registry_service import require_enabled
        require_enabled(session, [value['source']['strategy_id']])
    if body.get('expected_preview_sha256') != digest(value):
        raise TradeError('PORTFOLIO_EXPERIMENT_PLAN_CHANGED', '请重新预览并确认相同候选与冻结输入', 409)
    if (session.scalar(select(func.count()).select_from(PortfolioExperiment).where(PortfolioExperiment.deleted == 0,
        PortfolioExperiment.state.in_(('queued', 'running', 'paused')))) or 0) >= 5:
        raise TradeError('PORTFOLIO_EXPERIMENT_QUEUE_FULL', '最多保留5个未结束的组合实验', 429)
    now = utc_now()
    row = PortfolioExperiment(id=new_id(), name=name, source_run_id=value['source_run_id'], input_json=canonical(value),
        input_sha256=digest(value), code_sha256=value['code_sha256'], state='queued', total_points=len(value['plan']['samples']),
        completed_points=0, active_point=0, elapsed_ms=0, total_bytes=0, pause_requested=0, cancel_requested=0,
        deleted=0, created_at=now, updated_at=now, last_served_at=now)
    session.add(row)
    session.flush()
    for ordinal, axis in enumerate(value['plan']['samples']):
        session.add(PortfolioExperimentPoint(id=new_id(), experiment_id=row.id, ordinal=ordinal, axis_json=canonical(axis),
            point_sha256=digest({'input_sha256': row.input_sha256, 'axis_values': axis}), created_at=now))
    session.flush()
    return _data(row)


def _row(session, identifier):
    row = session.get(PortfolioExperiment, identifier)
    if row is None or row.deleted:
        raise TradeError('PORTFOLIO_EXPERIMENT_NOT_FOUND', '组合参数实验不存在或已删除', 404)
    return row


def _input(row):
    value = json.loads(row.input_json)
    if digest(value) != row.input_sha256:
        raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '冻结实验计划校验失败', 409)
    return value


def _data(row):
    return {key: getattr(row, key) for key in ('id', 'name', 'source_run_id', 'input_sha256', 'code_sha256', 'result_sha256',
        'completed_points', 'total_points', 'active_point', 'elapsed_ms', 'total_bytes', 'error', 'created_at', 'updated_at')} | {
        'state': 'cancelling' if row.cancel_requested else 'pausing' if row.pause_requested else row.state,
        'capabilities': {'pause': row.state in ('queued', 'running') and not row.pause_requested and not row.cancel_requested,
            'resume': row.state == 'paused', 'retry': row.state in ('failed', 'cancelled'),
            'cancel': row.state in ('queued', 'running', 'paused'), 'delete': row.state not in ('queued', 'running')}}


def list_experiments(session):
    return [_data(row) for row in session.scalars(select(PortfolioExperiment).options(load_only(*[
        getattr(PortfolioExperiment, key) for key in PortfolioExperiment.__table__.columns.keys() if key not in ('input_json', 'result_json')]))
        .where(PortfolioExperiment.deleted == 0).order_by(PortfolioExperiment.created_at.desc(), PortfolioExperiment.id).limit(100))]


def _point_data(session, point):
    metrics = json.loads(point.metrics_json) if point.metrics_json else None
    if metrics and digest(metrics) != point.metrics_sha256:
        raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '候选指标摘要校验失败', 409)
    child = session.get(PortfolioRun, point.child_run_id) if point.child_run_id else None
    if metrics and (child is None or child.state != 'succeeded' or child.result_sha256 != metrics['source_result_sha256']):
        raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '候选指标与组合结果不符', 409)
    return {'id': point.id, 'ordinal': point.ordinal, 'axis_values': json.loads(point.axis_json), 'point_sha256': point.point_sha256,
        'child_run_id': point.child_run_id, 'metrics': metrics['metrics'] if metrics else None,
        'source_result_sha256': metrics['source_result_sha256'] if metrics else None,
        'state': child.state if child else 'pending', 'completed_days': child.completed_days if child else 0,
        'total_days': child.total_days if child else None, 'chunk_count': child.chunk_count if child else 0,
        'error': child.error if child else None}


def get_experiment(session, identifier):
    row, points = _row(session, identifier), []
    value = _input(row)
    for point in session.scalars(select(PortfolioExperimentPoint).where(PortfolioExperimentPoint.experiment_id == identifier).order_by(PortfolioExperimentPoint.ordinal)):
        if point.point_sha256 != digest({'input_sha256': row.input_sha256, 'axis_values': json.loads(point.axis_json)}):
            raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '候选参数摘要校验失败', 409)
        points.append(_point_data(session, point))
    result = json.loads(row.result_json) if row.result_json else None
    if result and digest({'input_sha256': row.input_sha256, 'result': result}) != row.result_sha256:
        raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '实验分析摘要校验失败', 409)
    return {**_data(row), **{key: item for key, item in _preview(value).items() if key != 'preview_sha256'}, 'points': points, 'result': result}


def get_point(session, identifier, point_id):
    _row(session, identifier)
    point = session.get(PortfolioExperimentPoint, point_id)
    if point is None or point.experiment_id != identifier:
        raise TradeError('PORTFOLIO_EXPERIMENT_POINT_NOT_FOUND', '候选点不存在', 404)
    return {**_point_data(session, point), 'portfolio': portfolio.get_portfolio(session, point.child_run_id, full=True) if point.child_run_id else None}


def control_experiment(session, identifier, action):
    row = _row(session, identifier)
    if action == 'pause' and row.state in ('queued', 'running'):
        row.state, row.pause_requested = ('paused', 0) if row.state == 'queued' else ('running', 1)
    elif action in ('resume', 'retry') and row.state in (('paused',) if action == 'resume' else ('failed', 'cancelled')):
        value = _input(row)
        if row.code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '代码已变化，请创建新实验；成功候选保持冻结', 409)
        if row.elapsed_ms >= value['budget']['elapsed_ms'] or row.total_bytes >= value['budget']['result_bytes']:
            raise TradeError('PORTFOLIO_EXPERIMENT_BUDGET_EXHAUSTED', '实验总预算已耗尽', 409)
        row.state, row.error, row.attempt_id = 'queued', None, None
        row.pause_requested = row.cancel_requested = 0
    elif action == 'cancel' and row.state in ('queued', 'running', 'paused'):
        if row.state == 'running':
            row.cancel_requested = 1
        else:
            row.state, row.pause_requested = 'cancelled', 0
    elif action == 'delete' and row.state not in ('queued', 'running'):
        row.deleted = 1
    else:
        raise TradeError('PORTFOLIO_EXPERIMENT_STATE_CONFLICT', '当前状态不支持该操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(row)


def recover_interrupted_experiments(factory):
    with factory.begin() as session:
        for row in session.scalars(select(PortfolioExperiment).where(PortfolioExperiment.state.in_(('queued', 'running')))):
            row.state = 'cancelled' if row.cancel_requested else 'paused'
            row.error, row.attempt_id = 'RECOVERED_AFTER_RESTART', None
            row.pause_requested = row.cancel_requested = 0
            row.updated_at = utc_now()


def process_one_experiment(factory, data_dir, *, should_stop=None):
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(PortfolioExperiment.id).where(PortfolioExperiment.state == 'running').limit(1)):
            return False
        row = session.scalar(select(PortfolioExperiment).where(PortfolioExperiment.state == 'queued', PortfolioExperiment.deleted == 0)
                             .order_by(PortfolioExperiment.last_served_at, PortfolioExperiment.id).limit(1))
        if row is None:
            return False
        identifier, attempt = row.id, new_id()
        row.state, row.attempt_id = 'running', attempt
        row.last_served_at = row.updated_at = utc_now()
    error, child_id, before_ms, before_bytes = None, None, 0, 0
    prepared_metrics, prepared_final = None, None
    def stopped():
        if should_stop and should_stop():
            return True
        with factory() as session:
            current = session.get(PortfolioExperiment, identifier)
            return current is None or current.cancel_requested or current.attempt_id != attempt
    try:
        with factory.begin() as session:
            row = session.get(PortfolioExperiment, identifier)
            value = _input(row)
            if row.code_sha256 != code_sha256():
                raise TradeError('CODE_VERSION_CHANGED', '冻结组合实验的代码版本已更新', 409)
            if row.elapsed_ms >= value['budget']['elapsed_ms'] or row.total_bytes >= value['budget']['result_bytes']:
                raise TradeError('PORTFOLIO_EXPERIMENT_BUDGET_EXHAUSTED', '实验总预算已耗尽', 409)
            point = session.scalar(select(PortfolioExperimentPoint).where(PortfolioExperimentPoint.experiment_id == identifier,
                PortfolioExperimentPoint.metrics_json.is_(None)).order_by(PortfolioExperimentPoint.ordinal).limit(1))
            if point is None:
                raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '缺少待完成候选', 409)
            axis = json.loads(point.axis_json)
            if axis != value['plan']['samples'][point.ordinal] or point.point_sha256 != digest({'input_sha256': row.input_sha256, 'axis_values': axis}):
                raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '候选参数与冻结计划不符', 409)
            context = candidate_context(value['source'], axis)
            if point.child_run_id is None:
                child = portfolio.create_frozen_child(session, context, f'{row.name} · {point.ordinal + 1}', owner_kind='experiment', owner_id=row.id)
                point.child_run_id = child.id
            else:
                child = session.get(PortfolioRun, point.child_run_id)
            if child is None or child.input_sha256 != digest(context) or child.owner_kind != 'experiment' or child.owner_id != identifier:
                raise TradeError('PORTFOLIO_EXPERIMENT_CORRUPT', '内部组合任务与冻结候选不符', 409)
            if child.state in ('paused', 'failed', 'cancelled'):
                portfolio.control_portfolio(session, child.id, 'resume' if child.state == 'paused' else 'retry', internal=True)
            row.active_point = point.ordinal
            child_id, point_id, before_ms, before_bytes = child.id, point.id, child.elapsed_ms, child.total_bytes
            remaining = {'elapsed_ms': value['budget']['elapsed_ms'] - row.elapsed_ms,
                         'result_bytes': value['budget']['result_bytes'] - row.total_bytes}
        portfolio.process_one_portfolio(factory, data_dir, should_stop=stopped, target_run_id=child_id, remaining_budget=remaining)
        # Decode immutable evidence and calculate metrics without holding a
        # database writer transaction. Publication below is only a short CAS.
        with factory() as session:
            child = session.get(PortfolioRun, child_id)
            row = session.get(PortfolioExperiment, identifier)
            if child and child.state == 'succeeded':
                result = portfolio.get_portfolio(session, child.id, full=True)['result']
                prepared_metrics = {'source_result_sha256': child.result_sha256, 'metrics': portfolio_metrics(result)}
                if row.completed_points + 1 == row.total_points:
                    points = [_point_data(session, item) for item in session.scalars(select(PortfolioExperimentPoint)
                        .where(PortfolioExperimentPoint.experiment_id == identifier).order_by(PortfolioExperimentPoint.ordinal))]
                    for item in points:
                        if item['id'] == point_id:
                            item.update(metrics=prepared_metrics['metrics'], source_result_sha256=child.result_sha256)
                    prepared_final = score_points(points, value['plan']['axes'])
    except Exception as exc:
        error = f'{getattr(exc, "code", type(exc).__name__)}: {exc}'[:500]
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.get(PortfolioExperiment, identifier)
        if row is None or row.state != 'running' or row.attempt_id != attempt:
            return True
        child = session.get(PortfolioRun, child_id) if child_id else None
        if child:
            row.elapsed_ms += max(0, child.elapsed_ms - before_ms)
            row.total_bytes += max(0, child.total_bytes - before_bytes)
        row.updated_at, row.attempt_id = utc_now(), None
        if row.code_sha256 != code_sha256():
            error = 'CODE_VERSION_CHANGED: 发布前代码已更新'
        if row.cancel_requested:
            row.state, row.error = 'cancelled', 'CANCELLED_BY_USER'
        elif should_stop and should_stop():
            row.state, row.error = 'paused', 'INTERRUPTED_SHUTDOWN'
        elif error or child is None or child.state in ('failed', 'cancelled'):
            row.state, row.error = 'failed', error or (child.error if child else 'MISSING_CHILD')
        else:
            row.state, row.error = ('paused' if row.pause_requested or child.state == 'paused' else 'queued'), None
            if child.state == 'succeeded':
                if prepared_metrics is None or prepared_metrics['source_result_sha256'] != child.result_sha256:
                    row.state, row.error = 'failed', 'PORTFOLIO_EXPERIMENT_CORRUPT: 发布指标与子任务不符'
                else:
                    point = session.get(PortfolioExperimentPoint, point_id)
                    point.metrics_json, point.metrics_sha256 = canonical(prepared_metrics), digest(prepared_metrics)
                    row.completed_points += 1
                    if prepared_final is not None:
                        row.result_json, row.result_sha256 = canonical(prepared_final), digest({'input_sha256': row.input_sha256, 'result': prepared_final})
                        row.state = 'succeeded'
        row.pause_requested = row.cancel_requested = 0
    return True
