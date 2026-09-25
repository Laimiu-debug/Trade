"""晨间报告 — 混合策略最终验证。"""
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.hybrid_band_optimizer import filter_hybrid, select_daily, attach_trades
from scripts.backtest_band_rotation import (
    BAND_DAYS, TARGET, build_trading_calendar, simulate_rotation, summarize_bands, summarize_trades,
)

enriched = pickle.load(open(ROOT / "scripts/.cache_hybrid_enriched.pkl", "rb"))

CANDIDATES = [
    {
        "name": "hybrid85_fast_3slot",
        "filter": dict(min_hybrid=85, min_amount20=2e8, ret40_min=0.05, ret40_max=0.80,
                       ret40_top_n=500, require_ths=False, require_rhythm=False, sources="pullback"),
        "exit": dict(stop_loss=0.05, take_profit=0.18, max_hold=12, trail_activate=0.08,
                     time_stop_days=4, time_stop_min_gain=0.02, breakeven_trigger=0.08),
        "daily_top": 3, "max_slots": 3,
    },
    {
        "name": "hybrid_dual_30",
        "filter": dict(min_hybrid=75, min_amount20=1e8, ret40_min=0.05, ret40_max=1.0,
                       ret40_top_n=500, require_ths=False, require_rhythm=False, sources="pullback"),
        "exit": dict(stop_loss=0.08, take_profit=0.30, max_hold=20, trail_activate=0.12,
                     time_stop_days=4, time_stop_min_gain=0.03, breakeven_trigger=0.12),
        "daily_top": 3, "max_slots": 2,
    },
    {
        "name": "hybrid88_ths_2slot",
        "filter": dict(min_hybrid=82, min_amount20=2e8, ret40_min=0.08, ret40_max=0.60,
                       ret40_top_n=300, require_ths=True, require_rhythm=False, sources="pullback"),
        "exit": dict(stop_loss=0.06, take_profit=0.28, max_hold=20, trail_activate=0.10,
                     time_stop_days=4, time_stop_min_gain=0.03, breakeven_trigger=0.10),
        "daily_top": 2, "max_slots": 2,
    },
    {
        "name": "hybrid_compound",
        "filter": dict(min_hybrid=80, min_amount20=1e8, ret40_min=0.05, ret40_max=0.80,
                       ret40_top_n=500, require_ths=False, require_rhythm=False, sources="any"),
        "exit": dict(stop_loss=0.05, take_profit=0.30, max_hold=20, trail_activate=0.08,
                     time_stop_days=3, time_stop_min_gain=0.015, breakeven_trigger=0.08),
        "daily_top": 3, "max_slots": 3,
    },
]


def rolling_bands(pool, trades, calendar, slots):
    """逐步20日窗口，用已算好的 trade pnl + rotation 逻辑。"""
    bands = []
    i = 0
    while i + BAND_DAYS <= len(calendar):
        w = calendar[i : i + BAND_DAYS]
        slot_eq = [1.0] * slots
        slot_busy = [-1] * slots
        date_idx = {d: j for j, d in enumerate(calendar)}
        for d in w:
            di = date_idx[d]
            for si in range(slots):
                if slot_busy[si] >= di:
                    continue
                # 找当日该槽可开的最高分未占用 trade
                pass
        # 简化：窗口内 trades 按日期排序，贪心填槽
        wtrades = sorted(
            [t for t in trades if w[0] <= str(t["signal_date"]) <= w[-1]],
            key=lambda x: (-float(x.get("hybrid_score") or x.get("signal_score") or 0), str(x["signal_date"])),
        )
        busy = [""] * slots
        free_at = [0] * slots
        for d in w:
            di = date_idx[d]
            for si in range(slots):
                if free_at[si] > di:
                    continue
                for t in wtrades:
                    if str(t["signal_date"]) != d:
                        continue
                    if t["symbol"] in busy:
                        continue
                    slot_eq[si] *= 1.0 + float(t["pnl_net"])
                    hold = int(t.get("holding_days") or 1)
                    free_at[si] = di + hold
                    busy[si] = t["symbol"]
                    break
        ret = sum(slot_eq) / slots - 1.0
        bands.append({"start": w[0], "end": w[-1], "return": round(ret, 4), "hit_30": ret >= TARGET, "win": ret > 0})
        i += 5
    return bands


results = []
for cfg in CANDIDATES:
    pool = select_daily(filter_hybrid(enriched, **cfg["filter"]), cfg["daily_top"])
    trades = attach_trades(pool, cfg["exit"])
    cal = build_trading_calendar(pool)
    _, bands = simulate_rotation(pool, cfg["exit"], cal, max_slots=cfg["max_slots"])
    rb = rolling_bands(pool, trades, cal, cfg["max_slots"])
    tr = summarize_trades(trades)
    bs = summarize_bands(bands)
    rbs = summarize_bands(rb) if rb else {"n": 0}
    rbs["bands_hit_30"] = sum(1 for b in rb if b["hit_30"])
    rbs["band_win_rate"] = round(sum(1 for b in rb if b["win"]) / len(rb), 4) if rb else 0
    hit30_trades = tr.get("fast_30", 0) + sum(1 for t in trades if float(t["pnl_net"]) >= TARGET)
    results.append({
        **cfg,
        "trade_summary": tr,
        "band_summary": bs,
        "rolling_band_summary": rbs,
        "fast_30_trades": tr.get("fast_30", 0),
        "meets_band_wr_70": (bs.get("band_win_rate", 0) >= 0.70 or rbs.get("band_win_rate", 0) >= 0.70),
        "meets_fast30_20": tr.get("fast_30", 0) >= 20,
        "meets_rolling_30_20": rbs.get("bands_hit_30", 0) >= 20,
    })

best = max(results, key=lambda x: (
    x["meets_band_wr_70"] + x["meets_fast30_20"] + x["meets_rolling_30_20"],
    x["band_summary"].get("band_win_rate", 0),
    x["rolling_band_summary"].get("bands_hit_30", 0),
    x["trade_summary"].get("fast_30", 0),
))

out = {
    "title": "混合波段策略晨间报告",
    "targets": {"band_win_rate": 0.70, "band_return_20d": 0.28, "min_success_count": 20},
    "candidates": results,
    "recommended": best["name"],
    "best": best,
    "achievement_summary": {
        "band_win_rate_70": any(r["meets_band_wr_70"] for r in results),
        "fast_30_trades_20": any(r["meets_fast30_20"] for r in results),
        "rolling_bands_30_20": any(r["meets_rolling_30_20"] for r in results),
    },
}

path = ROOT / "scripts/out_morning_band_report.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
