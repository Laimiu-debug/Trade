"""Diagnostic: compare get_signals vs backtest (position + step1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import BacktestRunRequest
from app.store import InMemoryStore

RUN_ID = "1779963701941-93ef26"
STRATEGY = "ths_main_force_golden_cross_v1"
DATE_FROM = "2026-01-01"


def summarize_backtest(store: InMemoryStore, date_to: str) -> dict:
    payload = BacktestRunRequest(
        mode="trend_pool",
        run_id=RUN_ID,
        trend_step="step1",
        pool_roll_mode="position",
        strategy_id=STRATEGY,
        date_from=DATE_FROM,
        date_to=date_to,
        execution_path_preference="legacy",
        enable_advanced_analysis=False,
        max_symbols=120,
        min_score=55,
    )
    resp = store.run_backtest(payload)
    last_day = date_to
    plan = [s for s in resp.plan_signals if s.signal_date == last_day]
    open_s = [s for s in resp.open_signals if s.signal_date == last_day]
    trades_baoding = [t for t in resp.trades if t.symbol == "sz002552"]
    notes_pool = [n for n in (resp.notes or []) if "候选池" in n or "持仓触发" in n or "刷新" in n]
    return {
        "date_to": date_to,
        "plan_count_last_day": len(plan),
        "plan_symbols_last_day": [s.symbol for s in plan[:15]],
        "open_count_last_day": len(open_s),
        "open_symbols_last_day": [s.symbol for s in open_s[:15]],
        "trade_count": len(resp.trades),
        "baoding_trades": [
            {"entry": t.entry_date, "exit": t.exit_date, "signal": t.signal_date}
            for t in trades_baoding
        ],
        "pool_notes": notes_pool[:6],
        "tail_notes": (resp.notes or [])[-8:],
    }


def summarize_signals(store: InMemoryStore, as_of_date: str) -> dict:
    # Plain signals (static run pool when as_of == run.as_of_date)
    plain = store.get_signals(
        mode="trend_pool",
        run_id=RUN_ID,
        trend_step="step1",
        strategy_id=STRATEGY,
        as_of_date=as_of_date,
        refresh=True,
        min_score=60,
    )
    # Replay signals aligned with backtest position rolling
    replay = store.get_signals(
        mode="trend_pool",
        run_id=RUN_ID,
        trend_step="step1",
        strategy_id=STRATEGY,
        as_of_date=as_of_date,
        refresh=True,
        min_score=55,
        backtest_date_from=DATE_FROM,
        backtest_pool_roll_mode="position",
        backtest_max_symbols=120,
    )
    plain_syms = [s.symbol for s in plain.signals if s.signal_date == as_of_date]
    replay_syms = [s.symbol for s in replay.signals if s.signal_date == as_of_date]
    return {
        "as_of_date": as_of_date,
        "plain_count": len(plain.signals),
        "plain_today": plain_syms[:15],
        "plain_notes": (plain.notes or [])[-4:],
        "replay_count": len(replay.signals),
        "replay_today": replay_syms[:15],
        "replay_notes": (replay.notes or [])[-6:],
    }


def main() -> None:
    store = InMemoryStore()
    run = store.get_screener_run(RUN_ID)
    print("run_id:", RUN_ID)
    print("run as_of_date:", run.as_of_date if run else "?")
    print("step1 pool size (static run):", len(run.step_pools.step1) if run else "?")
    print("tdx:", store._config.tdx_data_path)
    print()

    for date_to in ("2026-05-22", "2026-05-26", "2026-05-28"):
        print("=" * 60)
        print("DATE_TO:", date_to)
        try:
            bt = summarize_backtest(store, date_to)
            print(json.dumps(bt, ensure_ascii=False, indent=2))
        except Exception as exc:
            print("backtest ERROR:", exc)
        try:
            sig = summarize_signals(store, date_to)
            print(json.dumps(sig, ensure_ascii=False, indent=2))
        except Exception as exc:
            print("signals ERROR:", exc)
        print()


if __name__ == "__main__":
    main()
