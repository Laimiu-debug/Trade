"""对比 紫转黄 / 金叉 / 主散节奏波 在单票与全市场的回测表现。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.force_rhythm_strategy import calculate_force_rhythm_signal, evaluate_force_rhythm_signal
from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from app.models import BacktestRunRequest
from app.store import InMemoryStore

SYMBOL = "sz301326"
SYMBOL_NAME = "唯科科技"
DATE_FROM = "2026-03-01"
DATE_TO = "2026-06-04"

STRATEGIES = [
    ("ths_main_force_flip_v1", "主力紫转黄V1", "purple_to_yellow"),
    ("ths_main_force_golden_cross_v1", "主力金叉V1", "golden_cross"),
    ("ths_force_rhythm_v1", "主散节奏波V1", "rhythm"),
]


def _summarize_trades(resp, *, label: str) -> dict:
    stats = resp.stats
    trades = list(resp.trades or [])
    return {
        "label": label,
        "strategy_id": resp.strategy_id,
        "trade_count": int(stats.trade_count),
        "win_rate": round(float(stats.win_rate), 4),
        "total_return": round(float(stats.total_return), 4),
        "max_drawdown": round(float(stats.max_drawdown), 4),
        "avg_pnl_ratio": round(float(stats.avg_pnl_ratio), 4),
        "profit_factor": round(float(stats.profit_factor), 4),
        "candidate_count": int(resp.candidate_count),
        "fill_rate": round(float(resp.fill_rate), 4),
        "sample_trades": [
            {
                "symbol": t.symbol,
                "name": t.name,
                "signal_date": t.signal_date,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_signal": t.entry_signal,
                "pnl_ratio": round(float(t.pnl_ratio), 4),
                "holding_days": int(t.holding_days),
            }
            for t in trades[:8]
        ],
    }


def _build_backtest_payload(strategy_id: str, *, full_market: bool) -> BacktestRunRequest:
    return BacktestRunRequest(
        mode="full_market" if full_market else "trend_pool",
        strategy_id=strategy_id,
        date_from=DATE_FROM,
        date_to=DATE_TO,
        window_days=80,
        min_score=0.0,
        require_sequence=False,
        min_event_count=0,
        entry_events=[],
        exit_events=[],
        initial_capital=1_000_000.0,
        position_pct=0.2,
        max_positions=5,
        stop_loss=0.08,
        take_profit=0.20,
        max_hold_days=20,
        fee_bps=10.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=200 if full_market else 120,
        pool_roll_mode="daily",
        enable_advanced_analysis=False,
        execution_path_preference="legacy",
        board_filters=[] if full_market else ["gem"],
    )


def analyze_single_stock_signals(store: InMemoryStore) -> dict:
    candles = store._ensure_candles(SYMBOL)
    if not candles:
        return {"error": f"无法加载 {SYMBOL} K线数据"}

    signal_rows: dict[str, list[dict]] = {sid: [] for sid, _, _ in STRATEGIES}
    simple_pnl: dict[str, list[float]] = {sid: [] for sid, _, _ in STRATEGIES}

    date_index = {c.time: idx for idx, c in enumerate(candles)}
    scan_dates = [c.time for c in candles if DATE_FROM <= c.time <= DATE_TO]

    for as_of in scan_dates:
        end_idx = date_index.get(as_of)
        if end_idx is None:
            continue
        sliced = candles[: end_idx + 1]
        if len(sliced) < 40:
            continue

        ths = calculate_ths_main_retail_signal(sliced)
        rhythm = calculate_force_rhythm_signal(sliced)
        rhythm_eval = evaluate_force_rhythm_signal(rhythm)

        close_today = float(sliced[-1].close)
        close_5d = float(sliced[-6].close) if len(sliced) >= 6 else close_today
        close_10d = float(sliced[-11].close) if len(sliced) >= 11 else close_today
        ret5 = (close_today - close_5d) / max(close_5d, 1e-6)
        ret10 = (close_today - close_10d) / max(close_10d, 1e-6)

        checks = [
            ("ths_main_force_flip_v1", bool(ths.get("purple_to_yellow")), float(ths.get("signal_score", 0.0))),
            ("ths_main_force_golden_cross_v1", bool(ths.get("golden_cross")), float(ths.get("signal_score", 0.0))),
            ("ths_force_rhythm_v1", bool(rhythm_eval.get("signal")), float(rhythm_eval.get("signal_score", 0.0))),
        ]
        for sid, triggered, score in checks:
            if not triggered:
                continue
            signal_rows[sid].append(
                {
                    "date": as_of,
                    "score": round(score, 2),
                    "main_force": round(float(ths.get("main_force", rhythm.get("main_force", 0.0))), 2),
                    "retail_force": round(float(ths.get("retail_force", rhythm.get("retail_force", 0.0))), 2),
                    "fwd_ret_5d": round(ret5, 4),
                    "fwd_ret_10d": round(ret10, 4),
                    "rhythm_score": round(float(rhythm.get("rhythm_regularity_score", 0.0)), 2),
                    "cycle_count": int(rhythm.get("cycle_count", 0)),
                }
            )
            simple_pnl[sid].append(ret10)

    summary = {}
    for sid, name, _ in STRATEGIES:
        rows = signal_rows[sid]
        pnls = simple_pnl[sid]
        summary[sid] = {
            "name": name,
            "signal_count": len(rows),
            "signal_dates": [r["date"] for r in rows],
            "avg_fwd_ret_10d": round(sum(pnls) / len(pnls), 4) if pnls else None,
            "win_rate_10d": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
            "details": rows,
        }

    latest_rhythm = calculate_force_rhythm_signal(candles)
    return {
        "symbol": SYMBOL,
        "name": SYMBOL_NAME,
        "candle_count": len(candles),
        "date_range": [candles[0].time, candles[-1].time],
        "latest_rhythm_regularity_score": float(latest_rhythm.get("rhythm_regularity_score", 0.0)),
        "latest_cycle_count": int(latest_rhythm.get("cycle_count", 0)),
        "strategies": summary,
    }


def run_full_market_backtests(store: InMemoryStore) -> list[dict]:
    results: list[dict] = []
    for sid, name, _ in STRATEGIES:
        payload = _build_backtest_payload(sid, full_market=True)
        print(f"[全市场] 回测 {name} ({sid}) ...", flush=True)
        resp = store.run_backtest(payload)
        item = _summarize_trades(resp, label=name)
        weike_trades = [
            {
                "signal_date": t.signal_date,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_signal": t.entry_signal,
                "pnl_ratio": round(float(t.pnl_ratio), 4),
            }
            for t in resp.trades
            if t.symbol.lower() == SYMBOL
        ]
        item["weike_trades"] = weike_trades
        results.append(item)
    return results


def run_weike_engine_backtests(store: InMemoryStore) -> list[dict]:
    results: list[dict] = []
    for sid, name, _ in STRATEGIES:
        payload = _build_backtest_payload(sid, full_market=True)
        payload = payload.model_copy(update={"max_symbols": 50, "max_positions": 1, "position_pct": 1.0})
        print(f"[唯科单票引擎] 回测 {name} ...", flush=True)
        resp = store.run_backtest(payload)
        weike = [t for t in resp.trades if t.symbol.lower() == SYMBOL]
        resp_filtered = resp.model_copy(update={"trades": weike})
        resp_filtered.stats = resp.stats.model_copy(update={"trade_count": len(weike)})
        item = _summarize_trades(resp_filtered, label=name)
        item["all_market_trade_count"] = len(resp.trades)
        results.append(item)
    return results


def main() -> None:
    store = InMemoryStore()
    cfg = store._config
    print("tdx_data_path:", getattr(cfg, "tdx_data_path", ""))
    print("market_data_source:", getattr(cfg, "market_data_source", ""))
    print("date_range:", DATE_FROM, "->", DATE_TO)
    print()

    print("=" * 70)
    print("1) 唯科科技 三策略信号对比（逐日扫描 + 信号后10日收益）")
    print("=" * 70)
    single = analyze_single_stock_signals(store)
    print(json.dumps(single, ensure_ascii=False, indent=2))
    print()

    print("=" * 70)
    print("2) 全市场回测（创业板 gem，三策略）")
    print("=" * 70)
    full_market = run_full_market_backtests(store)
    print(json.dumps(full_market, ensure_ascii=False, indent=2))
    print()

    out_path = ROOT / "scripts" / "compare_ths_strategies_backtest_result.json"
    out_path.write_text(
        json.dumps(
            {
                "single_stock": single,
                "full_market": full_market,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("结果已写入:", out_path)


if __name__ == "__main__":
    main()
