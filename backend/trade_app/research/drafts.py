"""Account-scoped signal-to-simulation drafts and funding previews."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, money_minor, money_text, new_id, price_text, price_units, utc_now
from trade_app.research.draft_models import SimOrderDraft
from trade_app.research.models import ResearchRun
from trade_app.trading.domain import calculate_fees, validate_order_quantity
from trade_app.trading.simulation import audit, create_order, fee_rule, minor, portfolio, sim_account


def _data(row: SimOrderDraft) -> dict:
    return {'id': row.id, 'account_id': row.account_id,
            'source_run_id': row.source_run_id, 'symbol': row.symbol,
            'signal_date': row.signal_date, 'side': 'buy',
            'quantity': row.quantity, 'limit_price': price_text(row.limit_price_units),
            'status': row.status, 'order_id': row.order_id,
            'revision': row.revision, 'created_at': row.created_at,
            'updated_at': row.updated_at}


def _draft(session: Session, account_id: str, draft_id: str) -> SimOrderDraft:
    sim_account(session, account_id)
    row = session.get(SimOrderDraft, draft_id)
    if row is None or row.account_id != account_id:
        raise TradeError('SIM_DRAFT_NOT_FOUND', '模拟委托草稿不存在', 404)
    return row


def list_drafts(session: Session, account_id: str) -> list[dict]:
    sim_account(session, account_id)
    return [_data(row) for row in session.scalars(select(SimOrderDraft).where(
        SimOrderDraft.account_id == account_id).order_by(
        SimOrderDraft.created_at.desc(), SimOrderDraft.id))]


def size_draft(session: Session, account_id: str, body: dict) -> dict:
    """Quote a 100-share buy size with the account's current fee rules."""
    _account, wallet = sim_account(session, account_id)
    current = portfolio(session, account_id)
    units = price_units(body['limit_price'])
    price = Decimal(units) / 10_000
    available = money_minor(current['available_cash'], '可用资金', allow_zero=True)
    buffer = money_minor(json.loads(wallet.config_json)['cash_buffer'], '现金缓冲', allow_zero=True)
    spendable = max(0, available - buffer)
    mode = body['mode']
    if mode == 'lots':
        try:
            count = int(body['value'])
        except (ValueError, TypeError) as exc:
            raise TradeError('INVALID_DRAFT_SIZE', '手数须为正整数') from exc
        if str(count) != body['value'].strip() or not 1 <= count <= 1_000_000:
            raise TradeError('INVALID_DRAFT_SIZE', '手数须为 1 至 1000000 的整数')
        requested = count * 100
        budget = spendable
    elif mode == 'amount':
        budget = money_minor(body['value'], '目标金额')
        requested = None
    else:
        try:
            percent = Decimal(body['value'])
        except (InvalidOperation, TypeError) as exc:
            raise TradeError('INVALID_DRAFT_SIZE', '资金比例须为 0 至 100 的数字') from exc
        if not percent.is_finite() or not 0 < percent <= 100:
            raise TradeError('INVALID_DRAFT_SIZE', '资金比例须大于 0 且不超过 100')
        budget = int(Decimal(spendable) * percent / 100)
        requested = None

    rule = fee_rule(json.loads(wallet.config_json))

    def cost(quantity: int) -> tuple[int, int]:
        if quantity == 0:
            return 0, 0
        fees = minor(calculate_fees(price, quantity, 'buy', rule).total)
        return minor(price * quantity) + fees, fees

    cap = min(spendable, budget)
    high = min(1_000_000, cap // max(1, minor(price * 100)) + 1)
    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        if cost(mid * 100)[0] <= cap:
            low = mid
        else:
            high = mid - 1
    affordable = low * 100
    quantity = requested if requested is not None else affordable
    required, fees = cost(quantity)
    gap = max(0, required - spendable)
    return {'mode': mode, 'quantity': quantity, 'lot_size': 100,
            'limit_price': price_text(units), 'estimated_fees': money_text(fees),
            'required_cash': money_text(required), 'available_cash': current['available_cash'],
            'cash_buffer': money_text(buffer), 'spendable_cash': money_text(spendable),
            'budget': money_text(budget), 'cash_gap': money_text(gap),
            'max_affordable_quantity': affordable,
            'can_create': quantity > 0 and gap == 0 and not wallet.frozen,
            'wallet_revision': wallet.revision, 'config_version': wallet.config_version,
            'note': '按 100 股一手和当前模拟费率估算；草稿保存后仍须在模拟账户重新预览'}


def create_draft(session: Session, account_id: str, body: dict) -> dict:
    account, _wallet = sim_account(session, account_id, mutable=True)
    run = session.get(ResearchRun, body['source_run_id'])
    if run is None:
        raise TradeError('RESEARCH_RUN_NOT_FOUND', '研究运行不存在', 404)
    result = json.loads(run.result_json)
    if result.get('status') != 'computed' or result.get('signal') is not True:
        raise TradeError('SIGNAL_NOT_ACTIONABLE', '仅可将触发的观察信号加入草稿', 409)
    if result.get('draft_eligible') is False or (run.strategy_id == 'wulong_cluster_v1' and
            (result.get('candidate_universe_filter_run') is not True or
             result.get('universe', {}).get('passed') is not True)):
        raise TradeError('SIGNAL_NOT_ACTIONABLE', result.get('draft_block_reason') or '该信号尚未完成入池条件核对，不能加入模拟委托草稿', 409)
    candidate = result.get('candidate') or {}
    symbol = candidate.get('symbol')
    signal_date = result.get('source_date')
    if not symbol or not signal_date:
        raise TradeError('SIGNAL_INCOMPLETE', '观察信号缺少代码或日期', 409)
    units = price_units(body['limit_price'])
    quantity = body['quantity']
    validate_order_quantity(quantity, 'buy')
    existing = session.scalar(select(SimOrderDraft).where(
        SimOrderDraft.account_id == account_id,
        SimOrderDraft.source_run_id == run.id).limit(1))
    if existing is not None:
        if existing.status == 'submitted':
            raise TradeError('DRAFT_ALREADY_SUBMITTED', '该信号已提交模拟委托', 409)
        if existing.status == 'draft':
            return _data(existing)
        before = _data(existing)
        existing.status = 'draft'
        existing.quantity = quantity
        existing.limit_price_units = units
        existing.revision += 1
        existing.updated_at = utc_now()
        audit(session, account, 'sim_order_draft', existing.id, 'restore', before, _data(existing))
        return _data(existing)
    now = utc_now()
    row = SimOrderDraft(id=new_id(), account_id=account_id, source_run_id=run.id,
                        symbol=symbol, signal_date=signal_date,
                        quantity=quantity, limit_price_units=units, status='draft',
                        order_id=None, revision=1, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    data = _data(row)
    audit(session, account, 'sim_order_draft', row.id, 'create', None, data)
    return data


def update_draft(session: Session, account_id: str, draft_id: str, body: dict) -> dict:
    account, _wallet = sim_account(session, account_id, mutable=True)
    row = _draft(session, account_id, draft_id)
    if row.revision != body['expected_revision']:
        raise TradeError('REVISION_CONFLICT', '草稿版本已变化', 409)
    if row.status != 'draft':
        raise TradeError('DRAFT_FINAL', '草稿已结束，不能编辑', 409)
    validate_order_quantity(body['quantity'], 'buy')
    before = _data(row)
    row.quantity = body['quantity']
    row.limit_price_units = price_units(body['limit_price'])
    row.revision += 1
    row.updated_at = utc_now()
    data = _data(row)
    audit(session, account, 'sim_order_draft', row.id, 'update', before, data)
    return data


def preview_draft(session: Session, account_id: str, draft_id: str) -> dict:
    row = _draft(session, account_id, draft_id)
    validate_order_quantity(row.quantity, 'buy')
    _account, wallet = sim_account(session, account_id)
    current = portfolio(session, account_id)
    price = Decimal(row.limit_price_units) / 10_000
    fees = calculate_fees(price, row.quantity, 'buy', fee_rule(json.loads(wallet.config_json)))
    gross = minor(price * row.quantity)
    required = gross + minor(fees.total)
    available = money_minor(current['available_cash'], '可用资金', allow_zero=True)
    buffer = money_minor(json.loads(wallet.config_json)['cash_buffer'], '现金缓冲', allow_zero=True)
    gap = max(0, required + buffer - available)
    source_run = session.get(ResearchRun, row.source_run_id)
    quality = json.loads(source_run.result_json).get('quality_flags', []) if source_run else []
    decision_date = _decision_date(source_run)
    eligible_date = row.signal_date <= wallet.as_of_date and decision_date <= wallet.as_of_date
    return {'draft': _data(row), 'submit_date': wallet.as_of_date,
            'signal_date': row.signal_date, 'decision_date': decision_date,
            'decision_at': source_run.decision_at, 'eligible_date': eligible_date,
            'gross': money_text(gross), 'estimated_fees': money_text(minor(fees.total)),
            'required_cash': money_text(required), 'available_cash': current['available_cash'],
            'cash_buffer': money_text(buffer), 'cash_gap': money_text(gap),
            'can_submit': not wallet.frozen and row.status == 'draft' and eligible_date and gap == 0,
            'config_version': wallet.config_version,
            'wallet_revision': wallet.revision,
            'signal_quality_flags': quality,
            'note': '观察信号的决策日期按上海时区核对，模拟日期不得早于它；提交后下一日才可尝试开盘价撮合'}


def _decision_date(source_run: ResearchRun | None) -> str:
    if source_run is None:
        raise TradeError('RESEARCH_RUN_NOT_FOUND', '草稿来源研究记录不存在', 409)
    try:
        decision = datetime.fromisoformat(source_run.decision_at.replace('Z', '+00:00'))
        if decision.tzinfo is None:
            raise ValueError('missing timezone')
        return decision.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    except ValueError as exc:
        raise TradeError('SIGNAL_DECISION_TIME_INVALID', '来源决策时间缺少有效时区，请重新运行研究', 409) from exc


def submit_draft(session: Session, account_id: str, draft_id: str,
                 expected_revision: int, expected_wallet_revision: int,
                 expected_config_version: int) -> dict:
    account, wallet = sim_account(session, account_id, mutable=True)
    row = _draft(session, account_id, draft_id)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '草稿版本已变化', 409)
    if row.status != 'draft':
        raise TradeError('DRAFT_FINAL', '草稿已结束，不能重复提交', 409)
    if wallet.revision != expected_wallet_revision or wallet.config_version != expected_config_version:
        raise TradeError('PREVIEW_STALE', '模拟账户或费用已变化，请重新预览草稿', 409)
    if _decision_date(session.get(ResearchRun, row.source_run_id)) > wallet.as_of_date:
        raise TradeError('SIGNAL_DECISION_IN_FUTURE', '模拟日期早于研究决策日期，不能提前使用该信号', 409)
    before = _data(row)
    order = create_order(session, account_id, {
        'symbol': row.symbol, 'side': 'buy', 'quantity': row.quantity,
        'limit_price': price_text(row.limit_price_units),
        'signal_date': row.signal_date, 'submit_date': wallet.as_of_date})
    row.status = 'submitted'
    row.order_id = order['id']
    row.revision += 1
    row.updated_at = utc_now()
    data = _data(row)
    audit(session, account, 'sim_order_draft', row.id, 'submit', before, data)
    return {'draft': data, 'order': order}


def cancel_draft(session: Session, account_id: str, draft_id: str,
                 expected_revision: int) -> dict:
    account, _wallet = sim_account(session, account_id, mutable=True)
    row = _draft(session, account_id, draft_id)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '草稿版本已变化', 409)
    if row.status != 'draft':
        raise TradeError('DRAFT_FINAL', '草稿已结束，不能取消', 409)
    before = _data(row)
    row.status = 'cancelled'
    row.revision += 1
    row.updated_at = utc_now()
    data = _data(row)
    audit(session, account, 'sim_order_draft', row.id, 'cancel', before, data)
    return data


def preview_draft_batch(session: Session, account_id: str,
                        draft_ids: list[str]) -> dict:
    _account, wallet = sim_account(session, account_id)
    if not 1 <= len(draft_ids) <= 50 or len(draft_ids) != len(set(draft_ids)):
        raise TradeError('INVALID_DRAFT_BATCH', '批量草稿需要 1 至 50 个不重复 ID')
    previews = [preview_draft(session, account_id, draft_id) for draft_id in draft_ids]
    current = portfolio(session, account_id)
    total_required = sum(money_minor(item['required_cash'], '所需资金') for item in previews)
    available = money_minor(current['available_cash'], '可用资金', allow_zero=True)
    buffer = money_minor(json.loads(wallet.config_json)['cash_buffer'], '现金缓冲', allow_zero=True)
    gap = max(0, total_required + buffer - available)
    return {'account_id': account_id, 'drafts': previews,
            'total_required_cash': money_text(total_required),
            'available_cash': current['available_cash'],
            'cash_buffer': money_text(buffer), 'cash_gap': money_text(gap),
            'can_submit': not wallet.frozen and gap == 0 and all(item['eligible_date'] and item['draft']['status'] == 'draft'
                                           for item in previews),
            'wallet_revision': wallet.revision, 'config_version': wallet.config_version,
            'submit_date': wallet.as_of_date}


def submit_draft_batch(session: Session, account_id: str, body: dict) -> dict:
    _account, wallet = sim_account(session, account_id, mutable=True)
    if wallet.revision != body['expected_wallet_revision'] or wallet.config_version != body['expected_config_version']:
        raise TradeError('PREVIEW_STALE', '模拟账户或费用已变化，请重新预览草稿', 409)
    items = body['drafts']
    preview = preview_draft_batch(session, account_id, [item['id'] for item in items])
    if not preview['can_submit']:
        raise TradeError('BATCH_PREVIEW_NOT_READY', '草稿日期或合计资金不满足提交条件', 409)
    results = []
    for item in items:
        results.append(submit_draft(session, account_id, item['id'],
                                    item['expected_revision'], wallet.revision,
                                    wallet.config_version))
    return {'account_id': account_id, 'submit_date': wallet.as_of_date,
            'orders': [result['order'] for result in results],
            'drafts': [result['draft'] for result in results],
            'portfolio': portfolio(session, account_id)}
