"""Bounded task summaries without loading result bodies or AI prompts."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from trade_app.ai.models import AICall, AISession
from trade_app.api.routes import read_session
from trade_app.market.models import MarketSyncJob
from trade_app.platform.models import RebuildRequest
from trade_app.research.backtest_models import BacktestRun
from trade_app.research.scan_job_models import StrategyScanJob
from trade_app.research.plateau_models import PlateauExperiment, PlateauPoint
from trade_app.research.walk_forward_models import WalkForwardFold, WalkForwardJob, WalkForwardTask
from trade_app.research.portfolio_models import PortfolioRun
from trade_app.research.portfolio_experiment_models import PortfolioExperiment
from trade_app.research.portfolio_walk_forward_models import PortfolioWalkForward
from trade_app.research.portfolio_analysis_models import PortfolioAnalysis
from trade_app.research.event_store_models import EventStoreJob
from trade_app.research.tdx_universe_models import TdxUniverseJob
from trade_app.trading.service import account_or_error


router = APIRouter(prefix='/api/v1', tags=['tasks'])
ACTIVE = ('queued', 'running', 'finalizing', 'cancelling', 'pausing')


@router.get('/tasks')
def tasks(account_id: str | None = None, session: Session = Depends(read_session)) -> dict:
    if account_id is not None:
        account_or_error(session, account_id)
    rows, totals = [], {}

    def fetch(model, columns, kind, state_column, condition=None, join_session=False):
        statement = select(*columns)
        count = select(func.count()).select_from(model)
        if join_session:
            statement = statement.outerjoin(AISession, model.session_id == AISession.id)
            count = count.outerjoin(AISession, model.session_id == AISession.id)
        if condition is not None:
            statement, count = statement.where(condition), count.where(condition)
        totals[kind] = session.scalar(count)
        return session.execute(statement.order_by(case((state_column.in_(ACTIVE), 0), else_=1),
                                                   model.created_at.desc(), model.id.desc()).limit(100)).mappings()

    def append(row, kind, title, page, *, state=None, progress=None, actions=None, note=None):
        rows.append({'id': row['id'], 'kind': kind, 'title': title, 'page': page,
                     'state': state or row['state'], 'progress': progress,
                     'created_at': row['created_at'], 'updated_at': row['updated_at'],
                     'error_code': row.get('error_code', row.get('error')), 'note': note,
                     'actions': actions or {}, 'pause_supported': kind in ('plateau', 'walk_forward', 'portfolio', 'portfolio_experiment', 'portfolio_walk_forward')})

    model = BacktestRun
    for row in fetch(model, [model.id, model.state, model.strategy_id, model.cancel_requested,
                             model.error, model.attempt_number, model.created_at, model.updated_at], 'backtest', model.state):
        state = 'cancelling' if row['state'] == 'running' and row['cancel_requested'] else row['state']
        actions = {}
        if state in ('queued', 'running'):
            actions['cancel'] = f'/backtests/{row["id"]}/cancel'
        if state in ('failed', 'cancelled'):
            actions['retry'] = f'/backtests/{row["id"]}/retry'
        append(row, 'backtest', row['strategy_id'], 'backtest', state=state, actions=actions,
               note=f'执行次数 {row["attempt_number"]}')

    model = StrategyScanJob
    for row in fetch(model, [model.id, model.state, model.cancel_requested, model.completed_count,
                             model.total_count, model.attempt_number, model.error, model.created_at, model.updated_at], 'scan', model.state):
        state = 'cancelling' if row['state'] == 'running' and row['cancel_requested'] else row['state']
        actions = {}
        if state in ('queued', 'running'):
            actions['cancel'] = f'/research/scan-jobs/{row["id"]}/cancel'
        if state in ('failed', 'cancelled'):
            actions['retry'] = f'/research/scan-jobs/{row["id"]}/retry'
        append(row, 'scan', '多策略日期扫描', 'research', state=state, actions=actions,
               progress={'done': row['completed_count'], 'total': row['total_count']},
               note=f'执行次数 {row["attempt_number"]}；完成后发布完整扫描结果')

    model = PlateauExperiment
    experiments = list(fetch(model, [model.id, model.name, model.state, model.cancel_requested,
                                     model.pause_requested, model.error, model.created_at, model.updated_at],
                             'plateau', model.state, model.deleted == 0))
    counts = {}
    if experiments:
        for experiment_id, state, count in session.execute(select(PlateauPoint.experiment_id, PlateauPoint.state, func.count())
                .where(PlateauPoint.experiment_id.in_([row['id'] for row in experiments]))
                .group_by(PlateauPoint.experiment_id, PlateauPoint.state)):
            counts.setdefault(experiment_id, {})[state] = count
    for row in experiments:
        state = 'cancelling' if row['cancel_requested'] else 'pausing' if row['pause_requested'] else row['state']
        point_counts = counts.get(row['id'], {})
        actions = {}
        for action, allowed in (
            ('pause', state in ('queued', 'running')), ('resume', state == 'paused'),
            ('cancel', state in ('queued', 'running', 'paused')),
            ('retry', state in ('failed', 'succeeded', 'cancelled') and bool(point_counts.get('failed') or point_counts.get('cancelled')))):
            if allowed:
                actions[action] = f'/research/plateaus/{row["id"]}/' + ('retry-failed' if action == 'retry' else action)
        append(row, 'plateau', row['name'], 'backtest', state=state, actions=actions,
               progress={'done': sum(point_counts.get(key, 0) for key in ('succeeded', 'failed', 'cancelled')), 'total': sum(point_counts.values())},
               note='参数点逐点保存；暂停完成当前点后生效')

    model = WalkForwardJob
    experiments = list(fetch(model, [model.id, model.name, model.state, model.cancel_requested,
                                     model.pause_requested, model.error, model.created_at, model.updated_at],
                             'walk_forward', model.state, model.deleted == 0))
    counts, retryable = {}, set()
    if experiments:
        ids = [row['id'] for row in experiments]
        for job_id, state, count in session.execute(select(WalkForwardTask.job_id, WalkForwardTask.state, func.count())
                .where(WalkForwardTask.job_id.in_(ids)).group_by(WalkForwardTask.job_id, WalkForwardTask.state)):
            counts.setdefault(job_id, {})[state] = count
        retryable = set(session.scalars(select(WalkForwardTask.job_id).distinct().join(WalkForwardFold, and_(
            WalkForwardTask.job_id == WalkForwardFold.job_id, WalkForwardTask.fold_index == WalkForwardFold.fold_index))
            .where(WalkForwardTask.job_id.in_(ids), WalkForwardTask.state.in_(('failed', 'cancelled')),
                   or_(WalkForwardTask.role == 'test', WalkForwardFold.selection_json.is_(None)))))
    for row in experiments:
        state = 'cancelling' if row['cancel_requested'] else 'pausing' if row['pause_requested'] else row['state']
        task_counts = counts.get(row['id'], {})
        actions = {}
        for action, allowed in (
            ('pause', state in ('queued', 'running')), ('resume', state == 'paused'),
            ('cancel', state in ('queued', 'running', 'paused')),
            ('retry', state in ('failed', 'succeeded', 'cancelled') and row['id'] in retryable)):
            if allowed:
                actions[action] = f'/research/walk-forwards/{row["id"]}/{action}'
        append(row, 'walk_forward', row['name'], 'backtest', state=state, actions=actions,
               progress={'done': sum(task_counts.get(key, 0) for key in ('succeeded', 'failed', 'cancelled', 'invalid', 'skipped')),
                         'total': sum(task_counts.values())},
               note='先冻结训练选择，再运行后续测试；重试保留已有选择')

    model = PortfolioRun
    for row in fetch(model, [model.id, model.name, model.state, model.pause_requested, model.cancel_requested,
                             model.completed_days, model.total_days, model.symbol_count,
                             model.error, model.created_at, model.updated_at], 'portfolio', model.state, (model.deleted == 0) & model.owner_kind.is_(None)):
        state = 'cancelling' if row['cancel_requested'] else 'pausing' if row['pause_requested'] else row['state']
        actions = {}
        for action, allowed in (('pause', state in ('queued', 'running')), ('resume', state == 'paused'),
                                ('cancel', state in ('queued', 'running', 'paused')), ('retry', state in ('failed', 'cancelled'))):
            if allowed:
                actions[action] = f'/research/portfolios/{row["id"]}/{action}'
        append(row, 'portfolio', row['name'], 'backtest', state=state, actions=actions,
               progress={'done': row['completed_days'], 'total': row['total_days']},
               note=f'固定研究样本 {row["symbol_count"]} 只；每日仓位、委托和资金随检查点保存')

    model = PortfolioExperiment
    for row in fetch(model, [model.id, model.name, model.state, model.pause_requested, model.cancel_requested,
                             model.completed_points, model.total_points, model.error, model.created_at, model.updated_at],
                     'portfolio_experiment', model.state, model.deleted == 0):
        state = 'cancelling' if row['cancel_requested'] else 'pausing' if row['pause_requested'] else row['state']
        actions = {}
        for action, allowed in (('pause', state in ('queued', 'running')), ('resume', state == 'paused'),
                                ('cancel', state in ('queued', 'running', 'paused')), ('retry', state in ('failed', 'cancelled'))):
            if allowed:
                actions[action] = f'/research/portfolio-experiments/{row["id"]}/{action}'
        append(row, 'portfolio_experiment', row['name'], 'backtest', state=state, actions=actions,
               progress={'done': row['completed_points'], 'total': row['total_points']},
               note='组合参数逐点比较；每5个样本日期保存检查点')

    model = PortfolioWalkForward
    for row in fetch(model, [model.id, model.name, model.state, model.pause_requested, model.cancel_requested,
                             model.completed_tasks, model.total_tasks, model.error, model.created_at, model.updated_at],
                     'portfolio_walk_forward', model.state, model.deleted == 0):
        state = 'cancelling' if row['cancel_requested'] else 'pausing' if row['pause_requested'] else row['state']
        actions = {}
        for action, allowed in (('pause', state in ('queued', 'running')), ('resume', state == 'paused'),
                                ('cancel', state in ('queued', 'running', 'paused')), ('retry', state in ('failed', 'cancelled'))):
            if allowed:
                actions[action] = f'/research/portfolio-walk-forwards/{row["id"]}/{action}'
        append(row, 'portfolio_walk_forward', row['name'], 'backtest', state=state, actions=actions,
               progress={'done': row['completed_tasks'], 'total': row['total_tasks']},
               note='每折训练后冻结选择；测试折重置资金与持仓')

    model = PortfolioAnalysis
    for row in fetch(model, [model.id, model.name, model.state, model.cancel_requested, model.error,
                             model.created_at, model.updated_at], 'portfolio_analysis', model.state, model.deleted == 0):
        state = 'cancelling' if row['cancel_requested'] and row['state'] == 'running' else row['state']
        actions = {}
        for action, allowed in (('resume', state == 'paused'), ('retry', state in ('failed', 'cancelled')),
                                ('cancel', state in ('queued', 'running', 'paused'))):
            if allowed:
                actions[action] = f'/research/portfolio-analyses/{row["id"]}/{action}'
        append(row, 'portfolio_analysis', row['name'], 'backtest', state=state, actions=actions,
               note='冻结组合产物后分析；中断恢复重新执行本次有界分析')

    model = EventStoreJob
    for row in fetch(model, [model.id, model.state, model.cancel_requested, model.completed_count,
                             model.total_count, model.cache_hits, model.written_count,
                             model.error, model.created_at, model.updated_at], 'event_store', model.state):
        state = 'cancelling' if row['state'] == 'running' and row['cancel_requested'] else row['state']
        actions = {}
        if state in ('queued', 'running'):
            actions['cancel'] = f'/research/event-store/jobs/{row["id"]}/cancel'
        if state in ('failed', 'cancelled'):
            actions['resume'] = f'/research/event-store/jobs/{row["id"]}/resume'
        append(row, 'event_store', '维科夫事件仓回填', 'events', state=state, actions=actions,
               progress={'done': row['completed_count'], 'total': row['total_count']},
               note=f'缓存复用 {row["cache_hits"]} 项；新写入 {row["written_count"]} 项')

    model = MarketSyncJob
    for row in fetch(model, [model.id, model.state, model.cancel_requested, model.request_json,
                             model.next_index, model.created_at, model.updated_at], 'market', model.state):
        request = json.loads(row['request_json'])
        state = 'cancelling' if row['state'] == 'running' and row['cancel_requested'] else row['state']
        actions = {}
        if state in ('queued', 'running'):
            actions['cancel'] = f'/market/sync-jobs/{row["id"]}/cancel'
        if state == 'partial_failed':
            actions['retry'] = f'/market/sync-jobs/{row["id"]}/retry-failed'
        append(row, 'market', f'{request["provider"]} · {request["start_date"]} 至 {request["end_date"]}',
               'market', state=state, actions=actions,
               progress={'done': row['next_index'], 'total': len(request['symbols'])},
               note='重试只提交失败的证券' if state == 'partial_failed' else None)

    model = TdxUniverseJob
    for row in fetch(model, [model.id, model.state, model.cancel_requested, model.request_json,
                             model.total_count, model.processed_count, model.error_code,
                             model.created_at, model.updated_at], 'universe', model.state):
        request = json.loads(row['request_json'])
        state = 'cancelling' if row['cancel_requested'] and row['state'] in ACTIVE else row['state']
        actions = {'cancel': f'/research/tdx-universe-jobs/{row["id"]}/cancel'} if state in ('queued', 'running', 'finalizing') else {}
        append(row, 'universe', f'{request.get("kind", "funnel")} · {request["as_of_date"]}', 'research', state=state,
               progress={'done': row['processed_count'], 'total': row['total_count']}, actions=actions)

    model = AICall
    condition = (model.account_id == account_id) & (model.session_id.is_(None) | (AISession.deleted == 0))
    for row in fetch(model, [model.id, model.status, model.kind, model.cancel_requested, model.error_code,
                             model.created_at, model.updated_at], 'ai', model.status, condition, True):
        state = 'cancelling' if row['status'] == 'running' and row['cancel_requested'] else row['status']
        scope = '?account_id=' + account_id if account_id else ''
        actions = {'cancel': f'/ai/runs/{row["id"]}/cancel{scope}'} if state in ('queued', 'running') else {}
        append(row, 'ai', row['kind'], 'ai', state=state, actions=actions,
               note='已冻结请求，需在 AI 页面明确开始' if state == 'queued' else None)

    if account_id is not None:
        model = RebuildRequest
        for row in fetch(model, [model.id, model.state, model.error, model.target_revision, model.earliest_date,
                                 model.created_at, model.updated_at], 'analytics', model.state, model.account_id == account_id):
            actions = {'retry': f'/accounts/{account_id}/analytics/retry'} if row['state'] == 'failed' else {}
            append(row, 'analytics', f'账户统计 · 输入版本 {row["target_revision"]}', 'overview', actions=actions,
                   note='从 ' + row['earliest_date'] + ' 重算')
    # Preserve newest-first ordering inside active and finished groups without parsing dates.
    rows = sorted(rows, key=lambda row: row['created_at'], reverse=True)
    rows.sort(key=lambda row: row['state'] not in ACTIVE)
    return {'data': {'items': rows, 'total_by_kind': totals, 'limit_per_kind': 100,
                     'account_id': account_id, 'active_count': sum(row['state'] in ACTIVE for row in rows)}}
