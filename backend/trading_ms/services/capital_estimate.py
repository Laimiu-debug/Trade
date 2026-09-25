"""根据出入金与交易流水，估算某日收盘总资产（现金 + 持仓市值）。"""

from datetime import date

from sqlalchemy.orm import Session

from ..models import CapitalFlow, Trade
from . import market as market_svc


def _close_on_or_before(klines: list[dict], day_str: str) -> float | None:
    valid = [k for k in klines if k["date"] <= day_str]
    if not valid:
        return None
    return float(valid[-1]["close"])


def estimate_snapshot(db: Session, snap_date: date) -> dict:
    flows = (
        db.query(CapitalFlow)
        .filter(CapitalFlow.flow_date <= snap_date)
        .order_by(CapitalFlow.flow_date, CapitalFlow.id)
        .all()
    )
    has_initial = any(f.kind == "initial" for f in flows)
    if not has_initial:
        return {"ok": False, "reason": "no_initial", "message": "请先录入初始资金"}

    cash = 0.0
    for f in flows:
        if f.kind in ("initial", "deposit"):
            cash += f.amount
        elif f.kind == "withdraw":
            cash -= f.amount

    trades = (
        db.query(Trade)
        .filter(Trade.trade_date <= snap_date)
        .order_by(Trade.trade_date, Trade.id)
        .all()
    )
    positions: dict[str, int] = {}
    for t in trades:
        fees = t.fee_commission + t.fee_stamp + t.fee_transfer
        amount = t.price * t.qty
        if t.side == "buy":
            cash -= amount + fees
            positions[t.code] = positions.get(t.code, 0) + t.qty
        else:
            cash += amount - fees
            positions[t.code] = positions.get(t.code, 0) - t.qty

    positions = {code: qty for code, qty in positions.items() if qty > 0}

    day_str = snap_date.isoformat()
    position_value = 0.0
    position_details: list[dict] = []
    missing_quotes: list[str] = []
    for code, qty in sorted(positions.items()):
        klines = market_svc.get_daily(db, code, limit=60)["klines"]
        close = _close_on_or_before(klines, day_str)
        if close is None:
            missing_quotes.append(code)
            continue
        mv = round(qty * close, 2)
        position_value += mv
        position_details.append({
            "code": code,
            "name": (market_svc.lookup_by_code(code) or {}).get("name") or "",
            "qty": qty,
            "price": close,
            "market_value": mv,
        })

    total_assets = round(cash + position_value, 2)
    return {
        "ok": True,
        "snap_date": day_str,
        "total_assets": total_assets,
        "cash": round(cash, 2),
        "position_value": round(position_value, 2),
        "positions": position_details,
        "missing_quotes": missing_quotes,
        "message": "根据初始资金、出入金与交易流水，按收盘价估算",
    }
