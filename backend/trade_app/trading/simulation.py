"""Persistent simulation; manual fills and frozen opening-price matches share execution rules."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.symbols import market_symbol_aliases, market_symbol_key, validated_market_symbol_key
from trade_app.platform.types import TradeError, decimal_value, money_minor, money_text, new_id, price_text, price_units, utc_now
from trade_app.trading.domain import DEFAULT_FEE_CONFIG, FeeRule, FillState, LotBalance, apply_fill, calculate_fees, consume_fifo, validate_order_quantity
from trade_app.trading.models import Account
from trade_app.trading.sim_models import SimFill, SimLot, SimOrder, SimWallet
from trade_app.trading.service import account_data, account_or_error


DEFAULT_CONFIG = {**DEFAULT_FEE_CONFIG, 'cash_buffer': '0.00', 'slippage_rate': '0'}


def validate_day(raw: str) -> str:
    try:
        day = date.fromisoformat(raw)
        if day.isoformat() != raw:
            raise ValueError(raw)
    except ValueError as exc:
        raise TradeError('INVALID_DATE', '日期必须使用 YYYY-MM-DD 格式') from exc
    return raw


def normalized_config(raw: dict) -> dict[str, str]:
    if set(raw) - {'slippage_rate'} != set(DEFAULT_CONFIG) - {'slippage_rate'}:
        raise TradeError('INVALID_SIM_CONFIG', '模拟费用配置字段不完整')
    result: dict[str, str] = {}
    for key in ('commission_rate', 'sell_stamp_rate', 'transfer_rate'):
        value = decimal_value(raw[key], key)
        if value < 0 or value > Decimal('0.01'):
            raise TradeError('INVALID_SIM_CONFIG', f'{key} 必须位于 0 至 1%')
        result[key] = format(value, 'f')
    for key in ('minimum_commission', 'cash_buffer'):
        result[key] = money_text(money_minor(raw[key], key, allow_zero=True))
    slippage = decimal_value(raw.get('slippage_rate', '0'), '滑点比例')
    if not Decimal(0) <= slippage <= Decimal('0.05'):
        raise TradeError('INVALID_SIM_CONFIG', '滑点比例必须位于 0 至 5%')
    result['slippage_rate'] = format(slippage, 'f')
    return result


def fee_rule(config: dict) -> FeeRule:
    return FeeRule(commission_rate=Decimal(config['commission_rate']),
                   minimum_commission=Decimal(config['minimum_commission']),
                   sell_stamp_rate=Decimal(config['sell_stamp_rate']),
                   transfer_rate=Decimal(config['transfer_rate']))


def minor(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def sim_account(session: Session, account_id: str, *, mutable: bool = False) -> tuple[Account, SimWallet]:
    account = account_or_error(session, account_id)
    if account.kind != 'sim':
        raise TradeError('SIM_ACCOUNT_NOT_FOUND', '模拟账户不存在', 404)
    wallet = session.get(SimWallet, account_id)
    if wallet is None:
        raise TradeError('SIM_ACCOUNT_NOT_FOUND', '模拟账户状态不存在', 404)
    if mutable and wallet.frozen:
        raise TradeError('SIM_ACCOUNT_FROZEN', '该模拟账户是只读恢复点；请先恢复为可操作账户', 409)
    return account, wallet


def audit(session: Session, account: Account, entity_type: str, entity_id: str,
          operation: str, before: dict | None, after: dict | None) -> None:
    account.input_revision += 1
    session.add(AuditEvent(id=new_id(), account_id=account.id, entity_type=entity_type,
                           entity_id=entity_id, operation=operation,
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(after, ensure_ascii=False) if after else None,
                           created_at=utc_now()))


def create_sim_account(session: Session, body: dict) -> dict:
    name = body['name'].strip()
    if not name:
        raise TradeError('INVALID_ACCOUNT_NAME', '账户名称不能为空')
    amount = money_minor(body['initial_capital'], '初始资金')
    start_date = validate_day(str(body['start_date']))
    config = normalized_config(body.get('config') or DEFAULT_CONFIG)
    account = Account(id=new_id(), name=name, kind='sim', currency='CNY',
                      input_revision=0, created_at=utc_now())
    session.add(account)
    session.add(SimWallet(account_id=account.id, initial_minor=amount, cash_minor=amount,
                          as_of_date=start_date, config_json=json.dumps(config, sort_keys=True),
                          config_version=1, revision=1, frozen=0,
                          reset_group_id=account.id))
    session.flush()
    data = account_data(account)
    audit(session, account, 'sim_account', account.id, 'create', None,
          {'account': data, 'initial_capital': money_text(amount), 'start_date': start_date})
    return account_data(account)


def update_config(session: Session, account_id: str, body: dict) -> dict:
    account, wallet = sim_account(session, account_id, mutable=True)
    if wallet.config_version != body['expected_version']:
        raise TradeError('REVISION_CONFLICT', '费用配置版本已变化', 409)
    config = normalized_config(body['config'])
    before = {'version': wallet.config_version, 'config': json.loads(wallet.config_json)}
    wallet.config_json = json.dumps(config, sort_keys=True)
    wallet.config_version += 1
    wallet.revision += 1
    after = {'version': wallet.config_version, 'config': config}
    audit(session, account, 'sim_config', account_id, 'update', before, after)
    return after


def order_data(row: SimOrder) -> dict:
    return {'id': row.id, 'account_id': row.account_id, 'symbol': row.symbol,
            'side': row.side, 'quantity': row.quantity, 'limit_price': price_text(row.limit_price_units),
            'signal_date': row.signal_date, 'submit_date': row.submit_date,
            'status': row.status, 'reserved_cash': money_text(row.reserve_minor),
            'config_version': row.config_version, 'revision': row.revision,
            'created_at': row.created_at,
            'legacy_origin': json.loads(row.legacy_origin_json) if row.legacy_origin_json else None}


def fill_data(row: SimFill) -> dict:
    return {'id': row.id, 'order_id': row.order_id, 'account_id': row.account_id,
            'fill_date': row.fill_date, 'fill_price': price_text(row.price_units),
            'gross': money_text(row.gross_minor), 'commission': money_text(row.commission_minor),
            'stamp': money_text(row.stamp_minor), 'transfer': money_text(row.transfer_minor),
            'realized_pnl': money_text(row.realized_pnl_minor), 'price_source': row.price_source,
            'buy_allocations': json.loads(row.allocations_json) if row.allocations_json is not None else None}


def list_orders(session: Session, account_id: str) -> list[dict]:
    sim_account(session, account_id)
    return [order_data(row) for row in session.scalars(select(SimOrder).where(
        SimOrder.account_id == account_id).order_by(SimOrder.created_at, SimOrder.id))]


def list_fills(session: Session, account_id: str) -> list[dict]:
    sim_account(session, account_id)
    return [fill_data(row) for row in session.scalars(select(SimFill).where(
        SimFill.account_id == account_id).order_by(SimFill.fill_date, SimFill.created_at, SimFill.id))]


def pending_orders(session: Session, account_id: str) -> list[SimOrder]:
    return list(session.scalars(select(SimOrder).where(
        SimOrder.account_id == account_id, SimOrder.status == 'pending')))


def _symbol_lots(session: Session, account_id: str, symbol: str) -> list[SimLot]:
    return list(session.scalars(select(SimLot).where(
        SimLot.account_id == account_id, SimLot.remaining_qty > 0,
        func.upper(func.trim(SimLot.symbol)).in_(market_symbol_aliases(symbol))).order_by(
            SimLot.acquired_date, SimLot.created_at, SimLot.id)))


def portfolio(session: Session, account_id: str) -> dict:
    _account, wallet = sim_account(session, account_id)
    pending = pending_orders(session, account_id)
    reserved_cash = sum(row.reserve_minor for row in pending if row.side == 'buy')
    reserved_sell: dict[str, int] = defaultdict(int)
    for row in pending:
        if row.side == 'sell':
            reserved_sell[market_symbol_key(row.symbol)] += row.quantity
    lots = list(session.scalars(select(SimLot).where(
        SimLot.account_id == account_id, SimLot.remaining_qty > 0).order_by(
            SimLot.symbol, SimLot.acquired_date, SimLot.created_at, SimLot.id)))
    positions: dict[str, dict] = {}
    for lot in lots:
        position = positions.setdefault(market_symbol_key(lot.symbol), {'symbol': lot.symbol, 'quantity': 0,
                                                       'sellable_quantity': 0, 'cost_minor': 0})
        position['quantity'] += lot.remaining_qty
        position['cost_minor'] += lot.cost_minor
        if lot.acquired_date < wallet.as_of_date:
            position['sellable_quantity'] += lot.remaining_qty
    result_positions = []
    for key, position in sorted(positions.items(), key=lambda item: item[1]['symbol']):
        position['sellable_quantity'] = max(0, position['sellable_quantity'] - reserved_sell[key])
        position['cost_basis'] = money_text(position.pop('cost_minor'))
        result_positions.append(position)
    return {'account_id': account_id, 'as_of_date': wallet.as_of_date,
            'frozen': bool(wallet.frozen), 'reset_group_id': wallet.reset_group_id,
            'initial_capital': money_text(wallet.initial_minor), 'cash': money_text(wallet.cash_minor),
            'reserved_cash': money_text(reserved_cash),
            'available_cash': money_text(wallet.cash_minor - reserved_cash),
            'config': {'slippage_rate': '0', **json.loads(wallet.config_json)}, 'config_version': wallet.config_version,
            'wallet_revision': wallet.revision, 'positions': result_positions,
            'valuation_quality': 'missing_quotes'}


def create_order(session: Session, account_id: str, body: dict) -> dict:
    account, wallet = sim_account(session, account_id, mutable=True)
    signal_day = validate_day(str(body['signal_date']))
    submit_day = validate_day(str(body['submit_date']))
    if signal_day > submit_day or submit_day != wallet.as_of_date:
        raise TradeError('INVALID_ORDER_DATE', '信号日期不得晚于提交日期，提交日期须等于模拟时钟')
    symbol = body['symbol'].strip().upper()
    if not symbol:
        raise TradeError('INVALID_SYMBOL', '证券代码不能为空')
    symbol_key = validated_market_symbol_key(symbol)
    quantity = body['quantity']
    validate_order_quantity(quantity, body['side'])
    units = price_units(body['limit_price'])
    price = Decimal(units) / 10_000
    config = json.loads(wallet.config_json)
    fees = calculate_fees(price, quantity, body['side'], fee_rule(config))
    pending = pending_orders(session, account_id)
    reserve_minor = 0
    if body['side'] == 'buy':
        reserve_minor = minor(price * quantity + fees.total)
        committed = sum(row.reserve_minor for row in pending if row.side == 'buy')
        buffer = money_minor(config['cash_buffer'], '现金缓冲', allow_zero=True)
        if reserve_minor + committed + buffer > wallet.cash_minor:
            raise TradeError('INSUFFICIENT_CASH', '可用模拟资金不足')
    else:
        lots = _symbol_lots(session, account_id, symbol)
        sellable = sum(row.remaining_qty for row in lots if row.acquired_date < wallet.as_of_date)
        committed = sum(row.quantity for row in pending if row.side == 'sell' and market_symbol_key(row.symbol) == symbol_key)
        if quantity + committed > sellable:
            raise TradeError('INSUFFICIENT_SELLABLE', '可卖数量不足（含待成交委托）')
    now = utc_now()
    row = SimOrder(id=new_id(), account_id=account_id, symbol=symbol, side=body['side'],
                   quantity=quantity, limit_price_units=units, signal_date=signal_day,
                   submit_date=submit_day, status='pending', reserve_minor=reserve_minor,
                   config_json=wallet.config_json, config_version=wallet.config_version,
                   revision=1, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    wallet.revision += 1
    data = order_data(row)
    audit(session, account, 'sim_order', row.id, 'create', None, data)
    return data


def _order(session: Session, account_id: str, order_id: str) -> tuple[Account, SimWallet, SimOrder]:
    account, wallet = sim_account(session, account_id, mutable=True)
    row = session.get(SimOrder, order_id)
    if row is None or row.account_id != account_id:
        raise TradeError('SIM_ORDER_NOT_FOUND', '模拟委托不存在', 404)
    return account, wallet, row


def cancel_order(session: Session, account_id: str, order_id: str, expected_revision: int) -> dict:
    account, wallet, row = _order(session, account_id, order_id)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '委托版本已变化', 409)
    if row.status != 'pending':
        raise TradeError('ORDER_FINAL', '委托已结束，无法撤销', 409)
    before = order_data(row)
    row.status = 'cancelled'
    row.revision += 1
    row.updated_at = utc_now()
    wallet.revision += 1
    data = order_data(row)
    audit(session, account, 'sim_order', row.id, 'cancel', before, data)
    return data


def settle(session: Session, account_id: str, to_date: str) -> dict:
    account, wallet = sim_account(session, account_id, mutable=True)
    to_date = validate_day(to_date)
    if to_date < wallet.as_of_date:
        raise TradeError('CLOCK_REWIND', '模拟时钟不能倒退')
    if to_date == wallet.as_of_date:
        return {**portfolio(session, account_id), 'already_settled': True}
    before = {'as_of_date': wallet.as_of_date}
    wallet.as_of_date = to_date
    wallet.revision += 1
    audit(session, account, 'sim_wallet', account_id, 'settle', before,
          {'as_of_date': to_date})
    return {**portfolio(session, account_id), 'already_settled': False}


def fill_order(session: Session, account_id: str, order_id: str, body: dict,
               *, price_source: str = 'manual') -> dict:
    account, wallet, row = _order(session, account_id, order_id)
    if row.revision != body['expected_revision']:
        raise TradeError('REVISION_CONFLICT', '委托版本已变化', 409)
    if row.status != 'pending':
        raise TradeError('ORDER_FINAL', '委托已结束，不能重复成交', 409)
    validate_order_quantity(row.quantity, row.side)
    fill_day = validate_day(str(body['fill_date']))
    if fill_day != wallet.as_of_date or fill_day < row.submit_date:
        raise TradeError('INVALID_FILL_DATE', '成交日期须等于模拟时钟且不早于提交日期')
    units = price_units(body['fill_price'])
    if row.side == 'buy' and units > row.limit_price_units:
        raise TradeError('LIMIT_NOT_MET', '买入成交价高于限价')
    if row.side == 'sell' and units < row.limit_price_units:
        raise TradeError('LIMIT_NOT_MET', '卖出成交价低于限价')
    price = Decimal(units) / 10_000
    fees = calculate_fees(price, row.quantity, row.side, fee_rule(json.loads(row.config_json)))
    lots = _symbol_lots(session, account_id, row.symbol)
    eligible = sum(lot.remaining_qty for lot in lots if lot.acquired_date < fill_day)
    state = FillState(cash=Decimal(wallet.cash_minor) / 100,
                      quantity=sum(lot.remaining_qty for lot in lots),
                      sellable_quantity=eligible,
                      cost_basis=Decimal(sum(lot.cost_minor for lot in lots)) / 100)
    next_state = apply_fill(state, side=row.side, price=price, quantity=row.quantity, fees=fees)
    if next_state.cash < 0:
        raise TradeError('INSUFFICIENT_CASH', '模拟账户现金不足')
    # The order's reservation is released by the state transition. Other orders retain priority.
    if row.side == 'buy':
        other_reserved = sum(item.reserve_minor for item in pending_orders(session, account_id)
                             if item.id != row.id and item.side == 'buy')
        if minor(next_state.cash) < other_reserved:
            raise TradeError('INSUFFICIENT_CASH', '成交会占用其他待成交委托的预留资金')
    else:
        others = sum(item.quantity for item in pending_orders(session, account_id)
                     if item.id != row.id and item.side == 'sell' and market_symbol_key(item.symbol) == market_symbol_key(row.symbol))
        if eligible - row.quantity < others:
            raise TradeError('INSUFFICIENT_SELLABLE', '成交会占用其他卖出委托的预留数量')
    gross = minor(price * row.quantity)
    commission, stamp, transfer = minor(fees.commission), minor(fees.stamp), minor(fees.transfer)
    realized: int | None = None
    fill_id = new_id()
    allocations = None
    if row.side == 'buy':
        session.add(SimLot(id=new_id(), account_id=account_id, symbol=row.symbol,
                           acquired_date=fill_day, quantity=row.quantity,
                           remaining_qty=row.quantity, cost_minor=gross + commission + stamp + transfer,
                           created_at=utc_now(), buy_fill_id=fill_id))
    else:
        changes, sold_cost = consume_fifo([
            LotBalance(lot.id, lot.acquired_date, lot.remaining_qty, lot.cost_minor) for lot in lots
        ], row.quantity, fill_day)
        allocations = []
        remaining_qty, remaining_gross, remaining_fees = row.quantity, gross, commission + stamp + transfer
        for lot in lots:
            if lot.id in changes:
                taken = lot.remaining_qty - changes[lot.id][0]
                removed_cost = lot.cost_minor - changes[lot.id][1]
                allocated_gross = int((Decimal(remaining_gross) * taken / remaining_qty).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                allocated_fees = int((Decimal(remaining_fees) * taken / remaining_qty).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                allocations.append({'lot_id': lot.id, 'buy_fill_id': lot.buy_fill_id,
                    'buy_date': lot.acquired_date, 'quantity': taken,
                    'cost_basis': money_text(removed_cost), 'sell_gross': money_text(allocated_gross),
                    'sell_fees': money_text(allocated_fees),
                    'realized_pnl': money_text(allocated_gross - allocated_fees - removed_cost)})
                remaining_qty -= taken
                remaining_gross -= allocated_gross
                remaining_fees -= allocated_fees
                lot.remaining_qty, lot.cost_minor = changes[lot.id]
        realized = gross - commission - stamp - transfer - sold_cost
    wallet.cash_minor = minor(next_state.cash)
    wallet.revision += 1
    before = order_data(row)
    row.status = 'filled'
    row.revision += 1
    row.updated_at = utc_now()
    fill = SimFill(id=fill_id, order_id=row.id, account_id=account_id, fill_date=fill_day,
                   price_units=units, gross_minor=gross, commission_minor=commission,
                   stamp_minor=stamp, transfer_minor=transfer, realized_pnl_minor=realized,
                   price_source=price_source, created_at=utc_now(),
                   allocations_json=json.dumps(allocations, ensure_ascii=False) if allocations is not None else None)
    session.add(fill)
    session.flush()
    data = {'order': order_data(row), 'fill': fill_data(fill), 'portfolio': portfolio(session, account_id)}
    audit(session, account, 'sim_order', row.id, 'fill', before, data['order'])
    return data


def reset_sim_account(session: Session, account_id: str, body: dict) -> dict:
    """Create a clean successor while keeping the entire old account as a read-only recovery point."""
    account, wallet = sim_account(session, account_id, mutable=True)
    if wallet.revision != body['expected_wallet_revision']:
        raise TradeError('REVISION_CONFLICT', '模拟账户版本已变化', 409)
    start_date = validate_day(str(body['start_date']))
    previous = {'account_id': account_id, 'wallet_revision': wallet.revision,
                'as_of_date': wallet.as_of_date, 'frozen': False}
    config = DEFAULT_CONFIG if body['reset_config'] else json.loads(wallet.config_json)
    successor = create_sim_account(session, {
        'name': account.name, 'initial_capital': money_text(wallet.initial_minor),
        'start_date': start_date, 'config': config})
    group = wallet.reset_group_id or account_id
    new_wallet = session.get(SimWallet, successor['id'])
    new_wallet.reset_group_id = group
    wallet.reset_group_id = group
    wallet.frozen = 1
    wallet.revision += 1
    audit(session, account, 'sim_account', account_id, 'freeze_for_reset', previous,
          {'account_id': account_id, 'wallet_revision': wallet.revision,
           'frozen': True, 'successor_account_id': successor['id']})
    return {'new_account': {**successor, 'frozen': False, 'reset_group_id': group},
            'recovery_account': {**account_data(account), 'frozen': True,
                                 'reset_group_id': group},
            'history_preserved': True}


def activate_sim_recovery(session: Session, account_id: str,
                          expected_wallet_revision: int) -> dict:
    """Switch the one mutable account in a reset lineage; retain every account's history."""
    account, wallet = sim_account(session, account_id)
    if wallet.revision != expected_wallet_revision:
        raise TradeError('REVISION_CONFLICT', '模拟账户版本已变化', 409)
    group = wallet.reset_group_id or account_id
    lineage = list(session.scalars(select(SimWallet).where(SimWallet.reset_group_id == group)))
    if not lineage:
        raise TradeError('RECOVERY_GROUP_MISSING', '未找到模拟账户恢复关系', 409)
    active = [item for item in lineage if not item.frozen]
    if len(active) != 1:
        raise TradeError('RECOVERY_GROUP_INVALID', '模拟账户恢复关系不唯一', 409)
    if active[0].account_id == account_id:
        return {'active_account_id': account_id, 'already_active': True,
                'reset_group_id': group}
    previous_id = active[0].account_id
    for item in (active[0], wallet):
        owner = session.get(Account, item.account_id)
        before = {'frozen': bool(item.frozen), 'wallet_revision': item.revision}
        item.frozen = 0 if item.account_id == account_id else 1
        item.revision += 1
        audit(session, owner, 'sim_account', item.account_id, 'activate_recovery', before,
              {'frozen': bool(item.frozen), 'wallet_revision': item.revision,
               'active_account_id': account_id})
    return {'active_account_id': account_id, 'previous_active_account_id': previous_id,
            'already_active': False, 'reset_group_id': group}
