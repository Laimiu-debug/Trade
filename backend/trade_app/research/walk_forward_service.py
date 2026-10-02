"""Durable train -> frozen selection -> untouched test workflow.

One shared-scheduler quantum computes one candidate, freezes one fold selection,
or publishes the final summary. No successful test can change its own selection.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import load_only

from trade_app.platform.compute_process import ComputeBudget, ComputeProcessError, run_json_process
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research import plateau_service
from trade_app.research.plateau_domain import canonical, digest, point_metrics
from trade_app.research.walk_forward_domain import PROTOCOL_VERSION, build_folds, select_candidate, summarize_folds
from trade_app.research.walk_forward_models import WalkForwardFold, WalkForwardJob, WalkForwardTask

FOLD_KEYS = ('initial_train_bars', 'test_bars', 'gap_bars', 'warmup_bars', 'max_folds', 'min_train_trades')


def code_sha256():
    return digest({'plateau': plateau_service.code_sha256(), 'workflow': [
        digest(Path(__file__).with_name(name).read_text(encoding='utf-8'))
        for name in ('walk_forward_domain.py', 'walk_forward_service.py')]})


def _preview(session, data_dir, body):
    context, sampling, _ = plateau_service._preview(session, data_dir, body)
    temporal = build_folds(context['bars'], candidate_count=sampling['actual_points'],
                           **{key: body[key] for key in FOLD_KEYS if key in body})
    candidates = sampling.pop('points')
    plan = {'sampling': sampling, 'temporal': temporal}
    context.update(candidates=candidates, plan_sha256=digest(plan), code_sha256=code_sha256(), protocol_version=PROTOCOL_VERSION)
    return context, plan, digest({'input': context, 'plan': plan})


def preview_walk_forward(session, data_dir, body):
    context, plan, preview_sha = _preview(session, data_dir, body)
    return {'preview_sha256': preview_sha, 'plan': plan, 'strategy_id': context['strategy_id'],
            'symbol': context['symbol'], 'candidates': context['candidates'], 'budget': context['budget']}


def _task_payload(candidate, fold, role):
    return {'params': candidate['params'], 'config': {**candidate['config'], 'trade_start_date': fold[role + '_start']},
            'axis_values': candidate['axis_values'], 'end_index': fold[role + '_end_index'],
            'role': role, 'fold_index': fold['index']}


def create_walk_forward(session, data_dir, body):
    name = body.get('name', '').strip()
    if not 1 <= len(name) <= 80:
        raise TradeError('INVALID_WALK_FORWARD_NAME', '实验名称须为 1 至 80 个字符')
    context, plan, preview_sha = _preview(session, data_dir, body)
    from trade_app.research.registry_service import require_enabled
    require_enabled(session, [context['strategy_id']])
    if body.get('expected_preview_sha256') != preview_sha:
        raise TradeError('WALK_FORWARD_PREVIEW_CHANGED', '请预览并确认相同种子、候选与时间折计划', 409)
    if (session.scalar(select(func.count()).select_from(WalkForwardJob).where(WalkForwardJob.deleted == 0,
            WalkForwardJob.state.in_(('queued', 'running', 'paused')))) or 0) >= 5:
        raise TradeError('WALK_FORWARD_QUEUE_FULL', '最多保留 5 个尚未结束的滚动验证', 429)
    now, job_id = utc_now(), new_id()
    session.add(WalkForwardJob(id=job_id, name=name, source_run_id=context['source_run_id'], strategy_id=context['strategy_id'],
                input_json=canonical(context), input_sha256=digest(context), plan_json=canonical(plan),
                code_sha256=context['code_sha256'], state='queued', pause_requested=0, cancel_requested=0,
                elapsed_ms=0, total_bytes=0, deleted=0, created_at=now, updated_at=now, last_served_at=now))
    session.flush()
    for fold in plan['temporal']['folds']:
        session.add(WalkForwardFold(id=digest([job_id, fold['index']]), job_id=job_id, fold_index=fold['index'],
                                   range_json=canonical(fold), state='waiting_train', updated_at=now))
        for candidate in context['candidates']:
            payload = _task_payload(candidate, fold, 'train')
            session.add(WalkForwardTask(id=digest([job_id, fold['index'], 'train', candidate['ordinal']]),
                job_id=job_id, fold_index=fold['index'], role='train', candidate_sha256=candidate['point_sha256'],
                ordinal=candidate['ordinal'], payload_json=canonical(payload), payload_sha256=digest(payload),
                state='invalid' if candidate['error'] else 'queued', attempt_number=0, error=candidate['error'], updated_at=now))
        session.add(WalkForwardTask(id=digest([job_id, fold['index'], 'test']), job_id=job_id, fold_index=fold['index'],
                    role='test', ordinal=0, state='blocked', attempt_number=0, updated_at=now))
    session.flush()
    return get_walk_forward(session, job_id)


def _row(session, job_id):
    row = session.get(WalkForwardJob, job_id)
    if row is None or row.deleted:
        raise TradeError('WALK_FORWARD_NOT_FOUND', '滚动验证不存在或已删除', 404)
    return row


def _tasks(session, job_id):
    return session.scalars(select(WalkForwardTask).options(load_only(*[getattr(WalkForwardTask, name)
             for name in WalkForwardTask.__table__.columns.keys() if name != 'result_json']))
             .where(WalkForwardTask.job_id == job_id).order_by(WalkForwardTask.fold_index, WalkForwardTask.role.desc(),
                                                            WalkForwardTask.ordinal)).all()


def _folds(session, job_id):
    return session.scalars(select(WalkForwardFold).where(WalkForwardFold.job_id == job_id)
                           .order_by(WalkForwardFold.fold_index)).all()


def _task_data(task, detail=False):
    payload = json.loads(task.payload_json) if task.payload_json else {}
    result = {'id': task.id, 'fold_index': task.fold_index, 'role': task.role, 'ordinal': task.ordinal,
              'candidate_sha256': task.candidate_sha256, 'state': task.state, 'error': task.error,
              'attempt_number': task.attempt_number, 'result_sha256': task.result_sha256,
              'metrics': json.loads(task.metrics_json) if task.metrics_json else None,
              'axis_values': payload.get('axis_values'), 'params': payload.get('params'), 'config': payload.get('config')}
    if detail:
        value = json.loads(task.result_json) if task.result_json else None
        if value is not None and digest(value) != task.result_sha256:
            raise TradeError('WALK_FORWARD_RESULT_CORRUPT', '计算点结果校验失败', 409)
        result['result'] = value
    return result


def _fold_data(row):
    selection = json.loads(row.selection_json) if row.selection_json else None
    if selection is not None and digest(selection) != row.selection_sha256:
        raise TradeError('WALK_FORWARD_SELECTION_CORRUPT', '训练选择检查点校验失败', 409)
    return {**json.loads(row.range_json), 'state': row.state, 'selection': selection, 'selection_sha256': row.selection_sha256}


def _data(session, row):
    counts = dict(session.execute(select(WalkForwardTask.state, func.count()).where(
        WalkForwardTask.job_id == row.id).group_by(WalkForwardTask.state)).all())
    retryable = session.scalar(select(func.count()).select_from(WalkForwardTask).join(WalkForwardFold, and_(
            WalkForwardTask.job_id == WalkForwardFold.job_id, WalkForwardTask.fold_index == WalkForwardFold.fold_index))
            .where(WalkForwardTask.job_id == row.id, WalkForwardTask.state.in_(('failed', 'cancelled')),
                   or_(WalkForwardTask.role == 'test', WalkForwardFold.selection_json.is_(None)))) or 0
    return {'id': row.id, 'name': row.name, 'strategy_id': row.strategy_id, 'source_run_id': row.source_run_id,
            'state': 'cancelling' if row.cancel_requested else 'pausing' if row.pause_requested else row.state,
            'plan': json.loads(row.plan_json), 'counts': counts, 'error': row.error,
            'input_sha256': row.input_sha256, 'result_sha256': row.result_sha256, 'code_sha256': row.code_sha256,
            'elapsed_ms': row.elapsed_ms, 'total_bytes': row.total_bytes, 'created_at': row.created_at,
            'capabilities': {'pause': row.state in ('queued', 'running') and not row.pause_requested and not row.cancel_requested,
                             'resume': row.state == 'paused', 'cancel': row.state in ('queued', 'running', 'paused') and not row.cancel_requested,
                             'retry': row.state in ('failed', 'cancelled', 'succeeded') and bool(retryable),
                             'delete': row.state not in ('queued', 'running')}}


def list_walk_forwards(session):
    return [_data(session, row) for row in session.scalars(select(WalkForwardJob).options(load_only(*[
        getattr(WalkForwardJob, name) for name in WalkForwardJob.__table__.columns.keys() if name not in ('input_json', 'summary_json')]))
        .where(WalkForwardJob.deleted == 0).order_by(WalkForwardJob.created_at.desc()).limit(100))]


def get_walk_forward(session, job_id):
    row = _row(session, job_id)
    context = json.loads(row.input_json)
    summary = json.loads(row.summary_json) if row.summary_json else None
    if summary is not None and digest(summary) != row.result_sha256:
        raise TradeError('WALK_FORWARD_RESULT_CORRUPT', '验证汇总校验失败', 409)
    return {**_data(session, row), 'symbol': context['symbol'], 'budget': context['budget'],
            'frozen_context': {key: value for key, value in context.items() if key not in ('bars', 'candidates')},
            'folds': [_fold_data(fold) for fold in _folds(session, job_id)],
            'tasks': [_task_data(task) for task in _tasks(session, job_id)], 'analysis': summary}


def get_task(session, job_id, task_id):
    _row(session, job_id)
    task = session.get(WalkForwardTask, task_id)
    if task is None or task.job_id != job_id:
        raise TradeError('WALK_FORWARD_TASK_NOT_FOUND', '训练或测试点不存在', 404)
    return _task_data(task, True)


def _validate(row):
    context, plan = json.loads(row.input_json), json.loads(row.plan_json)
    if digest(context) != row.input_sha256 or digest(plan) != context['plan_sha256']:
        raise TradeError('WALK_FORWARD_INPUT_CORRUPT', '冻结候选或时间计划校验失败', 409)
    if row.code_sha256 != code_sha256():
        raise TradeError('CODE_VERSION_CHANGED', '代码已更新，请创建新验证，已有训练与测试检查点保留', 409)
    if row.elapsed_ms >= context['budget']['max_compute_ms'] or row.total_bytes >= context['budget']['max_result_bytes']:
        raise TradeError('WALK_FORWARD_BUDGET_EXHAUSTED', '验证总资源预算已用尽', 409)
    return context, plan


def control_walk_forward(session, job_id, action):
    row = _row(session, job_id)
    if action == 'pause' and row.state in ('queued', 'running'):
        row.state, row.pause_requested = ('paused', 0) if row.state == 'queued' else ('running', 1)
    elif action in ('resume', 'retry'):
        if row.state not in (('paused',) if action == 'resume' else ('failed', 'cancelled', 'succeeded')):
            raise TradeError('WALK_FORWARD_STATE_CONFLICT', '当前状态不支持该操作', 409)
        _validate(row)
        if action == 'retry':
            folds = {fold.fold_index: fold for fold in _folds(session, job_id)}
            restored = 0
            for task in _tasks(session, job_id):
                # Published selections remain immutable even after their test was seen.
                if task.state in ('failed', 'cancelled') and (task.role == 'test' or folds[task.fold_index].selection_json is None):
                    task.state = 'queued' if task.payload_json else 'blocked'
                    task.attempt_id = task.error = None
                    restored += 1
            if not restored:
                raise TradeError('WALK_FORWARD_SELECTION_FROZEN', '已有训练选择不可更改；没有可重试的测试或中断点，请创建新验证', 409)
        row.state, row.error, row.pause_requested, row.cancel_requested = 'queued', None, 0, 0
        row.summary_json = row.result_sha256 = None
    elif action == 'cancel' and row.state in ('queued', 'running', 'paused'):
        if row.state == 'running':
            row.cancel_requested, row.pause_requested = 1, 0
        else:
            row.state = 'cancelled'
            for task in _tasks(session, job_id):
                if task.state in ('queued', 'blocked'):
                    task.state = 'cancelled'
    elif action == 'delete' and row.state not in ('queued', 'running'):
        row.deleted = 1
    else:
        raise TradeError('WALK_FORWARD_STATE_CONFLICT', '当前状态不支持该操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(session, row)


def recover_interrupted_walk_forwards(factory):
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(WalkForwardJob).where(WalkForwardJob.deleted == 0,
                WalkForwardJob.state.in_(('queued', 'running')))):
            row.state = 'cancelled' if row.cancel_requested else 'paused'
            row.attempt_id = None
            row.error = 'RECOVERED_AFTER_RESTART'
            row.pause_requested = row.cancel_requested = 0
            row.updated_at = utc_now()
            for task in _tasks(session, row.id):
                if task.state == 'running':
                    task.state, task.attempt_id = ('cancelled' if row.state == 'cancelled' else 'queued'), None
                    task.error = 'RECOVERED_AFTER_RESTART'
                elif row.state == 'cancelled' and task.state in ('queued', 'blocked'):
                    task.state = 'cancelled'


def process_one_walk_forward(factory, data_dir: Path, *, should_stop=None) -> bool:
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(WalkForwardJob.id).where(WalkForwardJob.state == 'running').limit(1)):
            return False
        row = session.scalar(select(WalkForwardJob).where(WalkForwardJob.deleted == 0, WalkForwardJob.state == 'queued')
                 .order_by(WalkForwardJob.last_served_at, WalkForwardJob.id).limit(1))
        if row is None:
            return False
        job_id, attempt_id = row.id, new_id()
        row.state, row.attempt_id = 'running', attempt_id
        row.last_served_at = row.updated_at = utc_now()
        action, task_id, fold_id = 'finalize', None, None
        for fold in _folds(session, job_id):
            tasks = [task for task in _tasks(session, job_id) if task.fold_index == fold.fold_index]
            train = [task for task in tasks if task.role == 'train' and task.state == 'queued']
            test = next(task for task in tasks if task.role == 'test')
            if train:
                action, active, fold_id = 'compute', train[0], fold.id
            elif fold.selection_json is None:
                action, fold_id = 'select', fold.id
                break
            elif test.state == 'queued':
                action, active, fold_id = 'compute', test, fold.id
            else:
                continue
            active.state, active.attempt_id = 'running', attempt_id
            active.attempt_number += 1
            active.error = None
            active.updated_at = utc_now()
            task_id = active.id
            fold.state = 'training' if active.role == 'train' else 'testing'
            break
        frozen_code = row.code_sha256

    started = time.monotonic()
    failure = result = metrics = selection = summary = None
    def stop_reason():
        if should_stop and should_stop():
            return 'shutdown'
        with factory() as session:
            current = session.get(WalkForwardJob, job_id)
            if current is None or current.cancel_requested or current.state != 'running' or current.attempt_id != attempt_id:
                return 'cancelled'
            if task_id:
                active_task = session.get(WalkForwardTask, task_id)
                if active_task is None or active_task.state != 'running' or active_task.attempt_id != attempt_id:
                    return 'cancelled'
        return None

    try:
        with factory() as session:
            row = session.get(WalkForwardJob, job_id)
            context, plan = _validate(row)
            if action == 'compute':
                task = session.get(WalkForwardTask, task_id)
                payload = json.loads(task.payload_json)
                if digest(payload) != task.payload_sha256:
                    raise TradeError('WALK_FORWARD_INPUT_CORRUPT', '计算点输入校验失败', 409)
                if task.role == 'test':
                    evidence = _fold_data(session.get(WalkForwardFold, fold_id))['selection']
                    if evidence['selected_candidate_sha256'] != task.candidate_sha256:
                        raise TradeError('WALK_FORWARD_SELECTION_CORRUPT', '测试候选不匹配训练选择', 409)
                budget = dict(payload['config']['compute_budget'])
                budget['timeout_seconds'] = min(budget['timeout_seconds'], (context['budget']['max_compute_ms'] - row.elapsed_ms) / 1000)
                remaining_bytes = context['budget']['max_result_bytes'] - row.total_bytes
                budget['output_bytes'] = min(budget['output_bytes'], remaining_bytes)
            elif action == 'select':
                fold = session.get(WalkForwardFold, fold_id)
                training = [{**_task_data(task), 'point_sha256': task.candidate_sha256}
                            for task in _tasks(session, job_id) if task.fold_index == fold.fold_index and task.role == 'train']
            else:
                output = []
                tasks = _tasks(session, job_id)
                for fold in _folds(session, job_id):
                    test = next(task for task in tasks if task.fold_index == fold.fold_index and task.role == 'test')
                    detail = _task_data(test, True)
                    output.append({**_fold_data(fold), 'test_state': test.state, 'test_metrics': detail['metrics'],
                                   'test_error': test.error, 'test_result': detail['result']})
        if action == 'compute':
            computed = run_json_process('trade_app.research.backtest_worker',
                {'attempt_id': attempt_id, 'symbol': context['symbol'], 'strategy_id': context['strategy_id'],
                 'bars': context['bars'][:payload['end_index'] + 1], 'params': payload['params'], 'config': payload['config']},
                budget=ComputeBudget(**budget), stop_reason=stop_reason)
            envelope = computed.value
            if not envelope.get('ok'):
                error = envelope.get('error') or {}
                raise ComputeProcessError(error.get('code', 'COMPUTE_FAILED'), error.get('message', '训练或测试点计算失败'))
            if envelope.get('attempt_id') != attempt_id or not isinstance(envelope.get('result'), dict):
                raise ComputeProcessError('COMPUTE_INVALID_RESULT', '返回结果不属于当前执行代号')
            result = {**envelope['result'], 'compute': computed.metrics}
            metrics = point_metrics(result)
            if len(canonical(result).encode()) > remaining_bytes:
                raise TradeError('WALK_FORWARD_BUDGET_EXHAUSTED', '结果存储预算已用尽', 409)
        elif action == 'select':
            selection = select_candidate(training, plan['sampling']['axes'], plan['temporal']['min_train_trades'])
        else:
            summary = summarize_folds(output)
        if frozen_code != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '计算期间代码已更新', 409)
    except Exception as exc:
        failure = f'{getattr(exc, "code", type(exc).__name__)}: {exc}'[:500]

    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.get(WalkForwardJob, job_id)
        task = session.get(WalkForwardTask, task_id) if task_id else None
        if row is None or row.state != 'running' or row.attempt_id != attempt_id or task is not None and task.attempt_id != attempt_id:
            return True
        row.elapsed_ms += int((time.monotonic() - started) * 1000)
        row.attempt_id = None
        row.updated_at = utc_now()
        if task:
            task.attempt_id = None
            task.updated_at = row.updated_at
        if frozen_code != code_sha256():
            failure = 'CODE_VERSION_CHANGED: 发布前算法版本已变更'
        if row.cancel_requested or failure and failure.startswith('COMPUTE_CANCELLED:'):
            row.state, row.pause_requested, row.cancel_requested = 'cancelled', 0, 0
            for current in _tasks(session, job_id):
                if current.state in ('running', 'queued', 'blocked'):
                    current.state, current.attempt_id = 'cancelled', None
            return True
        if should_stop and should_stop() or failure and failure.startswith('COMPUTE_SHUTDOWN:'):
            row.state, row.pause_requested, row.error = 'paused', 0, 'INTERRUPTED_SHUTDOWN'
            if task:
                task.state, task.error = 'queued', 'INTERRUPTED_SHUTDOWN'
            return True
        fatal = failure and (action != 'compute' or failure.startswith(('CODE_VERSION_CHANGED:', 'WALK_FORWARD_INPUT_CORRUPT:',
                                   'WALK_FORWARD_SELECTION_CORRUPT:', 'WALK_FORWARD_BUDGET_EXHAUSTED:')))
        if task:
            if failure:
                task.state, task.error = 'failed', failure
            else:
                task.state, task.result_json, task.result_sha256 = 'succeeded', canonical(result), digest(result)
                task.metrics_json = canonical(metrics)
                row.total_bytes += len(task.result_json.encode())
            if task.role == 'test':
                session.get(WalkForwardFold, fold_id).state = 'failed' if failure else 'completed'
        elif action == 'select' and failure is None:
            fold = session.get(WalkForwardFold, fold_id)
            # A published selection is never replaced, including after restart.
            if fold.selection_json is not None:
                raise TradeError('WALK_FORWARD_SELECTION_FROZEN', '训练选择已经冻结', 409)
            fold.selection_json, fold.selection_sha256 = canonical(selection), digest(selection)
            fold.updated_at = row.updated_at
            test = next(current for current in _tasks(session, job_id) if current.fold_index == fold.fold_index and current.role == 'test')
            if selection['selected_candidate_sha256'] is None:
                test.state, test.error, fold.state = 'skipped', selection['reason'], 'skipped'
            else:
                candidate = next(item for item in context['candidates'] if item['point_sha256'] == selection['selected_candidate_sha256'])
                payload = _task_payload(candidate, json.loads(fold.range_json), 'test')
                test.payload_json, test.payload_sha256, test.candidate_sha256 = canonical(payload), digest(payload), candidate['point_sha256']
                test.state, fold.state = 'queued', 'waiting_test'
        if fatal:
            row.state, row.error = 'failed', failure
        elif action == 'finalize':
            row.summary_json, row.result_sha256 = canonical(summary), digest(summary)
            row.state = 'succeeded' if summary['completed_test_folds'] else 'failed'
            row.error = None if summary['completed_test_folds'] else 'NO_SUCCESSFUL_TEST_FOLDS'
        else:
            row.state = 'paused' if row.pause_requested else 'queued'
        row.pause_requested = 0
    return True
