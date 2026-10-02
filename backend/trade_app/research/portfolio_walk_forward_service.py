"""Anchored portfolio train/select/test jobs on the shared portfolio executor."""
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import load_only

from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research import portfolio_service as portfolio, portfolio_experiment_service as experiments
from trade_app.research.portfolio_domain import canonical, digest
from trade_app.research.portfolio_models import PortfolioRun
from trade_app.research.portfolio_experiment_domain import sample_plan, portfolio_metrics
from trade_app.research.portfolio_walk_forward_domain import VERSION, build_plan, temporal_context, select_training, summarize
from trade_app.research.portfolio_walk_forward_models import PortfolioWalkForward as Run, PortfolioWalkForwardFold as Fold, PortfolioWalkForwardTask as Task

BUDGET = {'elapsed_ms': 4 * 60 * 60 * 1000, 'result_bytes': 256 * 1024 * 1024}
MAX_INPUT_BYTES = 128 * 1024 * 1024


def code_sha256():
    folder = Path(__file__).parent
    return digest({'portfolio_experiment': experiments.code_sha256(), 'walk_forward': [digest((folder / name).read_text(encoding='utf-8'))
        for name in ('portfolio_walk_forward_domain.py', 'portfolio_walk_forward_service.py', 'walk_forward_domain.py')]})


def _plan(session, body):
    source = experiments._source(session, body['base_run_id'])
    sampling = sample_plan(source, body)
    temporal = build_plan(source, sampling, body)
    total = 0
    for fold in temporal['folds']:
        # Input size is checked incrementally, never truncated. Test input uses
        # the largest candidate representation because selection is unknown.
        test_size = 0
        for axis in sampling['samples']:
            total += len(canonical(temporal_context(source, fold, 'train', axis)).encode())
            test_size = max(test_size, len(canonical(temporal_context(source, fold, 'test', axis)).encode()))
            if total > MAX_INPUT_BYTES:
                raise TradeError('PORTFOLIO_WF_INPUT_LIMIT', '训练与测试冻结输入合计超过128MiB，请缩小候选或折数')
        total += test_size
    if total > MAX_INPUT_BYTES:
        raise TradeError('PORTFOLIO_WF_INPUT_LIMIT', '训练与测试冻结输入合计超过128MiB，请缩小候选或折数')
    return {'version': VERSION, 'source_run_id': body['base_run_id'], 'source': source, 'sampling': sampling,
            'temporal': temporal, 'code_sha256': code_sha256(), 'budget': BUDGET, 'frozen_child_input_bytes': total}


def _preview(value):
    return {'preview_sha256': digest(value), 'sampling': value['sampling'], 'temporal': value['temporal'],
        'source': portfolio._context_metadata(value['source']), 'budget': value['budget'],
        'frozen_child_input_bytes': value['frozen_child_input_bytes']}


def preview_walk_forward(session, body):
    return _preview(_plan(session, body))


def create_walk_forward(session, body):
    name = str(body.get('name', '')).strip()
    if not 1 <= len(name) <= 80:
        raise TradeError('INVALID_PORTFOLIO_WF_NAME', '名称须为1至80字')
    value = _plan(session, body)
    if value['source']['mode'] == 'traditional_runtime14':
        from trade_app.research.registry_service import require_enabled
        require_enabled(session, [value['source']['strategy_id']])
    if body.get('expected_preview_sha256') != digest(value):
        raise TradeError('PORTFOLIO_WF_PLAN_CHANGED', '请重新预览并确认时间折与候选计划', 409)
    if (session.scalar(select(func.count()).select_from(Run).where(Run.deleted == 0, Run.state.in_(('queued', 'running', 'paused')))) or 0) >= 5:
        raise TradeError('PORTFOLIO_WF_QUEUE_FULL', '最多保留5个未结束的组合滚动验证任务', 429)
    now = utc_now()
    row = Run(id=new_id(), name=name, source_run_id=value['source_run_id'], input_json=canonical(value), input_sha256=digest(value),
        code_sha256=value['code_sha256'], state='queued', completed_tasks=0, total_tasks=value['temporal']['evaluation_points'],
        active_fold=0, elapsed_ms=0, total_bytes=0, pause_requested=0, cancel_requested=0, deleted=0,
        created_at=now, updated_at=now, last_served_at=now)
    session.add(row)
    session.flush()
    ordinal = 0
    for fold in value['temporal']['folds']:
        session.add(Fold(id=new_id(), run_id=row.id, ordinal=fold['index']))
        for axis in value['sampling']['samples']:
            session.add(Task(id=new_id(), run_id=row.id, fold_index=fold['index'], phase='train', ordinal=ordinal,
                axis_json=canonical(axis), candidate_sha256=digest(axis)))
            ordinal += 1
        session.add(Task(id=new_id(), run_id=row.id, fold_index=fold['index'], phase='test', ordinal=ordinal))
        ordinal += 1
    session.flush()
    return _data(row)


def _row(session, identifier):
    row = session.get(Run, identifier)
    if row is None or row.deleted:
        raise TradeError('PORTFOLIO_WF_NOT_FOUND', '组合滚动验证不存在或已删除', 404)
    return row


def _input(row):
    value = json.loads(row.input_json)
    if digest(value) != row.input_sha256:
        raise TradeError('PORTFOLIO_WF_CORRUPT', '冻结时间计划校验失败', 409)
    return value


def _data(row):
    return {key: getattr(row, key) for key in ('id', 'name', 'source_run_id', 'input_sha256', 'code_sha256', 'result_sha256',
        'completed_tasks', 'total_tasks', 'active_fold', 'elapsed_ms', 'total_bytes', 'error', 'created_at', 'updated_at')} | {
        'state': 'cancelling' if row.cancel_requested else 'pausing' if row.pause_requested else row.state,
        'capabilities': {'pause': row.state in ('queued', 'running') and not row.pause_requested and not row.cancel_requested,
            'resume': row.state == 'paused', 'retry': row.state in ('failed', 'cancelled'),
            'cancel': row.state in ('queued', 'running', 'paused'), 'delete': row.state not in ('queued', 'running')}}


def list_walk_forwards(session):
    return [_data(row) for row in session.scalars(select(Run).options(load_only(*[getattr(Run, key) for key in Run.__table__.columns.keys()
        if key not in ('input_json', 'result_json')])).where(Run.deleted == 0).order_by(Run.created_at.desc(), Run.id).limit(100))]


def _selection(fold):
    value = json.loads(fold.selection_json) if fold.selection_json else None
    if value is not None and digest(value) != fold.selection_sha256:
        raise TradeError('PORTFOLIO_WF_CORRUPT', '训练选择摘要校验失败', 409)
    return value


def _outcome(task):
    value = json.loads(task.outcome_json) if task.outcome_json else None
    if value is not None and digest(value) != task.outcome_sha256:
        raise TradeError('PORTFOLIO_WF_CORRUPT', '阶段结果摘要校验失败', 409)
    return value


def _task_data(session, task):
    outcome = _outcome(task)
    axis = json.loads(task.axis_json) if task.axis_json else None
    if axis is not None and digest(axis) != task.candidate_sha256:
        raise TradeError('PORTFOLIO_WF_CORRUPT', '候选参数摘要不符', 409)
    child = session.get(PortfolioRun, task.child_run_id) if task.child_run_id else None
    if outcome and not outcome.get('skipped') and (child is None or child.state != 'succeeded' or child.result_sha256 != outcome['source_result_sha256']):
        raise TradeError('PORTFOLIO_WF_CORRUPT', '阶段结果与真实组合不一致', 409)
    return {'id': task.id, 'ordinal': task.ordinal, 'fold_index': task.fold_index, 'phase': task.phase,
        'axis_values': axis, 'point_sha256': task.candidate_sha256, 'child_run_id': task.child_run_id,
        'state': 'skipped' if outcome and outcome.get('skipped') else child.state if child else 'pending',
        'metrics': outcome.get('metrics') if outcome else None, 'reason': outcome.get('reason') if outcome else None,
        'source_result_sha256': outcome.get('source_result_sha256') if outcome else None,
        'completed_days': child.completed_days if child else 0, 'total_days': child.total_days if child else None,
        'chunk_count': child.chunk_count if child else 0, 'error': child.error if child else None}


def _summary(session, row, value, *, override_task=None, override_outcome=None, override_selection=None):
    tasks = session.scalars(select(Task).where(Task.run_id == row.id).order_by(Task.ordinal)).all()
    folds = session.scalars(select(Fold).where(Fold.run_id == row.id).order_by(Fold.ordinal)).all()
    rows = []
    for fold in folds:
        test = next(task for task in tasks if task.fold_index == fold.ordinal and task.phase == 'test')
        outcome = override_outcome if test.id == override_task else _outcome(test)
        selection = override_selection if override_selection is not None and test.id == override_task else _selection(fold)
        rows.append({**value['temporal']['folds'][fold.ordinal], 'selection': selection,
            'selection_sha256': digest(selection) if selection else None, 'test_task_id': test.id, 'test_child_run_id': test.child_run_id,
            'test_status': 'skipped' if outcome and outcome.get('skipped') else 'succeeded' if outcome else 'pending',
            'test_result': outcome.get('test_result') if outcome else None})
    return summarize(rows)


def get_walk_forward(session, identifier):
    row = _row(session, identifier)
    value = _input(row)
    result = json.loads(row.result_json) if row.result_json else None
    if result is not None and digest({'input_sha256': row.input_sha256, 'result': result}) != row.result_sha256:
        raise TradeError('PORTFOLIO_WF_CORRUPT', '组合样本外汇总校验失败', 409)
    folds = [{**value['temporal']['folds'][fold.ordinal], 'selection': _selection(fold), 'selection_sha256': fold.selection_sha256}
             for fold in session.scalars(select(Fold).where(Fold.run_id == identifier).order_by(Fold.ordinal))]
    return {**_data(row), **{key: item for key, item in _preview(value).items() if key != 'preview_sha256'}, 'folds': folds,
        'tasks': [_task_data(session, task) for task in session.scalars(select(Task).where(Task.run_id == identifier).order_by(Task.ordinal))], 'result': result}


def get_task(session, identifier, task_id):
    _row(session, identifier)
    task = session.get(Task, task_id)
    if task is None or task.run_id != identifier:
        raise TradeError('PORTFOLIO_WF_TASK_NOT_FOUND', '训练或测试阶段不存在', 404)
    return {**_task_data(session, task), 'portfolio': portfolio.get_portfolio(session, task.child_run_id, full=True) if task.child_run_id else None}


def control_walk_forward(session, identifier, action):
    row = _row(session, identifier)
    if action == 'pause' and row.state in ('queued', 'running'):
        row.state, row.pause_requested = ('paused', 0) if row.state == 'queued' else ('running', 1)
    elif action in ('resume', 'retry') and row.state in (('paused',) if action == 'resume' else ('failed', 'cancelled')):
        value = _input(row)
        if row.code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '代码已变化，请创建新滚动验证；不能混用新旧算法', 409)
        if row.elapsed_ms >= value['budget']['elapsed_ms'] or row.total_bytes >= value['budget']['result_bytes']:
            raise TradeError('PORTFOLIO_WF_BUDGET_EXHAUSTED', '滚动验证总预算已耗尽', 409)
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
        raise TradeError('PORTFOLIO_WF_STATE_CONFLICT', '当前状态不支持该操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(row)


def recover_interrupted_walk_forwards(factory):
    with factory.begin() as session:
        for row in session.scalars(select(Run).where(Run.state.in_(('queued', 'running')))):
            row.state = 'cancelled' if row.cancel_requested else 'paused'
            row.error, row.attempt_id = 'RECOVERED_AFTER_RESTART', None
            row.pause_requested = row.cancel_requested = 0
            row.updated_at = utc_now()


def process_one_walk_forward(factory, data_dir, *, should_stop=None):
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(Run.id).where(Run.state == 'running').limit(1)):
            return False
        row = session.scalar(select(Run).where(Run.deleted == 0, Run.state == 'queued').order_by(Run.last_served_at, Run.id).limit(1))
        if row is None:
            return False
        identifier, attempt = row.id, new_id()
        row.state, row.attempt_id = 'running', attempt
        row.last_served_at = row.updated_at = utc_now()
    error = child_id = prepared_outcome = prepared_final = prepared_selection = None
    before_ms = before_bytes = 0
    def stopped():
        if should_stop and should_stop():
            return True
        with factory() as session:
            current = session.get(Run, identifier)
            return current is None or current.cancel_requested or current.attempt_id != attempt
    try:
        with factory() as session:
            row = session.get(Run, identifier)
            value = _input(row)
            if row.code_sha256 != code_sha256():
                raise TradeError('CODE_VERSION_CHANGED', '冻结组合滚动验证算法已变化', 409)
            remaining = {'elapsed_ms': value['budget']['elapsed_ms'] - row.elapsed_ms, 'result_bytes': value['budget']['result_bytes'] - row.total_bytes}
            if min(remaining.values()) <= 0:
                raise TradeError('PORTFOLIO_WF_BUDGET_EXHAUSTED', '滚动验证总预算已耗尽', 409)
            task = session.scalar(select(Task).where(Task.run_id == identifier, Task.outcome_json.is_(None)).order_by(Task.ordinal).limit(1))
            if task is None:
                raise TradeError('PORTFOLIO_WF_CORRUPT', '缺少待执行阶段', 409)
            task_id, fold_index, phase = task.id, task.fold_index, task.phase
            fold = session.scalar(select(Fold).where(Fold.run_id == identifier, Fold.ordinal == fold_index))
            selection = _selection(fold)
            if phase == 'test' and selection is None:
                training = [_task_data(session, item) for item in session.scalars(select(Task).where(Task.run_id == identifier,
                    Task.fold_index == fold_index, Task.phase == 'train').order_by(Task.ordinal))]
                if len(training) != value['sampling']['actual_points'] or any(item['state'] != 'succeeded' for item in training):
                    raise TradeError('PORTFOLIO_WF_CORRUPT', '本折训练尚未全部成功，不能选择或测试', 409)
                prepared_selection = select_training(training, value['sampling']['axes'], value['temporal']['min_train_cycles'])
                if prepared_selection['axis_values'] is None:
                    prepared_outcome = {'skipped': True, 'reason': 'NO_ELIGIBLE_TRAINING_CANDIDATE'}
                    if row.completed_tasks + 1 == row.total_tasks:
                        prepared_final = _summary(session, row, value, override_task=task_id, override_outcome=prepared_outcome, override_selection=prepared_selection)
            else:
                axis = json.loads(task.axis_json)
                if digest(axis) != task.candidate_sha256 or (phase == 'test' and axis != selection['axis_values']):
                    raise TradeError('PORTFOLIO_WF_CORRUPT', '阶段候选与不可变训练选择不符', 409)
                if phase == 'train' and axis != value['sampling']['samples'][task.ordinal % (value['sampling']['actual_points'] + 1)]:
                    raise TradeError('PORTFOLIO_WF_CORRUPT', '训练候选偏离冻结采样计划', 409)
                context = temporal_context(value['source'], value['temporal']['folds'][fold_index], phase, axis)
        if prepared_selection is None:
            with factory.begin() as session:
                row, task = session.get(Run, identifier), session.get(Task, task_id)
                if task.child_run_id is None:
                    child = portfolio.create_frozen_child(session, context, f'{row.name} · 折{fold_index + 1} · {phase}', owner_kind='walk_forward', owner_id=row.id)
                    task.child_run_id = child.id
                else:
                    child = session.get(PortfolioRun, task.child_run_id)
                if child is None or child.input_sha256 != digest(context) or child.owner_kind != 'walk_forward' or child.owner_id != identifier:
                    raise TradeError('PORTFOLIO_WF_CORRUPT', '阶段冻结输入与内部组合任务不符', 409)
                if child.state in ('paused', 'failed', 'cancelled'):
                    portfolio.control_portfolio(session, child.id, 'resume' if child.state == 'paused' else 'retry', internal=True)
                child_id, before_ms, before_bytes = child.id, child.elapsed_ms, child.total_bytes
            portfolio.process_one_portfolio(factory, data_dir, should_stop=stopped, target_run_id=child_id, remaining_budget=remaining)
            with factory() as session:
                row, child = session.get(Run, identifier), session.get(PortfolioRun, child_id)
                if child.state == 'succeeded':
                    result = portfolio.get_portfolio(session, child_id, full=True)['result']
                    metrics = portfolio_metrics(result)
                    prepared_outcome = {'source_result_sha256': child.result_sha256, 'metrics': metrics}
                    if phase == 'test':
                        prepared_outcome['test_result'] = {key: result[key] for key in ('initial_capital', 'ending_assets', 'total_return', 'quality_flags')}
                        prepared_outcome['test_result'].update(trade_count=metrics['trade_count'], equity=[{key: point[key] for key in ('date', 'total_assets')} for point in result['equity']])
                    if row.completed_tasks + 1 == row.total_tasks:
                        prepared_final = _summary(session, row, value, override_task=task_id, override_outcome=prepared_outcome)
    except Exception as exc:
        error = f'{getattr(exc, "code", type(exc).__name__)}: {exc}'[:500]
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.get(Run, identifier)
        if row is None or row.state != 'running' or row.attempt_id != attempt:
            return True
        child = session.get(PortfolioRun, child_id) if child_id else None
        if child:
            row.elapsed_ms += max(0, child.elapsed_ms - before_ms)
            row.total_bytes += max(0, child.total_bytes - before_bytes)
        row.updated_at, row.attempt_id = utc_now(), None
        if row.code_sha256 != code_sha256():
            error = 'CODE_VERSION_CHANGED: 发布前代码已变化'
        if row.cancel_requested:
            row.state, row.error = 'cancelled', 'CANCELLED_BY_USER'
        elif should_stop and should_stop():
            row.state, row.error = 'paused', 'INTERRUPTED_SHUTDOWN'
        elif error or child and child.state in ('failed', 'cancelled'):
            row.state, row.error = 'failed', error or child.error
        else:
            row.active_fold = fold_index
            row.state, row.error = ('paused' if row.pause_requested or child and child.state == 'paused' else 'queued'), None
            task = session.get(Task, task_id)
            if prepared_selection is not None:
                fold = session.scalar(select(Fold).where(Fold.run_id == identifier, Fold.ordinal == fold_index))
                if fold.selection_json is not None:
                    raise TradeError('PORTFOLIO_WF_SELECTION_IMMUTABLE', '已发布的训练选择不可重写', 409)
                fold.selection_json, fold.selection_sha256 = canonical(prepared_selection), digest(prepared_selection)
                if prepared_selection['axis_values'] is not None:
                    task.axis_json = canonical(prepared_selection['axis_values'])
                    task.candidate_sha256 = digest(prepared_selection['axis_values'])
            if prepared_outcome is not None:
                task.outcome_json, task.outcome_sha256 = canonical(prepared_outcome), digest(prepared_outcome)
                row.completed_tasks += 1
            if prepared_final is not None:
                row.result_json, row.result_sha256 = canonical(prepared_final), digest({'input_sha256': row.input_sha256, 'result': prepared_final})
                row.state = 'succeeded'
        row.pause_requested = row.cancel_requested = 0
    return True
