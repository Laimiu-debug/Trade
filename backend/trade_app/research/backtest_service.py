"""Durable, repeatable single-symbol backtest jobs."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from trade_app.market.models import MarketDataset
from trade_app.market.service import get_dataset
from trade_app.platform.types import TradeError, decimal_value, money_minor, new_id, utc_now
from trade_app.platform.compute_process import (ComputeBudget, ComputeProcessError,
                                               DEFAULT_COMPUTE_BUDGET, run_json_process)
from trade_app.research.backtest_domain import CALCULATION_VERSION, EXECUTION_VERSION
from trade_app.research.backtest_models import BacktestRun
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES
from trade_app.research.wyckoff_strategy import WYCKOFF_STRATEGY_IDS
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.service import strategy_catalog, normalize_strategy_params
from trade_app.trading.simulation import DEFAULT_CONFIG, normalized_config


def code_sha256() -> str:
    backend = Path(__file__).resolve().parents[1]
    files = [backend / path for path in (
        'research/backtest_service.py', 'research/backtest_domain.py', 'research/backtest_worker.py',
        'research/backtest_analytics.py',
        'platform/compute_process.py', 'platform/runtime.py', 'research/domain.py',
        'research/runtime.py', 'research/service.py', 'research/legacy_catalog.json',
        'research/classic_domain.py', 'research/classic_catalog.py',
        'research/trend_king_domain.py', 'research/limit_up_arb_domain.py', 'research/ths_volume_domain.py',
        'research/emotion_limit_up_domain.py', 'research/wulong_domain.py', 'research/wulong_universe.py',
        'research/force_rhythm_domain.py', 'research/wyckoff_domain.py', 'research/wyckoff_strategy.py',
        'research/event_profiles.py', 'research/event_profile_service.py', 'research/event_profile_catalog.json',
        'trading/domain.py', 'trading/simulation.py', 'market/domain.py',
        'market/service.py', 'market/symbols.py', 'platform/symbols.py', 'platform/types.py')]
    return hashlib.sha256(b''.join(file.read_bytes() for file in files)).hexdigest()


def _data(row: BacktestRun, *, detail: bool = False) -> dict:
    result = json.loads(row.result_json) if row.result_json else None
    return {'id': row.id, 'dataset_id': row.dataset_id,
            'strategy_id': row.strategy_id, 'strategy_version': row.strategy_version,
            'execution_version': row.execution_version,
            'calculation_version': row.calculation_version,
            'code_sha256': row.code_sha256,
            'attempt_number': row.attempt_number, 'result_sha256': row.result_sha256,
            'capabilities': {'cancel': row.state in ('queued', 'running'),
                             'retry': row.state in ('failed', 'cancelled'),
                             'pause': False, 'resume': False},
            'params': json.loads(row.params_json), 'config': json.loads(row.config_json),
            'state': 'cancelling' if row.state == 'running' and row.cancel_requested else row.state,
            'error': row.error, 'created_at': row.created_at, 'updated_at': row.updated_at,
            'summary': {key: result[key] for key in (
                'ending_assets', 'total_return', 'max_drawdown', 'trade_count', 'win_rate',
                'quality_flags') if key in result} if result else None,
            **({'result': result} if detail else {})}


def create_backtest(session: Session, body: dict) -> dict:
    strategy_id = body['strategy_id']
    from trade_app.research.registry_service import require_enabled
    admission = require_enabled(session, [strategy_id])
    if strategy_id not in SINGLE_SYMBOL_STRATEGIES:
        raise TradeError('STRATEGY_NOT_MIGRATED', '该策略尚未完成新架构迁移', 409)
    if session.get(MarketDataset, body['dataset_id']) is None:
        raise TradeError('DATASET_NOT_FOUND', '行情数据集不存在', 404)
    params = normalize_strategy_params(strategy_id, body.get('params') or {})
    strategy_version = next(item['version'] for item in strategy_catalog() if item['id'] == strategy_id)
    event_profile = None
    if strategy_id in WYCKOFF_STRATEGY_IDS:
        event_profile = freeze_profile(session, body.get('event_profile_id'), body.get('event_profile_revision'))
    elif body.get('event_profile_id') is not None or body.get('event_profile_revision') is not None:
        raise TradeError('EVENT_PROFILE_UNSUPPORTED', '该策略不使用事件模板，请移除模板参数')
    fees = normalized_config(body.get('fee_config') or DEFAULT_CONFIG)
    initial_minor = money_minor(body['initial_capital'], '回测初始资金')
    position_pct = decimal_value(body['max_position_pct'], '最大仓位比例')
    if not Decimal(0) < position_pct <= Decimal(1):
        raise TradeError('INVALID_BACKTEST_CONFIG', '最大仓位比例必须大于 0 且不超过 1')
    if not 1 <= body['holding_bars'] <= 60:
        raise TradeError('INVALID_BACKTEST_CONFIG', '持有期必须是 1 至 60 根 K 线')
    exit_config = {}
    for key, maximum in (('stop_loss_pct', '0.5'), ('take_profit_pct', '1.5'), ('trailing_stop_pct', '0.5')):
        value = decimal_value(body.get(key, '0'), key)
        if not Decimal(0) <= value <= Decimal(maximum):
            raise TradeError('INVALID_BACKTEST_CONFIG', f'{key} 超出支持范围')
        exit_config[key] = format(value.normalize(), 'f')
    advanced = body.get('advanced_analysis', False)
    seed = body.get('analysis_seed', 20260926)
    iterations = body.get('analysis_iterations', 400)
    if (type(advanced) is not bool or type(seed) is not int or not 0 <= seed <= 4294967295
            or type(iterations) is not int or not 100 <= iterations <= 2000):
        raise TradeError('INVALID_ANALYSIS_CONFIG', '高级分析配置或随机种子超出支持范围')
    config = {'initial_capital': f'{Decimal(initial_minor) / 100:.2f}',
              'holding_bars': body['holding_bars'],
              'max_position_pct': format(position_pct, 'f'),
              'strict': body['strict'], 'fee_config': fees, **exit_config,
              'event_profile': event_profile,
              'advanced_analysis': advanced, 'analysis_seed': seed, 'analysis_iterations': iterations,
              'compute_budget': DEFAULT_COMPUTE_BUDGET.to_dict(), 'registry_admission': admission}
    identity = {'dataset_id': body['dataset_id'], 'strategy_id': strategy_id,
                'strategy_version': strategy_version,
                'execution_version': EXECUTION_VERSION,
                'calculation_version': CALCULATION_VERSION,
                'code_sha256': code_sha256(),
                'params': params, 'config': config}
    run_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    existing = session.get(BacktestRun, run_id)
    if existing:
        return _data(existing)
    # A bounded queue prevents accidental repeated submissions from monopolizing local work.
    active_count = session.scalar(select(func.count()).select_from(BacktestRun).where(
        BacktestRun.state.in_(('queued', 'running')))) or 0
    if active_count >= 10:
        raise TradeError('BACKTEST_QUEUE_FULL', '回测队列已满，请稍后重试', 429)
    now = utc_now()
    row = BacktestRun(id=run_id, dataset_id=body['dataset_id'], strategy_id=strategy_id,
                      strategy_version=strategy_version, execution_version=EXECUTION_VERSION,
                      calculation_version=CALCULATION_VERSION,
                      code_sha256=identity['code_sha256'],
                      params_json=json.dumps(params, sort_keys=True),
                      config_json=json.dumps(config, sort_keys=True),
                      state='queued', cancel_requested=0, result_json=None,
                      error=None, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _data(row)


def list_backtests(session: Session) -> list[dict]:
    return [_data(row) for row in session.scalars(select(BacktestRun).order_by(
        BacktestRun.created_at.desc(), BacktestRun.id).limit(100))]


def get_backtest(session: Session, run_id: str) -> dict:
    row = session.get(BacktestRun, run_id)
    if row is None:
        raise TradeError('BACKTEST_NOT_FOUND', '回测任务不存在', 404)
    return _data(row, detail=True)


def cancel_backtest(session: Session, run_id: str) -> dict:
    row = session.get(BacktestRun, run_id)
    if row is None:
        raise TradeError('BACKTEST_NOT_FOUND', '回测任务不存在', 404)
    if row.state == 'queued':
        row.state = 'cancelled'
    elif row.state == 'running':
        row.cancel_requested = 1
    elif row.state != 'cancelled':
        raise TradeError('BACKTEST_FINAL', '已完成的回测不能取消', 409)
    row.updated_at = utc_now()
    session.flush()
    return _data(row)


def retry_backtest(session: Session, run_id: str) -> dict:
    row = session.get(BacktestRun, run_id)
    if row is None:
        raise TradeError('BACKTEST_NOT_FOUND', '回测任务不存在', 404)
    if row.state not in ('failed', 'cancelled'):
        raise TradeError('BACKTEST_NOT_RETRYABLE', '当前回测状态不能重试', 409)
    if row.code_sha256 != code_sha256():
        raise TradeError('CODE_VERSION_CHANGED', '回测算法代码已更新，请用原参数创建新任务', 409)
    active_count = session.scalar(select(func.count()).select_from(BacktestRun).where(
        BacktestRun.state.in_(('queued', 'running')))) or 0
    if active_count >= 10:
        raise TradeError('BACKTEST_QUEUE_FULL', '回测队列已满，请稍后重试', 429)
    row.state = 'queued'
    row.cancel_requested = 0
    row.error = None
    row.result_json = None
    row.result_sha256 = None
    row.attempt_id = None
    row.updated_at = utc_now()
    session.flush()
    return _data(row)


def recover_interrupted_backtests(factory: sessionmaker) -> None:
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for row in session.scalars(select(BacktestRun).where(BacktestRun.state == 'running')):
            row.state = 'cancelled' if row.cancel_requested else 'queued'
            row.cancel_requested = 0
            row.error = 'RECOVERED_AFTER_RESTART'
            row.attempt_id = None
            row.result_json = None
            row.result_sha256 = None
            row.updated_at = utc_now()


def process_one_backtest(factory: sessionmaker, data_dir: Path, *,
                         should_stop: Callable[[], bool] | None = None) -> bool:
    """Claim one durable attempt and publish only its complete, verified result."""
    if should_stop and should_stop():
        return False
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        if session.scalar(select(BacktestRun.id).where(BacktestRun.state == 'running').limit(1)):
            return False
        row = session.scalar(select(BacktestRun).where(BacktestRun.state == 'queued').order_by(
            BacktestRun.created_at, BacktestRun.id).limit(1))
        if row is None:
            return False
        row.state = 'running'
        row.attempt_id = attempt_id = new_id()
        row.attempt_number = (row.attempt_number or 0) + 1
        row.result_json = None
        row.result_sha256 = None
        row.error = None
        row.updated_at = utc_now()
        run_id = row.id
        dataset_id = row.dataset_id
        strategy_id = row.strategy_id
        params = json.loads(row.params_json)
        config = json.loads(row.config_json)
        queued_code_sha256 = row.code_sha256

    def stop_reason() -> str | None:
        if should_stop and should_stop():
            return 'shutdown'
        with factory() as session:
            current = session.get(BacktestRun, run_id)
            if (current is None or current.state != 'running' or current.attempt_id != attempt_id
                    or current.cancel_requested):
                return 'cancelled'
        return None

    def finish_error(exc: Exception) -> None:
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            current = session.get(BacktestRun, run_id)
            if current is None or current.attempt_id != attempt_id or current.state != 'running':
                return
            code = getattr(exc, 'code', type(exc).__name__)
            if current.cancel_requested or code == 'COMPUTE_CANCELLED':
                current.state, current.error = 'cancelled', None
            elif code == 'COMPUTE_SHUTDOWN':
                current.state, current.error = 'queued', 'INTERRUPTED_SHUTDOWN'
            else:
                current.state, current.error = 'failed', f'{code}: {exc}'[:500]
            current.cancel_requested = 0
            current.attempt_id = None
            current.result_json = None
            current.result_sha256 = None
            current.updated_at = utc_now()

    try:
        if queued_code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '回测算法代码已更新，请用原参数创建新任务', 409)
        with factory() as session:
            dataset = get_dataset(session, data_dir, dataset_id)
        reason = stop_reason()
        if reason:
            raise ComputeProcessError('COMPUTE_SHUTDOWN' if reason == 'shutdown' else 'COMPUTE_CANCELLED',
                                      '计算在启动前中断')
        computed = run_json_process('trade_app.research.backtest_worker', {
            'attempt_id': attempt_id, 'symbol': dataset['symbol'], 'bars': dataset['bars'],
            'strategy_id': strategy_id, 'params': params, 'config': config},
            budget=ComputeBudget(**config.get('compute_budget', DEFAULT_COMPUTE_BUDGET.to_dict())),
            stop_reason=stop_reason)
        envelope = computed.value
        if envelope.get('ok') is not True:
            error = envelope.get('error') or {}
            raise ComputeProcessError(str(error.get('code', 'COMPUTE_FAILED')),
                                      str(error.get('message', '计算进程返回失败'))[:500], computed.metrics)
        if envelope.get('attempt_id') != attempt_id or not isinstance(envelope.get('result'), dict):
            raise ComputeProcessError('COMPUTE_INVALID_RESULT', '计算结果与当前尝试不匹配')
        result = {**envelope['result'], 'compute': computed.metrics}
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        result_digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        if queued_code_sha256 != code_sha256():
            raise TradeError('CODE_VERSION_CHANGED', '计算期间算法代码已更新，请用原参数创建新任务', 409)
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(BacktestRun, run_id)
            if row is None or row.attempt_id != attempt_id or row.state != 'running':
                return True
            if row.cancel_requested:
                row.state = 'cancelled'
                row.result_json = None
                row.result_sha256 = None
                row.error = None
            elif should_stop and should_stop():
                row.state = 'queued'
                row.result_json = None
                row.result_sha256 = None
                row.error = 'INTERRUPTED_SHUTDOWN'
            else:
                row.state = 'succeeded'
                row.result_json = encoded
                row.result_sha256 = result_digest
                row.error = None
            row.attempt_id = None
            row.cancel_requested = 0
            row.updated_at = utc_now()
        return True
    except Exception as exc:
        finish_error(exc)
        return True
