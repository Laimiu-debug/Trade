"""Bounded per-point checkpoints, driven by the shared CPU dispatcher.

Every point owns an attempt token. Pause finishes the current point, cancel
terminates it, and interrupted experiments require explicit resume. Inputs are
frozen once; restarting never samples a new plan or reruns a successful point.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, load_only, sessionmaker

from trade_app.market.service import get_dataset
from trade_app.platform.compute_process import ComputeBudget, ComputeProcessError, run_json_process
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research import backtest_service
from trade_app.research.backtest_models import BacktestRun
from trade_app.research.plateau_domain import (MAX_POINTS, SCORING_VERSION, canonical, digest, number,
                                              point_metrics, robustness, sample_axes, text_number)
from trade_app.research.plateau_models import PlateauExperiment, PlateauPoint
from trade_app.research.preset_service import normalize_preset_params
from trade_app.research.service import normalize_strategy_params, strategy_catalog

TOTAL_RESULT_BYTES = 64 * 1024 * 1024
TOTAL_COMPUTE_MS = 60 * 60 * 1000
CONFIG_SCHEMA = {
    'holding_bars': {'type': 'integer', 'minimum': 1, 'maximum': 60},
    'max_position_pct': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1},
    'stop_loss_pct': {'type': 'number', 'minimum': 0, 'maximum': .5},
    'take_profit_pct': {'type': 'number', 'minimum': 0, 'maximum': 1.5},
    'trailing_stop_pct': {'type': 'number', 'minimum': 0, 'maximum': .5},
    'slippage_rate': {'type': 'number', 'minimum': 0, 'maximum': .05},
}


def code_sha256():
    folder = Path(__file__).parent
    return digest({'backtest': backtest_service.code_sha256(),
                   'plateau': [digest((folder / filename).read_text(encoding='utf-8')) for filename in
                               ('plateau_domain.py', 'plateau_service.py')]})


def supported_schema(strategy_id):
    descriptor = next((item for item in strategy_catalog() if item['id'] == strategy_id and item['signal_params'] is not None), None)
    if descriptor is None:
        raise TradeError('PLATEAU_STRATEGY_UNSUPPORTED', '仅支持已实现的单股策略', 409)
    keys = set(descriptor['signal_params'])
    # Ranking-only terms have no effect on a single-stock entry decision.
    if strategy_id == 'limit_up_arb_v1':
        keys.discard('sector_rank_weight')
    trend_keys = {'trend_king_limitup_v1': {'min_day_gain_a', 'min_hist'},
                  'trend_king_rally_v1': {'min_gain_3d_b', 'min_gain_5d_b'},
                  'trend_king_pullback_v1': {'max_vol_ratio_c', 'min_hist_c', 'min_prev_gain_c'}}
    if strategy_id in trend_keys:
        keys &= trend_keys[strategy_id]
    return {**{'params.' + key: spec for key in sorted(keys)
               if (spec := descriptor['params_schema'].get(key)) and not spec.get('readonly')},
            **{'config.' + key: value for key, value in CONFIG_SCHEMA.items()}}


def _source(session, data_dir, source_id):
    source = session.get(BacktestRun, source_id)
    if source is None or source.state != 'succeeded':
        raise TradeError('PLATEAU_SOURCE_REQUIRED', '请先完成一份单股回测，再创建参数实验', 409)
    if source.code_sha256 != backtest_service.code_sha256():
        raise TradeError('CODE_VERSION_CHANGED', '来源回测的代码版本已更新，请先以原参数重新回测', 409)
    dataset = get_dataset(session, data_dir, source.dataset_id)
    if not 32 <= len(dataset['bars']) <= 2000:
        raise TradeError('PLATEAU_BAR_LIMIT', '收益平原支持 32 至 2000 根冻结日线')
    config = json.loads(source.config_json)
    config['advanced_analysis'] = False
    public_params = normalize_preset_params(source.strategy_id, {key: value for key, value in json.loads(source.params_json).items()
                            if key in next(item['signal_params'] for item in strategy_catalog() if item['id'] == source.strategy_id)})
    return {'source_run_id': source.id, 'dataset_id': source.dataset_id, 'symbol': dataset['symbol'],
            'strategy_id': source.strategy_id, 'strategy_version': source.strategy_version,
            'execution_version': source.execution_version, 'calculation_version': source.calculation_version,
            'code_sha256': code_sha256(), 'bars': dataset['bars'], 'bars_sha256': digest(dataset['bars']),
            'params': public_params, 'config': config,
            'budget': {'max_result_bytes': TOTAL_RESULT_BYTES, 'max_compute_ms': TOTAL_COMPUTE_MS}}


def _preview(session, data_dir, body):
    context = _source(session, data_dir, body['base_run_id'])
    plan = sample_axes(body['sampling_mode'], body['axes'], supported_schema(context['strategy_id']),
                       seed=body.get('seed'), sample_points=body.get('sample_points', 24), max_points=body.get('max_points', 120))
    points = []
    for index, values in enumerate(plan.pop('samples')):
        params, config = dict(context['params']), {**context['config'], 'fee_config': dict(context['config']['fee_config'])}
        for key, value in values.items():
            group, parameter = key.split('.', 1)
            if group == 'params':
                params[parameter] = value
            elif parameter == 'slippage_rate':
                config['fee_config']['slippage_rate'] = value
            else:
                config[parameter] = int(value) if parameter == 'holding_bars' else value
        error = None
        try:
            normalized = normalize_strategy_params(context['strategy_id'], params)
        except TradeError as exc:
            normalized, error = params, f'{exc.code}: {exc}'
        point = {'params': normalized, 'config': config, 'axis_values': values}
        points.append({'ordinal': index, 'point_sha256': digest(point), **point, 'error': error})
    plan['points'] = points
    plan['points_sha256'] = digest(points)
    plan['invalid_points'] = sum(bool(point['error']) for point in points)
    context['plan_sha256'] = digest({key: value for key, value in plan.items() if key != 'points'})
    preview_sha = digest({'input': context, 'plan': plan})
    return context, plan, preview_sha


def preview_plateau(session, data_dir: Path, body: dict) -> dict:
    context, plan, preview_sha = _preview(session, data_dir, body)
    return {'preview_sha256': preview_sha, 'strategy_id': context['strategy_id'], 'symbol': context['symbol'],
            'schema': supported_schema(context['strategy_id']), 'plan': plan,
            'scope': 'single_symbol_daily', 'budget': context['budget'],
            'limitations': ['仅当前单股日线执行模型；不支持多股票组合、日内减仓、旧矩阵引擎与样本外验证',
                            '固定风险参数与事件模板来自来源回测；高级分析在参数点计算中关闭']}


def create_plateau(session, data_dir: Path, body: dict) -> dict:
    name = body.get('name', '').strip()
    if not 1 <= len(name) <= 80:
        raise TradeError('INVALID_PLATEAU_NAME', '实验名称须为 1 至 80 个字符')
    context, plan, preview_sha = _preview(session, data_dir, body)
    from trade_app.research.registry_service import require_enabled
    require_enabled(session, [context['strategy_id']])
    if body.get('expected_preview_sha256') != preview_sha:
        raise TradeError('PLATEAU_PREVIEW_CHANGED', '请先预览并确认相同种子、参数与数据的采样计划', 409)
    count = session.scalar(select(func.count()).select_from(PlateauExperiment).where(
        PlateauExperiment.deleted == 0, PlateauExperiment.state.in_(('queued', 'running', 'paused')))) or 0
    if count >= 5:
        raise TradeError('PLATEAU_QUEUE_FULL', '最多保留 5 个尚未结束的参数实验', 429)
    now, experiment_id = utc_now(), new_id()
    row = PlateauExperiment(id=experiment_id, name=name, source_run_id=context['source_run_id'],
                            strategy_id=context['strategy_id'], code_sha256=context['code_sha256'],
                            input_sha256=digest(context), input_json=canonical(context),
                            plan_json=canonical({key: value for key, value in plan.items() if key != 'points'}),
                            state='queued', pause_requested=0, cancel_requested=0, deleted=0,
                            total_bytes=0, elapsed_ms=0, created_at=now, updated_at=now, last_served_at=now)
    session.add(row)
    session.flush()
    for point in plan['points']:
        session.add(PlateauPoint(id=digest([experiment_id, point['ordinal']]), experiment_id=experiment_id,
                                 ordinal=point['ordinal'], point_sha256=point['point_sha256'],
                                 payload_json=canonical({key: point[key] for key in ('params', 'config', 'axis_values')}),
                                 state='invalid' if point['error'] else 'queued', attempt_number=0,
                                 error=point['error'], updated_at=now))
    session.flush()
    return get_plateau(session, experiment_id)


def _row(session, experiment_id):
    row = session.get(PlateauExperiment, experiment_id)
    if row is None or row.deleted:
        raise TradeError('PLATEAU_NOT_FOUND', '参数实验不存在或已删除', 404)
    return row


def _counts(session, experiment_id):
    return dict(session.execute(select(PlateauPoint.state, func.count()).where(
        PlateauPoint.experiment_id == experiment_id).group_by(PlateauPoint.state)).all())


def _data(session, row):
    counts = _counts(session, row.id)
    return {'id': row.id, 'name': row.name, 'source_run_id': row.source_run_id, 'strategy_id': row.strategy_id,
            'state': 'cancelling' if row.cancel_requested else 'pausing' if row.pause_requested else row.state,
            'plan': json.loads(row.plan_json), 'counts': counts, 'error': row.error,
            'result_sha256': row.result_sha256, 'code_sha256': row.code_sha256, 'input_sha256': row.input_sha256,
            'elapsed_ms': row.elapsed_ms, 'total_bytes': row.total_bytes,
            'created_at': row.created_at, 'updated_at': row.updated_at,
            'capabilities': {'pause': row.state in ('queued', 'running') and not row.pause_requested and not row.cancel_requested,
                             'resume': row.state == 'paused', 'cancel': row.state in ('queued', 'running', 'paused') and not row.cancel_requested,
                             'retry_failed': row.state in ('failed', 'succeeded', 'cancelled') and bool(counts.get('failed') or counts.get('cancelled')),
                             'delete': row.state not in ('queued', 'running')}}


def list_plateaus(session):
    return [_data(session, row) for row in session.scalars(select(PlateauExperiment).options(
                load_only(*[getattr(PlateauExperiment, column) for column in PlateauExperiment.__table__.columns.keys()
                            if column not in ('input_json', 'summary_json')])).where(PlateauExperiment.deleted == 0)
                .order_by(PlateauExperiment.created_at.desc(), PlateauExperiment.id).limit(100))]


def _point_data(row, *, detail=False):
    payload = json.loads(row.payload_json)
    value = {'id': row.id, 'ordinal': row.ordinal, 'point_sha256': row.point_sha256, 'state': row.state,
             'axis_values': payload['axis_values'], 'params': payload['params'], 'config': payload['config'],
             'attempt_number': row.attempt_number, 'error': row.error, 'result_sha256': row.result_sha256,
             'metrics': json.loads(row.metrics_json) if row.metrics_json else None}
    if detail:
        if row.result_json and digest(json.loads(row.result_json)) != row.result_sha256:
            raise TradeError('PLATEAU_RESULT_CORRUPT', '参数点结果校验失败', 409)
        value['result'] = json.loads(row.result_json) if row.result_json else None
    return value


def _points(session, experiment_id):
    return session.scalars(select(PlateauPoint).options(load_only(*[
        getattr(PlateauPoint, column) for column in PlateauPoint.__table__.columns.keys() if column != 'result_json']))
        .where(PlateauPoint.experiment_id == experiment_id).order_by(PlateauPoint.ordinal)).all()


def get_plateau(session, experiment_id):
    row = _row(session, experiment_id)
    context = json.loads(row.input_json)
    if row.summary_json and digest(json.loads(row.summary_json)) != row.result_sha256:
        raise TradeError('PLATEAU_RESULT_CORRUPT', '实验分析校验失败', 409)
    return {**_data(session, row), 'scope': 'single_symbol_daily', 'symbol': context['symbol'],
            'dataset_id': context['dataset_id'], 'budget': context['budget'],
            'frozen_context': {key: value for key, value in context.items() if key != 'bars'},
            'points': [_point_data(point) for point in _points(session, row.id)],
            'analysis': json.loads(row.summary_json) if row.summary_json else None}


def get_point(session, experiment_id, point_id):
    _row(session, experiment_id)
    point = session.get(PlateauPoint, point_id)
    if point is None or point.experiment_id != experiment_id:
        raise TradeError('PLATEAU_POINT_NOT_FOUND', '参数点不存在', 404)
    return _point_data(point, detail=True)


def control_plateau(session, experiment_id, action):
    row = _row(session, experiment_id)
    if action == 'pause' and row.state in ('queued', 'running'):
        row.state, row.pause_requested = ('paused', 0) if row.state == 'queued' else ('running', 1)
    elif action in ('resume', 'retry-failed'):
        allowed = row.state == 'paused' if action == 'resume' else row.state in ('failed', 'succeeded', 'cancelled')
        if not allowed:
            raise TradeError('PLATEAU_STATE_CONFLICT', '当前状态不支持该操作', 409)
        if row.code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '代码已更新，请从新回测创建实验；已有检查点保留', 409)
        context = json.loads(row.input_json)
        if digest(context) != row.input_sha256 or digest(json.loads(row.plan_json)) != context['plan_sha256']:
            raise TradeError('PLATEAU_INPUT_CORRUPT', '冻结输入校验失败', 409)
        if row.elapsed_ms >= context['budget']['max_compute_ms'] or row.total_bytes >= context['budget']['max_result_bytes']:
            raise TradeError('PLATEAU_BUDGET_EXHAUSTED', '本实验资源总预算已用尽，请缩小采样范围创建新实验', 409)
        if action == 'retry-failed':
            for point in _points(session, row.id):
                if point.state in ('failed', 'cancelled'):
                    point.state, point.attempt_id, point.error = 'queued', None, None
        row.state, row.error, row.pause_requested, row.cancel_requested = 'queued', None, 0, 0
        row.summary_json, row.result_sha256 = None, None
    elif action == 'cancel' and row.state in ('queued', 'running', 'paused'):
        if row.state == 'running':
            row.cancel_requested, row.pause_requested = 1, 0
        else:
            row.state = 'cancelled'
            for point in _points(session, row.id):
                if point.state == 'queued':
                    point.state = 'cancelled'
    elif action == 'delete' and row.state not in ('queued', 'running'):
        row.deleted = 1
    else:
        raise TradeError('PLATEAU_STATE_CONFLICT', '当前状态不支持该操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(session, row)


def recover_interrupted_plateaus(factory):
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(PlateauExperiment).where(
                PlateauExperiment.deleted == 0, PlateauExperiment.state.in_(('queued', 'running')))):
            row.state = 'cancelled' if row.cancel_requested else 'paused'
            row.error = 'RECOVERED_AFTER_RESTART'
            row.pause_requested = row.cancel_requested = 0
            row.updated_at = utc_now()
            for point in _points(session, row.id):
                if point.state == 'running':
                    point.state, point.attempt_id, point.error = ('cancelled' if row.state == 'cancelled' else 'queued'), None, 'RECOVERED_AFTER_RESTART'
                elif point.state == 'queued' and row.state == 'cancelled':
                    point.state = 'cancelled'


def process_one_plateau(factory: sessionmaker, data_dir: Path, *, should_stop: Callable[[], bool] | None = None) -> bool:
    """One dispatcher quantum: one bounded child and an atomic checkpoint."""
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(PlateauExperiment.id).where(PlateauExperiment.state == 'running').limit(1)):
            return False
        experiment = session.scalar(select(PlateauExperiment).where(PlateauExperiment.deleted == 0,
            PlateauExperiment.state == 'queued').order_by(PlateauExperiment.last_served_at, PlateauExperiment.id).limit(1))
        if experiment is None:
            return False
        experiment_id = experiment.id
        point = session.scalar(select(PlateauPoint).where(PlateauPoint.experiment_id == experiment.id,
                    PlateauPoint.state == 'queued').order_by(PlateauPoint.ordinal).limit(1))
        if point is None:
            experiment.state, experiment.error = 'failed', 'NO_RUNNABLE_POINTS'
            return True
        point.state, point.attempt_id = 'running', new_id()
        point.attempt_number += 1
        point.error = None
        experiment.state = 'running'
        experiment.last_served_at = experiment.updated_at = point.updated_at = utc_now()
        point_id, attempt_id = point.id, point.attempt_id
        frozen, payload = json.loads(experiment.input_json), json.loads(point.payload_json)
        frozen_plan = json.loads(experiment.plan_json)
        input_sha, point_sha, queued_code = experiment.input_sha256, point.point_sha256, experiment.code_sha256
        remaining_ms = frozen['budget']['max_compute_ms'] - experiment.elapsed_ms
        remaining_bytes = frozen['budget']['max_result_bytes'] - experiment.total_bytes

    def stop_reason():
        if should_stop and should_stop():
            return 'shutdown'
        with factory() as session:
            current, active = session.get(PlateauExperiment, experiment_id), session.get(PlateauPoint, point_id)
            if current is None or current.cancel_requested or current.state != 'running' or active is None or active.attempt_id != attempt_id:
                return 'cancelled'
        return None

    started = time.monotonic()
    encoded = metrics = failure = None
    try:
        if queued_code != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '算法已变更，请以当前版本创建新实验', 409)
        if digest(frozen) != input_sha or digest(payload) != point_sha or digest(frozen_plan) != frozen['plan_sha256']:
            raise TradeError('PLATEAU_INPUT_CORRUPT', '冻结输入或参数点校验失败', 409)
        if remaining_ms <= 0 or remaining_bytes <= 0:
            raise TradeError('PLATEAU_BUDGET_EXHAUSTED', '实验资源总预算已用尽', 409)
        compute_budget = dict(payload['config']['compute_budget'])
        compute_budget['timeout_seconds'] = min(compute_budget['timeout_seconds'], remaining_ms / 1000)
        compute_budget['output_bytes'] = min(compute_budget['output_bytes'], remaining_bytes)
        computed = run_json_process('trade_app.research.backtest_worker',
                    {'attempt_id': attempt_id, 'symbol': frozen['symbol'], 'bars': frozen['bars'],
                     'strategy_id': frozen['strategy_id'], 'params': payload['params'], 'config': payload['config']},
                    budget=ComputeBudget(**compute_budget), stop_reason=stop_reason)
        envelope = computed.value
        if not envelope.get('ok'):
            error = envelope.get('error') or {}
            raise ComputeProcessError(error.get('code', 'COMPUTE_FAILED'), error.get('message', '参数点计算失败'))
        if envelope.get('attempt_id') != attempt_id or not isinstance(envelope.get('result'), dict):
            raise ComputeProcessError('COMPUTE_INVALID_RESULT', '参数点计算结果与执行代号不符')
        result = {**envelope['result'], 'compute': computed.metrics}
        metrics = point_metrics(result)
        encoded = canonical(result)
        if len(encoded.encode()) > remaining_bytes:
            raise TradeError('PLATEAU_BUDGET_EXHAUSTED', '实验结果超出存储预算', 409)
        if queued_code != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '计算期间算法已变更，结果未发布', 409)
    except Exception as exc:
        failure = f'{getattr(exc, "code", type(exc).__name__)}: {exc}'[:500]

    # At most 400 compact summaries. Scoring is outside the SQLite write lock.
    with factory() as session:
        old_points = [_point_data(item) for item in _points(session, experiment_id)]
        plan = json.loads(session.get(PlateauExperiment, experiment_id).plan_json)
    final = not any(item['state'] == 'queued' for item in old_points)
    candidate_points = [{**item, **({'metrics': metrics if failure is None else None,
                                    'state': 'succeeded' if failure is None else 'failed', 'error': failure}
                                   if item['id'] == point_id else {})} for item in old_points]
    analysis = robustness(candidate_points, plan['axes']) if final else None
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        current, point = session.get(PlateauExperiment, experiment_id), session.get(PlateauPoint, point_id)
        if current is None or point is None or current.state != 'running' or point.attempt_id != attempt_id:
            return True
        current.elapsed_ms += int((time.monotonic() - started) * 1000)
        if queued_code != code_sha256():
            failure = 'CODE_VERSION_CHANGED: 发布前算法版本已变更'
        point.attempt_id = None
        current.updated_at = point.updated_at = utc_now()
        if current.cancel_requested or failure and failure.startswith('COMPUTE_CANCELLED:'):
            current.state, point.state = 'cancelled', 'cancelled'
            current.cancel_requested = current.pause_requested = 0
            for other in _points(session, experiment_id):
                if other.state == 'queued':
                    other.state = 'cancelled'
        elif should_stop and should_stop() or failure and failure.startswith('COMPUTE_SHUTDOWN:'):
            current.state, current.error, current.pause_requested = 'paused', 'INTERRUPTED_SHUTDOWN', 0
            point.state, point.error = 'queued', 'INTERRUPTED_SHUTDOWN'
        else:
            if failure:
                point.state, point.error = 'failed', failure
            else:
                point.state, point.error = 'succeeded', None
                point.result_json, point.result_sha256 = encoded, digest(json.loads(encoded))
                point.metrics_json = canonical(metrics)
                current.total_bytes += len(encoded.encode())
            fatal = failure and failure.startswith(('CODE_VERSION_CHANGED:', 'PLATEAU_INPUT_CORRUPT:', 'PLATEAU_BUDGET_EXHAUSTED:'))
            if fatal:
                current.state, current.error = 'failed', failure
            elif final:
                current.state = 'succeeded' if analysis['points'] else 'failed'
                current.summary_json, current.result_sha256 = canonical(analysis), digest(analysis)
                current.error = None if analysis['points'] else 'ALL_POINTS_FAILED'
            else:
                current.state = 'paused' if current.pause_requested else 'queued'
            current.pause_requested = 0
    return True
