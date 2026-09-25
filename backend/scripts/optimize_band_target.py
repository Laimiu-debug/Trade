"""波段目标优化 — 20 交易日 / 30% / 换股 / 优中选优。

目标：
  - 单笔或组合波段：约 20 个交易日内争取 ~30% 收益（可换股）
  - 胜率 ~70%（交易级或 20 日波段周期级）
  - 累计至少 20 次成功 30% 波段（越快越好）

用法：
  python scripts/optimize_band_target.py              # 首次会扫描并缓存信号
  python scripts/optimize_band_target.py --use-cache  # 仅内存搜索
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.chart_volume_swing_strategy import (
    dedupe_signals_by_score,
    resolve_chart_volume_swing_params,
)
from scripts.backtest_chart_volume_swing import (
    DATE_FROM,
    DATE_TO,
    collect_signals,
    simulate_swing_exit,
)

DEFAULT_TDX = r"D:\new_tdx\vipdoc"
CACHE_PATH = ROOT / "scripts" / ".cache_cvs_signals.pkl"
FEE = 10.0 / 10_000.0

TARGET_PNL = 0.28
BAND_DAYS = 20
TARGET_WIN_RATE = 0.70
TARGET_BAND_COUNT = 20


def _parse(d: str) -> datetime:
    return datetime.strptime(str(d)[:10], "%Y-%m-%d")


def simulate_band_exit(
    candles,
    entry_idx: int,
    entry_price: float,
    *,
    box_high: float,
    stop_loss: float,
    take_profit: float,
    max_hold: int,
    trail_activate: float,
    time_stop_days: int = 5,
    time_stop_min_gain: float = 0.04,
    breakeven_trigger: float = 0.12,
) -> dict[str, Any]:
    """20日波段出场：结构止损 + 时间止损（5日不涨则走）+ 保本 + MA10跟踪。"""
    from scripts.backtest_chart_volume_swing import _ma

    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    last = min(len(candles) - 1, entry_idx + max_hold)
    exit_idx, exit_price, reason = last, closes[last], "max_hold"
    peak = entry_price
    stop_px = entry_price * (1.0 - stop_loss)
    structural = max(stop_px, box_high * 0.95)
    be_active = False

    for idx in range(entry_idx + 1, last + 1):
        peak = max(peak, highs[idx])
        gain_peak = (peak - entry_price) / entry_price
        gain_close = (closes[idx] - entry_price) / entry_price
        hold_day = idx - entry_idx

        if gain_peak >= breakeven_trigger:
            be_active = True
            structural = max(structural, entry_price * 1.002)

        if lows[idx] <= structural:
            exit_idx, exit_price = idx, structural
            reason = "breakeven_stop" if be_active and structural >= entry_price else "stop_loss"
            break

        if hold_day >= time_stop_days and gain_peak < time_stop_min_gain:
            exit_idx, exit_price, reason = idx, closes[idx], "time_stop_flat"
            break

        if closes[idx] < _ma(closes, idx, 20) * 0.97 and closes[idx] < box_high * 0.95:
            exit_idx, exit_price, reason = idx, closes[idx], "ma20_box_break"
            break

        if highs[idx] >= entry_price * (1.0 + take_profit):
            exit_idx, exit_price, reason = idx, entry_price * (1.0 + take_profit), "take_profit"
            break

        if gain_peak >= trail_activate and closes[idx] < _ma(closes, idx, 10):
            exit_idx, exit_price, reason = idx, closes[idx], "ma10_trail"
            break

    entry_exec = entry_price * (1.0 + FEE)
    exit_exec = exit_price * (1.0 - FEE)
    pnl = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
    hit_30 = pnl >= TARGET_PNL
    fast_30 = hit_30 and (exit_idx - entry_idx) <= BAND_DAYS
    return {
        "pnl_net": pnl,
        "win": pnl > 0,
        "hit_tp": reason == "take_profit",
        "hit_30": hit_30,
        "fast_30": fast_30,
        "exit_reason": reason,
        "holding_days": exit_idx - entry_idx,
        "exit_idx": exit_idx,
    }


def attach_trade_outcomes(signals: list[dict], exit_cfg: dict) -> list[dict]:
    rows: list[dict] = []
    for s in signals:
        t = simulate_band_exit(
            s["candles"],
            s["entry_idx"],
            s["entry_price"],
            box_high=float(s["box_high"]),
            **exit_cfg,
        )
        rows.append({**{k: s[k] for k in s if k != "candles"}, **t})
    return rows


def filter_signals(
    signals: list[dict],
    *,
    min_score: float,
    min_amount20: float,
    min_vp: float,
    grade: str,
    confirm: str,
) -> list[dict]:
    out = []
    for s in signals:
        if float(s["signal_score"]) < min_score:
            continue
        if float(s["amount20"]) < min_amount20:
            continue
        if float(s.get("up_down_vol_ratio") or 0) < min_vp:
            continue
        if grade != "any" and str(s.get("event_grade")) != grade:
            continue
        if confirm != "any" and str(s.get("confirm_type")) != confirm:
            continue
        out.append(s)
    return out


def select_daily_top(signals: list[dict], daily_top: int) -> list[dict]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        by_date[str(s["signal_date"])].append(s)
    picked: list[dict] = []
    for d in sorted(by_date):
        day = sorted(by_date[d], key=lambda x: float(x["signal_score"]), reverse=True)[:daily_top]
        picked.extend(day)
    return sorted(picked, key=lambda x: str(x["signal_date"]))


def summarize_trades(trades: list[dict]) -> dict[str, Any]:
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
        "hit_30": sum(1 for t in trades if t.get("hit_30")),
        "fast_30": sum(1 for t in trades if t.get("fast_30")),
        "avg_hold": round(sum(t["holding_days"] for t in trades) / len(trades), 1),
    }


def simulate_portfolio_bands(
    trades: list[dict],
    *,
    band_days: int = BAND_DAYS,
    target_pnl: float = TARGET_PNL,
    max_positions: int = 2,
) -> dict[str, Any]:
    """20 交易日波段周期：等权换股，统计周期胜率与 30% 达成次数。"""
    if not trades:
        return {"band_count": 0, "band_win_rate": 0.0, "bands_hit_30": 0}

    # 构建交易日序列
    all_dates = sorted({str(t["signal_date"]) for t in trades})
    if len(all_dates) < band_days + 1:
        return {"band_count": 0, "band_win_rate": 0.0, "bands_hit_30": 0}

    # 按日期索引交易
    by_date: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(trades, key=lambda x: (str(x["signal_date"]), -float(x["signal_score"]))):
        by_date[str(t["signal_date"])].append(t)

    band_results: list[dict] = []
    i = 0
    while i + band_days <= len(all_dates):
        window_dates = all_dates[i : i + band_days]
        capital = 1.0
        slots = max_positions
        active: list[dict] = []
        day_ptr = 0

        for d in window_dates:
            # 释放已结束仓位
            still: list[dict] = []
            for pos in active:
                if str(pos.get("_exit_date", "")) <= d:
                    capital *= 1.0 + float(pos["pnl_net"]) / max(len(active), 1)
                else:
                    still.append(pos)
            active = still
            slots = max_positions - len(active)

            # 开新仓
            cands = by_date.get(d, [])
            for c in cands[:slots]:
                active.append({**c, "_exit_date": _exit_date_from_trade(c, all_dates)})
            slots = max_positions - len(active)

        # 波段末强平
        if active:
            avg_pnl = sum(float(p["pnl_net"]) for p in active) / len(active)
            capital *= 1.0 + avg_pnl

        ret = capital - 1.0
        band_results.append({"start": window_dates[0], "end": window_dates[-1], "return": ret, "hit_30": ret >= target_pnl})
        i += band_days

    hits = sum(1 for b in band_results if b["hit_30"])
    wins = sum(1 for b in band_results if b["return"] > 0)
    return {
        "band_count": len(band_results),
        "band_win_rate": round(wins / len(band_results), 4) if band_results else 0.0,
        "bands_hit_30": hits,
        "bands_hit_30_rate": round(hits / len(band_results), 4) if band_results else 0.0,
        "avg_band_return": round(sum(b["return"] for b in band_results) / len(band_results), 4) if band_results else 0.0,
        "sample_bands": band_results[:5],
    }


def _exit_date_from_trade(trade: dict, calendar: list[str]) -> str:
    """用 holding_days 近似退出日。"""
    start = str(trade["signal_date"])
    try:
        si = calendar.index(start)
    except ValueError:
        return start
    ei = min(len(calendar) - 1, si + int(trade.get("holding_days") or 0))
    return calendar[ei]


def fitness(tr: dict[str, Any], band: dict[str, Any]) -> float:
    if tr.get("n", 0) < 30:
        return -1.0
    wr = float(tr.get("win_rate") or 0)
    f30 = int(tr.get("fast_30") or 0)
    h30 = int(tr.get("hit_30") or 0)
    b30 = int(band.get("bands_hit_30") or 0)
    bwr = float(band.get("band_win_rate") or 0)

    score = 0.0
    score += wr * 10.0
    score += min(f30, 200) * 0.15
    score += min(h30, 300) * 0.08
    score += min(b30, 100) * 0.25
    score += bwr * 8.0
    if wr >= TARGET_WIN_RATE:
        score += 15.0
    if f30 >= TARGET_BAND_COUNT:
        score += 20.0
    if b30 >= TARGET_BAND_COUNT:
        score += 25.0
    return score


def load_or_collect(tdx_root: str, use_cache: bool) -> list[dict]:
    if use_cache and CACHE_PATH.exists():
        print(f"load cache {CACHE_PATH}", flush=True)
        with CACHE_PATH.open("rb") as f:
            return pickle.load(f)
    params = asdict(resolve_chart_volume_swing_params({"min_score": 0}))
    print("collecting signals (slow, once)...", flush=True)
    sigs = collect_signals(tdx_root, params)
    with CACHE_PATH.open("wb") as f:
        pickle.dump(sigs, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"cached {len(sigs)} signals", flush=True)
    return sigs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-root", default=os.environ.get("TDX_ROOT", DEFAULT_TDX))
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "scripts" / "out_band_target_opt.json"))
    args = parser.parse_args()

    raw = load_or_collect(args.tdx_root, args.use_cache or CACHE_PATH.exists())

    filter_grid = [
        {"min_score": ms, "min_vp": vp, "grade": gr, "confirm": cf, "daily_top": dt, "min_amount20": amt}
        for ms, vp, gr, cf, dt, amt in product(
            [75, 78, 80, 82, 85],
            [1.5, 1.6, 1.8, 2.0, 2.2],
            ["A", "any"],
            ["pullback_retest"],
            [1, 2, 3],
            [1e8, 2e8, 3e8],
        )
    ]

    exit_grid = [
        {
            "stop_loss": sl,
            "take_profit": 0.30,
            "max_hold": mh,
            "trail_activate": ta,
            "time_stop_days": ts,
            "time_stop_min_gain": tg,
            "breakeven_trigger": be,
        }
        for sl, mh, ta, ts, tg, be in product(
            [0.06, 0.08, 0.10],
            [20],
            [0.10, 0.12],
            [4, 5],
            [0.03, 0.04],
            [0.10, 0.12],
        )
    ]

    best: dict | None = None
    top: list[dict] = []
    deduped_all = dedupe_signals_by_score(raw, window_days=15)

    total = len(filter_grid) * len(exit_grid)
    done = 0
    print(f"deduped={len(deduped_all)} combos={total}", flush=True)
    exit_cache: dict[str, list[dict]] = {}

    for fg in filter_grid:
        pool = filter_signals(
            deduped_all,
            min_score=fg["min_score"],
            min_amount20=fg["min_amount20"],
            min_vp=fg["min_vp"],
            grade=fg["grade"],
            confirm=fg["confirm"],
        )
        pool = select_daily_top(pool, fg["daily_top"])
        if len(pool) < 25:
            continue
        keys = {(s["symbol"], s["signal_date"]) for s in pool}
        for ex in exit_grid:
            done += 1
            ex_key = json.dumps(ex, sort_keys=True)
            if ex_key not in exit_cache:
                print(f"  precompute exit {len(exit_cache)+1}/{len(exit_grid)}...", flush=True)
                exit_cache[ex_key] = attach_trade_outcomes(deduped_all, ex)
            trades = [t for t in exit_cache[ex_key] if (t["symbol"], t["signal_date"]) in keys]
            if len(trades) < 25:
                continue
            tr = summarize_trades(trades)
            band = simulate_portfolio_bands(trades, max_positions=fg["daily_top"])
            sc = fitness(tr, band)
            if sc < 0:
                continue
            row = {
                "filter": fg,
                "exit": ex,
                "trade_summary": tr,
                "band_summary": band,
                "score": round(sc, 4),
                "meets_win_rate": tr.get("win_rate", 0) >= TARGET_WIN_RATE,
                "meets_fast_30_count": tr.get("fast_30", 0) >= TARGET_BAND_COUNT,
                "meets_band_30_count": band.get("bands_hit_30", 0) >= TARGET_BAND_COUNT,
            }
            top.append(row)
            if best is None or sc > best["score"]:
                best = row
            if done % 500 == 0:
                print(f"  progress {done}/{total} best_score={best['score'] if best else -1}", flush=True)

    top.sort(key=lambda x: x["score"], reverse=True)
    result = {
        "target": {
            "win_rate": TARGET_WIN_RATE,
            "band_days": BAND_DAYS,
            "target_pnl": TARGET_PNL,
            "min_success_bands": TARGET_BAND_COUNT,
        },
        "date_range": [DATE_FROM, DATE_TO],
        "searched": done,
        "best": best,
        "top10": top[:10],
        "achieved": best is not None and (
            best.get("meets_win_rate") and (best.get("meets_fast_30_count") or best.get("meets_band_30_count"))
        ),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
