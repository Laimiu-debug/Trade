from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.backtest_engine import BacktestEngine
from app.core.backtest_matrix_engine import MatrixBundle
from app.core.backtest_signal_matrix import BacktestSignalMatrix
from app.core.strategy_registry import StrategyRegistry
from app.models import BacktestRunRequest, BacktestTrade, CandlePoint


def _build_candles(dates: list[str], base_price: float) -> list[CandlePoint]:
    candles: list[CandlePoint] = []
    for idx, day in enumerate(dates):
        px = base_price + idx * 0.1
        candles.append(
            CandlePoint(
                time=day,
                open=px,
                high=px * 1.02,
                low=px * 0.98,
                close=px * 1.01,
                volume=100000,
                amount=px * 100000,
            )
        )
    return candles


def _build_trading_days(start: str, count: int) -> list[str]:
    out: list[str] = []
    cursor = datetime.strptime(start, "%Y-%m-%d")
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    return out


def _build_linear_candles(dates: list[str], *, start: float = 10.0, step: float = 1.0) -> list[CandlePoint]:
    candles: list[CandlePoint] = []
    for idx, day in enumerate(dates):
        px = float(start + idx * step)
        candles.append(
            CandlePoint(
                time=day,
                open=px,
                high=px * 1.02,
                low=px * 0.98,
                close=px * 1.01,
                volume=100000,
                amount=px * 100000,
            )
        )
    return candles


@pytest.mark.parametrize("entry_delay_days", [1, 2, 3])
def test_legacy_entry_delay_days_controls_entry_date_and_price(entry_delay_days: int) -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[0]
    exit_day = dates[25]
    candles = _build_linear_candles(dates, start=10.0, step=1.0)

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        if day == exit_day:
            event_dates["X"] = day
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 85.0,
            "phase": "吸筹D",
            "structure_hhh": "HH|HL|HC",
            "trend_score": 78.0,
            "volatility_score": 70.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=entry_delay_days,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 1
    trade = result.trades[0]
    expected_entry_index = entry_delay_days
    assert trade.signal_date == signal_day
    assert trade.entry_date == dates[expected_entry_index]
    assert trade.entry_price == pytest.approx(candles[expected_entry_index].open, rel=1e-9)
    assert trade.delay_entry_days == entry_delay_days
    assert trade.delay_window_days == max(0, entry_delay_days - 1)


def test_legacy_plan_signals_do_not_depend_on_future_exit_window() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[2]
    candles = _build_linear_candles(dates, start=10.0, step=0.5)

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": ["E"] if day == signal_day else [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "phase": "吸筹D",
            "structure_hhh": "HH|HL|HC",
            "trend_score": 78.0,
            "volatility_score": 70.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )

    def _run(date_to: str):
        payload = BacktestRunRequest(
            mode="full_market",
            pool_roll_mode="daily",
            date_from=dates[0],
            date_to=date_to,
            window_days=60,
            min_score=0.0,
            require_sequence=False,
            min_event_count=1,
            entry_events=["E"],
            exit_events=["X"],
            initial_capital=100000.0,
            position_pct=1.0,
            max_positions=1,
            stop_loss=0.0,
            take_profit=0.0,
            max_hold_days=60,
            fee_bps=0.0,
            prioritize_signals=True,
            priority_mode="balanced",
            priority_topk_per_day=0,
            enforce_t1=True,
            entry_delay_days=1,
            delay_invalidation_enabled=False,
            max_symbols=20,
        )
        return engine.run(payload=payload, symbols=[symbol])

    same_day_result = _run(signal_day)
    no_sellable_day_result = _run(dates[3])
    full_window_result = _run(dates[-1])

    expected = [symbol]
    assert [row.symbol for row in same_day_result.plan_signals if row.signal_date == signal_day] == expected
    assert [row.symbol for row in no_sellable_day_result.plan_signals if row.signal_date == signal_day] == expected
    assert [row.symbol for row in full_window_result.plan_signals if row.signal_date == signal_day] == expected
    assert len(no_sellable_day_result.trades) == 1
    assert no_sellable_day_result.trades[0].entry_date == dates[3]
    assert no_sellable_day_result.trades[0].exit_reason == "open"
    assert no_sellable_day_result.trades[0].pnl_amount > 0
    assert no_sellable_day_result.stats.total_return > 0
    assert len(full_window_result.trades) == 1


def test_last_day_signal_keeps_plan_entry_after_backtest_end() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[-1]
    candles = _build_linear_candles(dates, start=10.0, step=0.5)

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": ["E"] if day == signal_day else [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "phase": "吸筹D",
            "structure_hhh": "HH|HL|HC",
            "trend_score": 78.0,
            "volatility_score": 70.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=[],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol])

    plans = [row for row in result.plan_signals if row.signal_date == signal_day]
    assert len(plans) == 1
    assert plans[0].entry_date == BacktestEngine._advance_trading_day(signal_day, 1)


def test_position_held_at_backtest_end_stays_open_without_forced_sell() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[2]
    entry_day = dates[3]
    last_day = dates[-1]
    candles = _build_linear_candles(dates, start=10.0, step=0.5)

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": ["E"] if day == signal_day else [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "phase": "吸筹D",
            "structure_hhh": "HH|HL|HC",
            "trend_score": 78.0,
            "volatility_score": 70.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=[],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == entry_day
    assert trade.exit_date == last_day
    assert trade.exit_reason == "open"
    assert trade.pnl_amount > 0
    assert result.stats.total_return > 0
    assert any("末日本持仓盯市" in note for note in result.notes)


@pytest.mark.parametrize("entry_delay_days", [1, 2, 3])
def test_matrix_entry_delay_days_controls_entry_date_and_price(entry_delay_days: int) -> None:
    dates = _build_trading_days("2025-01-02", 8)
    symbols = ["sh600001"]
    t, n = len(dates), len(symbols)

    open_px = [[10.0 + idx] for idx in range(t)]
    high_px = [[(10.0 + idx) * 1.02] for idx in range(t)]
    low_px = [[(10.0 + idx) * 0.98] for idx in range(t)]
    close_px = [[(10.0 + idx) * 1.01] for idx in range(t)]
    volume = [[100000.0] for _ in range(t)]
    valid = [[True] for _ in range(t)]

    buy = [[False] for _ in range(t)]
    buy[0][0] = True
    sell = [[False] for _ in range(t)]
    sell[6][0] = True
    score = [[0.0] for _ in range(t)]
    score[0][0] = 86.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=list(symbols),
        open=np.asarray(open_px, dtype=np.float64),
        high=np.asarray(high_px, dtype=np.float64),
        low=np.asarray(low_px, dtype=np.float64),
        close=np.asarray(close_px, dtype=np.float64),
        volume=np.asarray(volume, dtype=np.float64),
        valid_mask=np.asarray(valid, dtype=bool),
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=np.asarray(buy, dtype=bool),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.ones((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=np.asarray(buy, dtype=bool),
        buy_signal=np.asarray(buy, dtype=bool),
        sell_signal=np.asarray(sell, dtype=bool),
        score=np.asarray(score, dtype=np.float64),
    )

    engine = BacktestEngine(
        get_candles=lambda _: [],
        build_row=lambda symbol, as_of_date=None: {"symbol": symbol, "as_of_date": as_of_date},
        calc_snapshot=lambda row, window_days, as_of_date=None: {},
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=entry_delay_days,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )
    result = engine.run(
        payload=payload,
        symbols=list(symbols),
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    expected_entry_index = entry_delay_days
    assert trade.signal_date == dates[0]
    assert trade.entry_date == dates[expected_entry_index]
    assert trade.entry_price == pytest.approx(float(open_px[expected_entry_index][0]), rel=1e-9)
    assert trade.delay_entry_days == entry_delay_days
    assert trade.delay_window_days == max(0, entry_delay_days - 1)


def test_matrix_plan_signals_do_not_depend_on_future_exit_window() -> None:
    dates = _build_trading_days("2025-01-02", 8)
    symbols = ["sh600001"]
    signal_index = 2
    signal_day = dates[signal_index]
    t, n = len(dates), len(symbols)

    open_px = np.asarray([[10.0 + idx] for idx in range(t)], dtype=np.float64)
    high_px = open_px * 1.02
    low_px = open_px * 0.98
    close_px = open_px * 1.01
    volume = np.asarray([[100000.0] for _ in range(t)], dtype=np.float64)
    valid = np.ones((t, n), dtype=bool)
    buy = np.zeros((t, n), dtype=bool)
    buy[signal_index, 0] = True
    sell = np.zeros((t, n), dtype=bool)
    score = np.zeros((t, n), dtype=np.float64)
    score[signal_index, 0] = 83.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=list(symbols),
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        valid_mask=valid,
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=buy.copy(),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.ones((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=buy.copy(),
        buy_signal=buy.copy(),
        sell_signal=sell.copy(),
        score=score,
    )

    engine = BacktestEngine(
        get_candles=lambda _: [],
        build_row=lambda symbol, as_of_date=None: {"symbol": symbol, "as_of_date": as_of_date},
        calc_snapshot=lambda row, window_days, as_of_date=None: {},
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )

    def _run(date_to: str):
        payload = BacktestRunRequest(
            mode="full_market",
            pool_roll_mode="daily",
            date_from=dates[0],
            date_to=date_to,
            window_days=60,
            min_score=0.0,
            require_sequence=False,
            min_event_count=1,
            entry_events=["E"],
            exit_events=["X"],
            initial_capital=100000.0,
            position_pct=1.0,
            max_positions=1,
            stop_loss=0.0,
            take_profit=0.0,
            max_hold_days=60,
            fee_bps=0.0,
            prioritize_signals=True,
            priority_mode="balanced",
            priority_topk_per_day=0,
            enforce_t1=True,
            entry_delay_days=1,
            delay_invalidation_enabled=False,
            max_symbols=20,
        )
        return engine.run(
            payload=payload,
            symbols=list(symbols),
            matrix_bundle=bundle,
            matrix_signals=signals,
        )

    same_day_result = _run(signal_day)
    no_sellable_day_result = _run(dates[signal_index + 1])
    full_window_result = _run(dates[-1])

    expected = symbols
    assert [row.symbol for row in same_day_result.plan_signals if row.signal_date == signal_day] == expected
    assert [row.symbol for row in no_sellable_day_result.plan_signals if row.signal_date == signal_day] == expected
    assert [row.symbol for row in full_window_result.plan_signals if row.signal_date == signal_day] == expected
    assert len(no_sellable_day_result.trades) == 1
    assert no_sellable_day_result.trades[0].entry_date == dates[signal_index + 1]
    assert no_sellable_day_result.trades[0].exit_reason == "open"
    assert no_sellable_day_result.trades[0].pnl_amount > 0
    assert no_sellable_day_result.stats.total_return > 0
    assert len(full_window_result.trades) == 1


def test_matrix_and_legacy_match_on_trade_timeline_for_same_signal_case() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[0]
    exit_day = dates[25]
    candles = _build_linear_candles(dates, start=10.0, step=0.5)

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["SOS"] = day
            event_chain = [{"event": "SOS"}]
        if day == exit_day:
            event_dates["X"] = day
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": ["SOS"] if day == signal_day else [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 88.0,
            "phase": "吸筹D",
            "structure_hhh": "HH|HL|HC",
            "trend_score": 75.0,
            "volatility_score": 68.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["SOS"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=2,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )
    legacy_result = engine.run(payload=payload, symbols=[symbol])

    t = len(dates)
    open_px = np.asarray([[bar.open] for bar in candles], dtype=np.float64)
    high_px = np.asarray([[bar.high] for bar in candles], dtype=np.float64)
    low_px = np.asarray([[bar.low] for bar in candles], dtype=np.float64)
    close_px = np.asarray([[bar.close] for bar in candles], dtype=np.float64)
    volume = np.asarray([[float(bar.volume)] for bar in candles], dtype=np.float64)
    valid = np.ones((t, 1), dtype=bool)
    buy = np.zeros((t, 1), dtype=bool)
    buy[0, 0] = True
    sell = np.zeros((t, 1), dtype=bool)
    sell[25, 0] = True
    score = np.zeros((t, 1), dtype=np.float64)
    score[0, 0] = 88.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=[symbol],
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        valid_mask=valid,
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, 1), dtype=bool),
        s2=np.zeros((t, 1), dtype=bool),
        s3=np.zeros((t, 1), dtype=bool),
        s4=np.zeros((t, 1), dtype=bool),
        s5=buy.copy(),
        s6=np.zeros((t, 1), dtype=bool),
        s7=np.ones((t, 1), dtype=bool),
        s8=np.zeros((t, 1), dtype=bool),
        s9=np.zeros((t, 1), dtype=bool),
        in_pool=buy.copy(),
        buy_signal=buy.copy(),
        sell_signal=sell.copy(),
        score=score,
    )
    matrix_result = engine.run(
        payload=payload,
        symbols=[symbol],
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    assert len(legacy_result.trades) == 1
    assert len(matrix_result.trades) == 1
    legacy_trade = legacy_result.trades[0]
    matrix_trade = matrix_result.trades[0]
    assert matrix_trade.signal_date == legacy_trade.signal_date
    assert matrix_trade.entry_date == legacy_trade.entry_date
    assert matrix_trade.exit_date == legacy_trade.exit_date
    assert matrix_trade.entry_price == pytest.approx(legacy_trade.entry_price, rel=1e-9)
    assert matrix_trade.exit_price == pytest.approx(legacy_trade.exit_price, rel=1e-9)
    assert matrix_trade.exit_reason == legacy_trade.exit_reason


def test_operation_plan_counts_split_intraday_legs_as_one_position() -> None:
    engine = BacktestEngine(
        get_candles=lambda _symbol: [],
        build_row=lambda _symbol, _as_of_date=None: None,
        calc_snapshot=lambda _row, _window_days, _as_of_date=None: {},
        resolve_symbol_name=lambda symbol: symbol,
    )
    payload = BacktestRunRequest(date_from="2028-04-03", date_to="2028-04-10", max_positions=2)
    reduce_leg = BacktestTrade(
        symbol="sz002980",
        name="华盛昌",
        signal_date="2028-04-03",
        entry_date="2028-04-07",
        exit_date="2028-04-08",
        entry_signal="SOS",
        exit_reason="trailing_stop_intraday:reduce=20",
        quantity=200,
        entry_price=47.72,
        exit_price=47.0,
        holding_days=1,
        pnl_amount=-144.0,
        pnl_ratio=-0.015,
    )
    remain_leg = BacktestTrade(
        symbol="sz002980",
        name="华盛昌",
        signal_date="2028-04-03",
        entry_date="2028-04-07",
        exit_date="2028-04-15",
        entry_signal="SOS",
        exit_reason="take_profit",
        quantity=800,
        entry_price=47.72,
        exit_price=69.8,
        holding_days=6,
        pnl_amount=17664.0,
        pnl_ratio=0.462,
    )
    plan_buy = BacktestTrade(
        symbol="sz000880",
        name="沃尔核材",
        signal_date="2028-04-07",
        entry_date="2028-04-08",
        exit_date="2028-04-08",
        entry_signal="LPS",
        exit_reason="open",
        quantity=0,
        entry_price=13.85,
        exit_price=13.85,
        holding_days=0,
        pnl_amount=0.0,
        pnl_ratio=0.0,
    )
    plans = engine._build_operation_plans(
        trading_dates=["2028-04-07", "2028-04-08"],
        trades=[reduce_leg, remain_leg],
        plan_signals=[plan_buy],
        payload=payload,
    )
    plan_407 = next(item for item in plans if item.date == "2028-04-07")
    assert len(plan_407.buy_signals) == 1
    assert plan_407.buy_signals[0].symbol == "sz000880"


def test_entry_blocked_by_pending_intraday_reduce_detects_same_day_reduce() -> None:
    blocked = BacktestEngine._entry_blocked_by_pending_intraday_reduce(
        [
            {
                "symbol": "sz002980",
                "exit_date": "2028-04-15",
                "exit_reason": "take_profit",
                "cash_events": [
                    {"exit_date": "2028-04-08", "exit_amount": 9400.0, "pnl_amount": -100.0},
                ],
            }
        ],
        "sz002980",
        "2028-04-08",
    )
    assert blocked is True


def test_operation_plan_blocks_add_on_buy_after_intraday_reduce() -> None:
    engine = BacktestEngine(
        get_candles=lambda _symbol: [],
        build_row=lambda _symbol, _as_of_date=None: None,
        calc_snapshot=lambda _row, _window_days, _as_of_date=None: {},
        resolve_symbol_name=lambda symbol: symbol,
    )
    payload = BacktestRunRequest(date_from="2028-04-03", date_to="2028-04-10", max_positions=1)
    reduce_leg = BacktestTrade(
        symbol="sz002980",
        name="华盛昌",
        signal_date="2028-04-03",
        entry_date="2028-04-07",
        exit_date="2028-04-08",
        entry_signal="SOS",
        exit_reason="trailing_stop_intraday:reduce=20",
        quantity=200,
        entry_price=47.72,
        exit_price=47.0,
        holding_days=1,
        pnl_amount=-144.0,
        pnl_ratio=-0.015,
    )
    remain_leg = BacktestTrade(
        symbol="sz002980",
        name="华盛昌",
        signal_date="2028-04-03",
        entry_date="2028-04-07",
        exit_date="2028-04-15",
        entry_signal="SOS",
        exit_reason="take_profit",
        quantity=800,
        entry_price=47.72,
        exit_price=69.8,
        holding_days=6,
        pnl_amount=17664.0,
        pnl_ratio=0.462,
    )
    add_on_buy = BacktestTrade(
        symbol="sz002980",
        name="华盛昌",
        signal_date="2028-04-07",
        entry_date="2028-04-08",
        exit_date="2028-04-08",
        entry_signal="LPS",
        exit_reason="open",
        quantity=0,
        entry_price=48.5,
        exit_price=48.5,
        holding_days=0,
        pnl_amount=0.0,
        pnl_ratio=0.0,
    )
    plans = engine._build_operation_plans(
        trading_dates=["2028-04-07", "2028-04-08"],
        trades=[reduce_leg, remain_leg],
        plan_signals=[add_on_buy],
        payload=payload,
    )
    plan_407 = next(item for item in plans if item.date == "2028-04-07")
    assert plan_407.buy_signals == []


def test_last_day_open_signal_slots_count_pending_next_day_sell() -> None:
    engine = BacktestEngine(
        get_candles=lambda _symbol: [],
        build_row=lambda _symbol, _as_of_date=None: None,
        calc_snapshot=lambda _row, _window_days, _as_of_date=None: {},
        resolve_symbol_name=lambda symbol: symbol,
    )
    held = BacktestTrade(
        symbol="sh600001",
        name="宝鼎",
        signal_date="2026-05-26",
        entry_date="2026-05-20",
        exit_date="2026-05-28",
        entry_signal="SOS",
        entry_phase="阶段未明",
        entry_quality_score=80.0,
        candle_quality_score=0.0,
        cost_center_shift_score=0.0,
        weekly_context_score=50.0,
        weekly_context_multiplier=1.0,
        health_score=80.0,
        event_score=80.0,
        risk_score=0.0,
        confirmation_status="confirmed",
        event_grade="B",
        phase_context_score=0.0,
        event_recency_score=0.0,
        exit_reason="event_exit:UTAD",
        delay_entry_days=1,
        delay_window_days=0,
        quantity=1000,
        entry_price=10.0,
        exit_price=10.5,
        holding_days=4,
        pnl_amount=500.0,
        pnl_ratio=0.05,
    )
    buy = BacktestTrade(
        symbol="sz002842",
        name="候选",
        signal_date="2026-05-27",
        entry_date="2026-05-28",
        exit_date="2026-05-28",
        entry_signal="主力金叉",
        entry_phase="主散量能",
        entry_quality_score=85.0,
        candle_quality_score=0.0,
        cost_center_shift_score=0.0,
        weekly_context_score=50.0,
        weekly_context_multiplier=1.0,
        health_score=85.0,
        event_score=85.0,
        risk_score=0.0,
        confirmation_status="confirmed",
        event_grade="B",
        phase_context_score=0.0,
        event_recency_score=0.0,
        exit_reason="open",
        delay_entry_days=1,
        delay_window_days=0,
        quantity=0,
        entry_price=12.0,
        exit_price=12.0,
        holding_days=0,
        pnl_amount=0.0,
        pnl_ratio=0.0,
    )
    available_slots, blocking_symbols = engine._resolve_last_day_open_signal_slots(
        trades=[held],
        end_date="2026-05-27",
        next_trade_date="2026-05-28",
        max_positions=1,
    )
    assert available_slots == 1
    assert blocking_symbols == set()
    filtered = [
        signal
        for signal in [buy]
        if str(signal.symbol).strip().lower() not in blocking_symbols
    ][:available_slots]
    assert len(filtered) == 1
    assert filtered[0].symbol == "sz002842"


def test_position_mode_refills_after_skipped_signal() -> None:
    dates = _build_trading_days("2025-01-02", 32)
    first_signal_day = dates[0]
    exit_day_for_a = dates[2]
    second_signal_day_for_b = dates[2]
    expected_second_entry_day = dates[3]
    symbol_a = "sh600001"
    symbol_b = "sh600002"
    candles_by_symbol = {
        symbol_a: _build_candles(dates, 10.0),
        symbol_b: _build_candles(dates, 12.0),
    }

    def _get_candles(symbol: str) -> list[CandlePoint]:
        return list(candles_by_symbol.get(symbol, []))

    def _build_row(symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if as_of_date is None:
            return None
        return {"symbol": symbol, "as_of_date": as_of_date}

    def _calc_snapshot(row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        symbol = str(row.get("symbol", "")).strip().lower()
        event_dates: dict[str, str] = {}
        entry_chain: list[dict[str, str]] = []

        if symbol == symbol_a and day == first_signal_day:
            event_dates["E"] = day
            entry_chain = [{"event": "E"}]
        if symbol == symbol_a and day == exit_day_for_a:
            event_dates["X"] = day

        if symbol == symbol_b and day in {first_signal_day, second_signal_day_for_b}:
            event_dates["E"] = day
            entry_chain = [{"event": "E"}]

        return {
            "event_dates": event_dates,
            "event_chain": entry_chain,
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 80.0,
            "phase": "阶段未明",
            "structure_hhh": "HH|HL|-",
            "trend_score": 80.0,
            "volatility_score": 80.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda symbol: symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="position",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol_a, symbol_b])

    assert len(result.trades) == 2
    assert [trade.symbol for trade in result.trades] == [symbol_a, symbol_b]
    assert result.trades[1].entry_date == expected_second_entry_day
    assert result.max_concurrent_positions == 1


def test_position_mode_matrix_event_driven_refills_slots() -> None:
    dates = _build_trading_days("2025-01-02", 8)
    symbols = ["sh600001", "sh600002"]
    t, n = len(dates), len(symbols)

    open_px = [[10.0, 12.0] for _ in range(t)]
    high_px = [[10.4, 12.4] for _ in range(t)]
    low_px = [[9.6, 11.6] for _ in range(t)]
    close_px = [[10.1, 12.1] for _ in range(t)]
    volume = [[100000.0, 100000.0] for _ in range(t)]
    valid = [[True, True] for _ in range(t)]

    buy = [[False, False] for _ in range(t)]
    buy[0][0] = True
    buy[0][1] = True
    buy[2][1] = True

    sell = [[False, False] for _ in range(t)]
    sell[2][0] = True

    score = [[0.0, 0.0] for _ in range(t)]
    score[0][0] = 90.0
    score[0][1] = 80.0
    score[2][1] = 88.0

    bool_buy = buy
    bool_sell = sell

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=list(symbols),
        open=np.asarray(open_px, dtype=np.float64),
        high=np.asarray(high_px, dtype=np.float64),
        low=np.asarray(low_px, dtype=np.float64),
        close=np.asarray(close_px, dtype=np.float64),
        volume=np.asarray(volume, dtype=np.float64),
        valid_mask=np.asarray(valid, dtype=bool),
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=np.asarray(bool_buy, dtype=bool),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.zeros((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=np.asarray(bool_buy, dtype=bool),
        buy_signal=np.asarray(bool_buy, dtype=bool),
        sell_signal=np.asarray(bool_sell, dtype=bool),
        score=np.asarray(score, dtype=np.float64),
    )

    def _get_candles(_: str) -> list[CandlePoint]:
        return []

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=lambda symbol, as_of_date=None: {"symbol": symbol, "as_of_date": as_of_date},
        calc_snapshot=lambda row, window_days, as_of_date=None: {},
        resolve_symbol_name=lambda symbol: symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="position",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        max_symbols=20,
    )
    result = engine.run(
        payload=payload,
        symbols=list(symbols),
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    assert len(result.trades) == 2
    assert [trade.symbol for trade in result.trades] == ["sh600001", "sh600002"]
    assert result.trades[0].entry_date == dates[1]
    assert result.trades[1].entry_date == dates[3]
    assert result.max_concurrent_positions == 1


def test_entry_delay_and_legacy_delay_invalidation_by_risk_event() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    first_signal_day = dates[0]
    risk_day = dates[1]
    candles_by_symbol = {
        symbol: _build_candles(dates, 10.0),
    }

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles_by_symbol.get(raw_symbol, []))

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        risk_events: list[str] = []
        if day == first_signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        if day == risk_day:
            event_dates["UTAD"] = day
            risk_events = ["UTAD"]
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": [],
            "risk_events": risk_events,
            "sequence_ok": True,
            "entry_quality_score": 80.0,
            "phase": "阶段未明",
            "structure_hhh": "HH|HL|-",
            "trend_score": 80.0,
            "volatility_score": 80.0,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=3,
        delay_invalidation_enabled=True,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 0
    assert any("delay_invalidated_by_risk_event" in note for note in result.notes)


def test_matrix_delay_invalidation_by_sell_signal() -> None:
    dates = _build_trading_days("2025-01-02", 8)
    symbols = ["sh600001"]
    t, n = len(dates), len(symbols)

    open_px = [[10.0] for _ in range(t)]
    high_px = [[10.4] for _ in range(t)]
    low_px = [[9.6] for _ in range(t)]
    close_px = [[10.1] for _ in range(t)]
    volume = [[100000.0] for _ in range(t)]
    valid = [[True] for _ in range(t)]

    buy = [[False] for _ in range(t)]
    buy[0][0] = True

    sell = [[False] for _ in range(t)]
    sell[1][0] = True

    score = [[0.0] for _ in range(t)]
    score[0][0] = 88.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=list(symbols),
        open=np.asarray(open_px, dtype=np.float64),
        high=np.asarray(high_px, dtype=np.float64),
        low=np.asarray(low_px, dtype=np.float64),
        close=np.asarray(close_px, dtype=np.float64),
        volume=np.asarray(volume, dtype=np.float64),
        valid_mask=np.asarray(valid, dtype=bool),
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=np.asarray(buy, dtype=bool),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.zeros((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=np.asarray(buy, dtype=bool),
        buy_signal=np.asarray(buy, dtype=bool),
        sell_signal=np.asarray(sell, dtype=bool),
        score=np.asarray(score, dtype=np.float64),
    )

    engine = BacktestEngine(
        get_candles=lambda _: [],
        build_row=lambda symbol, as_of_date=None: {"symbol": symbol, "as_of_date": as_of_date},
        calc_snapshot=lambda row, window_days, as_of_date=None: {},
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=3,
        delay_invalidation_enabled=True,
        max_symbols=20,
    )
    result = engine.run(
        payload=payload,
        symbols=list(symbols),
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    assert len(result.trades) == 0
    assert any("delay_invalidated_by_sell_signal" in note for note in result.notes)


def test_matrix_aligned_semantic_delay_invalidation_by_risk_event() -> None:
    dates = _build_trading_days("2025-01-02", 8)
    symbols = ["sh600001"]
    t, n = len(dates), len(symbols)

    open_px = [[10.0 + idx * 0.1] for idx in range(t)]
    high_px = [[10.4 + idx * 0.1] for idx in range(t)]
    low_px = [[9.6 + idx * 0.1] for idx in range(t)]
    close_px = [[10.1 + idx * 0.1] for idx in range(t)]
    volume = [[100000.0] for _ in range(t)]
    valid = [[True] for _ in range(t)]

    buy = [[False] for _ in range(t)]
    buy[0][0] = True

    sell = [[False] for _ in range(t)]
    sell[6][0] = True

    score = [[0.0] for _ in range(t)]
    score[0][0] = 88.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=list(symbols),
        open=np.asarray(open_px, dtype=np.float64),
        high=np.asarray(high_px, dtype=np.float64),
        low=np.asarray(low_px, dtype=np.float64),
        close=np.asarray(close_px, dtype=np.float64),
        volume=np.asarray(volume, dtype=np.float64),
        valid_mask=np.asarray(valid, dtype=bool),
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=np.asarray(buy, dtype=bool),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.zeros((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=np.asarray(buy, dtype=bool),
        buy_signal=np.asarray(buy, dtype=bool),
        sell_signal=np.asarray(sell, dtype=bool),
        score=np.asarray(score, dtype=np.float64),
    )

    signal_day = dates[0]
    risk_day = dates[1]

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        risk_events: list[str] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain.append({"event": "E"})
        if day == risk_day:
            event_dates["UTAD"] = day
            risk_events = ["UTAD"]
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": [],
            "risk_events": risk_events,
            "sequence_ok": True,
            "entry_quality_score": 88.0,
            "phase": "阶段未明",
            "structure_hhh": "HH|HL|-",
            "trend_score": 70.0,
            "volatility_score": 65.0,
            "health_score": 75.0,
            "event_score": 72.0,
            "event_grade": "B",
        }

    engine = BacktestEngine(
        get_candles=lambda _: [],
        build_row=lambda symbol, as_of_date=None: {"symbol": symbol, "as_of_date": as_of_date},
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    base_payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=3,
        delay_invalidation_enabled=True,
        max_symbols=20,
        matrix_event_semantic_version="matrix_v1",
    )
    aligned_payload = base_payload.model_copy(update={"matrix_event_semantic_version": "aligned_wyckoff_v2"})

    result_v1 = engine.run(
        payload=base_payload,
        symbols=list(symbols),
        matrix_bundle=bundle,
        matrix_signals=signals,
    )
    result_aligned = engine.run(
        payload=aligned_payload,
        symbols=list(symbols),
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    assert len(result_v1.trades) == 1
    assert len(result_aligned.trades) == 0
    assert any("delay_invalidated_by_risk_event" in note for note in result_aligned.notes)


def test_balanced_priority_prefers_health_event_composite_rank() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    signal_day = dates[0]
    exit_day = dates[25]
    symbol_a = "sh600001"
    symbol_b = "sh600002"
    candles_by_symbol = {
        symbol_a: _build_linear_candles(dates, start=10.0, step=0.2),
        symbol_b: _build_linear_candles(dates, start=11.0, step=0.2),
    }

    def _get_candles(symbol: str) -> list[CandlePoint]:
        return list(candles_by_symbol.get(symbol, []))

    def _build_row(symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if as_of_date is None:
            return None
        return {"symbol": symbol, "as_of_date": as_of_date}

    def _calc_snapshot(row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        symbol = str(row.get("symbol", "")).strip().lower()
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        if day == exit_day:
            event_dates["X"] = day
        if symbol == symbol_a:
            quality = 95.0
            health = 22.0
            event_score = 25.0
            event_grade = "C"
        else:
            quality = 72.0
            health = 92.0
            event_score = 90.0
            event_grade = "A"
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": quality,
            "phase": "闃舵鏈槑",
            "structure_hhh": "HH|HL|-",
            "trend_score": quality,
            "volatility_score": quality,
            "health_score": health,
            "event_score": event_score,
            "event_grade": event_grade,
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda symbol: symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        rank_weight_health=0.45,
        rank_weight_event=0.55,
        enforce_t1=True,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol_a, symbol_b])

    assert len(result.trades) == 1
    # 预期 B 票虽然 quality 更低，但 health/event 复合分显著更高，应优先成交。
    assert result.trades[0].symbol == symbol_b
    assert result.trades[0].health_score > result.trades[0].entry_quality_score


def test_event_grade_gate_blocks_low_grade_entry() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[0]
    exit_day = dates[25]
    candles_by_symbol = {symbol: _build_linear_candles(dates, start=10.0, step=0.2)}

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles_by_symbol.get(raw_symbol, []))

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        if day == exit_day:
            event_dates["X"] = day
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 88.0,
            "phase": "闃舵鏈槑",
            "structure_hhh": "HH|HL|-",
            "trend_score": 78.0,
            "volatility_score": 75.0,
            "health_score": 85.0,
            "event_score": 84.0,
            "event_grade": "C",
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        event_grade_min="B",
        enforce_t1=True,
        max_symbols=20,
    )
    result = engine.run(payload=payload, symbols=[symbol])

    assert result.trades == []
    assert result.candidate_count == 0


def test_require_key_event_confirmation_blocks_unconfirmed_entries() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[0]
    exit_day = dates[25]
    candles_by_symbol = {symbol: _build_linear_candles(dates, start=10.0, step=0.2)}
    confirmation_state = {"value": "partial"}

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles_by_symbol.get(raw_symbol, []))

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        if day == signal_day:
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        if day == exit_day:
            event_dates["X"] = day
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 88.0,
            "phase": "闃舵鏈槑",
            "structure_hhh": "HH|HL|-",
            "trend_score": 78.0,
            "volatility_score": 75.0,
            "health_score": 85.0,
            "event_score": 84.0,
            "event_grade": "A",
            "confirmation_status": str(confirmation_state["value"]),
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        event_grade_min="B",
        require_key_event_confirmation=True,
        enforce_t1=True,
        max_symbols=20,
    )
    blocked = engine.run(payload=payload, symbols=[symbol])
    assert blocked.trades == []

    confirmation_state["value"] = "confirmed"
    passed = engine.run(payload=payload, symbols=[symbol])
    assert len(passed.trades) == 1


def test_matrix_semantic_alignment_toggle_changes_candidate_filtering() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    t, n = len(dates), 1

    open_px = [[10.0 + idx * 0.2] for idx in range(t)]
    high_px = [[(10.0 + idx * 0.2) * 1.02] for idx in range(t)]
    low_px = [[(10.0 + idx * 0.2) * 0.98] for idx in range(t)]
    close_px = [[(10.0 + idx * 0.2) * 1.01] for idx in range(t)]
    volume = [[100000.0] for _ in range(t)]
    valid = [[True] for _ in range(t)]
    buy = [[False] for _ in range(t)]
    buy[0][0] = True
    sell = [[False] for _ in range(t)]
    sell[25][0] = True
    score = [[0.0] for _ in range(t)]
    score[0][0] = 86.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=[symbol],
        open=np.asarray(open_px, dtype=np.float64),
        high=np.asarray(high_px, dtype=np.float64),
        low=np.asarray(low_px, dtype=np.float64),
        close=np.asarray(close_px, dtype=np.float64),
        volume=np.asarray(volume, dtype=np.float64),
        valid_mask=np.asarray(valid, dtype=bool),
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=np.asarray(buy, dtype=bool),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.ones((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=np.asarray(buy, dtype=bool),
        buy_signal=np.asarray(buy, dtype=bool),
        sell_signal=np.asarray(sell, dtype=bool),
        score=np.asarray(score, dtype=np.float64),
    )

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        # 故意不给 entry_event（E），用于验证 aligned 语义会过滤。
        event_dates = {"X": day} if day == dates[25] else {}
        return {
            "event_dates": event_dates,
            "event_chain": [],
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 90.0,
            "phase": "闃舵鏈槑",
            "structure_hhh": "HH|HL|-",
            "trend_score": 80.0,
            "volatility_score": 80.0,
            "health_score": 88.0,
            "event_score": 86.0,
            "event_grade": "A",
        }

    engine = BacktestEngine(
        get_candles=lambda _: [],
        build_row=lambda raw_symbol, as_of_date=None: {"symbol": raw_symbol, "as_of_date": as_of_date},
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )

    payload_v1 = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=["X"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        matrix_event_semantic_version="matrix_v1",
        enforce_t1=True,
        max_symbols=20,
    )
    payload_aligned = payload_v1.model_copy(update={"matrix_event_semantic_version": "aligned_wyckoff_v2"})

    result_v1 = engine.run(
        payload=payload_v1,
        symbols=[symbol],
        matrix_bundle=bundle,
        matrix_signals=signals,
    )
    result_aligned = engine.run(
        payload=payload_aligned,
        symbols=[symbol],
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    assert len(result_v1.trades) == 1
    assert result_aligned.trades == []


def test_score_only_strategy_enters_without_wyckoff_entry_event() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600001"
    signal_day = dates[0]
    candles = _build_linear_candles(dates, start=10.0, step=0.2)

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        entry_score = 90.0 if day == signal_day else 20.0
        return {
            "event_dates": {},
            "event_chain": [],
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": entry_score,
            "phase": "闃舵鏈槑",
            "structure_hhh": "HH|HL|-",
            "trend_score": 72.0,
            "volatility_score": 68.0,
            "health_score": 65.0,
            "event_score": 63.0,
            "event_grade": "C",
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        strategy_id="score_only_rank_v1",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=80.0,
        require_sequence=False,
        min_event_count=0,
        entry_events=["Spring", "SOS", "JOC", "LPS"],
        exit_events=["UTAD", "SOW", "LPSY"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=8,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        max_symbols=20,
    )

    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 1
    assert result.trades[0].signal_date == signal_day
    assert result.trades[0].entry_signal == "SCORE"


def test_ths_main_force_golden_cross_enters_without_wyckoff_events() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sz002842"
    signal_day = dates[0]
    candles = _build_linear_candles(dates, start=10.0, step=0.5)
    registry = StrategyRegistry()

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        return {"symbol": raw_symbol, "as_of_date": as_of_date}

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        is_signal_day = str(as_of_date or "") == signal_day
        return {
            "event_dates": {},
            "event_chain": [],
            "events": [],
            "risk_events": [],
            "sequence_ok": False,
            "entry_quality_score": 82.0 if is_signal_day else 40.0,
            "phase": "闃舵鏈槑",
            "structure_hhh": "-",
            "trend_score": 56.0,
            "volatility_score": 44.0,
            "health_score": 51.0,
            "event_score": 12.0,
            "event_grade": "C",
            "ths_main_retail_signal": {
                "golden_cross": is_signal_day,
                "purple_to_yellow": False,
            },
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
        strategy_signal_filter=lambda strategy_id, row, snapshot, params: registry.generate_signals(
            strategy_id=strategy_id,
            row=row,
            snapshot=snapshot,
            params=params,
        ),
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        strategy_id="ths_main_force_golden_cross_v1",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=True,
        min_event_count=5,
        entry_events=[],
        exit_events=[],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=2,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )

    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 1
    assert result.trades[0].signal_date == signal_day
    assert result.trades[0].entry_signal == "主力金叉"
    assert result.trades[0].exit_reason == "time_exit"


def disabled_test_main_force_strategy_enters_only_when_volume_path_filter_passes() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600888"
    signal_day = dates[3]
    candles = _build_linear_candles(dates, start=10.0, step=0.2)
    registry = StrategyRegistry()

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> SimpleNamespace | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        is_signal_day = as_of_date == signal_day
        return SimpleNamespace(
            symbol=raw_symbol,
            as_of_date=as_of_date,
            ret40=0.18 if is_signal_day else 0.22,
            retrace20=0.10 if is_signal_day else 0.23,
            up_down_volume_ratio=1.42 if is_signal_day else 0.98,
            vol_slope20=0.08 if is_signal_day else -0.02,
            pullback_volume_ratio=0.72 if is_signal_day else 1.08,
            price_vs_ma20=0.04 if is_signal_day else -0.05,
            ma10_above_ma20_days=7 if is_signal_day else 1,
            pullback_days=2 if is_signal_day else 8,
            has_blowoff_top=False,
            has_divergence_5d=False,
            has_upper_shadow_risk=False,
        )

    def _calc_snapshot(_row: SimpleNamespace, _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        entry_score = 88.0 if day == signal_day else 65.0
        return {
            "event_dates": {},
            "event_chain": [],
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": entry_score,
            "phase": "阶段未明",
            "structure_hhh": "HH|HL|-",
            "trend_score": 72.0,
            "volatility_score": 68.0,
            "health_score": 65.0,
            "event_score": 63.0,
            "event_grade": "C",
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
        strategy_signal_filter=lambda strategy_id, row, snapshot, params: registry.generate_signals(
            strategy_id=strategy_id,
            row=row,
            snapshot=snapshot,
            params=params,
        ),
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        strategy_id="__removed_force_crossover_v1",
        strategy_params={
            "min_ret40": 0.06,
            "max_ret40": 0.42,
            "min_up_down_volume_ratio": 1.05,
            "min_vol_slope20": 0.0,
            "max_retrace20": 0.20,
            "max_pullback_volume_ratio": 0.95,
            "min_price_vs_ma20": -0.02,
            "max_price_vs_ma20": 0.12,
            "min_ma10_above_ma20_days": 3,
            "min_main_force_score": 52.0,
            "min_path_quality_score": 55.0,
            "min_force_gap_score": 58.0,
            "max_retail_pressure_score": 52.0,
            "max_pullback_days": 6,
            "entry_quality_floor": 45.0,
        },
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=50.0,
        require_sequence=False,
        min_event_count=0,
        entry_events=["Spring", "SOS", "JOC", "LPS"],
        exit_events=["UTAD", "SOW", "LPSY"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=8,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        max_symbols=20,
    )

    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 1
    assert result.trades[0].signal_date == signal_day
    assert result.trades[0].entry_signal == "SCORE"


def disabled_test_main_force_pullback_strategy_enters_after_pullback_setup() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol = "sh600889"
    signal_day = dates[4]
    candles = _build_linear_candles(dates, start=12.0, step=0.15)
    registry = StrategyRegistry()

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        return list(candles) if raw_symbol == symbol else []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> SimpleNamespace | None:
        if raw_symbol != symbol or as_of_date is None:
            return None
        is_signal_day = as_of_date == signal_day
        return SimpleNamespace(
            symbol=raw_symbol,
            as_of_date=as_of_date,
            ret40=0.20,
            retrace20=0.12 if is_signal_day else 0.09,
            up_down_volume_ratio=1.30 if is_signal_day else 1.10,
            vol_slope20=0.03 if is_signal_day else 0.02,
            pullback_volume_ratio=0.74 if is_signal_day else 0.78,
            price_vs_ma20=0.02 if is_signal_day else 0.05,
            ma10_above_ma20_days=6,
            pullback_days=3 if is_signal_day else 1,
            has_blowoff_top=False,
            has_divergence_5d=False,
            has_upper_shadow_risk=False,
        )

    def _calc_snapshot(_row: SimpleNamespace, _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        entry_score = 82.0 if day == signal_day else 60.0
        return {
            "event_dates": {},
            "event_chain": [],
            "events": [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": entry_score,
            "phase": "阶段未明",
            "structure_hhh": "HH|HL|-",
            "trend_score": 70.0,
            "volatility_score": 66.0,
            "health_score": 64.0,
            "event_score": 60.0,
            "event_grade": "C",
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
        strategy_signal_filter=lambda strategy_id, row, snapshot, params: registry.generate_signals(
            strategy_id=strategy_id,
            row=row,
            snapshot=snapshot,
            params=params,
        ),
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        strategy_id="__removed_force_pullback_v1",
        strategy_params=registry.get("__removed_force_pullback_v1").default_params,  # type: ignore[union-attr]
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=50.0,
        require_sequence=False,
        min_event_count=0,
        entry_events=["Spring", "SOS", "JOC", "LPS"],
        exit_events=["UTAD", "SOW", "LPSY"],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=8,
        fee_bps=0.0,
        prioritize_signals=True,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        max_symbols=20,
    )

    result = engine.run(payload=payload, symbols=[symbol])

    assert len(result.trades) == 1
    assert result.trades[0].signal_date == signal_day
    assert result.trades[0].entry_signal == "SCORE"


def test_same_day_liquidation_releases_slots_for_new_entry() -> None:
    dates = _build_trading_days("2025-01-02", 34)
    symbol_a = "sh600001"
    symbol_b = "sh600002"
    symbol_c = "sz003036"
    signal_day = dates[30]
    entry_day = dates[31]

    def _get_candles(raw_symbol: str) -> list[CandlePoint]:
        if raw_symbol == symbol_a:
            return _build_linear_candles(dates, start=10.0, step=0.5)
        if raw_symbol == symbol_b:
            return _build_linear_candles(dates, start=12.0, step=0.5)
        if raw_symbol == symbol_c:
            return _build_linear_candles(dates, start=20.0, step=0.5)
        return []

    def _build_row(raw_symbol: str, as_of_date: str | None) -> dict[str, str] | None:
        if as_of_date is None:
            return None
        if raw_symbol in {symbol_a, symbol_b, symbol_c}:
            return {"symbol": raw_symbol, "as_of_date": as_of_date}
        return None

    def _calc_snapshot(_row: dict[str, str], _window_days: int, as_of_date: str | None) -> dict[str, object]:
        day = str(as_of_date or "")
        raw_symbol = str(_row.get("symbol") or "")
        event_dates: dict[str, str] = {}
        event_chain: list[dict[str, str]] = []
        signal_by_symbol = {
            symbol_a: dates[28],
            symbol_b: dates[29],
            symbol_c: signal_day,
        }
        if day == signal_by_symbol.get(raw_symbol):
            event_dates["E"] = day
            event_chain = [{"event": "E"}]
        return {
            "event_dates": event_dates,
            "event_chain": event_chain,
            "events": ["E"] if event_dates else [],
            "risk_events": [],
            "sequence_ok": True,
            "entry_quality_score": 80.0 if event_dates else 40.0,
            "phase": "吸筹D",
            "structure_hhh": "HH|HL|HC",
            "trend_score": 78.0,
            "volatility_score": 70.0,
            "health_score": 75.0,
            "event_score": 70.0,
            "event_grade": "B",
        }

    engine = BacktestEngine(
        get_candles=_get_candles,
        build_row=_build_row,
        calc_snapshot=_calc_snapshot,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=[],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=2,
        stop_loss=0.0,
        take_profit=0.0,
        max_hold_days=1,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )

    result = engine.run(payload=payload, symbols=[symbol_a, symbol_b, symbol_c])

    titan = next((trade for trade in result.trades if trade.symbol == symbol_c), None)
    assert titan is not None
    assert titan.signal_date == signal_day
    assert titan.entry_date == entry_day
    assert any(trade.symbol == symbol_a and trade.exit_date == dates[30] for trade in result.trades)
    assert any(trade.symbol == symbol_b and trade.exit_date == entry_day for trade in result.trades)


def test_trailing_stop_uses_prior_peak_not_same_day_high() -> None:
    candles = [
        CandlePoint(time="2026-05-26", open=44.0, high=44.0, low=44.0, close=44.0, volume=1.0, amount=44.0),
        CandlePoint(time="2026-05-27", open=48.0, high=53.0, low=46.0, close=50.53, volume=1.0, amount=50.53),
        CandlePoint(time="2026-05-28", open=48.79, high=55.58, low=48.79, close=55.58, volume=1.0, amount=55.58),
    ]
    payload = BacktestRunRequest(
        date_from="2026-05-26",
        date_to="2026-05-28",
        trailing_stop_pct=0.0639,
        take_profit=0.0,
        stop_loss=0.0,
        enforce_t1=True,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=True,
    )
    exit_resolved = BacktestEngine._resolve_exit(candles, 1, {}, payload)
    assert exit_resolved is not None
    exit_index, exit_price, exit_reason = exit_resolved
    assert exit_reason == "open"
    assert exit_index == 2
    assert exit_price == pytest.approx(55.58)


def test_trailing_stop_entry_day_open_to_close_triggers_intraday() -> None:
    candles = [
        CandlePoint(time="2026-05-26", open=10.0, high=10.0, low=10.0, close=10.0, volume=1.0, amount=10.0),
        CandlePoint(time="2026-05-27", open=10.0, high=10.5, low=9.0, close=9.2, volume=1.0, amount=9.2),
        CandlePoint(time="2026-05-28", open=9.1, high=9.2, low=9.0, close=9.05, volume=1.0, amount=9.05),
    ]
    payload = BacktestRunRequest(
        date_from="2026-05-26",
        date_to="2026-05-28",
        trailing_stop_pct=0.05,
        take_profit=0.0,
        stop_loss=0.0,
        enforce_t1=True,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=False,
    )
    exit_resolved = BacktestEngine._resolve_exit(candles, 1, {}, payload)
    assert exit_resolved is not None
    _, _, exit_reason = exit_resolved
    assert exit_reason.startswith("trailing_stop_intraday")


def test_trailing_stop_triggers_on_close_breach_not_intraday_low() -> None:
    candles = [
        CandlePoint(time="2026-05-26", open=10.0, high=10.0, low=10.0, close=10.0, volume=1.0, amount=10.0),
        CandlePoint(time="2026-05-27", open=10.0, high=12.0, low=9.0, close=11.5, volume=1.0, amount=11.5),
        CandlePoint(time="2026-05-28", open=9.5, high=12.5, low=9.5, close=12.0, volume=1.0, amount=12.0),
    ]
    payload = BacktestRunRequest(
        date_from="2026-05-26",
        date_to="2026-05-28",
        trailing_stop_pct=0.05,
        take_profit=0.0,
        stop_loss=0.0,
        enforce_t1=True,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=True,
        daily_trailing_confirm_days=2,
    )
    exit_resolved = BacktestEngine._resolve_exit(candles, 1, {}, payload)
    assert exit_resolved is not None
    _, _, exit_reason = exit_resolved
    assert exit_reason == "open"


def test_operation_plan_sell_does_not_require_next_day_in_scan_dates() -> None:
    engine = BacktestEngine(
        get_candles=lambda symbol: [],
        build_row=lambda *args, **kwargs: None,
        calc_snapshot=lambda *args, **kwargs: None,
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(date_from="2026-05-26", date_to="2026-05-28", max_positions=5)
    trades = [
        BacktestTrade(
            symbol="sz002552",
            name="宝鼎科技",
            signal_date="2026-05-26",
            entry_date="2026-05-27",
            exit_date="2026-05-28",
            entry_signal="金叉",
            exit_reason="trailing_stop_daily",
            quantity=100,
            entry_price=48.0,
            exit_price=50.35,
            holding_days=1,
            pnl_amount=235.0,
            pnl_ratio=0.049,
        )
    ]
    plans = engine._build_operation_plans(
        trading_dates=["2026-05-26", "2026-05-27", "2026-05-28"],
        trades=trades,
        plan_signals=[],
        payload=payload,
    )
    plan_527 = next(item for item in plans if item.date == "2026-05-27")
    assert len(plan_527.sell_signals) == 1
    assert plan_527.sell_signals[0].symbol == "sz002552"


def test_compute_reduce_sell_shares_respects_lot_size() -> None:
    assert BacktestEngine._compute_reduce_sell_shares(1000, 0.5) == 500
    assert BacktestEngine._compute_reduce_sell_shares(300, 0.5) == 100
    assert BacktestEngine._compute_reduce_sell_shares(100, 0.5) == 100


def test_resolve_exit_matrix_legs_emits_reduce_before_daily_clear() -> None:
    open_col = np.array([10.0, 10.0, 10.0, 9.0, 8.5, 8.0], dtype=float)
    high_col = np.array([10.0, 12.0, 11.0, 9.5, 8.8, 8.2], dtype=float)
    low_col = np.array([10.0, 9.0, 8.5, 8.4, 8.0, 7.8], dtype=float)
    close_col = np.array([10.0, 9.2, 8.8, 8.6, 8.2, 8.0], dtype=float)
    valid_col = np.ones(len(open_col), dtype=bool)
    sell_col = np.zeros(len(open_col), dtype=bool)
    payload = BacktestRunRequest(
        date_from="2026-05-26",
        date_to="2026-05-31",
        trailing_stop_pct=0.05,
        take_profit=0.0,
        stop_loss=0.0,
        enforce_t1=True,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=True,
        daily_trailing_confirm_days=2,
        intraday_trailing_reduce_ratio=0.5,
    )
    dates = ["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30", "2026-05-31"]
    legs = BacktestEngine._resolve_exit_matrix_legs(
        entry_index=1,
        entry_price=10.0,
        open_col=open_col,
        high_col=high_col,
        low_col=low_col,
        close_col=close_col,
        valid_col=valid_col,
        sell_col=sell_col,
        sell_label_col=None,
        payload=payload,
        dates=dates,
    )
    assert len(legs) >= 2
    assert legs[0].exit_reason.startswith("trailing_stop_intraday:reduce=")
    assert legs[-1].exit_reason in {"trailing_stop_daily", "open", "time_exit"} or legs[-1].exit_reason.startswith("event_exit")


def test_run_records_intraday_reduce_trades_in_trade_list() -> None:
    dates = ["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30", "2026-05-31"]
    symbols = ["sh600001"]
    t, n = len(dates), len(symbols)
    open_px = np.asarray([[10.0], [10.0], [10.0], [9.0], [8.5], [8.0]], dtype=np.float64)
    high_px = np.asarray([[10.0], [12.0], [11.0], [9.5], [8.8], [8.2]], dtype=np.float64)
    low_px = np.asarray([[10.0], [9.0], [8.5], [8.4], [8.0], [7.8]], dtype=np.float64)
    close_px = np.asarray([[10.0], [9.2], [8.8], [8.6], [8.2], [8.0]], dtype=np.float64)
    volume = np.asarray([[100000.0] for _ in range(t)], dtype=np.float64)
    valid = np.ones((t, n), dtype=bool)
    buy = np.zeros((t, n), dtype=bool)
    buy[0, 0] = True
    sell = np.zeros((t, n), dtype=bool)
    score = np.zeros((t, n), dtype=np.float64)
    score[0, 0] = 90.0

    bundle = MatrixBundle(
        dates=list(dates),
        symbols=list(symbols),
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        valid_mask=valid,
    )
    signals = BacktestSignalMatrix(
        s1=np.zeros((t, n), dtype=bool),
        s2=np.zeros((t, n), dtype=bool),
        s3=np.zeros((t, n), dtype=bool),
        s4=np.zeros((t, n), dtype=bool),
        s5=buy.copy(),
        s6=np.zeros((t, n), dtype=bool),
        s7=np.ones((t, n), dtype=bool),
        s8=np.zeros((t, n), dtype=bool),
        s9=np.zeros((t, n), dtype=bool),
        in_pool=buy.copy(),
        buy_signal=buy.copy(),
        sell_signal=sell.copy(),
        score=score,
    )
    engine = BacktestEngine(
        get_candles=lambda _: [],
        build_row=lambda symbol, as_of_date=None: {"symbol": symbol, "as_of_date": as_of_date},
        calc_snapshot=lambda row, window_days, as_of_date=None: {},
        resolve_symbol_name=lambda raw_symbol: raw_symbol,
    )
    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from=dates[0],
        date_to=dates[-1],
        window_days=60,
        min_score=0.0,
        require_sequence=False,
        min_event_count=1,
        entry_events=["E"],
        exit_events=[],
        initial_capital=100000.0,
        position_pct=1.0,
        max_positions=1,
        stop_loss=0.0,
        take_profit=0.0,
        trailing_stop_pct=0.05,
        intraday_trailing_enabled=True,
        daily_trailing_clear_enabled=True,
        intraday_trailing_reduce_ratio=0.5,
        daily_trailing_confirm_days=2,
        max_hold_days=60,
        fee_bps=0.0,
        prioritize_signals=False,
        priority_mode="balanced",
        priority_topk_per_day=0,
        enforce_t1=True,
        entry_delay_days=1,
        delay_invalidation_enabled=False,
        max_symbols=20,
    )
    result = engine.run(
        payload=payload,
        symbols=list(symbols),
        matrix_bundle=bundle,
        matrix_signals=signals,
    )

    reduce_trades = [
        trade
        for trade in result.trades
        if str(trade.exit_reason).startswith("trailing_stop_intraday:reduce=")
    ]
    assert len(reduce_trades) >= 1
    assert reduce_trades[0].quantity > 0
    total_qty = sum(trade.quantity for trade in result.trades if trade.symbol == "sh600001")
    assert total_qty >= reduce_trades[0].quantity
    assert len(result.trades) >= 2
