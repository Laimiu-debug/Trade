from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PendingTrade, Trade
from ..services import ai as ai_svc
from ..services import capital_estimate as capital_est_svc
from ..services import fees as fees_svc
from ..services import market as market_svc
from ..services import rounds as rounds_svc

router = APIRouter(prefix="/api/trades", tags=["trades"])


class TradeIn(BaseModel):
    trade_date: date
    code: str
    name: str = ""
    side: str  # buy | sell
    price: float
    qty: int
    note: str = ""


def _validate(body: TradeIn):
    if body.side not in ("buy", "sell"):
        raise HTTPException(400, "side 必须是 buy/sell")
    if body.price <= 0 or body.qty <= 0:
        raise HTTPException(400, "价格和数量必须为正")


@router.get("")
def list_trades(db: Session = Depends(get_db)):
    rows = db.query(Trade).order_by(Trade.trade_date.desc(), Trade.id.desc()).all()
    return [
        {
            "id": r.id, "trade_date": r.trade_date.isoformat(), "code": r.code,
            "name": r.name, "side": r.side, "price": r.price, "qty": r.qty,
            "fee_commission": r.fee_commission, "fee_stamp": r.fee_stamp,
            "fee_transfer": r.fee_transfer,
            "fees": round(r.fee_commission + r.fee_stamp + r.fee_transfer, 2),
            "amount": round(r.price * r.qty, 2), "note": r.note, "source": r.source,
        }
        for r in rows
    ]


@router.post("")
def add_trade(body: TradeIn, db: Session = Depends(get_db)):
    _validate(body)
    code, name = market_svc.resolve_stock(body.code.strip(), body.name.strip())
    fees = fees_svc.compute_fees(db, body.side, body.price, body.qty)
    row = Trade(
        trade_date=body.trade_date, code=code, name=name or body.name.strip(),
        side=body.side, price=body.price, qty=body.qty, note=body.note, **fees,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, **fees}


@router.put("/{trade_id}")
def update_trade(trade_id: int, body: TradeIn, db: Session = Depends(get_db)):
    _validate(body)
    row = db.get(Trade, trade_id)
    if row is None:
        raise HTTPException(404, "交易不存在")
    fees = fees_svc.compute_fees(db, body.side, body.price, body.qty)
    code, name = market_svc.resolve_stock(body.code.strip(), body.name.strip())
    row.trade_date = body.trade_date
    row.code = code
    row.name = name or body.name.strip()
    row.side = body.side
    row.price = body.price
    row.qty = body.qty
    row.note = body.note
    row.fee_commission = fees["fee_commission"]
    row.fee_stamp = fees["fee_stamp"]
    row.fee_transfer = fees["fee_transfer"]
    db.commit()
    return {"ok": True}


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, db: Session = Depends(get_db)):
    row = db.get(Trade, trade_id)
    if row is None:
        raise HTTPException(404, "交易不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/rounds")
def rounds(db: Session = Depends(get_db)):
    result = rounds_svc.build_rounds(db)
    enriched = []
    for r in result:
        excerpt, review_dates = rounds_svc.round_review_meta(db, r)
        enriched.append({
            **r,
            "review_snippet": excerpt,
            "review_summary": rounds_svc.get_round_summary(db, r["code"], r["start_date"]),
            "review_dates": review_dates,
            "trade_count": len(r.get("trades") or []),
        })
    return {"rounds": enriched, "stats": rounds_svc.round_stats(result)}


class RoundSummaryIn(BaseModel):
    review_summary: str = ""


@router.put("/rounds/{code}/{start_date}/summary")
def save_round_summary(code: str, start_date: date, body: RoundSummaryIn, db: Session = Depends(get_db)):
    if rounds_svc.find_round(db, code, start_date.isoformat()) is None:
        raise HTTPException(404, "回合不存在")
    text = rounds_svc.set_round_summary(db, code, start_date.isoformat(), body.review_summary)
    return {"review_summary": text}


@router.post("/rounds/{code}/{start_date}/ai-review")
def ai_round_review(code: str, start_date: date, db: Session = Depends(get_db)):
    if rounds_svc.find_round(db, code, start_date.isoformat()) is None:
        raise HTTPException(404, "回合不存在")
    try:
        result = ai_svc.generate_round_review(db, code, start_date.isoformat())
    except ai_svc.AIUnavailable as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    summary = str(result.get("review_summary") or "").strip()
    if summary:
        rounds_svc.set_round_summary(db, code, start_date.isoformat(), summary)
    return {"review_summary": summary}


# ---------- 截图导入 ----------

@router.post("/import/screenshot")
async def import_screenshot(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    mime = file.content_type or "image/png"
    try:
        items = ai_svc.parse_screenshot(db, content, mime)
    except ai_svc.AIUnavailable as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"识别失败: {exc}") from exc

    created = 0
    for item in items:
        side = _normalize_side(str(item.get("side", "")))
        if side not in ("buy", "sell"):
            continue  # 持仓行(hold)不能直接变成交易
        try:
            code, name = market_svc.resolve_stock(
                str(item.get("code", "")).strip(),
                str(item.get("name", "")).strip(),
            )
            row = PendingTrade(
                trade_date=date.fromisoformat(item["date"]) if item.get("date") else date.today(),
                code=code,
                name=name,
                side=side,
                price=float(item.get("price", 0)),
                qty=int(item.get("qty", 0)),
                raw_text=str(item),
            )
        except (ValueError, TypeError):
            continue
        db.add(row)
        created += 1
    db.commit()
    return {"recognized": len(items), "pending_created": created}


@router.get("/pending")
def list_pending(db: Session = Depends(get_db)):
    rows = db.query(PendingTrade).order_by(PendingTrade.id.desc()).all()
    return [
        {
            "id": r.id, "trade_date": r.trade_date.isoformat(), "code": r.code,
            "name": r.name, "side": r.side, "price": r.price, "qty": r.qty,
        }
        for r in rows
    ]


class PendingConfirm(BaseModel):
    trade_date: date
    code: str
    name: str = ""
    side: str
    price: float
    qty: int


def _normalize_side(side: str) -> str | None:
    s = (side or "").strip().lower()
    if s in ("buy", "b", "买入", "证券买入", "买"):
        return "buy"
    if s in ("sell", "s", "卖出", "证券卖出", "卖"):
        return "sell"
    return None


def _valid_code(code: str) -> bool:
    digits = "".join(ch for ch in code if ch.isdigit())
    return len(digits) == 6


def _prepare_pending_body(row: PendingTrade) -> tuple[PendingConfirm | None, str | None]:
    side = _normalize_side(row.side)
    if side is None:
        return None, "买卖方向无法识别"
    if row.price <= 0:
        return None, "价格无效"
    if row.qty <= 0:
        return None, "数量无效"
    code, name = market_svc.resolve_stock(row.code, row.name)
    if not _valid_code(code):
        label = row.name or row.code or f"#{row.id}"
        return None, f"{label} 缺少有效股票代码"
    return PendingConfirm(
        trade_date=row.trade_date,
        code=code,
        name=name or row.name,
        side=side,
        price=row.price,
        qty=row.qty,
    ), None


def _confirm_pending_row(db: Session, row: PendingTrade, body: PendingConfirm) -> None:
    side = _normalize_side(body.side)
    if side is None or body.price <= 0 or body.qty <= 0:
        raise HTTPException(400, "数据不合法")
    code, name = market_svc.resolve_stock(body.code, body.name)
    if not _valid_code(code):
        raise HTTPException(400, "无法识别股票代码，请从下拉列表选择或输入 6 位代码")
    fees = fees_svc.compute_fees(db, side, body.price, body.qty)
    db.add(Trade(
        trade_date=body.trade_date, code=code, name=name or body.name.strip(),
        side=side, price=body.price, qty=body.qty, source="import", **fees,
    ))
    db.delete(row)


@router.put("/pending/{pending_id}")
def update_pending(pending_id: int, body: PendingConfirm, db: Session = Depends(get_db)):
    row = db.get(PendingTrade, pending_id)
    if row is None:
        raise HTTPException(404, "待确认记录不存在")
    side = _normalize_side(body.side)
    if side is None:
        raise HTTPException(400, "side 必须是 buy/sell")
    row.trade_date = body.trade_date
    code, name = market_svc.resolve_stock(body.code.strip(), body.name.strip())
    row.code = code
    row.name = name or body.name.strip()
    row.side = side
    row.price = body.price
    row.qty = body.qty
    db.commit()
    return {"ok": True}


@router.post("/pending/confirm-all")
def confirm_all_pending(db: Session = Depends(get_db)):
    rows = db.query(PendingTrade).order_by(PendingTrade.trade_date, PendingTrade.id).all()
    confirmed = 0
    skipped = 0
    skipped_details: list[dict] = []
    latest_date: date | None = None
    for row in rows:
        body, reason = _prepare_pending_body(row)
        if body is None:
            skipped += 1
            skipped_details.append({
                "id": row.id,
                "name": row.name or row.code or str(row.id),
                "reason": reason or "数据不合法",
            })
            continue
        _confirm_pending_row(db, row, body)
        confirmed += 1
        if latest_date is None or row.trade_date > latest_date:
            latest_date = row.trade_date
    db.commit()

    suggestion = None
    if latest_date is not None and confirmed > 0:
        est = capital_est_svc.estimate_snapshot(db, latest_date)
        if est.get("ok"):
            suggestion = est

    return {
        "confirmed": confirmed,
        "skipped": skipped,
        "skipped_details": skipped_details,
        "snapshot_suggestion": suggestion,
    }


@router.post("/pending/{pending_id}/confirm")
def confirm_pending(pending_id: int, body: PendingConfirm, db: Session = Depends(get_db)):
    row = db.get(PendingTrade, pending_id)
    if row is None:
        raise HTTPException(404, "待确认记录不存在")
    _confirm_pending_row(db, row, body)
    db.commit()
    return {"ok": True}


@router.delete("/pending/{pending_id}")
def delete_pending(pending_id: int, db: Session = Depends(get_db)):
    row = db.get(PendingTrade, pending_id)
    if row is None:
        raise HTTPException(404, "待确认记录不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
