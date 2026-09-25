"""涨停套利胜率回测 — 筛选逻辑走策略参数，出场逻辑对齐回测页配置。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.limit_up_arb_strategy import (
    calculate_limit_up_arb_signal,
    evaluate_limit_up_arb_exit,
    evaluate_limit_up_arb_signal,
    resolve_limit_up_arb_exit_config,
    resolve_limit_up_arb_params,
)
from app.core.market_momentum import _bars_from_candles, _iter_tdx_symbols
from app.models import CandlePoint
from app.tdx_loader import load_candles_for_symbol

DEFAULT_TDX = r"E:\TDX\vipdoc"
FEE_BPS = 10.0


def _fee_rate() -> float:
    return FEE_BPS / 10_000.0


def _bar_to_candle(bar: dict) -> CandlePoint:
    return CandlePoint(
        time=str(bar["date"]),
        open=float(bar["open"]),
        high=float(bar["high"]),
        low=float(bar["low"]),
        close=float(bar["close"]),
        volume=float(bar["volume"]),
        amount=float(bar.get("amount") or 0.0),
    )


def _scan_symbol(
    *,
    tdx_root: str,
    symbol: str,
    date_from: str,
    date_to: str,
    params: dict,
    exit_config: dict,
    allowed_symbols_by_date: dict[str, set[str]] | None = None,
) -> list[dict]:
    candles = load_candles_for_symbol(tdx_root, symbol, window=500, market_data_source="tdx_only")
    if not candles or len(candles) < 5:
        return []

    date_to_index = {str(candle.time): idx for idx, candle in enumerate(candles)}
    bars = [bar for bar in _bars_from_candles(candles) if date_from <= str(bar["date"]) <= date_to]
    if len(bars) < 3:
        return []

    trades: list[dict] = []
    for index in range(2, len(bars) - 1):
        bar = bars[index]
        bar_index = date_to_index.get(str(bar["date"]))
        if bar_index is None:
            continue
        window_candles = candles[: bar_index + 1]
        indicator = calculate_limit_up_arb_signal(window_candles, symbol=symbol)
        evaluation = evaluate_limit_up_arb_signal(indicator, params)
        if not evaluation.get("signal"):
            continue

        signal_date = str(bars[index]["date"])
        if allowed_symbols_by_date is not None:
            allowed_today = allowed_symbols_by_date.get(signal_date, set())
            if symbol not in allowed_today:
                continue

        entry_price = float(indicator.get("entry_price") or bar["close"])
        next_bar = bars[index + 1]
        exit_eval = evaluate_limit_up_arb_exit(
            entry_price,
            _bar_to_candle(next_bar),
            exit_config=exit_config,
        )
        fee = _fee_rate()
        entry_exec = entry_price * (1.0 + fee)
        exit_exec = float(exit_eval["exit_price"]) * (1.0 - fee)
        pnl_net = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0

        trades.append(
            {
                "symbol": symbol,
                "signal_date": str(bars[index]["date"]),
                "entry_price": round(entry_price, 4),
                "day_gain": float(evaluation.get("day_gain") or 0.0),
                "volume_ratio_prev": float(evaluation.get("volume_ratio_prev") or 0.0),
                "volume_ratio_ma5": float(evaluation.get("volume_ratio_ma5") or 0.0),
                "hit_take_profit": bool(exit_eval.get("hit_take_profit")),
                "hit_stop_loss": bool(exit_eval.get("hit_stop_loss")),
                "exit_reason": str(exit_eval.get("exit_reason")),
                "pnl_gross": float(exit_eval.get("pnl_ratio") or 0.0),
                "pnl_net": pnl_net,
                "win_gross": float(exit_eval.get("pnl_ratio") or 0.0) > 0,
                "win_net": pnl_net > 0,
            }
        )
    return trades


def _summarize(trades: list[dict], *, exit_config: dict) -> dict:
    if not trades:
        return {"trade_count": 0}

    total = float(len(trades))
    return {
        "trade_count": int(len(trades)),
        "win_rate_hit_take_profit": round(sum(1 for item in trades if item["hit_take_profit"]) / total, 4),
        "win_rate_hit_stop_loss": round(sum(1 for item in trades if item["hit_stop_loss"]) / total, 4),
        "win_rate_gross_positive": round(sum(1 for item in trades if item["win_gross"]) / total, 4),
        "win_rate_net_positive": round(sum(1 for item in trades if item["win_net"]) / total, 4),
        "avg_pnl_gross": round(sum(item["pnl_gross"] for item in trades) / total, 4),
        "avg_pnl_net": round(sum(item["pnl_net"] for item in trades) / total, 4),
        "exit_config": exit_config,
        "exit_reason_breakdown": {
            reason: int(sum(1 for item in trades if item["exit_reason"] == reason))
            for reason in sorted({item["exit_reason"] for item in trades})
        },
    }


def _build_trend_pool_allowed_symbols(
    *,
    date_from: str,
    date_to: str,
    trend_step: str,
    pool_roll_mode: str,
    max_symbols: int,
) -> tuple[list[str], dict[str, set[str]], list[str]]:
    from app.models import BacktestRunRequest
    from app.store import InMemoryStore

    store = InMemoryStore()
    payload = BacktestRunRequest(
        mode="trend_pool",
        trend_step=trend_step,  # type: ignore[arg-type]
        pool_roll_mode=pool_roll_mode,  # type: ignore[arg-type]
        strategy_id="limit_up_arb_v1",
        date_from=date_from,
        date_to=date_to,
        max_symbols=max(20, min(2000, int(max_symbols))),
        enable_advanced_analysis=False,
        execution_path_preference="legacy",
    )
    screener_params = store._build_backtest_screener_params_from_config()
    symbols_union, allowed_symbols_by_date, notes, _, _ = store._build_trend_pool_rolling_universe(
        payload=payload,
        screener_params=screener_params,
        board_filters=[],
    )
    return symbols_union, allowed_symbols_by_date, notes


def main() -> None:
    parser = argparse.ArgumentParser(description="涨停套利胜率回测")
    parser.add_argument("--tdx-root", default=os.environ.get("TDX_ROOT", DEFAULT_TDX))
    parser.add_argument("--date-from", default="2024-01-01")
    parser.add_argument("--date-to", default="2026-06-01")
    parser.add_argument("--min-volume-ratio-prev", type=float, default=1.2)
    parser.add_argument("--min-volume-ratio-ma5", type=float, default=0.0, help="0 表示不启用")
    parser.add_argument("--min-day-gain", type=float, default=3.0)
    parser.add_argument("--take-profit", type=float, default=0.03, help="止盈比例，与回测页一致，如 0.03=3%")
    parser.add_argument("--stop-loss", type=float, default=0.0, help="止损比例，0 表示不启用")
    parser.add_argument("--max-symbols", type=int, default=0, help="0 表示全市场；趋势池模式下为单日池上限")
    parser.add_argument("--trend-step", default="", choices=["", "step1", "step2", "step3", "step4"])
    parser.add_argument("--pool-roll-mode", default="daily", choices=["daily", "weekly", "position"])
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    if not os.path.isdir(args.tdx_root):
        print(json.dumps({"error": f"TDX 路径不存在: {args.tdx_root}"}, ensure_ascii=False))
        raise SystemExit(1)

    params = resolve_limit_up_arb_params(
        {
            "min_day_gain": args.min_day_gain,
            "min_volume_ratio_prev": args.min_volume_ratio_prev,
            "min_volume_ratio_ma5": args.min_volume_ratio_ma5,
        }
    )
    exit_config = resolve_limit_up_arb_exit_config(
        {
            "take_profit": args.take_profit,
            "stop_loss": args.stop_loss,
        }
    )

    pool_notes: list[str] = []
    allowed_symbols_by_date: dict[str, set[str]] | None = None
    trend_step = str(args.trend_step or "").strip()
    if trend_step:
        pool_max_symbols = args.max_symbols if args.max_symbols > 0 else 120
        symbols, allowed_symbols_by_date, pool_notes = _build_trend_pool_allowed_symbols(
            date_from=args.date_from,
            date_to=args.date_to,
            trend_step=trend_step,
            pool_roll_mode=str(args.pool_roll_mode),
            max_symbols=pool_max_symbols,
        )
    else:
        symbols = [item[1] for item in _iter_tdx_symbols(args.tdx_root, ["sh", "sz"])]
        if args.max_symbols > 0:
            symbols = symbols[: args.max_symbols]

    all_trades: list[dict] = []
    for symbol in symbols:
        all_trades.extend(
            _scan_symbol(
                tdx_root=args.tdx_root,
                symbol=symbol,
                date_from=args.date_from,
                date_to=args.date_to,
                params=params,
                exit_config=exit_config,
                allowed_symbols_by_date=allowed_symbols_by_date,
            )
        )

    by_month: dict[str, list[dict]] = defaultdict(list)
    for trade in all_trades:
        by_month[str(trade["signal_date"])[:7]].append(trade)

    result = {
        "params": params,
        "exit_config": exit_config,
        "date_range": [args.date_from, args.date_to],
        "pool_filter": {
            "trend_step": trend_step or None,
            "pool_roll_mode": str(args.pool_roll_mode) if trend_step else None,
            "max_symbols_per_day": (args.max_symbols if args.max_symbols > 0 else 120) if trend_step else None,
            "notes": pool_notes,
        },
        "symbols_scanned": len(symbols),
        "summary": _summarize(all_trades, exit_config=exit_config),
        "monthly": {
            month: _summarize(items, exit_config=exit_config)
            for month, items in sorted(by_month.items())
        },
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
