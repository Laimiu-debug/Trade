"""图形+量价波段策略 — TDX 独立回测（不接入主系统）。

出场按结构：破 MA20 / 破箱顶止损；+15% 后 MA10 跟踪；30% 止盈。
同股 15 日内去重，保留最高分（优中选优）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.chart_volume_swing_strategy import (
    calculate_chart_volume_swing_signal,
    dedupe_signals_by_score as dedupe_cvs_signals,
    evaluate_chart_volume_swing_signal,
    resolve_chart_volume_swing_params,
)
from app.core.market_momentum import _bars_from_candles, _iter_tdx_symbols
from app.models import CandlePoint
from app.tdx_loader import load_candles_for_symbol

DEFAULT_TDX = r"D:\new_tdx\vipdoc"
DATE_FROM = "2024-01-01"
DATE_TO = "2026-06-01"
FEE = 10.0 / 10_000.0


def _parse_date(d: str) -> datetime:
    return datetime.strptime(str(d)[:10], "%Y-%m-%d")


def _ret_n(closes: list[float], idx: int, n: int) -> float:
    s = idx - n
    if s < 0 or closes[s] <= 0:
        return 0.0
    return (closes[idx] - closes[s]) / closes[s]


def _ma(closes: list[float], idx: int, w: int) -> float:
    s = max(0, idx - w + 1)
    seg = closes[s : idx + 1]
    return sum(seg) / len(seg) if seg else closes[idx]


def _avg_amount(candles: list[CandlePoint], idx: int, w: int = 20) -> float:
    s = max(0, idx - w + 1)
    seg = candles[s : idx + 1]
    return sum(float(c.amount or 0) for c in seg) / len(seg) if seg else 0.0


def simulate_swing_exit(
    candles: list[CandlePoint],
    entry_idx: int,
    entry_price: float,
    *,
    box_high: float,
    stop_loss: float = 0.12,
    take_profit: float = 0.30,
    max_hold: int = 60,
    trail_activate: float = 0.15,
    box_stop_ratio: float = 0.95,
    ma20_buffer: float = 0.97,
) -> dict:
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    last = min(len(candles) - 1, entry_idx + max_hold)
    exit_idx, exit_price, reason = last, closes[last], "max_hold"
    peak = entry_price
    structural_stop = max(entry_price * (1.0 - stop_loss), box_high * box_stop_ratio)

    for idx in range(entry_idx + 1, last + 1):
        peak = max(peak, highs[idx])
        gain = (peak - entry_price) / entry_price

        if lows[idx] <= structural_stop:
            exit_idx = idx
            exit_price = structural_stop
            reason = "structure_stop" if closes[idx] < _ma(closes, idx, 20) * ma20_buffer else "stop_loss"
            break

        if closes[idx] < _ma(closes, idx, 20) * ma20_buffer and closes[idx] < box_high * box_stop_ratio:
            exit_idx, exit_price, reason = idx, closes[idx], "ma20_box_break"
            break

        if highs[idx] >= entry_price * (1.0 + take_profit):
            exit_idx, exit_price, reason = idx, entry_price * (1.0 + take_profit), "take_profit"
            break

        if gain >= trail_activate and closes[idx] < _ma(closes, idx, 10):
            exit_idx, exit_price, reason = idx, closes[idx], "ma10_trail"
            break

    entry_exec = entry_price * (1.0 + FEE)
    exit_exec = exit_price * (1.0 - FEE)
    pnl = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
    return {
        "pnl_net": pnl,
        "win": pnl > 0,
        "hit_tp": reason == "take_profit",
        "exit_reason": reason,
        "holding_days": exit_idx - entry_idx,
    }


def collect_signals(tdx_root: str, params: dict) -> list[dict]:
    symbols = [x[1] for x in _iter_tdx_symbols(tdx_root, ["sh", "sz"])]
    p = dict(params)
    p["min_score"] = 0.0
    out: list[dict] = []
    for n, symbol in enumerate(symbols, 1):
        candles = load_candles_for_symbol(tdx_root, symbol, window=320, market_data_source="tdx_only")
        if not candles or len(candles) < 100:
            continue
        idx_map = {str(c.time): i for i, c in enumerate(candles)}
        bars = [b for b in _bars_from_candles(candles) if DATE_FROM <= str(b["date"]) <= DATE_TO]
        seen_dates: set[str] = set()
        for bar in bars:
            d = str(bar["date"])
            si = idx_map.get(d)
            if si is None or si + 1 >= len(candles) or d in seen_dates:
                continue
            w = candles[: si + 1]
            closes = [float(c.close) for c in w]
            ind = calculate_chart_volume_swing_signal(w, params=p)
            ev = evaluate_chart_volume_swing_signal(ind, p)
            if not ev.get("signal"):
                continue
            ep = float(candles[si + 1].open)
            if ep <= 0:
                continue
            seen_dates.add(d)
            out.append(
                {
                    "symbol": symbol,
                    "signal_date": d,
                    "entry_idx": si + 1,
                    "entry_price": ep,
                    "candles": candles,
                    "signal_score": float(ev.get("signal_score") or 0.0),
                    "event_grade": str(ev.get("event_grade") or "C"),
                    "confirm_type": str(ind.get("confirm_type") or ""),
                    "score_breakdown": ev.get("score_breakdown") or {},
                    "box_high": float(ind.get("box_high") or 0.0),
                    "box_range": float(ind.get("box_range") or 0.0),
                    "ret_40": _ret_n(closes, len(closes) - 1, 40),
                    "price_pos_60": float(ind.get("price_pos_60") or 0.0),
                    "up_down_vol_ratio": float(ind.get("up_down_vol_ratio") or 0.0),
                    "amount20": _avg_amount(w, len(w) - 1),
                }
            )
        if n % 800 == 0:
            print(f"  scanned {n}/{len(symbols)} raw={len(out)}", flush=True)
    return out


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    pnls = [float(t["pnl_net"]) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    return {
        "n": len(trades),
        "win_rate": round(wins / len(trades), 4),
        "avg_pnl": round(sum(pnls) / len(trades), 4),
        "pf": round(gp / gl, 4) if gl > 0 else 99.0,
        "tp_rate": round(sum(1 for t in trades if t.get("hit_tp")) / len(trades), 4),
        "hit_30pct": sum(1 for p in pnls if p >= 0.28),
        "hit_25pct": sum(1 for p in pnls if p >= 0.25),
        "avg_hold": round(sum(t["holding_days"] for t in trades) / len(trades), 1),
    }


def score_buckets(trades: list[dict]) -> list[dict]:
    edges = [(62, 70), (70, 75), (75, 82), (82, 101)]
    rows: list[dict] = []
    for lo, hi in edges:
        bucket = [t for t in trades if lo <= float(t["signal_score"]) < hi]
        if bucket:
            rows.append({"score_range": f"[{lo},{hi})", **summarize(bucket)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-root", default=os.environ.get("TDX_ROOT", DEFAULT_TDX))
    parser.add_argument("--min-score", type=float, default=75.0)
    parser.add_argument("--min-amount20", type=float, default=1e8)
    parser.add_argument("--min-vp", type=float, default=2.0, help="上涨日量/下跌日量下限")
    parser.add_argument("--prefer", default="pullback_retest", choices=["any", "pullback_retest", "hold_above"])
    parser.add_argument("--grade", default="A", choices=["any", "A", "B", "C"])
    parser.add_argument("--daily-top", type=int, default=3, help="每日全市场取得分前N")
    parser.add_argument("--dedupe-days", type=int, default=15)
    parser.add_argument("--output", default=str(ROOT / "scripts" / "out_chart_volume_swing.json"))
    args = parser.parse_args()

    if not os.path.isdir(args.tdx_root):
        print(json.dumps({"error": f"TDX不存在: {args.tdx_root}"}, ensure_ascii=False))
        raise SystemExit(1)

    params = asdict(resolve_chart_volume_swing_params({"min_score": 0}))
    print("collecting signals...", flush=True)
    sigs = collect_signals(args.tdx_root, params)
    filtered = [
        s
        for s in sigs
        if s["signal_score"] >= args.min_score
        and s["amount20"] >= args.min_amount20
        and float(s.get("up_down_vol_ratio") or 0) >= args.min_vp
        and (args.grade == "any" or s["event_grade"] == args.grade)
        and (args.prefer == "any" or s["confirm_type"] == args.prefer)
    ]
    filtered = dedupe_cvs_signals(filtered, window_days=args.dedupe_days)
    by_date: dict[str, list] = defaultdict(list)
    for s in filtered:
        by_date[str(s["signal_date"])].append(s)
    pool = []
    for d in sorted(by_date):
        day = sorted(by_date[d], key=lambda x: x["signal_score"], reverse=True)[: args.daily_top]
        pool.extend(day)
    print(f"raw={len(sigs)} filtered={len(filtered)} deduped={len(pool)}", flush=True)

    exit_cfg = {
        "stop_loss": 0.12,
        "take_profit": 0.30,
        "max_hold": 60,
        "trail_activate": 0.15,
    }

    trades = []
    for s in pool:
        t = simulate_swing_exit(
            s["candles"],
            s["entry_idx"],
            s["entry_price"],
            box_high=s["box_high"],
            **exit_cfg,
        )
        trades.append(
            {
                "symbol": s["symbol"],
                "signal_date": s["signal_date"],
                "signal_score": round(s["signal_score"], 2),
                "event_grade": s["event_grade"],
                "confirm_type": s["confirm_type"],
                "score_breakdown": s.get("score_breakdown") or {},
                "box_range": round(s["box_range"], 4),
                "ret_40": round(s["ret_40"], 4),
                "price_pos_60": round(s["price_pos_60"], 4),
                "up_down_vol_ratio": round(s["up_down_vol_ratio"], 4),
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()},
            }
        )

    by_year: dict[str, list] = defaultdict(list)
    for t in trades:
        by_year[str(t["signal_date"])[:4]].append(t)

    result = {
        "strategy": "chart_volume_swing_v1",
        "date_range": [DATE_FROM, DATE_TO],
        "pipeline": {
            "min_score": args.min_score,
            "min_amount20": args.min_amount20,
            "prefer_confirm": args.prefer,
            "dedupe_days": args.dedupe_days,
            "dedupe_rule": "同股N日内保留最高分",
        },
        "exit": exit_cfg,
        "entry": "signal_day_close_confirm -> next_open",
        "counts": {"raw": len(sigs), "filtered": len(filtered), "deduped": len(pool)},
        "summary": summarize(trades),
        "yearly": {y: summarize(items) for y, items in sorted(by_year.items())},
        "by_grade": {
            g: summarize([t for t in trades if t["event_grade"] == g])
            for g in ("A", "B", "C")
            if any(t["event_grade"] == g for t in trades)
        },
        "score_buckets": score_buckets(trades),
        "top30_by_score": sorted(trades, key=lambda x: x["signal_score"], reverse=True)[:30],
        "trades_hit_30pct": [t for t in trades if t["pnl_net"] >= 0.28],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
