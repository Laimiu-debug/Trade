"""回测「同日同时触达止损和止盈」歧义策略回归测试。

日K无法还原日内价格先后顺序。当 low <= stop_price 且 high >= take_price 时,
依据 payload.ambiguity_policy 决定成交价:
- conservative(默认) = 取止损价(假定最坏路径,实盘可达成)
- optimistic         = 取止盈价(假定最优路径)
"""

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
        "daily_trailing_clear_enabled": False,
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
) -> list[ExitLeg]:
    return BacktestEngine._resolve_exit_matrix_legs(
        entry_index=entry_index,
        entry_price=entry_price,
        open_col=np.asarray(opens, dtype=float),
        high_col=np.asarray(highs, dtype=float),
        low_col=np.asarray(lows, dtype=float),
        close_col=np.asarray(closes, dtype=float),
        valid_col=np.ones(len(opens), dtype=bool),
        sell_col=np.zeros(len(opens), dtype=bool),
        sell_label_col=None,
        payload=payload,
    )


def _both_hit_bars() -> dict[str, list[float]]:
    """构造一根同时触达 stop_loss 和 take_profit 的 bar。

    entry_price=10.0, stop_loss=0.1, take_profit=0.1
      → stop_price=9.0, take_price=11.0
    bar1: low=8.5(<=9.0), high=11.5(>=11.0) → 同日双触达
    """
    return {
        "opens": [10.0, 10.0],
        "highs": [10.0, 11.5],
        "lows": [10.0, 8.5],
        "closes": [10.0, 9.5],
    }


def test_conservative_picks_stop_loss_when_both_hit() -> None:
    payload = _base_payload(
        stop_loss=0.1,
        take_profit=0.1,
        ambiguity_policy="conservative",
    )
    legs = _resolve_matrix_legs(entry_index=0, entry_price=10.0, payload=payload, **_both_hit_bars())
    assert legs
    assert legs[-1].exit_reason == "stop_loss"
    assert legs[-1].exit_price == 9.0


def test_optimistic_picks_take_profit_when_both_hit() -> None:
    payload = _base_payload(
        stop_loss=0.1,
        take_profit=0.1,
        ambiguity_policy="optimistic",
    )
    legs = _resolve_matrix_legs(entry_index=0, entry_price=10.0, payload=payload, **_both_hit_bars())
    assert legs
    assert legs[-1].exit_reason == "take_profit"
    assert legs[-1].exit_price == 11.0


def test_default_policy_is_conservative() -> None:
    """不显式传 ambiguity_policy 时,默认 conservative,与历史行为一致。"""
    payload = _base_payload(stop_loss=0.1, take_profit=0.1)
    assert payload.ambiguity_policy == "conservative"
    legs = _resolve_matrix_legs(entry_index=0, entry_price=10.0, payload=payload, **_both_hit_bars())
    assert legs
    assert legs[-1].exit_reason == "stop_loss"


def test_single_stop_hit_unaffected_by_policy() -> None:
    """只触止损(low<=9.0 但 high<11.0)时,两种 policy 结果都是 stop_loss。"""
    bars = {
        "opens": [10.0, 10.0],
        "highs": [10.0, 10.5],
        "lows": [10.0, 8.5],
        "closes": [10.0, 9.2],
    }
    for policy in ("conservative", "optimistic"):
        payload = _base_payload(stop_loss=0.1, take_profit=0.1, ambiguity_policy=policy)  # type: ignore[arg-type]
        legs = _resolve_matrix_legs(entry_index=0, entry_price=10.0, payload=payload, **bars)
        assert legs, f"policy={policy} should produce legs"
        assert legs[-1].exit_reason == "stop_loss", f"policy={policy} should hit stop_loss only"
        assert legs[-1].exit_price == 9.0


def test_single_take_profit_hit_unaffected_by_policy() -> None:
    """只触止盈(high>=11.0 但 low>9.0)时,两种 policy 结果都是 take_profit。"""
    bars = {
        "opens": [10.0, 10.0],
        "highs": [10.0, 11.5],
        "lows": [10.0, 9.5],
        "closes": [10.0, 11.2],
    }
    for policy in ("conservative", "optimistic"):
        payload = _base_payload(stop_loss=0.1, take_profit=0.1, ambiguity_policy=policy)  # type: ignore[arg-type]
        legs = _resolve_matrix_legs(entry_index=0, entry_price=10.0, payload=payload, **bars)
        assert legs, f"policy={policy} should produce legs"
        assert legs[-1].exit_reason == "take_profit", f"policy={policy} should hit take_profit only"
        assert legs[-1].exit_price == 11.0
