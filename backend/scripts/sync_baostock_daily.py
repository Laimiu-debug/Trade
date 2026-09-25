from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_data_sync import sync_baostock_daily


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync A-share daily candles from Baostock into local CSV store.")
    parser.add_argument("--symbols", default="", help="Comma separated symbols, supports 600519/sh600519/sh.600519.")
    parser.add_argument("--all-market", action="store_true", help="Sync all A-share symbols from Baostock list.")
    parser.add_argument("--limit", type=int, default=300, help="Limit symbols when --all-market is enabled.")
    parser.add_argument("--mode", choices=["incremental", "full"], default="incremental", help="Sync mode.")
    parser.add_argument("--start-date", default="", help="Start date, format YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="End date, format YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--initial-days", type=int, default=420, help="Initial backfill days when local file is missing.")
    parser.add_argument("--sleep-sec", type=float, default=0.01, help="Sleep seconds between symbols.")
    parser.add_argument("--out-dir", default=str(Path.home() / ".tdx-trend" / "akshare" / "daily"), help="Output directory.")
    args = parser.parse_args()

    summary = sync_baostock_daily(
        symbols_text=args.symbols,
        all_market=bool(args.all_market),
        limit=max(1, int(args.limit)),
        mode=args.mode,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_days=max(1, int(args.initial_days)),
        sleep_sec=max(0.0, float(args.sleep_sec)),
        out_dir=args.out_dir,
    )
    print(
        "[done] provider={provider} mode={mode} out={out_dir} "
        "symbols={symbol_count} ok={ok_count} fail={fail_count} skip={skipped_count} new={new_rows_total} "
        "cost={duration_sec}s".format(**summary)
    )
    errors = summary.get("errors") or []
    for item in errors[:20]:
        print(f"[error] {item}", file=sys.stderr)
    return 0 if int(summary.get("fail_count", 0)) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

