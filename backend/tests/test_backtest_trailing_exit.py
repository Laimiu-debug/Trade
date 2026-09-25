from __future__ import annotations

import numpy as np

from app.core.backtest_engine import BacktestEngine, ExitLeg
from app.models import BacktestRunRequest


def _base_payload(**overrides) -> BacktestRunRequest:
    data = {
        "date_from": "2024-01-02",
        "date_to": "2024-03-01",
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "max_hold_days": 30,
        "enforce_t1": False,
        "trailing_stop_pct": 0.0,
        "intraday_trailing_enabled": False,
        "daily_trailing_clear_enabled": True,
        "intraday_trailing_reduce_ratio": 0.5,
        "daily_trailing_confirm_days": 2,
    }
    data.update(overrides)
    return BacktestRunRequest(**data)


def _resolve_matrix_legs(
    *,
    entry_index: int,
    entry_price: float,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    payload: BacktestRunRequest,
    sell_flags: list[bool] | None = None,
) -> list[ExitLeg]:
    sell = sell_flags or [False] * len(opens)
    return BacktestEngine._resolve_exit_matrix_legs(
        entry_index=entry_index,
        entry_price=entry_price,
        open_col=np.asarray(opens, dtype=float),
        high_col=np.asarray(highs, dtype=float),
        low_col=np.asarray(lows, dtype=float),
        close_col=np.asarray(closes, dtype=float),
        valid_col=np.ones(len(opens), dtype=bool),
        sell_col=np.asarray(sell, dtype=bool),
        sell_label_col=None,
        payload=payload,
    )


def test_daily_trailing_requires_confirm_days() -> None:
    payload = _base_payload(trailing_stop_pct=0.1, daily_trailing_confirm_days=2, enforce_t1=False)
    legs = _resolve_matrix_legs(
        entry_index=0,
        entry_price=10.0,
        opens=[10.0, 10.0, 10.0, 10.0, 10.0],
        highs=[10.0, 12.0, 11.0, 10.5, 10.3],
        lows=[9.8, 10.5, 10.4, 10.0, 9.9],
        closes=[10.0, 11.5, 10.6, 10.2, 10.1],
        payload=payload,
    )
    assert legs
    assert legs[-1].exit_reason == "trailing_stop_daily"
    assert legs[-1].exit_index == 4


def test_intraday_reduce_leg_when_still_above_entry_trigger() -> None:
    payload = _base_payload(
        trailing_stop_pct=0.1,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=False,
        intraday_trailing_reduce_ratio=0.5,
        enforce_t1=False,
    )
    legs = _resolve_matrix_legs(
        entry_index=0,
        entry_price=8.0,
        opens=[8.0, 10.5, 10.0, 10.0],
        highs=[8.0, 11.0, 10.2, 10.0],
        lows=[7.9, 9.9, 9.8, 9.7],
        closes=[8.0, 10.5, 9.85, 9.8],
        payload=payload,
    )
    assert legs
    assert legs[0].exit_reason.startswith("trailing_stop_intraday:reduce=")
    assert legs[0].sell_fraction == 0.5


def test_intraday_trigger_requires_failed_recovery() -> None:
    payload = _base_payload(
        trailing_stop_pct=0.05,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=False,
        intraday_trailing_reduce_ratio=1.0,
        enforce_t1=False,
    )
    legs = _resolve_matrix_legs(
        entry_index=0,
        entry_price=10.0,
        opens=[10.0, 10.0],
        highs=[10.0, 10.8],
        lows=[9.9, 10.2],
        closes=[10.0, 10.7],
        payload=payload,
    )
    assert legs
    assert all(not leg.exit_reason.startswith("trailing_stop_intraday") for leg in legs)
