"""模拟交易现金校验缓冲回归测试。

修复前:create_order 用估算价(估算开盘价+滑点)校验现金,
实际成交可能高开导致 _fill_pending_order 拒单,用户看到"提交成功"次日却 rejected。

修复后:提交时预留 0.5% 开盘波动缓冲,提前拦截现金不足。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.models import CandlePoint, CreateOrderRequest, SimTradingConfig
from app.sim_engine import SimAccountEngine, SimEngineError


def _build_engine(
    candles: list[CandlePoint],
    *,
    initial_capital: float,
    state_path: Path,
    slippage_rate: float = 0.001,
    commission_rate: float = 0.0003,
    min_commission: float = 5.0,
    stamp_tax_rate: float = 0.001,
    transfer_fee_rate: float = 0.00002,
) -> SimAccountEngine:
    """构造一个可控的 SimAccountEngine,现金 = initial_capital。"""
    config = SimTradingConfig(
        initial_capital=initial_capital,
        slippage_rate=slippage_rate,
        commission_rate=commission_rate,
        min_commission=min_commission,
        stamp_tax_rate=stamp_tax_rate,
        transfer_fee_rate=transfer_fee_rate,
    )

    engine = SimAccountEngine(
        get_candles=lambda symbol: candles,
        resolve_symbol_name=lambda symbol: "TEST",
        now_date=lambda: candles[-1].time,
        now_datetime=lambda: candles[-1].time + "T00:00:00",
        state_path=str(state_path),
    )
    engine.set_config(config)
    return engine


def _make_candles(prices: list[float], start_date: str = "2024-01-02") -> list[CandlePoint]:
    """从收盘价序列构造 CandlePoint 列表(open=high=low=close 简化)。"""
    from datetime import date, timedelta

    base = date.fromisoformat(start_date)
    candles: list[CandlePoint] = []
    for i, price in enumerate(prices):
        # 跳过周末
        d = base + timedelta(days=i)
        while d.weekday() >= 5:
            base = base + timedelta(days=1)
            d = base + timedelta(days=i)
        candles.append(
            CandlePoint(
                time=d.isoformat(),
                open=price,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=100000,
                amount=price * 100000,
            )
        )
    return candles


def test_buy_with_tight_cash_rejected_by_buffer(tmp_path: Path) -> None:
    """现金刚好够估算价但不够缓冲时,提交阶段就应被拒绝(而非次日静默拒单)。

    构造:candles 收盘价 10.0,估算开盘价 10.0,滑点 0.1% → 估算成交价 10.01
    买 100 股: gross = 10.01 * 100 = 1001.0
    无缓冲: est_cost = 1001.0 + 手续费,现金 1010 够通过 → 但次日高开可能拒单
    有缓冲: buffered_cost = 1001.0 * 1.005 + 手续费 ≈ 1006 + 手续费,现金 1010 仍够
    把现金压到刚好够 est_cost 但不够 buffered_cost → 应被拒绝。
    """
    candles = _make_candles([10.0, 10.0, 10.0])
    # 估算价 = 10.0 * (1+0.001) = 10.01, gross = 1001.0
    # est_cost = 1001.0 + max(1001*0.0003, 5) + 0 + 1001*0.00002 = 1001 + 5 + 0.02 = 1006.02
    # buffered = 1001.0 * 1.005 + 5.02 = 1005.005 + 5.02 = 1010.525
    engine = _build_engine(candles, initial_capital=1008.0, state_path=tmp_path / "sim.json")
    payload = CreateOrderRequest(
        symbol="sz300001",
        side="buy",
        quantity=100,
        signal_date=candles[0].time,
        submit_date=candles[0].time,
    )
    # 现金 1008 > est_cost 1006.02 但 < buffered 1010.525 → 应拒绝
    with pytest.raises(SimEngineError) as exc_info:
        engine.create_order(payload)
    assert exc_info.value.code == "SIM_INSUFFICIENT_CASH"
    assert "缓冲" in exc_info.value.message


def test_buy_with_sufficient_buffered_cash_succeeds(tmp_path: Path) -> None:
    """现金足够覆盖缓冲时,提交成功(回归保护:不能把正常订单也拦掉)。"""
    candles = _make_candles([10.0, 10.0, 10.0])
    engine = _build_engine(candles, initial_capital=1100.0, state_path=tmp_path / "sim.json")
    payload = CreateOrderRequest(
        symbol="sz300001",
        side="buy",
        quantity=100,
        signal_date=candles[0].time,
        submit_date=candles[0].time,
    )
    response = engine.create_order(payload)
    assert response.order.status == "pending"
    assert response.order.side == "buy"


def test_buy_far_below_cash_rejected_without_buffer(tmp_path: Path) -> None:
    """现金远低于估算价时,无论缓冲与否都应拒绝(基础回归)。"""
    candles = _make_candles([100.0, 100.0, 100.0])
    # gross = 100 * 100.1 = 10010, 远超 5000 现金
    engine = _build_engine(candles, initial_capital=5000.0, state_path=tmp_path / "sim.json")
    payload = CreateOrderRequest(
        symbol="sz300001",
        side="buy",
        quantity=100,
        signal_date=candles[0].time,
        submit_date=candles[0].time,
    )
    with pytest.raises(SimEngineError) as exc_info:
        engine.create_order(payload)
    assert exc_info.value.code == "SIM_INSUFFICIENT_CASH"


def test_sell_not_affected_by_cash_buffer(tmp_path: Path) -> None:
    """卖出校验可卖数量,不受现金缓冲影响。先买入建仓,再卖出。"""
    candles = _make_candles([10.0, 10.0, 10.0, 10.0, 10.0])
    engine = _build_engine(candles, initial_capital=100000.0, state_path=tmp_path / "sim.json")
    buy_payload = CreateOrderRequest(
        symbol="sz300001",
        side="buy",
        quantity=100,
        signal_date=candles[0].time,
        submit_date=candles[0].time,
    )
    engine.create_order(buy_payload)
    engine.settle()  # 成交建仓

    sell_payload = CreateOrderRequest(
        symbol="sz300001",
        side="sell",
        quantity=100,
        signal_date=candles[-2].time,
        submit_date=candles[-2].time,
    )
    response = engine.create_order(sell_payload)
    assert response.order.status == "pending"
