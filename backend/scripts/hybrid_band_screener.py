#!/usr/bin/env python3
"""混合波段独立选股脚本（不接入 FinalTrade 主程序）。

基于 TDX 本地日线，扫描全市场混合波段信号，按日输出 Top N 候选。

用法:
  cd backend
  python scripts/hybrid_band_screener.py --tdx D:\\new_tdx\\vipdoc
  python scripts/hybrid_band_screener.py --as-of 2026-06-27 --top 5 --csv out.csv
  python scripts/hybrid_band_screener.py --json --output picks.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.chart_volume_swing_strategy import (
    calculate_chart_volume_swing_signal,
    evaluate_chart_volume_swing_signal,
)
from app.core.force_rhythm_strategy import calculate_force_rhythm_signal
from app.core.hybrid_band_strategy import evaluate_hybrid_band_signal, resolve_hybrid_band_params
from app.core.market_momentum import _iter_tdx_symbols
from app.core.ths_volume_signal import calculate_ths_main_retail_signal
from app.models import CandlePoint
from app.tdx_loader import load_candles_for_symbol

DEFAULT_TDX = r"D:\new_tdx\vipdoc"
DEFAULT_CONFIG = ROOT / "scripts" / "out_hybrid_band_final.json"
MIN_BARS = 80


def _parse_date(text: str) -> datetime:
    return datetime.strptime(str(text)[:10], "%Y-%m-%d")


def _ret_n(closes: list[float], idx: int, n: int) -> float:
    s = idx - n
    if s < 0 or closes[s] <= 0:
        return 0.0
    return (closes[idx] - closes[s]) / closes[s]


def _ma_at(values: list[float], idx: int, w: int) -> float:
    s = max(0, idx - w + 1)
    seg = values[s : idx + 1]
    return sum(seg) / len(seg) if seg else values[idx]


def _avg_amount(candles: list[CandlePoint], idx: int, w: int = 20) -> float:
    s = max(0, idx - w + 1)
    seg = candles[s : idx + 1]
    return sum(float(c.amount or 0) for c in seg) / len(seg) if seg else 0.0


def _trend_ma10_pullback(closes: list[float], vols: list[float], idx: int) -> bool:
    if idx < 45:
        return False
    ma5, ma10, ma20 = _ma_at(closes, idx, 5), _ma_at(closes, idx, 10), _ma_at(closes, idx, 20)
    if not (ma5 > ma10 > ma20 and closes[idx] > ma20):
        return False
    if _ret_n(closes, idx, 40) < 0.05:
        return False
    if not (ma10 * 0.98 <= closes[idx] <= ma10 * 1.03):
        return False
    if vols[idx] > _ma_at(vols, idx, 5) * 0.85:
        return False
    up_v = down_v = 0.0
    for j in range(max(1, idx - 19), idx + 1):
        if closes[j] >= closes[j - 1]:
            up_v += vols[j]
        else:
            down_v += vols[j]
    return up_v >= down_v * 1.2


def load_default_params(config_path: Path | None) -> dict[str, Any]:
    path = config_path or DEFAULT_CONFIG
    params: dict[str, Any] = resolve_hybrid_band_params(None)
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        pipeline = data.get("pipeline") if isinstance(data, dict) else None
        if isinstance(pipeline, dict):
            params.update(pipeline)
    return params


def scan_symbol(
    symbol: str,
    *,
    as_of: str,
    params: dict[str, Any],
    min_amount20: float,
) -> dict[str, Any] | None:
    candles = load_candles_for_symbol(symbol)
    if len(candles) < MIN_BARS:
        return None

    target_idx = None
    for i, c in enumerate(candles):
        if str(c.time)[:10] == as_of:
            target_idx = i
            break
    if target_idx is None:
        return None

    window = candles[: target_idx + 1]
    if len(window) < MIN_BARS:
        return None

    closes = [float(c.close) for c in window]
    vols = [float(c.volume) for c in window]
    ret40 = _ret_n(closes, len(closes) - 1, 40)
    amount20 = _avg_amount(window, len(window) - 1, 20)
    if amount20 < min_amount20:
        return None

    chart = calculate_chart_volume_swing_signal(window, params=params)
    if not chart.get("has_data"):
        return None
    chart_ev = evaluate_chart_volume_swing_signal(chart, params)
    chart_score = float(chart_ev.get("signal_score") or 0.0)

    ths = calculate_ths_main_retail_signal(window)
    rhythm = calculate_force_rhythm_signal(window)
    trend_pb = _trend_ma10_pullback(closes, vols, len(closes) - 1)

    hybrid = chart_score
    hybrid += 10.0 if ths.get("purple_to_yellow") else 0.0
    hybrid += 8.0 if ths.get("golden_cross") else 0.0
    rs = float(rhythm.get("signal_score") or 0.0)
    hybrid += 12.0 if rhythm.get("trough_turn") and rs >= 55 else 0.0
    hybrid += 6.0 if trend_pb else 0.0

    has_pattern = bool(chart.get("signal")) or trend_pb
    if not has_pattern:
        return None

    indicator: dict[str, Any] = {
        "signal": hybrid >= float(params.get("min_hybrid_score", 82.0)),
        "hybrid_score": round(hybrid, 2),
        "chart_score": chart_score,
        "trigger_date": as_of,
        "confirm_type": chart.get("confirm_type") or ("trend_ma10_pullback" if trend_pb else ""),
        "purple_to_yellow": bool(ths.get("purple_to_yellow")),
        "golden_cross": bool(ths.get("golden_cross")),
        "trough_turn": bool(rhythm.get("trough_turn")),
        "trend_ma10_pullback": trend_pb,
        "ret_40": ret40,
    }
    evaluation = evaluate_hybrid_band_signal(indicator, params)
    if not evaluation.get("signal"):
        return None

    tags: list[str] = []
    if indicator["purple_to_yellow"]:
        tags.append("紫转黄")
    if indicator["golden_cross"]:
        tags.append("金叉")
    if indicator["trough_turn"]:
        tags.append("节奏波谷")
    if trend_pb:
        tags.append("MA10回踩")
    if indicator["confirm_type"] and indicator["confirm_type"] != "trend_ma10_pullback":
        tags.append(str(indicator["confirm_type"]))

    return {
        "symbol": symbol,
        "signal_date": as_of,
        "close": round(closes[-1], 3),
        "ret40": round(ret40, 4),
        "amount20": round(amount20, 0),
        "hybrid_score": indicator["hybrid_score"],
        "chart_score": chart_score,
        "event_grade": evaluation.get("event_grade"),
        "confirm_type": indicator["confirm_type"],
        "tags": "+".join(tags),
    }


def apply_ret40_pool(candidates: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    ret40_min = float(params.get("ret40_min", 0.08))
    ret40_max = float(params.get("ret40_max", 0.80))
    top_n = int(params.get("ret40_top_n", 500))
    filtered = [c for c in candidates if ret40_min <= float(c["ret40"]) <= ret40_max]
    filtered.sort(key=lambda x: float(x["ret40"]), reverse=True)
    return filtered[:top_n]


def select_daily_top(candidates: list[dict[str, Any]], daily_top: int) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_date[str(row["signal_date"])].append(row)
    picked: list[dict[str, Any]] = []
    for day in sorted(by_date):
        day_rows = sorted(by_date[day], key=lambda x: float(x["hybrid_score"]), reverse=True)
        picked.extend(day_rows[: max(1, daily_top)])
    return picked


def resolve_as_of(tdx_root: str, explicit: str | None) -> str:
    if explicit:
        return str(explicit)[:10]
    for probe in ("sh000001", "sz399001"):
        try:
            candles = load_candles_for_symbol(probe)
            if candles:
                return str(candles[-1].time)[:10]
        except Exception:
            continue
    latest = ""
    for symbol in _iter_tdx_symbols(tdx_root):
        try:
            candles = load_candles_for_symbol(symbol)
        except Exception:
            continue
        if not candles:
            continue
        last = str(candles[-1].time)[:10]
        if last > latest:
            latest = last
    if latest:
        return latest
    return datetime.now().strftime("%Y-%m-%d")


def run_scan(args: argparse.Namespace) -> list[dict[str, Any]]:
    tdx_root = args.tdx or os.environ.get("TDX_DATA_PATH", DEFAULT_TDX)
    if not os.path.isdir(tdx_root):
        raise SystemExit(f"TDX 目录不存在: {tdx_root}")

    params = load_default_params(Path(args.config) if args.config else None)
    if args.min_hybrid is not None:
        params["min_hybrid_score"] = args.min_hybrid
    if args.ret40_top_n is not None:
        params["ret40_top_n"] = args.ret40_top_n

    min_amount20 = float(args.min_amount20)
    daily_top = int(args.top)
    as_of = resolve_as_of(tdx_root, args.as_of)

    raw: list[dict[str, Any]] = []
    total = 0
    for symbol in _iter_tdx_symbols(tdx_root):
        total += 1
        if args.limit and total > args.limit:
            break
        if args.verbose and total % 500 == 0:
            print(f"扫描进度 {total} ...", flush=True)
        try:
            row = scan_symbol(symbol, as_of=as_of, params=params, min_amount20=min_amount20)
        except Exception:
            continue
        if row is not None:
            raw.append(row)

    pooled = apply_ret40_pool(raw, params)
    for rank, row in enumerate(sorted(pooled, key=lambda x: float(x["ret40"]), reverse=True), 1):
        row["ret40_rank"] = rank

    results = select_daily_top(pooled, daily_top)
    for row in results:
        row["name"] = row["symbol"]
    return results


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("无符合条件的混合波段信号。")
        return
    print(f"{'日期':<12} {'代码':<12} {'混合分':>7} {'图形分':>7} {'ret40':>8} {'等级':>4}  标签")
    print("-" * 72)
    for r in rows:
        print(
            f"{r['signal_date']:<12} {r['symbol']:<12} "
            f"{r['hybrid_score']:>7.1f} {r['chart_score']:>7.1f} "
            f"{float(r['ret40']):>7.2%} {str(r.get('event_grade') or ''):>4}  {r.get('tags') or ''}"
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "signal_date", "symbol", "hybrid_score", "chart_score", "ret40", "ret40_rank",
        "event_grade", "confirm_type", "tags", "close", "amount20",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="混合波段独立选股（TDX 全市场扫描）")
    parser.add_argument("--tdx", default="", help="通达信 vipdoc 根目录")
    parser.add_argument("--as-of", default="", help="信号日期 YYYY-MM-DD，默认 TDX 最新交易日")
    parser.add_argument("--config", default="", help=f"参数 JSON，默认 {DEFAULT_CONFIG.name}")
    parser.add_argument("--top", type=int, default=3, help="每日输出 Top N（默认 3）")
    parser.add_argument("--min-hybrid", type=float, default=None, help="混合分下限")
    parser.add_argument("--min-amount20", type=float, default=3e8, help="20 日均成交额下限")
    parser.add_argument("--ret40-top-n", type=int, default=None, help="ret40 趋势池 Top N")
    parser.add_argument("--limit", type=int, default=0, help="调试：最多扫描 N 只股票")
    parser.add_argument("--csv", default="", help="导出 CSV 路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印结果")
    parser.add_argument("--output", default="", help="JSON 输出文件")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    rows = run_scan(args)
    if args.csv:
        write_csv(Path(args.csv), rows)
        print(f"已写入 CSV: {args.csv} ({len(rows)} 条)")
    if args.output:
        Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 JSON: {args.output} ({len(rows)} 条)")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not args.csv and not args.output:
        print_table(rows)


if __name__ == "__main__":
    main()
