from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_data_sync import normalize_symbol, resolve_symbol_start_date


def test_normalize_symbol_variants() -> None:
    assert normalize_symbol("600519") == "sh600519"
    assert normalize_symbol("sh600519") == "sh600519"
    assert normalize_symbol("sh.600519") == "sh600519"
    assert normalize_symbol("sz.300750") == "sz300750"
    assert normalize_symbol("bad_symbol") is None


def test_resolve_symbol_start_date_incremental() -> None:
    existing = {
        "2026-02-12": {
            "date": "2026-02-12",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10",
            "volume": "1000",
            "amount": "10000",
            "symbol": "sz300750",
        }
    }
    resolved = resolve_symbol_start_date(
        explicit_start="",
        full_history=False,
        default_start="20250101",
        end_date="20260213",
        existing=existing,
    )
    assert resolved == "20260210"


def test_resolve_symbol_start_date_up_to_date() -> None:
    existing = {
        "2026-02-13": {
            "date": "2026-02-13",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10",
            "volume": "1000",
            "amount": "10000",
            "symbol": "sz300750",
        }
    }
    resolved = resolve_symbol_start_date(
        explicit_start="",
        full_history=False,
        default_start="20250101",
        end_date="20260213",
        existing=existing,
    )
    assert resolved == "20260211"
