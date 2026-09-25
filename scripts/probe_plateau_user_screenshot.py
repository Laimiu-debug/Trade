"""按用户截图参数复现收益平原耗时（LHS 2 点 + 日内减仓 0~100%）。"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8010"
POLL_INTERVAL_SEC = 3


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def build_payload(*, date_to: str = "2025-12-31") -> dict:
    # 与前端 handleRunPlateau 一致的换算（日内 0 会被 clamp 到 10%）
    def pct_list(values: list[float]) -> list[float]:
        return [float(v) for v in values]

    stop_loss_pct = pct_list([3, 20])
    take_profit_pct = pct_list([10, 150])
    trailing_pct = pct_list([3, 8])
    intraday_pct = [max(10.0, min(100.0, v)) for v in pct_list([0, 100])]
    position_pct = pct_list([10, 50])

    return {
        "base_payload": {
            "mode": "full_market",
            "run_id": "",
            "trend_step": "auto",
            "pool_roll_mode": "daily",
            "board_filters": [],
            "date_from": "2024-01-02",
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
            "daily_trailing_clear_enabled": False,
            "intraday_trailing_reduce_ratio": 0.5,
            "trailing_stop_pct": 0.08,
        },
        "sampling_mode": "lhs",
        "sample_points": 2,
        "random_seed": 20260221,
        "window_days_list": [40, 60],
        "min_score_list": [30, 80],
        "stop_loss_list": [x / 100 for x in stop_loss_pct],
        "take_profit_list": [x / 100 for x in take_profit_pct],
        "trailing_stop_pct_list": [x / 100 for x in trailing_pct],
        "intraday_trailing_reduce_ratio_list": [x / 100 for x in intraday_pct],
        "max_positions_list": [3, 8],
        "position_pct_list": [x / 100 for x in position_pct],
        "max_symbols_list": [80, 200],
        "priority_topk_per_day_list": [1, 10],
    }


def run_probe(label: str, payload: dict) -> int:
    print(f"\n=== {label} ===", flush=True)
    t0 = time.perf_counter()
    start = http_json("POST", "/api/backtest/plateau/tasks", payload)
    task_id = start["task_id"]
    print(f"task_id={task_id}", flush=True)

    last_processed = -1
    prebuild_done_at: float | None = None
    first_point_at: float | None = None

    while True:
        st = http_json("GET", f"/api/backtest/plateau/tasks/{task_id}", timeout=60)
        prog = st.get("progress") or {}
        processed = int(prog.get("processed_points") or 0)
        total = int(prog.get("total_points") or 0)
        status = st.get("status")
        msg = str(prog.get("message", ""))
        elapsed = time.perf_counter() - t0

        if "预构建完成" in msg and prebuild_done_at is None:
            prebuild_done_at = elapsed
        if processed > 0 and first_point_at is None:
            first_point_at = elapsed

        if processed != last_processed or "预构建" in msg:
            print(f"  [{elapsed:7.1f}s] {status} {processed}/{total} | {msg[:100]}", flush=True)
            last_processed = processed

        if status in {"succeeded", "failed", "cancelled"}:
            print(f"  TOTAL {elapsed:.1f}s status={status}", flush=True)
            if prebuild_done_at is not None:
                print(f"  prebuild phase ~{prebuild_done_at:.1f}s", flush=True)
            elif first_point_at is not None:
                print(f"  first point at ~{first_point_at:.1f}s (prebuild+eval)", flush=True)
            result = st.get("result") or {}
            points = result.get("points") or []
            for i, pt in enumerate(points):
                p = pt.get("params") or {}
                print(
                    f"  point[{i}] window={p.get('window_days')} max_sym={p.get('max_symbols')} "
                    f"reduce={p.get('intraday_trailing_reduce_ratio')} trades={(pt.get('stats') or {}).get('trade_count')}",
                    flush=True,
                )
            return 0 if status == "succeeded" else 1
        time.sleep(POLL_INTERVAL_SEC)


def main() -> int:
    payload = build_payload()
    print("Payload highlights:", flush=True)
    print("  sample_points=2, seed=20260221", flush=True)
    print("  max_symbols axis [80,200] -> prebuild uses max=200", flush=True)
    print("  intraday reduce axis [0,100] -> sent as [0.1, 1.0] (frontend clamp)", flush=True)
    print(f"  date range {payload['base_payload']['date_from']} ~ {payload['base_payload']['date_to']}", flush=True)
    return run_probe("user screenshot params (long range)", payload)


if __name__ == "__main__":
    sys.exit(main())
