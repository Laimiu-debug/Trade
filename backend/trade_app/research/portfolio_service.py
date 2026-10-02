"""Frozen portfolio experiments with one five-day checkpoint per CPU quantum."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import time

from sqlalchemy import func, select, text
from sqlalchemy.orm import load_only

from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.compute_process import ComputeBudget, ComputeProcessError, run_json_process
from trade_app.platform.types import TradeError, decimal_value, money_minor, money_text, new_id, utc_now
from trade_app.research.backtest_service import code_sha256 as runtime_code_sha256
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.portfolio_domain import (VERSION, MATRIX_VERSION, CHUNK_DAYS, LIMITATIONS,
                                                canonical, digest, initial_checkpoint, summary)
from trade_app.research.portfolio_models import PortfolioRun, PortfolioChunk
from trade_app.research.portfolio_event_domain import (DEFAULTS as EVENT_DEFAULTS, PARAM_SCHEMA as EVENT_PARAM_SCHEMA,
                                                      VERSION as EVENT_VERSION, normalize_event_params)
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES
from trade_app.research.service import normalize_strategy_params, strategy_catalog
from trade_app.research.wyckoff_strategy import WYCKOFF_STRATEGY_IDS
from trade_app.trading.simulation import DEFAULT_CONFIG, normalized_config

MAX_SYMBOLS, MAX_BARS = 64, 60000
COMPUTE_BUDGET = ComputeBudget(version='portfolio-five-day-v1', timeout_seconds=120,
    input_bytes=24 * 1024 * 1024, output_bytes=4 * 1024 * 1024)
DEFAULTS = {'initial_capital': '1000000.00', 'position_pct': '.2', 'max_positions': 5,
    'max_holding_bars': 60, 'top_n': 500, 'entry_top_k': 0, 'entry_delay_bars': 1,
    'execution_strict': True, 'pool_roll': 'daily', 'stop_loss_pct': '.05', 'take_profit_pct': '.15',
    'trailing_stop_pct': '0', 'intraday_trailing': True, 'trailing_reduce_ratio': '.5',
    'daily_weak_clear': True, 'weak_confirm_days': 2, 'invalidate_pending': True,
    'trail_activate': '0', 'time_stop_days': 0, 'time_stop_min_gain': '0', 'breakeven_trigger': '0',
    'structural_box_stop': False, 'ma20_box_break': False,
    'ambiguity_policy': 'conservative', 'fee_config': DEFAULT_CONFIG}


def code_sha256():
    folder = Path(__file__).parent
    return digest({'runtime': runtime_code_sha256(), 'portfolio': [digest((folder / name).read_text(encoding='utf-8'))
        for name in ('portfolio_domain.py', 'portfolio_worker.py', 'portfolio_service.py', 'portfolio_event_domain.py', 'portfolio_exit_rules.py')]})


def normalize_config(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise TradeError('INVALID_PORTFOLIO_CONFIG', '存在未实现的组合执行参数')
    config = {**DEFAULTS, **raw}
    for key, lo, hi in (('max_positions', 1, 64), ('max_holding_bars', 1, 240), ('top_n', 1, 2000),
                       ('entry_top_k', 0, 64), ('entry_delay_bars', 1, 5), ('weak_confirm_days', 1, 20), ('time_stop_days', 0, 240)):
        if type(config[key]) is not int or not lo <= config[key] <= hi:
            raise TradeError('INVALID_PORTFOLIO_CONFIG', f'{key} 须为 {lo} 至 {hi} 的整数')
    for key in ('execution_strict', 'intraday_trailing', 'daily_weak_clear', 'invalidate_pending', 'structural_box_stop', 'ma20_box_break'):
        if type(config[key]) is not bool:
            raise TradeError('INVALID_PORTFOLIO_CONFIG', f'{key} 必须是布尔值')
    for key, maximum in (('position_pct', 1), ('stop_loss_pct', .5), ('take_profit_pct', 1.5),
                         ('trailing_stop_pct', .5), ('trailing_reduce_ratio', 1), ('trail_activate', 2),
                         ('time_stop_min_gain', 2), ('breakeven_trigger', 2)):
        value = decimal_value(config[key], key)
        if value < 0 or value > Decimal(str(maximum)) or key in ('position_pct', 'trailing_reduce_ratio') and value == 0:
            raise TradeError('INVALID_PORTFOLIO_CONFIG', f'{key} 超出支持范围')
        config[key] = format(value.normalize(), 'f')
    if config['pool_roll'] not in ('static', 'daily', 'weekly', 'position') or config['ambiguity_policy'] not in ('conservative', 'optimistic'):
        raise TradeError('INVALID_PORTFOLIO_CONFIG', '股票池刷新或双触发策略无效')
    config['initial_capital'] = money_text(money_minor(config['initial_capital'], '组合初始资金'))
    config['fee_config'] = normalized_config(config['fee_config'])
    if config['time_stop_days'] == 0 and Decimal(config['time_stop_min_gain']) > 0:
        raise TradeError('INVALID_PORTFOLIO_CONFIG', '时间止损收益门槛须同时启用正的time_stop_days')
    return config


def _freeze(session, data_dir, body):
    ids = body.get('dataset_ids', [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SYMBOLS or len(set(ids)) != len(ids):
        raise TradeError('PORTFOLIO_DATASET_LIMIT', '须选择1至64份不重复的冻结行情，每个证券一份')
    config = normalize_config(body.get('config') or {})
    if config['structural_box_stop'] or config['ma20_box_break']:
        raise TradeError('PORTFOLIO_BOX_SIGNAL_UNSUPPORTED', '箱体退出需要实验室图形/混合信号冻结入场箱顶，当前3条生产路径不提供此依据')
    datasets, seen, total = [], set(), 0
    for dataset_id in ids:
        item = get_dataset(session, data_dir, dataset_id)
        symbol = ''.join(normalize_a_share_symbol(item['symbol']))
        if symbol in seen:
            raise TradeError('PORTFOLIO_DUPLICATE_SYMBOL', f'{symbol} 有重复别名或多份行情，请只选一份')
        seen.add(symbol)
        if len(item['bars']) < 32:
            raise TradeError('PORTFOLIO_INSUFFICIENT_BARS', f'{symbol} 至少需要32根日线')
        datasets.append({'dataset_id': dataset_id, 'symbol': symbol, 'bars': item['bars'], 'bars_sha256': digest(item['bars'])})
        total += len(item['bars'])
    if total > MAX_BARS:
        raise TradeError('PORTFOLIO_BAR_LIMIT', '固定研究样本合计最多60000根日线')
    datasets.sort(key=lambda item: item['symbol'])
    all_calendar = sorted({bar['event_date'] for item in datasets for bar in item['bars']})
    if len(all_calendar) > 2000:
        raise TradeError('PORTFOLIO_CALENDAR_LIMIT', '组合并集日历最多2000个样本日期')
    mode = body.get('mode')
    profile = None
    if mode == 'matrix_raw_s1_s9':
        if (body.get('params') or body.get('event_profile_id') is not None or body.get('event_profile_revision') is not None
                or body.get('strategy_id') not in (None, 'matrix_raw_s1_s9')):
            raise TradeError('PORTFOLIO_UNUSED_PARAMS', '原始矩阵使用固定S1-S9公式及组合top_n，不接收单股参数或事件模板')
        strategy_id, strategy_version, params = 'matrix_raw_s1_s9', MATRIX_VERSION, {}
    elif mode == 'aligned_wyckoff_events':
        if body.get('strategy_id') not in (None, 'aligned_wyckoff_events'):
            raise TradeError('PORTFOLIO_UNUSED_PARAMS', '事件矩阵有独立参数，不复用传统策略编号')
        strategy_id, strategy_version = 'aligned_wyckoff_events', EVENT_VERSION
        params = normalize_event_params(body.get('params') or {})
        profile = freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
    elif mode == 'traditional_runtime14':
        strategy_id = body.get('strategy_id')
        if strategy_id not in SINGLE_SYMBOL_STRATEGIES:
            raise TradeError('PORTFOLIO_STRATEGY_UNSUPPORTED', '传统路径须选择目录中已实现的单股策略')
        params = normalize_strategy_params(strategy_id, body.get('params') or {})
        strategy_version = next(item['version'] for item in strategy_catalog() if item['id'] == strategy_id)
        if strategy_id in WYCKOFF_STRATEGY_IDS:
            profile = freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
        elif body.get('event_profile_id') is not None or body.get('event_profile_revision') is not None:
            raise TradeError('EVENT_PROFILE_UNSUPPORTED', '当前策略不使用事件模板')
    else:
        raise TradeError('PORTFOLIO_MODE_UNSUPPORTED', '请选择原始矩阵、传统策略运行器或事件矩阵')
    first = body.get('start_date') or all_calendar[min(60 if mode == 'matrix_raw_s1_s9' else 31, len(all_calendar) - 2)]
    last = body.get('end_date') or all_calendar[-1]
    try:
        if date.fromisoformat(first).isoformat() != first or date.fromisoformat(last).isoformat() != last:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise TradeError('INVALID_PORTFOLIO_DATE', '日期须为 YYYY-MM-DD') from exc
    calendar = [day for day in all_calendar if first <= day <= last]
    if len(calendar) < 2:
        raise TradeError('PORTFOLIO_EMPTY_WINDOW', '交易区间至少需要两个样本日期')
    context = {'version': VERSION, 'mode': mode, 'strategy_id': strategy_id, 'strategy_version': strategy_version,
        'universe_scope': 'fixed_research_sample', 'params': params, 'event_profile': profile,
        'datasets': datasets, 'all_calendar': all_calendar, 'calendar': calendar, 'config': config,
        'code_sha256': code_sha256(), 'budget': {'compute': COMPUTE_BUDGET.to_dict(),
            'max_elapsed_ms': 3600000, 'max_result_bytes': 64 * 1024 * 1024}}
    if len(canonical(context).encode()) > 20 * 1024 * 1024:
        raise TradeError('PORTFOLIO_INPUT_LIMIT', '冻结输入超过20MiB，请缩小研究样本')
    return context


def _context_metadata(context):
    return {**{key: value for key, value in context.items() if key not in ('datasets', 'all_calendar', 'calendar')},
        'start_date': context['calendar'][0], 'end_date': context['calendar'][-1], 'total_days': len(context['calendar']),
        'datasets': [{key: value for key, value in item.items() if key != 'bars'} | {'bar_count': len(item['bars'])} for item in context['datasets']]}


def preview_portfolio(session, data_dir, body):
    context = _freeze(session, data_dir, body)
    return {'preview_sha256': digest(context), 'frozen_context': _context_metadata(context), 'limitations': LIMITATIONS}


def create_portfolio(session, data_dir, body):
    name = body.get('name', '').strip()
    if not 1 <= len(name) <= 80:
        raise TradeError('INVALID_PORTFOLIO_NAME', '名称须为1至80个字符')
    context = _freeze(session, data_dir, body)
    if context['mode'] == 'traditional_runtime14':
        from trade_app.research.registry_service import require_enabled
        require_enabled(session, [context['strategy_id']])
    if body.get('expected_preview_sha256') != digest(context):
        raise TradeError('PORTFOLIO_PREVIEW_CHANGED', '请先预览并确认相同的数据、参数与代码版本', 409)
    if (session.scalar(select(func.count()).select_from(PortfolioRun).where(PortfolioRun.deleted == 0, PortfolioRun.owner_kind.is_(None),
        PortfolioRun.state.in_(('queued', 'running', 'paused')))) or 0) >= 5:
        raise TradeError('PORTFOLIO_QUEUE_FULL', '最多保留5个未结束的组合任务', 429)
    return _data(_store_frozen(session, context, name))


def create_frozen_child(session, context, name, *, owner_kind, owner_id):
    """Internal orchestration only. Context comes from a verified frozen plan."""
    if owner_kind not in ('experiment', 'walk_forward') or not owner_id or context['code_sha256'] != code_sha256():
        raise TradeError('INVALID_PORTFOLIO_OWNER', '内部组合任务来源或代码版本无效', 409)
    return _store_frozen(session, context, name[:80], owner_kind=owner_kind, owner_id=owner_id)


def _store_frozen(session, context, name, *, owner_kind=None, owner_id=None):
    now, checkpoint = utc_now(), initial_checkpoint(context)
    row = PortfolioRun(id=new_id(), name=name, mode=context['mode'], strategy_id=context['strategy_id'],
        input_json=canonical(context), input_sha256=digest(context), code_sha256=context['code_sha256'], state='queued',
        checkpoint_json=canonical(checkpoint), checkpoint_sha256=digest(checkpoint), summary_json=canonical(summary(context, checkpoint)),
        completed_days=0, total_days=len(context['calendar']), symbol_count=len(context['datasets']), chunk_count=0,
        elapsed_ms=0, total_bytes=0, pause_requested=0, cancel_requested=0, deleted=0, created_at=now, updated_at=now, last_served_at=now,
        owner_kind=owner_kind, owner_id=owner_id)
    session.add(row)
    session.flush()
    return row


def _row(session, run_id):
    row = session.get(PortfolioRun, run_id)
    if row is None or row.deleted:
        raise TradeError('PORTFOLIO_NOT_FOUND', '组合任务不存在或已删除', 404)
    return row


def _data(row):
    return {'id': row.id, 'name': row.name, 'mode': row.mode, 'strategy_id': row.strategy_id,
        'owner_kind': row.owner_kind, 'owner_id': row.owner_id,
        'state': 'cancelling' if row.cancel_requested else 'pausing' if row.pause_requested else row.state,
        'input_sha256': row.input_sha256, 'code_sha256': row.code_sha256, 'checkpoint_sha256': row.checkpoint_sha256,
        'result_sha256': row.result_sha256, 'completed_days': row.completed_days, 'total_days': row.total_days,
        'symbol_count': row.symbol_count, 'chunk_count': row.chunk_count, 'elapsed_ms': row.elapsed_ms,
        'total_bytes': row.total_bytes, 'error': row.error, 'summary': json.loads(row.summary_json),
        'created_at': row.created_at, 'updated_at': row.updated_at,
        'capabilities': {'pause': row.owner_kind is None and row.state in ('queued', 'running') and not row.pause_requested and not row.cancel_requested,
            'resume': row.owner_kind is None and row.state == 'paused', 'retry': row.owner_kind is None and row.state in ('failed', 'cancelled'),
            'cancel': row.owner_kind is None and row.state in ('queued', 'running', 'paused') and not row.cancel_requested,
            'delete': row.owner_kind is None and row.state not in ('queued', 'running')}}


def list_portfolios(session):
    return [_data(row) for row in session.scalars(select(PortfolioRun).options(load_only(*[
        getattr(PortfolioRun, column) for column in PortfolioRun.__table__.columns.keys() if column not in ('input_json', 'checkpoint_json')]))
        .where(PortfolioRun.deleted == 0, PortfolioRun.owner_kind.is_(None)).order_by(PortfolioRun.created_at.desc(), PortfolioRun.id).limit(100))]


def _verified(row):
    context, checkpoint = json.loads(row.input_json), json.loads(row.checkpoint_json)
    if digest(context) != row.input_sha256 or digest(checkpoint) != row.checkpoint_sha256 or checkpoint['cursor'] != row.completed_days:
        raise TradeError('PORTFOLIO_CHECKPOINT_CORRUPT', '冻结输入或检查点校验失败', 409)
    return context, checkpoint


def get_portfolio(session, run_id, *, full=False):
    row = _row(session, run_id)
    context, checkpoint = _verified(row)
    data = {**_data(row), 'frozen_context': _context_metadata(context), 'checkpoint': checkpoint, 'limitations': LIMITATIONS}
    if full:
        result = {'trades': [], 'equity': [], 'pool_history': [], 'decisions': []}
        chunks = session.scalars(select(PortfolioChunk).where(PortfolioChunk.run_id == run_id).order_by(PortfolioChunk.ordinal)).all()
        previous = digest(initial_checkpoint(context))
        for chunk in chunks:
            value = json.loads(chunk.result_json)
            if digest(value) != chunk.result_sha256 or chunk.prior_sha256 != previous:
                raise TradeError('PORTFOLIO_RESULT_CORRUPT', '组合分块证据校验失败', 409)
            previous = digest(value['checkpoint'])
            for key in result:
                result[key].extend(value[key])
        if previous != row.checkpoint_sha256:
            raise TradeError('PORTFOLIO_RESULT_CORRUPT', '组合结果与检查点不符', 409)
        if row.result_sha256 and digest({'summary': data['summary'], 'chunks': [chunk.result_sha256 for chunk in chunks],
                                         'input_sha256': row.input_sha256}) != row.result_sha256:
            raise TradeError('PORTFOLIO_RESULT_CORRUPT', '组合结果摘要校验失败', 409)
        data['result'] = {**data['summary'], **result}
    return data


def control_portfolio(session, run_id, action, *, internal=False):
    row = _row(session, run_id)
    if row.owner_kind and not internal:
        raise TradeError('PORTFOLIO_OWNED_TASK', '内部候选由所属参数实验或滚动验证控制，请操作主任务', 409)
    if action == 'pause' and row.state in ('queued', 'running'):
        row.state, row.pause_requested = ('paused', 0) if row.state == 'queued' else ('running', 1)
    elif action in ('resume', 'retry') and row.state in (('paused',) if action == 'resume' else ('failed', 'cancelled')):
        context, _ = _verified(row)
        if row.code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '代码已更新，请创建新任务；原结果保留', 409)
        if row.elapsed_ms >= context['budget']['max_elapsed_ms'] or row.total_bytes >= context['budget']['max_result_bytes']:
            raise TradeError('PORTFOLIO_BUDGET_EXHAUSTED', '组合任务资源总预算已用尽', 409)
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
        raise TradeError('PORTFOLIO_STATE_CONFLICT', '当前状态不支持该操作', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(row)


def recover_interrupted_portfolios(factory):
    with factory.begin() as session:
        for row in session.scalars(select(PortfolioRun).where(PortfolioRun.state.in_(('queued', 'running')))):
            row.state = 'cancelled' if row.cancel_requested else 'paused'
            row.error, row.attempt_id = 'RECOVERED_AFTER_RESTART', None
            row.pause_requested = row.cancel_requested = 0
            row.updated_at = utc_now()


def process_one_portfolio(factory, data_dir, *, should_stop=None, target_run_id=None, remaining_budget=None):
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(PortfolioRun.id).where(PortfolioRun.state == 'running').limit(1)):
            return False
        row = session.scalar(select(PortfolioRun).where(PortfolioRun.deleted == 0, PortfolioRun.state == 'queued',
                             PortfolioRun.id == target_run_id if target_run_id else PortfolioRun.owner_kind.is_(None))
                             .order_by(PortfolioRun.last_served_at, PortfolioRun.id).limit(1))
        if row is None:
            return False
        run_id, attempt = row.id, new_id()
        row.state, row.attempt_id = 'running', attempt
        row.last_served_at = row.updated_at = utc_now()
        context, checkpoint = json.loads(row.input_json), json.loads(row.checkpoint_json)
        input_sha, prior_sha, queued_code = row.input_sha256, row.checkpoint_sha256, row.code_sha256
        ordinal, remaining_ms, remaining_bytes = row.chunk_count, context['budget']['max_elapsed_ms'] - row.elapsed_ms, context['budget']['max_result_bytes'] - row.total_bytes
        if remaining_budget:
            remaining_ms = min(remaining_ms, remaining_budget['elapsed_ms'])
            remaining_bytes = min(remaining_bytes, remaining_budget['result_bytes'])
    def stop_reason():
        if should_stop and should_stop():
            return 'shutdown'
        with factory() as session:
            current = session.get(PortfolioRun, run_id)
            if current is None or current.cancel_requested or current.state != 'running' or current.attempt_id != attempt:
                return 'cancelled'
        return None
    started, failure, result, encoded = time.monotonic(), None, None, None
    try:
        if queued_code != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '组合算法已变化', 409)
        if digest(context) != input_sha or digest(checkpoint) != prior_sha:
            raise TradeError('PORTFOLIO_CHECKPOINT_CORRUPT', '冻结输入或检查点不一致', 409)
        if remaining_ms <= 0 or remaining_bytes <= 0:
            raise TradeError('PORTFOLIO_BUDGET_EXHAUSTED', '资源总预算已用尽', 409)
        budget = {**context['budget']['compute']}
        budget['timeout_seconds'] = min(budget['timeout_seconds'], remaining_ms / 1000)
        budget['output_bytes'] = min(budget['output_bytes'], remaining_bytes)
        computed = run_json_process('trade_app.research.portfolio_worker', {'attempt_id': attempt, 'context': context, 'checkpoint': checkpoint},
                                   budget=ComputeBudget(**budget), stop_reason=stop_reason)
        envelope = computed.value
        if not envelope.get('ok'):
            error = envelope.get('error') or {}
            raise ComputeProcessError(error.get('code', 'COMPUTE_FAILED'), error.get('message', '组合计算失败'))
        result = envelope.get('result')
        if envelope.get('attempt_id') != attempt or not isinstance(result, dict) or result['checkpoint']['cursor'] != min(len(context['calendar']), checkpoint['cursor'] + CHUNK_DAYS):
            raise ComputeProcessError('COMPUTE_INVALID_RESULT', '组合结果与执行检查点不符')
        encoded = canonical(result)
        if len(encoded.encode()) > remaining_bytes:
            raise TradeError('PORTFOLIO_BUDGET_EXHAUSTED', '组合输出超过总预算', 409)
    except Exception as exc:
        failure = f'{getattr(exc, "code", type(exc).__name__)}: {exc}'[:500]
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        current = session.get(PortfolioRun, run_id)
        if current is None or current.state != 'running' or current.attempt_id != attempt:
            return True
        current.elapsed_ms += int((time.monotonic() - started) * 1000)
        current.updated_at, current.attempt_id = utc_now(), None
        if queued_code != code_sha256():
            failure = 'CODE_VERSION_CHANGED: 发布前代码已变化'
        if current.cancel_requested or failure and failure.startswith('COMPUTE_CANCELLED:'):
            current.state, current.error = 'cancelled', 'CANCELLED_BY_USER'
        elif should_stop and should_stop() or failure and failure.startswith('COMPUTE_SHUTDOWN:'):
            current.state, current.error = 'paused', 'INTERRUPTED_SHUTDOWN'
        elif failure:
            current.state, current.error = 'failed', failure
        elif current.checkpoint_sha256 != prior_sha or current.input_sha256 != input_sha:
            current.state, current.error = 'failed', 'PORTFOLIO_CHECKPOINT_CORRUPT'
        else:
            next_state = result['checkpoint']
            chunk_sha = digest(result)
            session.add(PortfolioChunk(id=digest([run_id, ordinal]), run_id=run_id, ordinal=ordinal,
                start_cursor=checkpoint['cursor'], end_cursor=next_state['cursor'], prior_sha256=prior_sha,
                result_json=encoded, result_sha256=chunk_sha, created_at=utc_now()))
            current.checkpoint_json, current.checkpoint_sha256 = canonical(next_state), digest(next_state)
            current.completed_days, current.chunk_count = next_state['cursor'], ordinal + 1
            current.total_bytes += len(encoded.encode())
            current.summary_json = canonical(summary(context, next_state))
            current.error = None
            current.state = 'succeeded' if result['done'] else 'paused' if current.pause_requested else 'queued'
            if result['done']:
                session.flush()
                hashes = list(session.scalars(select(PortfolioChunk.result_sha256).where(PortfolioChunk.run_id == run_id).order_by(PortfolioChunk.ordinal)))
                current.result_sha256 = digest({'summary': json.loads(current.summary_json), 'chunks': hashes, 'input_sha256': input_sha})
        current.pause_requested = current.cancel_requested = 0
    return True
