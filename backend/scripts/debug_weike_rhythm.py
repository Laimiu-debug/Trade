"""唯科科技节奏波 vs 紫转黄信号对比诊断。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.force_rhythm_strategy import (
    calculate_force_rhythm_signal,
    detect_rhythm_wave_pattern,
    detect_retail_sell_signal,
    evaluate_force_rhythm_signal,
)
from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from app.store import InMemoryStore

SYMBOL = "sz301326"
DATE_FROM = "2026-03-01"
DATE_TO = "2026-06-04"
TARGETS = {"2026-04-08", "2026-04-28", "2026-04-30", "2026-06-02", "2026-04-01", "2026-04-03"}


def main() -> None:
    store = InMemoryStore()
    candles = store._ensure_candles(SYMBOL)
    if not candles:
        print("no candles")
        return

    date_index = {c.time: i for i, c in enumerate(candles)}
    scan_dates = [c.time for c in candles if DATE_FROM <= c.time <= DATE_TO]

    purple_dates: list[str] = []
    rhythm_dates: list[str] = []
    near_miss: list[dict] = []
    daily_rows: list[dict] = []

    for as_of in scan_dates:
        idx = date_index[as_of]
        sliced = candles[: idx + 1]
        if len(sliced) < 40:
            continue

        ths = calculate_ths_main_retail_signal(sliced)
        rhythm = calculate_force_rhythm_signal(sliced)
        ev = evaluate_force_rhythm_signal(rhythm)
        pattern = detect_rhythm_wave_pattern(rhythm)
        retail = detect_retail_sell_signal(rhythm)

        row = {
            "date": as_of,
            "purple": bool(ths.get("purple_to_yellow")),
            "rhythm_signal": bool(ev.get("signal")),
            "pattern": bool(pattern.get("rhythm_pattern_formed")),
            "trough_turn": bool(rhythm.get("trough_turn")),
            "trough_pct": round(float(rhythm.get("trough_percentile", 0)), 3),
            "retail_sell": bool(retail.get("retail_sell_signal")),
            "retail_pct": round(float(rhythm.get("retail_percentile", 0)), 3),
            "cycle_count": int(rhythm.get("cycle_count", 0)),
            "cycle_cv": rhythm.get("cycle_cv"),
            "amp_cv": rhythm.get("amplitude_cv"),
            "autocorr": rhythm.get("autocorr"),
            "swing": rhythm.get("wave_swing_ratio"),
            "main_state": rhythm.get("main_force_state"),
            "prev_main_state": ths.get("prev_main_force_state"),
            "main": round(float(rhythm.get("main_force", 0)), 1),
            "retail": round(float(rhythm.get("retail_force", 0)), 1),
        }
        daily_rows.append(row)

        if ths.get("purple_to_yellow"):
            purple_dates.append(as_of)
        if ev.get("signal"):
            rhythm_dates.append(as_of)

        if as_of in TARGETS or ths.get("purple_to_yellow"):
            near_miss.append(row)

    print("=== 紫转黄触发日 ===")
    print(purple_dates)
    print("\n=== 节奏波买点 ===")
    print(rhythm_dates)
    print("\n=== 目标日 & 紫转黄明细 ===")
    for row in near_miss:
        mark = " *TARGET*" if row["date"] in TARGETS else ""
        print(row, mark)

    print("\n=== 目标覆盖 ===")
    for d in sorted(TARGETS):
        hit_p = d in purple_dates
        hit_r = d in rhythm_dates
        print(f"{d}: purple={hit_p} rhythm={hit_r}")

    print("\n=== 4月下旬~6月初逐日 ===")
    for row in daily_rows:
        d = row["date"]
        if "2026-04-20" <= d <= "2026-05-05" or "2026-05-25" <= d <= "2026-06-06":
            mark = " *TARGET*" if d in TARGETS else ""
            print(
                d,
                "P" if row["purple"] else "-",
                "R" if row["rhythm_signal"] else "-",
                f"tp={row['trough_pct']}",
                f"tt={row['trough_turn']}",
                f"rs={row['retail_sell']}",
                row["prev_main_state"],
                row["main_state"],
                mark,
            )


if __name__ == "__main__":
    main()
