"""用户截图参数对比：max_symbols 80~200 vs 仅 80，各 2 点 LHS。"""
from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8010"


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 300) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def base_payload(*, date_from: str, date_to: str) -> dict:
    return {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
        "intraday_trailing_enabled": True,
        "intraday_trailing_reduce_ratio": 0.5,
        "trailing_stop_pct": 0.08,
    }


def plateau_payload(*, max_symbols_list: list[int], date_from: str, date_to: str) -> dict:
    intraday_pct = [max(10.0, min(100.0, v)) for v in [0.0, 100.0]]
    return {
        "base_payload": base_payload(date_from=date_from, date_to=date_to),
        "sampling_mode": "lhs",
        "sample_points": 2,
        "random_seed": 20260221,
        "window_days_list": [40, 60],
        "min_score_list": [30, 80],
        "stop_loss_list": [0.03, 0.20],
        "take_profit_list": [0.10, 1.50],
        "trailing_stop_pct_list": [0.03, 0.08],
        "intraday_trailing_reduce_ratio_list": [x / 100 for x in intraday_pct],
        "max_positions_list": [3, 8],
        "position_pct_list": [0.10, 0.50],
        "max_symbols_list": max_symbols_list,
        "priority_topk_per_day_list": [1, 10],
    }


def run_case(name: str, payload: dict, max_wait_sec: int = 600) -> None:
    print(f"\n--- {name} ---", flush=True)
    t0 = time.perf_counter()
    start = http_json("POST", "/api/backtest/plateau/tasks", payload, timeout=120)
    task_id = start["task_id"]
    print(f"task_id={task_id}", flush=True)

    last_line = ""
    while time.perf_counter() - t0 < max_wait_sec:
        st = http_json("GET", f"/api/backtest/plateau/tasks/{task_id}", timeout=60)
        prog = st.get("progress") or {}
        processed = int(prog.get("processed_points") or 0)
        total = int(prog.get("total_points") or 0)
        status = st.get("status")
        msg = str(prog.get("message", ""))[:90]
        line = f"{status} {processed}/{total} {msg}"
        if line != last_line:
            print(f"  {time.perf_counter() - t0:6.1f}s {line}", flush=True)
            last_line = line
        if status in {"succeeded", "failed", "cancelled"}:
            elapsed = time.perf_counter() - t0
            print(f"  => {status} in {elapsed:.1f}s", flush=True)
            if st.get("result"):
                for i, pt in enumerate(st["result"].get("points") or []):
                    p = pt.get("params") or {}
                    print(
                        f"     [{i}] window={p.get('window_days')} max_sym={p.get('max_symbols')} "
                        f"reduce={float(p.get('intraday_trailing_reduce_ratio', 0)):.3f}",
                        flush=True,
                    )
            return
        time.sleep(2)
    print(f"  => TIMEOUT {max_wait_sec}s", flush=True)
    try:
        http_json("POST", f"/api/backtest/plateau/tasks/{task_id}/cancel", timeout=30)
    except Exception:
        pass


def main() -> None:
    short_from, short_to = "2025-01-02", "2025-02-14"
    long_from, long_to = "2024-01-02", "2025-12-31"

    print("对比说明：采样均为 2 点、种子 20260221、含日内减仓轴 0~100%", flush=True)

    run_case(
        "A 短区间 + max_symbols 仅 80",
        plateau_payload(max_symbols_list=[80, 80], date_from=short_from, date_to=short_to),
        max_wait_sec=120,
    )
    run_case(
        "B 短区间 + max_symbols 80~200（截图）",
        plateau_payload(max_symbols_list=[80, 200], date_from=short_from, date_to=short_to),
        max_wait_sec=300,
    )
    run_case(
        "C 长区间 + max_symbols 80~200（易卡预构建）",
        plateau_payload(max_symbols_list=[80, 200], date_from=long_from, date_to=long_to),
        max_wait_sec=180,
    )


if __name__ == "__main__":
    main()
