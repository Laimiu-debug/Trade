"""Read-only position rehearsal. Market identity and price evidence are injected.

Snapshot and ledger baselines are mutually exclusive. A rehearsal never creates
an order, pending trade, asset snapshot, or fee setting.
"""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, money_text, price_text, price_units
from trade_app.reviews.service import validate_day
from trade_app.trading.domain import FeeRule, calculate_fees
from trade_app.trading.models import AssetSnapshot, CashFlow, SnapshotPosition, Trade
from trade_app.trading.real_fees import fee_settings
from trade_app.trading.service import account_or_error


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _cents(price: Decimal, quantity: int) -> int:
    return int((price * quantity * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def planning_baseline(session: Session, account_id: str, day: str, source: str,
                      *, symbol_key: Callable[[str], str]) -> dict:
    validate_day(day)
    account = account_or_error(session, account_id, real=True)
    if source not in ('snapshot', 'ledger'):
        raise TradeError('INVALID_PLAN_BASELINE', '请选择确认快照或已入账流水作为基准')
    flags, positions, facts = [], {}, []
    cash = None
    snapshot_id = snapshot_revision = None
    def add(symbol, name, quantity):
        key = symbol_key(symbol)
        row = positions.setdefault(key, {'code': symbol, 'name': name, 'qty': 0, 'key': key})
        row['qty'] += quantity
        if name:
            row['name'] = name
        return row
    if source == 'snapshot':
        snapshot = session.scalar(select(AssetSnapshot).where(AssetSnapshot.account_id == account_id,
                                                              AssetSnapshot.snap_date == day))
        if snapshot:
            snapshot_id, snapshot_revision = snapshot.id, snapshot.revision
            cash = snapshot.available_cash_minor
            rows = session.scalars(select(SnapshotPosition).where(SnapshotPosition.snapshot_id == snapshot.id)
                                   .order_by(SnapshotPosition.sequence)).all()
            for row in rows:
                add(row.symbol, row.name, row.quantity)
            if not rows and (snapshot.position_value_minor or (
                    snapshot.total_assets_minor - cash if cash is not None else 0)):
                flags.append('snapshot_positions_missing')
            facts = [{'id': snapshot.id, 'revision': snapshot.revision}]
            if cash is None:
                flags.append('snapshot_cash_missing')
        else:
            flags.append('snapshot_missing')
    else:
        flows = session.scalars(select(CashFlow).where(CashFlow.account_id == account_id,
            CashFlow.flow_date <= day, CashFlow.voided_at.is_(None)).order_by(CashFlow.flow_date, CashFlow.id)).all()
        trades = session.scalars(select(Trade).where(Trade.account_id == account_id,
            Trade.trade_date <= day, Trade.voided_at.is_(None)).order_by(Trade.trade_date, Trade.sequence, Trade.id)).all()
        initial = any(row.kind == 'initial' for row in flows)
        inferred = sum(row.amount_minor if row.kind in ('initial', 'deposit') else -row.amount_minor for row in flows)
        for row in trades:
            gross = _cents(Decimal(row.price_units) / 10000, row.quantity)
            inferred += (-gross - row.fee_minor) if row.side == 'buy' else (gross - row.fee_minor)
            item = add(row.symbol, row.name, row.quantity if row.side == 'buy' else -row.quantity)
            if item['qty'] < 0:
                flags.append('oversold_trade_history')
        if initial:
            cash = inferred
        else:
            flags.append('initial_capital_missing')
        if inferred < 0:
            flags.append('negative_inferred_cash')
        facts = [{'type': 'flow', 'id': row.id, 'revision': row.revision} for row in flows]
        facts += [{'type': 'trade', 'id': row.id, 'revision': row.revision} for row in trades]
    result = {'account_id': account_id, 'as_of_date': day, 'source': source,
        'account_input_revision': account.input_revision, 'snapshot_id': snapshot_id,
        'snapshot_revision': snapshot_revision, 'cash': money_text(cash),
        'positions': [row for _, row in sorted(positions.items()) if row['qty'] > 0],
        'quality_flags': sorted(set(flags)), 'facts': facts,
        'fee_settings': fee_settings(session, account_id),
        'method': ('仅使用指定日期的人工确认快照，缺现金不由资产倒推。' if source == 'snapshot'
                   else '仅按截至复盘日期的有效入账初始资金、出入金和成交（含全部实付费用）推导，未与快照相加。')}
    if len(result['positions']) > 500:
        raise TradeError('PLANNING_POSITION_LIMIT', '持仓超过 500 个，请缩小复盘基准')
    result['sha256'] = digest(result)
    return result


def preview_rehearsal(baseline: dict, rows: list[dict], quotes: dict, *,
                       symbol_key: Callable[[str], str], expected_baseline_sha256: str | None = None) -> dict:
    if expected_baseline_sha256 and expected_baseline_sha256 != baseline['sha256']:
        raise TradeError('PLAN_BASELINE_CHANGED', '持仓、现金或费用设置已变化，请刷新基准后重算', 409)
    if not isinstance(rows, list) or len(rows) > 100:
        raise TradeError('INVALID_REHEARSAL', '一次预演最多 100 个目标持仓')
    targets = {}
    for item in rows:
        if (not isinstance(item, dict) or set(item) - {'code', 'name', 'qty', 'note', 'price'}
                or not isinstance(item.get('code'), str) or not item['code'].strip()
                or len(item['code']) > 80 or type(item.get('qty')) is not int
                or not 0 <= item['qty'] <= 100_000_000):
            raise TradeError('INVALID_REHEARSAL', '持仓预演需填写有效代码与非负整数数量')
        key = symbol_key(item['code'])
        if key in targets:
            raise TradeError('DUPLICATE_REHEARSAL_SYMBOL', '目标持仓有重复代码或同一标的的不同写法')
        targets[key] = item
    current = {item['key']: item for item in baseline['positions']}
    config = baseline['fee_settings']['config']
    rule = FeeRule(**{key: Decimal(value) for key, value in config.items()})
    results, flags = [], set(baseline['quality_flags'])
    cash_delta, costs, position_value = 0, 0, 0
    cash_prices_known, all_marks_known = True, True
    buy_debit = sell_credit = 0
    for key in sorted(set(current) | set(targets)):
        original, planned = current.get(key), targets.get(key)
        old = original['qty'] if original else 0
        qty = planned['qty'] if planned else old  # Removing a form row never implies liquidation.
        delta = qty - old
        source = planned or original
        evidence = dict(quotes.get(key, {}))
        manual = planned.get('price') if planned else None
        if manual is not None and str(manual).strip():
            evidence = {'price': price_text(price_units(manual)), 'source': 'manual_assumption',
                        'quote_date': None, 'dataset_id': None, 'quality_flags': []}
        raw_price = evidence.get('price')
        price = Decimal(price_units(raw_price)) / 10000 if raw_price is not None else None
        row_flags = set(evidence.get('quality_flags', []))
        if delta > 0 and delta % 100:
            row_flags.add('buy_quantity_not_100_multiple')
        if delta < 0 and abs(delta) % 100 and qty:
            row_flags.add('partial_sell_not_100_multiple')
        gross = fee = movement = mark = None
        breakdown = None
        if price is not None:
            gross = _cents(price, abs(delta))
            mark = _cents(price, qty)
            if delta:
                calculated = calculate_fees(price, abs(delta), 'buy' if delta > 0 else 'sell', rule)
                fee = _cents(calculated.total, 1)
                breakdown = {key: str(getattr(calculated, key)) for key in ('commission', 'stamp', 'transfer')}
                movement = (-gross if delta > 0 else gross) - fee
                costs += fee
                cash_delta += movement
                if delta > 0:
                    buy_debit -= movement
                else:
                    sell_credit += movement
            else:
                fee = movement = 0
            position_value += mark
        else:
            if delta:
                cash_prices_known = False
                row_flags.add('execution_price_missing')
            if qty:
                all_marks_known = False
                row_flags.add('valuation_price_missing')
            if not delta:
                gross = fee = movement = 0
            if not qty:
                mark = 0
        flags.update(row_flags)
        results.append({'code': source['code'], 'name': source.get('name', ''), 'key': key,
            'current_qty': old, 'target_qty': qty, 'quantity_delta': delta,
            'action': 'buy' if delta > 0 else 'sell' if delta < 0 else 'hold',
            'explicit_target': planned is not None, 'price': price_text(price_units(raw_price)) if price is not None else None,
            'price_evidence': evidence or None, 'gross': money_text(gross), 'fees': money_text(fee),
            'fee_breakdown': breakdown, 'cash_delta': money_text(movement), 'position_value': money_text(mark),
            'quality_flags': sorted(row_flags)})
    baseline_cash = int(Decimal(baseline['cash']) * 100) if baseline['cash'] is not None else None
    baseline_known = not set(baseline['quality_flags']) & {
        'snapshot_missing', 'snapshot_positions_missing', 'initial_capital_missing', 'oversold_trade_history'}
    remaining = baseline_cash + cash_delta if baseline_cash is not None and cash_prices_known and baseline_known else None
    cash_status = 'unknown' if remaining is None else 'insufficient' if remaining < 0 else 'sufficient'
    if remaining is not None and remaining < 0:
        flags.add('insufficient_cash')
    relies_on_sells = remaining is not None and baseline_cash - buy_debit < 0 and sell_credit > 0
    if relies_on_sells:
        flags.add('sell_proceeds_required_first')
    result = {'baseline': baseline, 'rows': results, 'cash_status': cash_status,
        'remaining_cash': money_text(remaining), 'cash_delta': money_text(cash_delta) if cash_prices_known else None,
        'estimated_fees': money_text(costs) if cash_prices_known else None,
        'known_position_value': money_text(position_value),
        'position_value': money_text(position_value) if all_marks_known and baseline_known else None,
        'projected_total': money_text(remaining + position_value) if remaining is not None and all_marks_known else None,
        'requires_sell_proceeds': relies_on_sells, 'quality_flags': sorted(flags),
        'method': '按每只标的一笔数量差额估费；未列出的基准持仓继续持有，清仓须显式填 0。现金为全部买卖完成后的估算，依赖卖出回款时须先卖后买。100 股单位仅作现有模拟规则提示，未验证板块特殊规则、涨跌停、停牌或成交；不会生成订单或修改账本。'}
    result['sha256'] = digest(result)
    return result
