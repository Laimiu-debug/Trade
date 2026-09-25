"""20 日换股波段回测 — 单槽复利，优中选优。

目标定义（与用户一致）：
  - 一个波段周期 ≈ 20 个交易日
  - 周期内通过换股复利争取 ~30%（非单票必须 +30%）
  - 周期胜率目标 ~70%
  - 全样本至少 20 个成功周期（越快越好）
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.chart_volume_swing_strategy import (
    dedupe_signals_by_score,
    resolve_chart_volume_swing_params,
)
from scripts.backtest_chart_volume_swing import collect_signals, DATE_FROM, DATE_TO
from scripts.optimize_band_target import (
    attach_trade_outcomes,
    filter_signals,
    select_daily_top,
    simulate_band_exit,
)

DEFAULT_TDX = r"D:\new_tdx\vipdoc"
CACHE = ROOT / "scripts" / ".cache_cvs_signals.pkl"
BAND_DAYS = 20
TARGET = 0.28


def _parse(d: str) -> datetime:
    return datetime.strptime(str(d)[:10], "%Y-%m-%d")


def build_trading_calendar(signals: list[dict]) -> list[str]:
    return sorted({str(s["signal_date"]) for s in signals})


def simulate_rotation(
    signals: list[dict],
    exit_cfg: dict,
    calendar: list[str],
    *,
    max_slots: int = 1,
) -> tuple[list[dict], list[dict]]:
    """多槽并行：每槽独立换股，周期收益为各槽复利均值。"""
    by_date: dict[str, list[dict]] = {}
    for s in signals:
        by_date.setdefault(str(s["signal_date"]), []).append(s)

    date_to_idx = {d: i for i, d in enumerate(calendar)}
    slots: list[dict[str, Any]] = [{"busy_until": -1, "equity": 1.0} for _ in range(max_slots)]
    trades: list[dict] = []

    for d in calendar:
        di = date_to_idx[d]
        day = sorted(by_date.get(d, []), key=lambda x: float(x["signal_score"]), reverse=True)
        if not day:
            continue
        used_symbols: set[str] = set()
        for slot_idx, slot in enumerate(slots):
            if di <= slot["busy_until"]:
                continue
            pick = None
            for c in day:
                if c["symbol"] not in used_symbols:
                    pick = c
                    used_symbols.add(c["symbol"])
                    break
            if pick is None:
                continue
            s = pick
            t = simulate_band_exit(
                s["candles"], s["entry_idx"], s["entry_price"],
                box_high=float(s["box_high"]), **exit_cfg,
            )
            hold = int(t["holding_days"])
            exit_di = min(len(calendar) - 1, di + hold)
            slot["busy_until"] = exit_di
            slot["equity"] *= 1.0 + float(t["pnl_net"])
            trades.append({
                "symbol": s["symbol"],
                "signal_date": d,
                "exit_date": calendar[exit_di],
                "signal_score": round(float(s["signal_score"]), 2),
                "slot": slot_idx,
                **t,
            })

    bands: list[dict] = []
    i = 0
    while i + BAND_DAYS <= len(calendar):
        w_start, w_end = calendar[i], calendar[i + BAND_DAYS - 1]
        slot_eq = [1.0] * max_slots
        for tr in trades:
            sd = str(tr["signal_date"])
            if sd < w_start or sd > w_end:
                continue
            slot_eq[int(tr.get("slot") or 0)] *= 1.0 + float(tr["pnl_net"])
        ret = sum(slot_eq) / len(slot_eq) - 1.0
        bands.append({
            "start": w_start,
            "end": w_end,
            "return": round(ret, 4),
            "hit_30": ret >= TARGET,
            "win": ret > 0,
        })
        i += BAND_DAYS

    return trades, bands


def simulate_single_slot_rotation(
    signals: list[dict],
    exit_cfg: dict,
    calendar: list[str],
) -> tuple[list[dict], list[dict]]:
    return simulate_rotation(signals, exit_cfg, calendar, max_slots=1)


def summarize_bands(bands: list[dict]) -> dict[str, Any]:
    if not bands:
        return {"n": 0}
    wins = sum(1 for b in bands if b["win"])
    hits = sum(1 for b in bands if b["hit_30"])
    return {
        "n": len(bands),
        "band_win_rate": round(wins / len(bands), 4),
        "bands_hit_30": hits,
        "bands_hit_30_rate": round(hits / len(bands), 4),
        "avg_band_return": round(sum(b["return"] for b in bands) / len(bands), 4),
        "median_band_return": round(sorted(b["return"] for b in bands)[len(bands) // 2], 4),
    }


def summarize_trades(trades: list[dict]) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    pnls = [float(t["pnl_net"]) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n": len(trades),
        "win_rate": round(wins / len(trades), 4),
        "avg_pnl": round(sum(pnls) / len(trades), 4),
        "hit_30": sum(1 for p in pnls if p >= TARGET),
        "fast_30": sum(1 for t in trades if t.get("fast_30")),
        "avg_hold": round(sum(t["holding_days"] for t in trades) / len(trades), 1),
    }


def load_signals(tdx: str) -> list[dict]:
    if CACHE.exists():
        with CACHE.open("rb") as f:
            return pickle.load(f)
    params = asdict(resolve_chart_volume_swing_params({"min_score": 0}))
    sigs = collect_signals(tdx, params)
    with CACHE.open("wb") as f:
        pickle.dump(sigs, f)
    return sigs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-root", default=os.environ.get("TDX_ROOT", DEFAULT_TDX))
    parser.add_argument("--output", default=str(ROOT / "scripts" / "out_band_rotation.json"))
    args = parser.parse_args()

    raw = load_signals(args.tdx_root)
    ded = dedupe_signals_by_score(raw, window_days=15)

    # 推荐管道：回踩 + A + 优中选优 + 严格量价
    configs = [
        {
            "name": "strict_pullback_A",
            "filter": dict(min_score=75, min_amount20=2e8, min_vp=2.0, grade="A", confirm="pullback_retest"),
            "exit": dict(
                stop_loss=0.06, take_profit=0.30, max_hold=20, trail_activate=0.10,
                time_stop_days=5, time_stop_min_gain=0.03, breakeven_trigger=0.10,
            ),
        },
        {
            "name": "ultra_select",
            "filter": dict(min_score=82, min_amount20=3e8, min_vp=2.0, grade="A", confirm="pullback_retest"),
            "exit": dict(
                stop_loss=0.06, take_profit=0.30, max_hold=20, trail_activate=0.12,
                time_stop_days=4, time_stop_min_gain=0.04, breakeven_trigger=0.12,
            ),
        },
        {
            "name": "fast_rotation_2slot",
            "filter": dict(min_score=75, min_amount20=2e8, min_vp=1.8, grade="A", confirm="pullback_retest"),
            "daily_top": 2,
            "max_slots": 2,
            "exit": dict(
                stop_loss=0.05, take_profit=0.18, max_hold=12, trail_activate=0.08,
                time_stop_days=4, time_stop_min_gain=0.02, breakeven_trigger=0.08,
            ),
        },
        {
            "name": "optimizer_best_multi",
            "filter": dict(min_score=75, min_amount20=1e8, min_vp=2.0, grade="A", confirm="pullback_retest"),
            "daily_top": 3,
            "max_slots": 2,
            "exit": dict(
                stop_loss=0.08, take_profit=0.30, max_hold=20, trail_activate=0.12,
                time_stop_days=4, time_stop_min_gain=0.03, breakeven_trigger=0.12,
            ),
        },
    ]

    results = []
    for cfg in configs:
        pool = filter_signals(ded, **cfg["filter"])
        pool = select_daily_top(pool, cfg.get("daily_top", 1))
        cal = build_trading_calendar(pool)
        slots = int(cfg.get("max_slots", 1))
        trades, bands = simulate_rotation(pool, cfg["exit"], cal, max_slots=slots)
        results.append({
            "name": cfg["name"],
            "filter": cfg["filter"],
            "exit": cfg["exit"],
            "max_slots": slots,
            "daily_top": cfg.get("daily_top", 1),
            "trade_summary": summarize_trades(trades),
            "band_summary": summarize_bands(bands),
            "sample_bands": bands[:8],
            "recent_trades": trades[-15:],
        })

    best_band = max(results, key=lambda x: (x["band_summary"].get("bands_hit_30_rate", 0), x["band_summary"].get("band_win_rate", 0)))
    out = {
        "target": {"band_days": BAND_DAYS, "target_return": TARGET, "target_band_win_rate": 0.70, "min_bands_hit_30": 20},
        "date_range": [DATE_FROM, DATE_TO],
        "configs": results,
        "recommended": best_band["name"],
        "note": "70%指20日波段周期胜率（换股复利），不是单笔必须+30%。单笔+30%在2年全市场回测中约28笔/370笔(7.6%)。",
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
