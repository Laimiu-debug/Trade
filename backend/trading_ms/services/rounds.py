"""回合归组：同一标的从建仓（持仓 0 → 正）到清仓（回到 0）为一个回合。"""

from datetime import date

from sqlalchemy.orm import Session

from ..models import DailyReview, RoundReview, Trade


def _trade_dict(t: Trade) -> dict:
    fees = t.fee_commission + t.fee_stamp + t.fee_transfer
    return {
        "id": t.id,
        "date": t.trade_date.isoformat(),
        "code": t.code,
        "name": t.name,
        "side": t.side,
        "price": t.price,
        "qty": t.qty,
        "fees": round(fees, 2),
        "note": t.note,
        "source": t.source,
    }


def build_rounds(db: Session) -> list[dict]:
    trades = db.query(Trade).order_by(Trade.trade_date, Trade.id).all()
    by_code: dict[str, list[Trade]] = {}
    for t in trades:
        by_code.setdefault(t.code, []).append(t)

    rounds: list[dict] = []
    for code, items in by_code.items():
        position = 0
        current: list[Trade] = []
        for t in items:
            if position == 0 and t.side == "sell":
                # 数据异常（无持仓先卖），单独成组避免污染
                rounds.append(_make_round(code, [t], position_after=0, anomaly=True))
                continue
            current.append(t)
            position += t.qty if t.side == "buy" else -t.qty
            if position <= 0:
                rounds.append(_make_round(code, current, position_after=position))
                current = []
                position = 0
        if current:
            rounds.append(_make_round(code, current, position_after=position))

    rounds.sort(key=lambda r: r["start_date"], reverse=True)
    return rounds


def _make_round(code: str, trades: list[Trade], position_after: int, anomaly: bool = False) -> dict:
    buy_amount = sum(t.price * t.qty for t in trades if t.side == "buy")
    sell_amount = sum(t.price * t.qty for t in trades if t.side == "sell")
    fees = sum(t.fee_commission + t.fee_stamp + t.fee_transfer for t in trades)
    closed = position_after == 0 and not anomaly
    pnl = sell_amount - buy_amount - fees if closed else None
    pnl_pct = (pnl / buy_amount * 100) if closed and buy_amount > 0 else None
    name = next((t.name for t in reversed(trades) if t.name), "")
    return {
        "code": code,
        "name": name,
        "start_date": trades[0].trade_date.isoformat(),
        "end_date": trades[-1].trade_date.isoformat() if closed else None,
        "status": "closed" if closed else ("anomaly" if anomaly else "open"),
        "position": position_after,
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "fees": round(fees, 2),
        "pnl": round(pnl, 2) if pnl is not None else None,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "trades": [_trade_dict(t) for t in trades],
    }


def round_stats(rounds: list[dict]) -> dict:
    closed = [r for r in rounds if r["status"] == "closed"]
    closed.sort(key=lambda r: r["end_date"] or "")
    wins = [r for r in closed if (r["pnl"] or 0) > 0]
    losses = [r for r in closed if (r["pnl"] or 0) <= 0]

    win_streak = lose_streak = cur_win = cur_lose = 0
    for r in closed:
        if (r["pnl"] or 0) > 0:
            cur_win += 1
            cur_lose = 0
        else:
            cur_lose += 1
            cur_win = 0
        win_streak = max(win_streak, cur_win)
        lose_streak = max(lose_streak, cur_lose)

    avg_win = sum(r["pnl"] for r in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(r["pnl"] for r in losses) / len(losses)) if losses else 0.0
    return {
        "total_rounds": len(closed),
        "open_rounds": len([r for r in rounds if r["status"] == "open"]),
        "win_count": len(wins),
        "lose_count": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_loss_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
        "max_win_streak": win_streak,
        "max_lose_streak": lose_streak,
        "total_pnl": round(sum(r["pnl"] for r in closed), 2) if closed else 0.0,
    }


def closed_rounds_in_period(rounds: list[dict], start: date, end: date) -> list[dict]:
    start_s, end_s = start.isoformat(), end.isoformat()
    return [
        r for r in rounds
        if r["status"] == "closed" and r["end_date"] and start_s <= r["end_date"] <= end_s
    ]


def rounds_touching_day(rounds: list[dict], day: date) -> list[dict]:
    """当日有成交，或当日清仓的回合。"""
    day_str = day.isoformat()
    result: list[dict] = []
    seen: set[str] = set()
    for r in rounds:
        key = f"{r['code']}:{r['start_date']}"
        if key in seen:
            continue
        if r.get("end_date") == day_str:
            result.append(r)
            seen.add(key)
            continue
        for t in r.get("trades") or []:
            if t.get("date") == day_str:
                result.append(r)
                seen.add(key)
                break
    return result


def round_line_summary(r: dict, review_snippet: str = "") -> str:
    status = r.get("status")
    span = f"{r['start_date']}→{r['end_date'] or '持仓中'}"
    if status == "closed" and r.get("pnl") is not None:
        line = (
            f"{r['name']}({r['code']}) {span} "
            f"盈亏 {r['pnl']}元 ({r.get('pnl_pct')}%)"
        )
    elif status == "open":
        line = f"{r['name']}({r['code']}) {span} 持仓中 {r.get('position', 0)}股"
    else:
        line = f"{r['name']}({r['code']}) {span} 异常回合"
    if review_snippet:
        line += f" | 复盘摘录: {review_snippet}"
    return line


def round_review_meta(db: Session, r: dict) -> tuple[str, list[str]]:
    """回合跨度内的每日复盘摘录。"""
    start = date.fromisoformat(r["start_date"])
    if r.get("end_date"):
        end = date.fromisoformat(r["end_date"])
    else:
        trade_dates = [
            date.fromisoformat(t["date"])
            for t in (r.get("trades") or [])
            if t.get("date")
        ]
        end = max(trade_dates) if trade_dates else start
    rows = (
        db.query(DailyReview)
        .filter(DailyReview.review_date >= start, DailyReview.review_date <= end)
        .order_by(DailyReview.review_date)
        .all()
    )
    review_dates: list[str] = []
    parts: list[str] = []
    for row in rows:
        has_content = any([
            (row.market_observation or "").strip(),
            (row.decision_review or "").strip(),
            (row.mistakes or "").strip(),
            (row.ai_summary or "").strip(),
        ])
        if not has_content:
            continue
        review_dates.append(row.review_date.isoformat())
        if (row.mistakes or "").strip():
            parts.append(f"{row.review_date} 教训:{row.mistakes.strip()[:80]}")
        elif (row.decision_review or "").strip():
            parts.append(f"{row.review_date} 决策:{row.decision_review.strip()[:80]}")
        elif (row.ai_summary or "").strip():
            parts.append(f"{row.review_date} 总评:{row.ai_summary.strip()[:80]}")
        elif (row.market_observation or "").strip():
            parts.append(f"{row.review_date} 观察:{row.market_observation.strip()[:80]}")
    return " | ".join(parts[:2]), review_dates


def find_round(db: Session, code: str, start_date: str) -> dict | None:
    for r in build_rounds(db):
        if r["code"] == code and r["start_date"] == start_date:
            return r
    return None


def get_round_summary(db: Session, code: str, start_date: str) -> str:
    row = (
        db.query(RoundReview)
        .filter(RoundReview.code == code, RoundReview.start_date == date.fromisoformat(start_date))
        .first()
    )
    return (row.review_summary or "").strip() if row else ""


def set_round_summary(db: Session, code: str, start_date: str, summary: str) -> str:
    start = date.fromisoformat(start_date)
    row = (
        db.query(RoundReview)
        .filter(RoundReview.code == code, RoundReview.start_date == start)
        .first()
    )
    text = (summary or "").strip()
    if row is None:
        row = RoundReview(code=code, start_date=start, review_summary=text)
        db.add(row)
    else:
        row.review_summary = text
    db.commit()
    return text
