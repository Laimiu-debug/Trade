"""网格搜索节奏波买点参数，对齐唯科目标日。"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.force_rhythm_strategy import (
    calculate_force_rhythm_signal,
    evaluate_force_rhythm_signal,
    resolve_force_rhythm_params,
)
from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from app.store import InMemoryStore

SYMBOL = "sz301326"
DATE_FROM = "2026-03-01"
DATE_TO = "2026-06-04"
WANT = {"2026-04-08", "2026-04-30", "2026-06-02"}
PURPLE = set()


def _purple_flip(ths: dict) -> bool:
    return bool(ths.get("purple_to_yellow"))


def _eval_custom(indicator: dict, ths: dict, params: dict) -> bool:
    from app.core.force_rhythm_strategy import (
        detect_main_peak_sell_signal,
        detect_retail_sell_signal,
        detect_rhythm_wave_pattern,
    )

    cfg = resolve_force_rhythm_params(params)
    pattern = detect_rhythm_wave_pattern(indicator, params)
    retail = detect_retail_sell_signal(indicator, params)
    peak = detect_main_peak_sell_signal(indicator, params)

    if not pattern["rhythm_pattern_formed"]:
        return False

    trough_pct = float(indicator.get("trough_percentile", 0.5))
    main_state = str(indicator.get("main_force_state") or "flat")
    prev_state = str(ths.get("prev_main_force_state") or "flat")
    flip = prev_state == "falling" and main_state == "rising"

    peak_reject = float(params.get("peak_reject_percentile_min", 0.72))
    if trough_pct >= peak_reject:
        return False

    deep_trough_max = float(params.get("deep_trough_percentile_max", 0.08))
    flip_band_min = float(params.get("flip_trough_percentile_min", 0.30))
    flip_band_max = float(params.get("flip_trough_percentile_max", 0.55))

    deep_trough = trough_pct <= deep_trough_max and main_state in {"falling", "flat"}
    flip_band = flip and flip_band_min <= trough_pct <= flip_band_max
    classic_turn = bool(indicator.get("trough_turn"))

    trigger_mode = str(params.get("buy_trigger_mode", "combined"))
    if trigger_mode == "flip_only":
        triggered = flip_band
    elif trigger_mode == "turn_only":
        triggered = classic_turn
    else:
        triggered = flip_band or deep_trough or (
            classic_turn and trough_pct <= cfg["trough_percentile_max"]
        )

    if not triggered:
        return False

    retail_block_pct = float(params.get("retail_block_min_trough_percentile", 0.25))
    if cfg["block_buy_on_retail_sell"] and retail["retail_sell_signal"]:
        if trough_pct > retail_block_pct:
            return False

    if peak["main_peak_sell_signal"] and not deep_trough:
        return False

    return True


def main() -> None:
    store = InMemoryStore()
    candles = store._ensure_candles(SYMBOL)
    date_index = {c.time: i for i, c in enumerate(candles)}
    scan_dates = [c.time for c in candles if DATE_FROM <= c.time <= DATE_TO]

    snapshots: list[tuple[str, dict, dict]] = []
    for as_of in scan_dates:
        sliced = candles[: date_index[as_of] + 1]
        if len(sliced) < 40:
            continue
        ths = calculate_ths_main_retail_signal(sliced)
        rhythm = calculate_force_rhythm_signal(sliced)
        if _purple_flip(ths):
            PURPLE.add(as_of)
        snapshots.append((as_of, ths, rhythm))

    grid = {
        "trough_percentile_max": [0.45, 0.50, 0.55],
        "flip_trough_percentile_min": [0.30, 0.35, 0.40],
        "flip_trough_percentile_max": [0.50, 0.55, 0.60],
        "deep_trough_percentile_max": [0.05, 0.08, 0.12, 0.15],
        "peak_reject_percentile_min": [0.70, 0.75, 0.80],
        "retail_block_min_trough_percentile": [0.20, 0.25, 0.30],
    }

    best = None
    keys = list(grid.keys())
    for values in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, values))
        params["block_buy_on_retail_sell"] = True
        hits: set[str] = set()
        all_dates: list[str] = []
        for as_of, ths, rhythm in snapshots:
            if _eval_custom(rhythm, ths, params):
                all_dates.append(as_of)
                if as_of in WANT:
                    hits.add(as_of)

        if hits == WANT:
            false_purple = [d for d in all_dates if d in PURPLE and d not in WANT]
            extra = [d for d in all_dates if d not in WANT and d not in PURPLE]
            score = len(all_dates) + len(false_purple) * 2 + len(extra)
            if best is None or score < best[0]:
                best = (score, params, sorted(all_dates), sorted(false_purple))

    print("PURPLE dates:", sorted(PURPLE))
    if best:
        print("BEST score", best[0])
        print("params", best[1])
        print("signals", best[2])
        print("extra purple hits", best[3])
    else:
        print("No exact match; showing partial best...")
        partial_best = None
        for values in itertools.product(*(grid[k] for k in keys)):
            params = dict(zip(keys, values))
            params["block_buy_on_retail_sell"] = True
            hits = set()
            all_dates = []
            for as_of, ths, rhythm in snapshots:
                if _eval_custom(rhythm, ths, params):
                    all_dates.append(as_of)
                    if as_of in WANT:
                        hits.add(as_of)
            if len(hits) > (partial_best[0] if partial_best else -1):
                partial_best = (len(hits), sorted(hits), params, sorted(all_dates))
        if partial_best:
            print("partial", partial_best)


if __name__ == "__main__":
    main()
