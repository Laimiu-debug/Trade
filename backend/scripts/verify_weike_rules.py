"""验证节奏波买点规则组合。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.force_rhythm_strategy import calculate_force_rhythm_signal, detect_rhythm_wave_pattern
from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from app.store import InMemoryStore

WANT = {"2026-04-08", "2026-04-28", "2026-04-30", "2026-06-02"}
REJECT = {"2026-03-04", "2026-03-16", "2026-03-24", "2026-04-01", "2026-04-15", "2026-05-14", "2026-05-20", "2026-06-04"}


def should_buy(ths: dict, rhythm: dict, *, pattern: dict) -> tuple[bool, str]:
    if not pattern.get("rhythm_pattern_formed"):
        return False, "no_pattern"

    tp = float(rhythm.get("trough_percentile", 0.5))
    if tp >= 0.72:
        return False, "peak_zone"

    prev_state = str(ths.get("prev_main_force_state") or "flat")
    main_state = str(rhythm.get("main_force_state") or "flat")
    flip = prev_state == "falling" and main_state == "rising"

    retail_sell = bool(rhythm.get("retail_sell_signal"))
    if retail_sell and tp > 0.22:
        return False, "retail_block"

    if flip and 0.35 <= tp <= 0.52:
        return True, "flip_band"

    if bool(rhythm.get("trough_turn")) and tp <= 0.30 and tp >= 0.17:
        return True, "trough_turn_band"

    if tp <= 0.05 and main_state == "falling":
        return True, "deep_trough"

    return False, "miss"


def main() -> None:
    store = InMemoryStore()
    candles = store._ensure_candles("sz301326")
    idx = {c.time: i for i, c in enumerate(candles)}
    hits: list[str] = []
    for as_of in [c.time for c in candles if "2026-03-01" <= c.time <= "2026-06-04"]:
        s = candles[: idx[as_of] + 1]
        if len(s) < 40:
            continue
        ths = calculate_ths_main_retail_signal(s)
        r = calculate_force_rhythm_signal(s)
        p = detect_rhythm_wave_pattern(r)
        ok, reason = should_buy(ths, r, pattern=p)
        if ok:
            hits.append(as_of)
            mark = ""
            if as_of in WANT:
                mark = " WANT"
            if as_of in REJECT:
                mark = " REJECT?"
            print(as_of, reason, mark)

    print("\nWANT coverage:", {d: d in hits for d in WANT})
    print("False positives:", [d for d in hits if d in REJECT])
    print("Extra:", [d for d in hits if d not in WANT and d not in REJECT])


if __name__ == "__main__":
    main()
