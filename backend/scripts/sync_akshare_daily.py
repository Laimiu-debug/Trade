from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def _default_out_dir() -> Path:
    return Path.home() / ".tdx-trend" / "akshare" / "daily"


def _normalize_date_text(text: str) -> str:
    raw = text.strip()
    if not raw:
        raise ValueError("empty date")
    if "-" in raw:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    else:
        dt = datetime.strptime(raw, "%Y%m%d")
    return dt.strftime("%Y%m%d")


def _normalize_symbol(raw: str) -> str | None:
    text = raw.strip().lower()
    if not text:
        return None
    if text.startswith(("sh", "sz", "bj")) and len(text) >= 8:
        code = text[2:8]
    else:
        code = text[:6]
    if len(code) != 6 or not code.isdigit():
        return None
    return code


def _symbol_with_market(code: str) -> str:
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    lookup = {col.strip(): col for col in columns}

    def pick(options: list[str]) -> str:
        for option in options:
            if option in lookup:
                return lookup[option]
        raise KeyError(f"missing column: {options}; available={columns}")

    return {
        "date": pick(["日期", "date", "Date", "datetime"]),
        "open": pick(["开盘", "open", "Open"]),
        "high": pick(["最高", "high", "High"]),
        "low": pick(["最低", "low", "Low"]),
        "close": pick(["收盘", "close", "Close"]),
        "volume": pick(["成交量", "volume", "Volume"]),
        "amount": pick(["成交额", "amount", "Amount"]),
    }


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_existing(file_path: Path) -> dict[str, dict[str, str]]:
    if not file_path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with file_path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            date_text = str(row.get("date") or "").strip()
            if len(date_text) >= 10:
                rows[date_text[:10]] = {
                    "date": date_text[:10],
                    "open": str(row.get("open") or ""),
                    "high": str(row.get("high") or ""),
                    "low": str(row.get("low") or ""),
                    "close": str(row.get("close") or ""),
                    "volume": str(row.get("volume") or "0"),
                    "amount": str(row.get("amount") or "0"),
                    "symbol": str(row.get("symbol") or ""),
                }
    return rows


def _write_rows(file_path: Path, rows: dict[str, dict[str, str]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [rows[date] for date in sorted(rows.keys())]
    with file_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["date", "open", "high", "low", "close", "volume", "amount", "symbol"],
        )
        writer.writeheader()
        writer.writerows(ordered)


def _collect_symbols(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.symbols:
        values.extend([item.strip() for item in args.symbols.split(",") if item.strip()])
    if args.symbols_file:
        path = Path(args.symbols_file)
        if path.exists():
            values.extend(path.read_text(encoding="utf-8").splitlines())

    if args.all_market:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("akshare not installed") from exc
        spot = ak.stock_zh_a_spot_em()
        if "代码" not in spot.columns:
            raise RuntimeError("stock_zh_a_spot_em missing 代码 column")
        codes = [str(item).strip() for item in spot["代码"].tolist()]
        if args.limit and args.limit > 0:
            codes = codes[: args.limit]
        values.extend(codes)

    if not values:
        values = ["600519", "300750", "601899", "002594", "000333"]

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        code = _normalize_symbol(value)
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _latest_existing_yyyymmdd(existing: dict[str, dict[str, str]]) -> str | None:
    latest: datetime | None = None
    for raw in existing.keys():
        text = str(raw).strip()
        if len(text) < 10:
            continue
        try:
            current = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if latest is None or current > latest:
            latest = current
    if latest is None:
        return None
    return latest.strftime("%Y%m%d")


def _resolve_symbol_start_date(
    *,
    explicit_start: str,
    full_history: bool,
    default_start: str,
    end_date: str,
    existing: dict[str, dict[str, str]],
) -> str | None:
    if explicit_start:
        return explicit_start if explicit_start <= end_date else None
    if full_history:
        return default_start if default_start <= end_date else None
    latest = _latest_existing_yyyymmdd(existing)
    if latest:
        next_day = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        return next_day if next_day <= end_date else None
    return default_start if default_start <= end_date else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync A-share daily candles from AkShare into local cache CSV.")
    parser.add_argument("--symbols", default="", help="Comma separated symbol list, supports 600519 or sh600519.")
    parser.add_argument("--symbols-file", default="", help="Text file with one symbol per line.")
    parser.add_argument("--all-market", action="store_true", help="Sync all A-share symbols via stock_zh_a_spot_em.")
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols count when --all-market is enabled.")
    parser.add_argument("--start-date", default="", help="Start date, format YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="End date, format YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--initial-days", type=int, default=420, help="Initial backfill days when symbol cache not found.")
    parser.add_argument("--full-history", action="store_true", help="Ignore local progress and resync full range.")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="AkShare adjust mode.")
    parser.add_argument("--out-dir", default=str(_default_out_dir()), help="Cache output directory.")
    parser.add_argument(
        "--volume-multiplier",
        type=float,
        default=100.0,
        help="Multiply AkShare volume by this factor before saving. A-share default volume unit is lot=100 shares.",
    )
    parser.add_argument("--sleep-sec", type=float, default=0.15, help="Sleep seconds between symbol requests.")
    args = parser.parse_args()

    try:
        import akshare as ak
    except ImportError:
        print("akshare is not installed. Please run: pip install akshare", file=sys.stderr)
        return 2

    today = datetime.now().strftime("%Y%m%d")
    initial_days = max(1, int(args.initial_days))
    start_default = (datetime.now() - timedelta(days=initial_days)).strftime("%Y%m%d")
    explicit_start_date = _normalize_date_text(args.start_date) if args.start_date else ""
    end_date = _normalize_date_text(args.end_date) if args.end_date else today
    out_dir = Path(args.out_dir).expanduser().resolve()
    symbols = _collect_symbols(args)
    if not symbols:
        print("no valid symbols to sync", file=sys.stderr)
        return 1

    ok_count = 0
    fail_count = 0
    new_rows_total = 0

    print(
        f"[sync] symbols={len(symbols)} end={end_date} out={out_dir} "
        f"mode={'full' if args.full_history else 'incremental'} initial_days={initial_days}"
    )

    for index, code in enumerate(symbols, start=1):
        full_symbol = _symbol_with_market(code)
        output_file = out_dir / f"{full_symbol}.csv"
        try:
            existing = _read_existing(output_file)
            before_count = len(existing)
            start_date = _resolve_symbol_start_date(
                explicit_start=explicit_start_date,
                full_history=bool(args.full_history),
                default_start=start_default,
                end_date=end_date,
                existing=existing,
            )
            if not start_date:
                ok_count += 1
                print(f"[{index}/{len(symbols)}] skip {full_symbol}: up-to-date")
                continue

            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=args.adjust,
            )
            if df is None or df.empty:
                ok_count += 1
                print(f"[{index}/{len(symbols)}] ok {full_symbol}: no update")
                continue

            columns = _resolve_columns([str(col) for col in df.columns.tolist()])
            for _, row in df.iterrows():
                date_val = str(row[columns["date"]]).strip()
                date_obj = datetime.strptime(date_val[:10], "%Y-%m-%d")
                date_text = date_obj.strftime("%Y-%m-%d")

                open_price = _to_float(row[columns["open"]])
                high_price = _to_float(row[columns["high"]])
                low_price = _to_float(row[columns["low"]])
                close_price = _to_float(row[columns["close"]])
                volume_raw = _to_float(row[columns["volume"]]) or 0.0
                amount_raw = _to_float(row[columns["amount"]]) or 0.0

                if (
                    open_price is None
                    or high_price is None
                    or low_price is None
                    or close_price is None
                    or open_price <= 0
                    or high_price <= 0
                    or low_price <= 0
                    or close_price <= 0
                ):
                    continue

                if high_price < low_price:
                    high_price, low_price = low_price, high_price

                volume = int(max(0.0, round(volume_raw * max(args.volume_multiplier, 0.0))))
                amount = amount_raw if amount_raw > 0 else close_price * max(volume, 1)
                existing[date_text] = {
                    "date": date_text,
                    "open": f"{open_price:.2f}",
                    "high": f"{high_price:.2f}",
                    "low": f"{low_price:.2f}",
                    "close": f"{close_price:.2f}",
                    "volume": str(volume),
                    "amount": f"{amount:.2f}",
                    "symbol": full_symbol,
                }

            _write_rows(output_file, existing)
            new_rows = max(0, len(existing) - before_count)
            new_rows_total += new_rows
            ok_count += 1
            print(f"[{index}/{len(symbols)}] ok {full_symbol}: start={start_date} rows={len(existing)} new={new_rows}")
        except Exception as exc:
            fail_count += 1
            print(f"[{index}/{len(symbols)}] fail {full_symbol}: {exc}", file=sys.stderr)

        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

    print(f"[done] ok={ok_count} fail={fail_count} new_rows={new_rows_total}")
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
