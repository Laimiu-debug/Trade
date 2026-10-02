from __future__ import annotations

import json
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent, RebuildRequest
from trade_app.platform.symbols import validated_market_symbol_key
from trade_app.platform.types import TradeError, money_minor, money_text, new_id, price_text, price_units, utc_now
from trade_app.trading.models import Account, AssetSnapshot, CashFlow, SnapshotPosition, Trade
from trade_app.trading.nav import FlowFact, SnapshotFact, calculate_nav
from trade_app.trading.real_fees import resolve_trade_fee


def account_or_error(session: Session, account_id: str, *, real: bool = False) -> Account:
    account = session.get(Account, account_id)
    if account is None or (real and account.kind != "real"):
        raise TradeError("ACCOUNT_NOT_FOUND", "账户不存在或不支持该操作", 404)
    return account


def account_data(row: Account) -> dict:
    return {"id": row.id, "name": row.name, "kind": row.kind,
            "currency": row.currency, "input_revision": row.input_revision,
            "created_at": row.created_at}


def trade_data(row: Trade) -> dict:
    return {"id": row.id, "account_id": row.account_id, "trade_date": row.trade_date,
            "sequence": row.sequence, "symbol": row.symbol, "name": row.name,
            "side": row.side, "quantity": row.quantity, "price": price_text(row.price_units),
            "fee": money_text(row.fee_minor), "calculated_fee": money_text(row.calculated_fee_minor),
            "fee_source": row.fee_source, "fee_rule_version": row.fee_rule_version,
            "fee_breakdown": json.loads(row.fee_breakdown_json) if row.fee_breakdown_json else None,
            "note": row.note, "revision": row.revision}


def flow_data(row: CashFlow) -> dict:
    return {"id": row.id, "account_id": row.account_id, "flow_date": row.flow_date,
            "kind": row.kind, "amount": money_text(row.amount_minor),
            "note": row.note, "revision": row.revision}


def snapshot_data(row: AssetSnapshot) -> dict:
    return {"id": row.id, "account_id": row.account_id, "snap_date": row.snap_date,
            "total_assets": money_text(row.total_assets_minor),
            "available_cash": money_text(row.available_cash_minor),
            "position_value": money_text(row.position_value_minor),
            "positions": [{"symbol": item.symbol, "name": item.name,
                           "quantity": item.quantity,
                           "market_value": money_text(item.market_value_minor)}
                          for item in row.positions],
            "note": row.note, "revision": row.revision}


def list_accounts(session: Session) -> list[dict]:
    from trade_app.trading.sim_models import SimWallet
    wallets = {row.account_id: row for row in session.scalars(select(SimWallet))}
    return [{**account_data(row),
             'frozen': bool(wallets[row.id].frozen) if row.id in wallets else False,
             'reset_group_id': wallets[row.id].reset_group_id if row.id in wallets else None}
            for row in session.scalars(select(Account).order_by(Account.created_at, Account.id))]


def create_account(session: Session, *, name: str) -> dict:
    name = name.strip()
    if not name:
        raise TradeError("INVALID_ACCOUNT_NAME", "账户名称不能为空")
    row = Account(id=new_id(), name=name, kind="real", currency="CNY",
                  input_revision=0, created_at=utc_now())
    session.add(row)
    session.flush()
    session.add(AuditEvent(id=new_id(), account_id=row.id, entity_type="account", entity_id=row.id,
                           operation="create", before_json=None, after_json=json.dumps(account_data(row), ensure_ascii=False),
                           created_at=utc_now()))
    return account_data(row)


def mark_changed(session: Session, account: Account, day: str, kind: str,
                 entity_type: str, entity_id: str, before: dict | None, after: dict | None) -> None:
    account.input_revision += 1
    queued = session.scalar(select(RebuildRequest).where(
        RebuildRequest.account_id == account.id, RebuildRequest.state == "queued"
    ).order_by(RebuildRequest.created_at).limit(1))
    if queued:
        queued.earliest_date = min(queued.earliest_date, day)
        queued.target_revision = account.input_revision
        queued.change_kind = "multiple"
        queued.updated_at = utc_now()
    else:
        session.add(RebuildRequest(id=new_id(), account_id=account.id,
                                   earliest_date=day, target_revision=account.input_revision,
                                   change_kind=kind, state="queued", created_at=utc_now(), updated_at=utc_now()))
    session.add(AuditEvent(id=new_id(), account_id=account.id, entity_type=entity_type,
                           entity_id=entity_id, operation=kind,
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(after, ensure_ascii=False) if after else None,
                           created_at=utc_now()))


def _real_trade(session: Session, account_id: str, trade_id: str) -> tuple[Account, Trade]:
    account = account_or_error(session, account_id, real=True)
    row = session.get(Trade, trade_id)
    if row is None or row.account_id != account_id or row.voided_at is not None:
        raise TradeError("TRADE_NOT_FOUND", "交易记录不存在", 404)
    return account, row


def list_trades(session: Session, account_id: str) -> list[dict]:
    account_or_error(session, account_id, real=True)
    return [trade_data(row) for row in session.scalars(select(Trade).where(
        Trade.account_id == account_id, Trade.voided_at.is_(None)
    ).order_by(Trade.trade_date, Trade.sequence))]


def create_trade(session: Session, account_id: str, payload: dict) -> dict:
    account = account_or_error(session, account_id, real=True)
    day = str(payload["trade_date"])
    date.fromisoformat(day)
    last = session.scalar(select(func.max(Trade.sequence)).where(
        Trade.account_id == account_id, Trade.trade_date == day)) or 0
    now = utc_now()
    symbol = payload["symbol"].strip().upper()
    if not symbol:
        raise TradeError("INVALID_SYMBOL", "证券代码不能为空")
    validated_market_symbol_key(symbol)
    fee_fields = resolve_trade_fee(session, account_id, payload)
    row = Trade(id=new_id(), account_id=account_id, trade_date=day, sequence=last + 1,
                symbol=symbol, name=payload.get("name", "").strip(),
                side=payload["side"], quantity=payload["quantity"],
                price_units=price_units(payload["price"]),
                **fee_fields,
                note=payload.get("note", ""), revision=1, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    data = trade_data(row)
    mark_changed(session, account, day, "create", "trade", row.id, None, data)
    return data


def revise_trade(session: Session, account_id: str, trade_id: str, payload: dict) -> dict:
    account, row = _real_trade(session, account_id, trade_id)
    if row.revision != payload["expected_revision"]:
        raise TradeError("REVISION_CONFLICT", "该交易已被其他页面修改，请刷新后核对", 409)
    before = trade_data(row)
    old_day = row.trade_date
    row.trade_date = str(payload["trade_date"])
    if row.trade_date != old_day:
        row.sequence = (session.scalar(select(func.max(Trade.sequence)).where(
            Trade.account_id == account_id, Trade.trade_date == row.trade_date)) or 0) + 1
    symbol = payload["symbol"].strip().upper()
    if not symbol:
        raise TradeError("INVALID_SYMBOL", "证券代码不能为空")
    validated_market_symbol_key(symbol)
    row.symbol = symbol
    row.name = payload.get("name", "").strip()
    row.side = payload["side"]
    row.quantity = payload["quantity"]
    row.price_units = price_units(payload["price"])
    fee_fields = resolve_trade_fee(session, account_id, payload)
    for key, value in fee_fields.items():
        setattr(row, key, value)
    row.note = payload.get("note", "")
    row.revision += 1
    row.updated_at = utc_now()
    data = trade_data(row)
    mark_changed(session, account, min(old_day, row.trade_date), "update", "trade", row.id, before, data)
    return data


def void_trade(session: Session, account_id: str, trade_id: str, expected_revision: int) -> dict:
    account, row = _real_trade(session, account_id, trade_id)
    if row.revision != expected_revision:
        raise TradeError("REVISION_CONFLICT", "该交易已被其他页面修改，请刷新后核对", 409)
    before = trade_data(row)
    row.revision += 1
    row.voided_at = utc_now()
    row.updated_at = row.voided_at
    mark_changed(session, account, row.trade_date, "void", "trade", row.id, before, None)
    return {"deleted": True, "id": trade_id}


def list_flows(session: Session, account_id: str) -> list[dict]:
    account_or_error(session, account_id, real=True)
    return [flow_data(row) for row in session.scalars(select(CashFlow).where(
        CashFlow.account_id == account_id, CashFlow.voided_at.is_(None)
    ).order_by(CashFlow.flow_date, CashFlow.created_at, CashFlow.id))]


def nav_inputs(session: Session, account_id: str) -> tuple[list[FlowFact], list[SnapshotFact]]:
    flows = [FlowFact(id=row.id, date=row.flow_date, kind=row.kind,
                      amount_minor=row.amount_minor, order=row.created_at)
             for row in session.scalars(select(CashFlow).where(
                 CashFlow.account_id == account_id, CashFlow.voided_at.is_(None)))]
    snaps = [SnapshotFact(date=row.snap_date, total_assets_minor=row.total_assets_minor)
             for row in session.scalars(select(AssetSnapshot).where(AssetSnapshot.account_id == account_id))]
    return flows, snaps


def create_flow(session: Session, account_id: str, payload: dict) -> dict:
    account = account_or_error(session, account_id, real=True)
    now = utc_now()
    row = CashFlow(id=new_id(), account_id=account_id, flow_date=str(payload["flow_date"]),
                   kind=payload["kind"], amount_minor=money_minor(payload["amount"], "金额"),
                   note=payload.get("note", ""), revision=1, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    calculate_nav(*nav_inputs(session, account_id))
    data = flow_data(row)
    mark_changed(session, account, row.flow_date, "create", "cash_flow", row.id, None, data)
    return data


def void_flow(session: Session, account_id: str, flow_id: str, expected_revision: int) -> dict:
    account = account_or_error(session, account_id, real=True)
    row = session.get(CashFlow, flow_id)
    if row is None or row.account_id != account_id or row.voided_at is not None:
        raise TradeError("FLOW_NOT_FOUND", "资金流水不存在", 404)
    if row.revision != expected_revision:
        raise TradeError("REVISION_CONFLICT", "资金流水版本已变化", 409)
    before = flow_data(row)
    row.revision += 1
    row.voided_at = utc_now()
    row.updated_at = row.voided_at
    session.flush()
    calculate_nav(*nav_inputs(session, account_id))
    mark_changed(session, account, row.flow_date, "void", "cash_flow", row.id, before, None)
    return {"deleted": True, "id": flow_id}


def list_snapshots(session: Session, account_id: str) -> list[dict]:
    account_or_error(session, account_id, real=True)
    return [snapshot_data(row) for row in session.scalars(select(AssetSnapshot).where(
        AssetSnapshot.account_id == account_id).order_by(AssetSnapshot.snap_date))]


def save_snapshot(session: Session, account_id: str, payload: dict) -> dict:
    account = account_or_error(session, account_id, real=True)
    day = str(payload["snap_date"])
    total = money_minor(payload["total_assets"], "总资产", allow_zero=True)
    cash_raw = payload.get("available_cash")
    position_raw = payload.get("position_value")
    cash = money_minor(cash_raw, "可用现金", allow_zero=True) if cash_raw is not None else None
    position = money_minor(position_raw, "持仓市值", allow_zero=True) if position_raw is not None else None
    position_rows = payload.get('positions')
    normalized_positions = None
    if position_rows is not None:
        normalized_positions = []
        seen: set[str] = set()
        for index, item in enumerate(position_rows, start=1):
            symbol = item['symbol'].strip().upper()
            key = validated_market_symbol_key(symbol)
            if not symbol or key in seen:
                raise TradeError('DUPLICATE_SNAPSHOT_SYMBOL', '持仓代码不能为空或重复')
            seen.add(key)
            normalized_positions.append(dict(id=new_id(), sequence=index, symbol=symbol,
                                             name=item.get('name', '').strip(), quantity=item['quantity'],
                                             market_value_minor=money_minor(item['market_value'],
                                                                            '持仓市值', allow_zero=True)))
        if normalized_positions:
            details_total = sum(item['market_value_minor'] for item in normalized_positions)
            if position is None:
                position = details_total
            elif abs(position - details_total) > 1:
                raise TradeError('POSITION_DETAIL_MISMATCH', '持仓明细市值之和与持仓市值不一致')
    if cash is not None and position is not None and abs(cash + position - total) > 1:
        raise TradeError("SNAPSHOT_MISMATCH", "可用现金与持仓市值之和必须等于总资产")
    row = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.account_id == account_id, AssetSnapshot.snap_date == day))
    before = snapshot_data(row) if row else None
    if row:
        if row.revision != payload["expected_revision"]:
            raise TradeError("REVISION_CONFLICT", "资产快照已被其他页面修改", 409)
        row.total_assets_minor = total
        row.available_cash_minor = cash
        row.position_value_minor = position
        row.note = payload.get("note", "")
        row.revision += 1
        row.updated_at = utc_now()
        if normalized_positions is not None:
            row.positions.clear()
            session.flush()
            row.positions.extend(SnapshotPosition(snapshot_id=row.id, **item)
                                 for item in normalized_positions)
    else:
        if payload["expected_revision"] != 0:
            raise TradeError("REVISION_CONFLICT", "资产快照不存在，请刷新后重试", 409)
        now = utc_now()
        row = AssetSnapshot(id=new_id(), account_id=account_id, snap_date=day,
                            total_assets_minor=total, available_cash_minor=cash,
                            position_value_minor=position, note=payload.get("note", ""),
                            revision=1, created_at=now, updated_at=now)
        session.add(row)
        if normalized_positions is not None:
            row.positions.extend(SnapshotPosition(snapshot_id=row.id, **item)
                                 for item in normalized_positions)
    session.flush()
    calculate_nav(*nav_inputs(session, account_id))
    data = snapshot_data(row)
    mark_changed(session, account, day, "update" if before else "create", "asset_snapshot", row.id, before, data)
    return data
