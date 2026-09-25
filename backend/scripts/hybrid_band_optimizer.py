"""混合波段优化器 — 趋势池 + 图形量价 + THS/节奏波，通宵搜索目标配置。

目标（2024-01 ~ 2026-06 TDX）：
  - 20 日波段周期胜率 >= 70%
  - 20 日周期内组合收益 >= 28% 的周期数 >= 20（可换股复利）
  - 或 20 日内单笔触达 30% 的交易 >= 20
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections import defaultdict
from dataclasses import asdict
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.chart_volume_swing_strategy import (
    dedupe_signals_by_score,
    resolve_chart_volume_swing_params,
)
from app.core.force_rhythm_strategy import calculate_force_rhythm_signal
from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from scripts.backtest_band_rotation import (
    BAND_DAYS,
    TARGET,
    build_trading_calendar,
    simulate_rotation,
    summarize_bands,
    summarize_trades,
)
from scripts.optimize_band_target import simulate_band_exit

CACHE = ROOT / "scripts" / ".cache_cvs_signals.pkl"
ENRICHED = ROOT / "scripts" / ".cache_hybrid_enriched.pkl"


def _ma_at(closes: list[float], idx: int, w: int) -> float:
    s = max(0, idx - w + 1)
    seg = closes[s : idx + 1]
    return sum(seg) / len(seg) if seg else closes[idx]


def _ret_n(closes: list[float], idx: int, n: int) -> float:
    s = idx - n
    if s < 0 or closes[s] <= 0:
        return 0.0
    return (closes[idx] - closes[s]) / closes[s]


def _trend_ma10_pullback(candles, si: int) -> bool:
    if si < 45:
        return False
    w = candles[: si + 1]
    closes = [float(c.close) for c in w]
    vols = [float(c.volume) for c in w]
    i = len(closes) - 1
    ma5, ma10, ma20 = _ma_at(closes, i, 5), _ma_at(closes, i, 10), _ma_at(closes, i, 20)
    if not (ma5 > ma10 > ma20 and closes[i] > ma20):
        return False
    r40 = _ret_n(closes, i, 40)
    if r40 < 0.05:
        return False
    if not (ma10 * 0.98 <= closes[i] <= ma10 * 1.03):
        return False
    ma5v = _ma_at(vols, i, 5)
    if vols[i] > ma5v * 0.85:
        return False
    up_v = down_v = 0.0
    for j in range(max(1, i - 19), i + 1):
        if closes[j] >= closes[j - 1]:
            up_v += vols[j]
        else:
            down_v += vols[j]
    return up_v >= down_v * 1.2


def enrich_signals(raw: list[dict]) -> list[dict]:
    if ENRICHED.exists():
        print(f"load enriched {ENRICHED}", flush=True)
        with ENRICHED.open("rb") as f:
            return pickle.load(f)

    ded = dedupe_signals_by_score(raw, window_days=15)
    # 每日 ret40 排名
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in ded:
        by_date[str(s["signal_date"])].append(s)
    for d, items in by_date.items():
        items.sort(key=lambda x: float(x.get("ret_40") or 0), reverse=True)
        for rank, s in enumerate(items, 1):
            s["ret40_rank"] = rank

    out: list[dict] = []
    trend_extra: list[dict] = []
    print(f"enriching {len(ded)} deduped signals...", flush=True)

    for n, s in enumerate(ded, 1):
        candles = s["candles"]
        si = int(s["entry_idx"]) - 1
        w = candles[: si + 1] if si >= 0 else candles
        ths = calculate_ths_main_retail_signal(w)
        rhythm = calculate_force_rhythm_signal(w)
        purple = bool(ths.get("purple_to_yellow"))
        golden = bool(ths.get("golden_cross"))
        trough = bool(rhythm.get("trough_turn"))
        rhythm_score = float(rhythm.get("signal_score") or 0)
        trend_pb = _trend_ma10_pullback(candles, si)

        base_score = float(s.get("signal_score") or 0)
        hybrid = base_score
        hybrid += 10.0 if purple else 0.0
        hybrid += 8.0 if golden else 0.0
        hybrid += 12.0 if trough and rhythm_score >= 55 else 0.0
        hybrid += 6.0 if trend_pb else 0.0
        rank = int(s.get("ret40_rank") or 9999)
        if rank <= 100:
            hybrid += 8.0
        elif rank <= 200:
            hybrid += 5.0
        elif rank <= 500:
            hybrid += 2.0

        row = {
            **{k: v for k, v in s.items() if k != "candles"},
            "candles": candles,
            "purple_to_yellow": purple,
            "golden_cross": golden,
            "trough_turn": trough,
            "rhythm_score": rhythm_score,
            "trend_ma10_pullback": trend_pb,
            "hybrid_score": round(hybrid, 2),
            "ret40_rank": rank,
        }
        out.append(row)

        if trend_pb and str(s.get("confirm_type")) != "pullback_retest":
            trend_extra.append({
                **row,
                "signal_source": "trend_ma10",
                "confirm_type": "trend_ma10_pullback",
                "hybrid_score": round(hybrid + 5.0, 2),
            })

        if n % 2000 == 0:
            print(f"  enriched {n}/{len(ded)}", flush=True)

    out.extend(trend_extra)
    out.sort(key=lambda x: (str(x["signal_date"]), -float(x["hybrid_score"])))
    with ENRICHED.open("wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"cached enriched n={len(out)}", flush=True)
    return out


def filter_hybrid(
    pool: list[dict],
    *,
    min_hybrid: float,
    min_amount20: float,
    ret40_min: float,
    ret40_max: float,
    ret40_top_n: int,
    require_ths: bool,
    require_rhythm: bool,
    sources: str,
) -> list[dict]:
    rows = []
    for s in pool:
        if float(s.get("hybrid_score") or 0) < min_hybrid:
            continue
        if float(s.get("amount20") or 0) < min_amount20:
            continue
        r40 = float(s.get("ret_40") or 0)
        if r40 < ret40_min or r40 > ret40_max:
            continue
        if int(s.get("ret40_rank") or 99999) > ret40_top_n:
            continue
        if require_ths and not (s.get("purple_to_yellow") or s.get("golden_cross")):
            continue
        if require_rhythm and not s.get("trough_turn"):
            continue
        src = str(s.get("signal_source") or "chart")
        if sources == "chart_only" and src != "chart":
            continue
        if sources == "trend_only" and not s.get("trend_ma10_pullback"):
            continue
        if sources == "pullback" and str(s.get("confirm_type")) not in ("pullback_retest", "trend_ma10_pullback"):
            continue
        rows.append(s)
    return rows


def select_daily(pool: list[dict], top: int) -> list[dict]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in pool:
        by_date[str(s["signal_date"])].append(s)
    picked = []
    for d in sorted(by_date):
        day = sorted(by_date[d], key=lambda x: float(x["hybrid_score"]), reverse=True)[:top]
        picked.extend(day)
    return picked


def attach_trades(pool: list[dict], exit_cfg: dict) -> list[dict]:
    trades = []
    for s in pool:
        t = simulate_band_exit(
            s["candles"], s["entry_idx"], s["entry_price"],
            box_high=float(s.get("box_high") or s["entry_price"] * 0.95),
            **exit_cfg,
        )
        trades.append({**{k: v for k, v in s.items() if k != "candles"}, **t})
    return trades


def overlapping_bands(trades: list[dict], calendar: list[str], step: int = 5) -> list[dict]:
    """步进5日的重叠20日窗口，增加波段样本。"""
    if len(calendar) < BAND_DAYS:
        return []
    by_date = {str(t["signal_date"]): t for t in trades}
    bands = []
    i = 0
    while i + BAND_DAYS <= len(calendar):
        window = calendar[i : i + BAND_DAYS]
        eq = 1.0
        for tr in trades:
            sd = str(tr["signal_date"])
            if sd < window[0] or sd > window[-1]:
                continue
            eq *= 1.0 + float(tr["pnl_net"])
        ret = eq - 1.0
        bands.append({"start": window[0], "end": window[-1], "return": ret, "hit_30": ret >= TARGET, "win": ret > 0})
        i += step
    return bands


def summarize_overlap_bands(bands: list[dict]) -> dict[str, Any]:
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
        "best_band_return": round(max(b["return"] for b in bands), 4),
    }


def fitness(tr: dict, band: dict, ob: dict) -> float:
    if tr.get("n", 0) < 10:
        return -1.0
    sc = 0.0
    bwr = float(band.get("band_win_rate") or ob.get("band_win_rate") or 0)
    h30 = int(band.get("bands_hit_30") or 0) + int(ob.get("bands_hit_30") or 0)
    f30 = int(tr.get("fast_30") or 0)
    sc += bwr * 20.0
    sc += min(h30, 50) * 0.8
    sc += min(f30, 50) * 0.5
    sc += float(tr.get("win_rate") or 0) * 5.0
    if bwr >= 0.70:
        sc += 25.0
    if h30 >= 20 or ob.get("bands_hit_30", 0) >= 20:
        sc += 30.0
    if f30 >= 20:
        sc += 15.0
    if float(ob.get("best_band_return") or 0) >= TARGET:
        sc += 10.0
    return sc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "scripts" / "out_hybrid_band_final.json"))
    args = parser.parse_args()

    with CACHE.open("rb") as f:
        raw = pickle.load(f)
    enriched = enrich_signals(raw)

    filter_grid = [
        dict(min_hybrid=mh, min_amount20=amt, ret40_min=rmin, ret40_max=rmax,
             ret40_top_n=rtop, require_ths=ths, require_rhythm=rh, sources=src)
        for mh, amt, rmin, rmax, rtop, ths, rh, src in product(
            [82, 85, 88, 90, 92],
            [1e8, 2e8, 3e8],
            [0.05, 0.08],
            [0.50, 0.80, 1.20],
            [200, 500],
            [False, True],
            [False, True],
            ["any", "pullback"],
        )
    ]

    exit_presets = [
        {"name": "fast18", "stop_loss": 0.05, "take_profit": 0.18, "max_hold": 12, "trail_activate": 0.08, "time_stop_days": 4, "time_stop_min_gain": 0.02, "breakeven_trigger": 0.08},
        {"name": "band20_tp30", "stop_loss": 0.06, "take_profit": 0.30, "max_hold": 20, "trail_activate": 0.10, "time_stop_days": 5, "time_stop_min_gain": 0.03, "breakeven_trigger": 0.10},
        {"name": "band20_tp28", "stop_loss": 0.06, "take_profit": 0.28, "max_hold": 20, "trail_activate": 0.10, "time_stop_days": 4, "time_stop_min_gain": 0.03, "breakeven_trigger": 0.10},
        {"name": "tight6", "stop_loss": 0.06, "take_profit": 0.30, "max_hold": 20, "trail_activate": 0.12, "time_stop_days": 4, "time_stop_min_gain": 0.04, "breakeven_trigger": 0.12},
        {"name": "wide8", "stop_loss": 0.08, "take_profit": 0.30, "max_hold": 20, "trail_activate": 0.12, "time_stop_days": 4, "time_stop_min_gain": 0.03, "breakeven_trigger": 0.12},
        {"name": "ultra5", "stop_loss": 0.05, "take_profit": 0.25, "max_hold": 15, "trail_activate": 0.08, "time_stop_days": 3, "time_stop_min_gain": 0.02, "breakeven_trigger": 0.08},
        {"name": "rhythm12", "stop_loss": 0.06, "take_profit": 0.22, "max_hold": 18, "trail_activate": 0.10, "time_stop_days": 4, "time_stop_min_gain": 0.025, "breakeven_trigger": 0.10},
        {"name": "compound", "stop_loss": 0.05, "take_profit": 0.30, "max_hold": 20, "trail_activate": 0.08, "time_stop_days": 3, "time_stop_min_gain": 0.015, "breakeven_trigger": 0.08},
    ]
    exit_grid = [{k: v for k, v in p.items() if k != "name"} for p in exit_presets]
    exit_names = [p["name"] for p in exit_presets]

    rot_grid = [(1, 1), (2, 2), (3, 2), (3, 3)]

    best = None
    top: list[dict] = []
    exit_cache: dict[str, list[dict]] = {}
    total = len(filter_grid) * len(exit_grid) * len(rot_grid)
    done = 0

    base_pool = enriched
    print(f"search space={total} enriched={len(base_pool)}", flush=True)

    for fg in filter_grid:
        filtered = filter_hybrid(base_pool, **fg)
        if len(filtered) < 15:
            continue
        for dt, slots in rot_grid:
            pool = select_daily(filtered, dt)
            if len(pool) < 15:
                continue
            keys = {(s["symbol"], s["signal_date"]) for s in pool}
            cal = build_trading_calendar(pool)
            for ex_i, ex in enumerate(exit_grid):
                done += 1
                ex_key = exit_names[ex_i]
                if ex_key not in exit_cache:
                    print(f"  precompute exit {ex_key} ({ex_i+1}/{len(exit_grid)})...", flush=True)
                    exit_cache[ex_key] = attach_trades(enriched, ex)
                all_tr = exit_cache[ex_key]
                trades = [t for t in all_tr if (t["symbol"], t["signal_date"]) in keys]
                if len(trades) < 10:
                    continue
                tr = summarize_trades(trades)
                _, bands = simulate_rotation(
                    [s for s in pool], ex, cal, max_slots=slots,
                )
                bs = summarize_bands(bands)
                ob = summarize_overlap_bands(overlapping_bands(trades, cal, step=5))
                sc = fitness(tr, bs, ob)
                if sc < 0:
                    continue
                row = {
                    "filter": fg,
                    "exit": ex,
                    "exit_name": ex_key,
                    "daily_top": dt,
                    "max_slots": slots,
                    "trade_summary": tr,
                    "band_summary": bs,
                    "overlap_band_summary": ob,
                    "score": round(sc, 4),
                    "achieved_band_wr_70": (bs.get("band_win_rate", 0) >= 0.70 or ob.get("band_win_rate", 0) >= 0.70),
                    "achieved_bands_30_20": (bs.get("bands_hit_30", 0) >= 20 or ob.get("bands_hit_30", 0) >= 20),
                    "achieved_fast30_20": tr.get("fast_30", 0) >= 20,
                }
                top.append(row)
                if best is None or sc > best["score"]:
                    best = row
                if done % 1000 == 0:
                    print(f"  {done}/{total} best={best['score'] if best else -1}", flush=True)

    top.sort(key=lambda x: x["score"], reverse=True)
    achieved = best and (
        best.get("achieved_band_wr_70")
        and (best.get("achieved_bands_30_20") or best.get("achieved_fast30_20"))
    )
    result = {
        "strategy": "hybrid_band_v1",
        "components": ["chart_volume_swing", "ret40_rank", "ths", "force_rhythm", "trend_ma10_pullback"],
        "searched": done,
        "achieved_all_targets": achieved,
        "best": best,
        "top15": top[:15],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
