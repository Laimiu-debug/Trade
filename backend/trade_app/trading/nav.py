"""Pure unit-NAV calculation: same-day flows precede the confirmed close."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from trade_app.platform.types import TradeError, decimal_text, money_text


@dataclass(frozen=True)
class FlowFact:
    id: str
    date: str
    kind: str
    amount_minor: int
    order: str


@dataclass(frozen=True)
class SnapshotFact:
    date: str
    total_assets_minor: int


def calculate_nav(flows: list[FlowFact], snapshots: list[SnapshotFact], *,
                  multiplier: str = '1.30', node_count: int = 50,
                  target_version: int = 1) -> dict:
    flow_by_day: dict[str, list[FlowFact]] = {}
    for flow in flows:
        flow_by_day.setdefault(flow.date, []).append(flow)
    snap_by_day = {item.date: item for item in snapshots}
    days = sorted(set(flow_by_day) | set(snap_by_day))
    shares = Decimal(0)
    nav: Decimal | None = None
    peak = Decimal(1)
    segment = 0
    points: list[dict] = []
    node_events: list[dict] = []
    lit_levels: set[int] = set()
    threshold = [Decimal(multiplier) ** i for i in range(1, node_count + 1)]
    first_lit: dict[tuple[int, int], str] = {}
    segment_starts: dict[int, str] = {}

    for day in days:
        for flow in sorted(flow_by_day.get(day, []), key=lambda f: (f.order, f.id)):
            amount = Decimal(flow.amount_minor) / 100
            if flow.kind == "initial":
                if shares > 0 and nav != 0:
                    raise TradeError("INVALID_INITIAL_FLOW", f"{day} 初始资金只能用于新净值段")
                segment += 1
                segment_starts[segment] = day
                shares = amount
                nav = Decimal(1)
                peak = Decimal(1)
                lit_levels.clear()
            elif flow.kind in ("deposit", "withdraw"):
                if shares <= 0 or nav is None or nav <= 0:
                    raise TradeError("NAV_UNAVAILABLE", f"{day} 缺少可用于折算份额的有效净值")
                delta = amount / nav
                if flow.kind == "withdraw":
                    if delta > shares:
                        raise TradeError("OVER_WITHDRAWAL", f"{day} 出金超过可赎回份额")
                    shares -= delta
                else:
                    shares += delta
            else:
                raise TradeError("INVALID_FLOW_KIND", "资金流水类型无效")

        snap = snap_by_day.get(day)
        if snap is not None:
            assets = Decimal(snap.total_assets_minor) / 100
            if shares == 0:
                if assets != 0:
                    raise TradeError("UNFUNDED_SNAPSHOT", f"{day} 尚无份额，不能确认正资产快照")
                nav = None
            else:
                nav = assets / shares
        else:
            assets = shares * nav if nav is not None else Decimal(0)
        if shares == 0:
            nav = None
        if nav is not None and nav > peak:
            peak = nav

        current_lit = {i for i, limit in enumerate(threshold, start=1) if nav is not None and nav >= limit}
        for level in sorted(current_lit - lit_levels):
            if (segment, level) not in first_lit:
                first_lit[(segment, level)] = day
            node_events.append({"date": day, "level": level, "kind": "lit", "segment": segment,
                                'target_version': target_version,
                                'threshold': decimal_text(threshold[level - 1])})
        for level in sorted(lit_levels - current_lit):
            node_events.append({"date": day, "level": level, "kind": "extinguished", "segment": segment,
                                'target_version': target_version,
                                'threshold': decimal_text(threshold[level - 1])})
        lit_levels = current_lit
        drawdown = (nav / peak - 1) * 100 if nav is not None and peak > 0 else None
        points.append({
            "date": day,
            "segment": segment,
            "nav": decimal_text(nav) if nav is not None else None,
            "shares": decimal_text(shares),
            "assets": money_text(snap.total_assets_minor) if snap is not None else decimal_text(assets, 2),
            "drawdown_pct": decimal_text(drawdown, 4) if drawdown is not None else None,
            "quality": "confirmed" if snap is not None else "carried",
        })

    final_nav = nav if points else None
    next_level = len(lit_levels) + 1 if len(lit_levels) < node_count and shares > 0 else None
    first_achievements = [{'segment': part, 'level': level, 'first_lit_date': day,
                           'days_from_start': (date.fromisoformat(day) - date.fromisoformat(start)).days,
                           'target_version': target_version}
                          for (part, level), day in sorted(first_lit.items())
                          if (start := segment_starts.get(part)) is not None]
    return {
        "points": points,
        "node_events": node_events,
        'first_achievements': first_achievements,
        'target_config': {'version': target_version, 'multiplier': multiplier,
                          'node_count': node_count},
        "current": {
            "nav": decimal_text(final_nav) if final_nav is not None else None,
            "shares": decimal_text(shares),
            "assets": points[-1]["assets"] if points else None,
            "date": points[-1]["date"] if points else None,
            "quality": points[-1]["quality"] if points else "missing",
            "lit_count": len(lit_levels),
            "next_level": next_level,
            "next_threshold": decimal_text(threshold[next_level - 1]) if next_level else None,
            'next_target_assets': decimal_text(threshold[next_level - 1] * shares, 2)
            if next_level and final_nav is not None else None,
            'next_gap_amount': decimal_text(max(Decimal(0),
                (threshold[next_level - 1] - final_nav) * shares), 2)
            if next_level and final_nav is not None else None,
            "next_gap_pct": decimal_text((threshold[next_level - 1] / final_nav - 1) * 100, 4)
            if next_level and final_nav and final_nav > 0 else None,
        },
    }
