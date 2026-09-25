"""图形+量价波段 — 打分验证 + 15日同股去重回测。

验证目标：分数/等级越高，胜率与 PF 应单调更好（优中选优）。
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

from app.core.chart_volume_swing_strategy import resolve_chart_volume_swing_params
from scripts.backtest_chart_volume_swing import (
    DATE_FROM,
    DATE_TO,
    collect_signals,
    simulate_swing_exit,
    summarize,
)

DEFAULT_TDX = r"D:\new_tdx\vipdoc"


def _parse_date(d: str) -> datetime:
    return datetime.strptime(str(d)[:10], "%Y-%m-%d")


def dedupe_signals_by_score(signals: list[dict], window_days: int = 15) -> list[dict]:
    """同股 window_days 日内只保留 signal_score 最高的一笔。"""
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        by_symbol[str(s["symbol"])].append(s)

    kept: list[dict] = []
    for items in by_symbol.values():
        items.sort(key=lambda x: str(x["signal_date"]))
        cluster: list[dict] = []
        for s in items:
            if not cluster:
                cluster = [s]
                continue
            if (_parse_date(s["signal_date"]) - _parse_date(cluster[0]["signal_date"])).days < window_days:
                cluster.append(s)
            else:
                kept.append(max(cluster, key=lambda x: float(x["signal_score"])))
                cluster = [s]
        if cluster:
            kept.append(max(cluster, key=lambda x: float(x["signal_score"])))
    return sorted(kept, key=lambda x: str(x["signal_date"]))


def build_trades(pool: list[dict], exit_cfg: dict) -> list[dict]:
    trades: list[dict] = []
    for s in pool:
        t = simulate_swing_exit(
            s["candles"],
            s["entry_idx"],
            s["entry_price"],
            box_high=float(s["box_high"]),
            **exit_cfg,
        )
        trades.append(
            {
                "symbol": s["symbol"],
                "signal_date": s["signal_date"],
                "signal_score": round(float(s["signal_score"]), 2),
                "event_grade": s["event_grade"],
                "confirm_type": s["confirm_type"],
                "score_breakdown": s.get("score_breakdown") or {},
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()},
            }
        )
    return trades


def score_buckets(trades: list[dict]) -> list[dict]:
    edges = [(0, 62), (62, 70), (70, 75), (75, 82), (82, 101)]
    rows: list[dict] = []
    for lo, hi in edges:
        bucket = [t for t in trades if lo <= float(t["signal_score"]) < hi]
        if not bucket:
            continue
        sm = summarize(bucket)
        rows.append({"score_range": f"[{lo},{hi})", **sm})
    return rows


def grade_buckets(trades: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for g in ("C", "B", "A"):
        bucket = [t for t in trades if str(t.get("event_grade")) == g]
        if bucket:
            out[g] = summarize(bucket)
    return out


def monotonic_ok(rows: list[dict], key: str) -> bool:
    vals = [float(r[key]) for r in rows if r.get("n", 0) >= 20]
    if len(vals) < 2:
        return True
    return all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-root", default=os.environ.get("TDX_ROOT", DEFAULT_TDX))
    parser.add_argument("--min-score", type=float, default=62.0)
    parser.add_argument("--min-amount20", type=float, default=1e8)
    parser.add_argument("--dedupe-days", type=int, default=15)
    parser.add_argument("--prefer", default="pullback_retest", choices=["any", "pullback_retest", "hold_above"])
    parser.add_argument("--output", default=str(ROOT / "scripts" / "out_chart_volume_swing_scored.json"))
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
        if float(s["signal_score"]) >= args.min_score
        and float(s["amount20"]) >= args.min_amount20
        and (args.prefer == "any" or s["confirm_type"] == args.prefer)
    ]
    deduped = dedupe_signals_by_score(filtered, window_days=args.dedupe_days)

    exit_cfg = {
        "stop_loss": 0.12,
        "take_profit": 0.30,
        "max_hold": 60,
        "trail_activate": 0.15,
    }

    trades_all = build_trades(filtered, exit_cfg)
    trades_best = build_trades(deduped, exit_cfg)

    bucket_rows = score_buckets(trades_best)
    grade_rows = grade_buckets(trades_best)

    validation = {
        "win_rate_monotonic": monotonic_ok(bucket_rows, "win_rate"),
        "pf_monotonic": monotonic_ok(bucket_rows, "pf"),
        "avg_pnl_monotonic": monotonic_ok(bucket_rows, "avg_pnl"),
        "score_buckets": bucket_rows,
        "grade_buckets": grade_rows,
    }

    top_sample = sorted(trades_best, key=lambda x: float(x["signal_score"]), reverse=True)[:30]

    result = {
        "strategy": "chart_volume_swing_v1_scored",
        "date_range": [DATE_FROM, DATE_TO],
        "pipeline": {
            "min_score": args.min_score,
            "min_amount20": args.min_amount20,
            "prefer_confirm": args.prefer,
            "dedupe_days": args.dedupe_days,
            "dedupe_rule": "同股N日内保留最高分",
        },
        "exit": exit_cfg,
        "counts": {
            "raw_signals": len(sigs),
            "after_filter": len(filtered),
            "after_dedupe": len(deduped),
        },
        "summary_before_dedupe": summarize(trades_all),
        "summary_after_dedupe": summarize(trades_best),
        "validation": validation,
        "top30_by_score": top_sample,
        "trades_hit_30pct": [t for t in trades_best if float(t["pnl_net"]) >= 0.28],
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
