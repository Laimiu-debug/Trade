from __future__ import annotations

from pathlib import Path

from app.tdx_loader import _build_row, _parse_day_file, probe_tdx_latest_trade_date


def test_probe_tdx_latest_trade_date_reads_index_files() -> None:
    tdx_root = Path(__file__).resolve().parents[2] / "backend" / ".test-state" / "tdx-mock"
    # Skip when no local TDX in dev environment.
    real_root = Path("E:/TDX/vipdoc")
    if not (real_root / "sh" / "lday" / "sh000001.day").exists():
        return

    last_day = probe_tdx_latest_trade_date(str(real_root.parent))
    assert last_day is not None
    assert len(last_day) == 10


def test_build_row_latest_price_uses_last_close() -> None:
    # _build_row 要求 len(closes) >= max(return_window_days+1, 40) 且 total_bars > 250。
    # 构造足够长的数据:末尾 5 根用可辨识的收盘价,前面用常数填充。
    base_closes = [100.0] * 40
    tail_closes = [100.0, 102.0, 101.0, 105.0, 103.0]
    closes = base_closes + tail_closes
    series = {
        "symbol": "sz300001",
        "total_bars": len(closes) + 250,  # 满足 total_bars > 250
        "dates": [f"2026-01-{(idx % 28) + 1:02d}" for idx in range(len(closes))],
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "amount": [1.0] * len(closes),
        "volume": [1000] * len(closes),
    }
    row = _build_row(series, 40, 1_000_000_000.0, None)
    assert row is not None
    assert row.latest_price == tail_closes[-1]
    assert row.day_change == round(tail_closes[-1] - tail_closes[-2], 2)
