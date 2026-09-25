"""涨停套利 — 趋势池 step1 限定胜率回测（走系统回测引擎 + 逐笔统计）。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import BacktestRunRequest
from app.store import InMemoryStore


def _summarize_trades(trades: list) -> dict:
    if not trades:
        return {"trade_count": 0}
    total = float(len(trades))
    take_profit_hits = sum(1 for t in trades if str(getattr(t, "exit_reason", "")) == "take_profit")
    return {
        "trade_count": len(trades),
        "win_rate_hit_take_profit": round(take_profit_hits / total, 4),
        "win_rate_positive": round(sum(1 for t in trades if float(t.pnl_ratio) > 0) / total, 4),
        "avg_pnl_ratio": round(sum(float(t.pnl_ratio) for t in trades) / total, 4),
        "total_return_engine": None,
        "exit_reason_breakdown": {
            reason: int(sum(1 for t in trades if str(getattr(t, "exit_reason", "")) == reason))
            for reason in sorted({str(getattr(t, "exit_reason", "")) for t in trades})
        },
    }


def main() -> None:
    store = InMemoryStore()
    payload = BacktestRunRequest(
        mode="trend_pool",
        trend_step="step1",
        pool_roll_mode="daily",
        strategy_id="limit_up_arb_v1",
        strategy_params={
            "min_day_gain": 3.0,
            "min_volume_ratio_prev": 1.2,
            "min_volume_ratio_ma5": 0.0,
        },
        date_from="2024-01-01",
        date_to="2026-06-04",
        window_days=80,
        min_score=0.0,
        require_sequence=False,
        min_event_count=0,
        entry_events=[],
        exit_events=[],
        initial_capital=1_000_000.0,
        position_pct=1.0,
        max_positions=100,
        stop_loss=0.0,
        take_profit=0.03,
        max_hold_days=1,
        fee_bps=10.0,
        prioritize_signals=False,
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=120,
        enable_advanced_analysis=False,
        execution_path_preference="legacy",
    )
    resp = store.run_backtest(payload)
    trades = list(resp.trades or [])
    by_month: dict[str, list] = defaultdict(list)
    for trade in trades:
        by_month[str(trade.signal_date)[:7]].append(trade)

    summary = _summarize_trades(trades)
    summary["total_return_engine"] = round(float(resp.stats.total_return), 4)
    summary["win_rate_engine"] = round(float(resp.stats.win_rate), 4)

    result = {
        "pool_filter": {
            "mode": "trend_pool",
            "trend_step": "step1",
            "pool_roll_mode": "daily",
            "max_symbols_per_day": 120,
        },
        "exit_config": {"take_profit": 0.03, "stop_loss": 0.0, "max_hold_days": 1},
        "date_range": [payload.date_from, payload.date_to],
        "candidate_count": int(resp.candidate_count),
        "summary": summary,
        "monthly": {
            month: _summarize_trades(items)
            for month, items in sorted(by_month.items())
        },
        "notes": list(resp.notes or [])[:8],
    }
    out_path = ROOT / "scripts" / "limit_up_arb_step1_backtest_result.json"
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    out_path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
