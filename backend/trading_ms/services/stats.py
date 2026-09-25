"""统计：净值曲线衍生指标、周期收益率、漏写复盘检测。"""

from datetime import date

from sqlalchemy.orm import Session

from ..models import DailyReview, Snapshot, Trade
from .netvalue import NavPoint


def nav_curve(points: list[NavPoint]) -> list[dict]:
    curve = []
    peak = 0.0
    for p in points:
        peak = max(peak, p.nav)
        curve.append({
            "date": p.day.isoformat(),
            "nav": round(p.nav, 6),
            "assets": round(p.assets, 2),
            "drawdown_pct": round((p.nav / peak - 1) * 100, 2) if peak > 0 else 0.0,
        })
    return curve


def max_drawdown(points: list[NavPoint]) -> float:
    peak = 0.0
    mdd = 0.0
    for p in points:
        peak = max(peak, p.nav)
        if peak > 0:
            mdd = min(mdd, (p.nav / peak - 1) * 100)
    return round(mdd, 2)


def period_returns(points: list[NavPoint], keyfn) -> list[dict]:
    """按 keyfn 分组（周/月），组内首尾净值算收益率。"""
    groups: dict[str, list[NavPoint]] = {}
    for p in points:
        groups.setdefault(keyfn(p.day), []).append(p)
    keys = sorted(groups)
    result = []
    prev_nav = None
    for key in keys:
        pts = groups[key]
        start_nav = prev_nav if prev_nav is not None else pts[0].nav
        end_nav = pts[-1].nav
        ret = (end_nav / start_nav - 1) * 100 if start_nav > 0 else 0.0
        result.append({"period": key, "return_pct": round(ret, 2), "end_nav": round(end_nav, 4)})
        prev_nav = end_nav
    return result


def week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def missing_reviews(db: Session) -> list[str]:
    """有交易或快照、但没写每日复盘的日期。"""
    active_days: set[date] = set()
    for (d,) in db.query(Trade.trade_date).distinct():
        active_days.add(d)
    for (d,) in db.query(Snapshot.snap_date).distinct():
        active_days.add(d)
    reviewed = {d for (d,) in db.query(DailyReview.review_date).distinct()}
    return sorted(d.isoformat() for d in active_days - reviewed)


def missing_snapshots(db: Session) -> list[str]:
    """有交易记录、但没有资金快照的日期（影响净值曲线）。"""
    trade_days = {d for (d,) in db.query(Trade.trade_date).distinct()}
    snap_days = {d for (d,) in db.query(Snapshot.snap_date).distinct()}
    return sorted(d.isoformat() for d in trade_days - snap_days)
