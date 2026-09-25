from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_STATE_ROOT = ROOT / ".test-state"
TEST_STATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TDX_TREND_APP_STATE_PATH", str(TEST_STATE_ROOT / "app_state.json"))
os.environ.setdefault("TDX_TREND_SIM_STATE_PATH", str(TEST_STATE_ROOT / "sim_state.json"))
os.environ.setdefault("TDX_TREND_WYCKOFF_STORE_PATH", str(TEST_STATE_ROOT / "wyckoff_events.sqlite"))
os.environ.setdefault("TDX_TREND_WYCKOFF_STORE_ENABLED", "1")
os.environ.setdefault("TDX_TREND_WYCKOFF_STORE_READ_ONLY", "0")

from app.main import app
from app.models import CandlePoint, SignalResult, SignalsResponse
from app.store import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_signal_etf_store() -> None:
    store._signal_etf_backtest_store = {}
    yield
    store._signal_etf_backtest_store = {}


@pytest.fixture
def patch_signal_etf_candles(monkeypatch: pytest.MonkeyPatch):
    dates = [
        "2026-02-03",
        "2026-02-04",
        "2026-02-05",
        "2026-02-06",
        "2026-02-07",
        "2026-02-10",
    ]

    def _build(base_open: float, daily_step: float) -> list[CandlePoint]:
        out: list[CandlePoint] = []
        for idx, day in enumerate(dates):
            open_price = base_open + daily_step * idx
            close_price = open_price * (1 + 0.01 * ((idx % 3) - 1))
            out.append(
                CandlePoint(
                    time=day,
                    open=round(open_price, 2),
                    high=round(open_price * 1.02, 2),
                    low=round(open_price * 0.98, 2),
                    close=round(close_price, 2),
                    volume=1_000_000,
                    amount=round(close_price * 1_000_000, 2),
                    price_source="vwap",
                )
            )
        return out

    candles_map = {
        "sz300750": _build(100, 1.5),
        "sh600519": _build(200, 1.2),
        "sh000001": _build(280, 1.0),
        "sh000300": _build(300, 0.8),
    }

    def _fake_load_signal_etf_candles(
        symbol: str,
        *,
        window_bars: int,
        candle_cache: dict[str, list[CandlePoint]],
    ) -> list[CandlePoint]:
        _ = window_bars
        key = symbol.strip().lower()
        if key in candle_cache:
            return candle_cache[key]
        rows = [item.model_copy(deep=True) for item in candles_map.get(key, [])]
        candle_cache[key] = rows
        return rows

    monkeypatch.setattr(store, "_load_signal_etf_candles", _fake_load_signal_etf_candles)
    return dates


def _build_create_payload() -> dict[str, object]:
    return {
        "strategy_id": "wyckoff_trend_v1",
        "strategy_name": "趋势策略A",
        "signal_date": "2026-02-03",
        "constituents": [
            {
                "symbol": "sz300750",
                "name": "宁德时代",
                "signal_date": "2026-02-03",
                "signal_primary": "B",
                "signal_event": "SOS",
                "signal_reason": "测试信号1",
            },
            {
                "symbol": "sh600519",
                "name": "贵州茅台",
                "signal_date": "2026-02-03",
                "signal_primary": "A",
                "signal_event": "LPS",
                "signal_reason": "测试信号2",
            },
        ],
    }


def test_signal_etf_backtest_crud_flow(patch_signal_etf_candles) -> None:
    _ = patch_signal_etf_candles
    create_resp = client.post("/api/signals/etf-backtests", json=_build_create_payload())
    assert create_resp.status_code == 200
    created = create_resp.json()
    record_id = created["record_id"]
    assert record_id.startswith("setf_")
    assert created["name"] == "趋势策略A_2026-02-03"
    assert len(created["constituents"]) == 2

    list_resp = client.get("/api/signals/etf-backtests")
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert len(list_body["items"]) == 1
    assert list_body["items"][0]["summary"]["strategy_stats"]["total_records"] == 1

    detail_resp = client.get(f"/api/signals/etf-backtests/{record_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["record_id"] == record_id
    assert len(detail["curve"]) > 0

    patch_resp = client.patch(
        f"/api/signals/etf-backtests/{record_id}",
        json={"name": "自定义ETF名称", "notes": "这是备注"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["name"] == "自定义ETF名称"
    assert patched["notes"] == "这是备注"

    delete_resp = client.delete(f"/api/signals/etf-backtests/{record_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    list_after_delete = client.get("/api/signals/etf-backtests")
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["items"] == []


def test_signal_etf_backtest_duplicate_name_suffix_and_strategy_stats(patch_signal_etf_candles) -> None:
    _ = patch_signal_etf_candles
    payload = _build_create_payload()

    first = client.post("/api/signals/etf-backtests", json=payload)
    second = client.post("/api/signals/etf-backtests", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["record_id"] != second.json()["record_id"]
    assert second.json()["name"] == "趋势策略A_2026-02-03-2"

    listed = client.get("/api/signals/etf-backtests")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    names = {item["name"] for item in items}
    assert "趋势策略A_2026-02-03" in names
    assert "趋势策略A_2026-02-03-2" in names
    for row in items:
        stats = row["summary"]["strategy_stats"]
        assert stats["total_records"] == 2
        assert 0.0 <= stats["win_rate_t1"] <= 1.0
        assert 0.0 <= stats["win_rate_t2"] <= 1.0


def test_signal_etf_backtest_detail_supports_as_of_date(patch_signal_etf_candles) -> None:
    _ = patch_signal_etf_candles
    create_resp = client.post("/api/signals/etf-backtests", json=_build_create_payload())
    assert create_resp.status_code == 200
    record_id = create_resp.json()["record_id"]

    latest_resp = client.get(f"/api/signals/etf-backtests/{record_id}", params={"refresh": "true"})
    cutoff_resp = client.get(
        f"/api/signals/etf-backtests/{record_id}",
        params={"refresh": "true", "as_of_date": "2026-02-05"},
    )
    assert latest_resp.status_code == 200
    assert cutoff_resp.status_code == 200

    latest = latest_resp.json()
    cutoff = cutoff_resp.json()
    assert len(latest["curve"]) >= len(cutoff["curve"])
    if cutoff["curve"]:
        assert cutoff["curve"][-1]["date"] <= "2026-02-05"

    for row in cutoff["constituents"]:
        current_date = row.get("current_date")
        if isinstance(current_date, str) and current_date:
            assert current_date <= "2026-02-05"


def test_signal_etf_backtest_detail_supports_holding_days(patch_signal_etf_candles) -> None:
    _ = patch_signal_etf_candles
    create_resp = client.post("/api/signals/etf-backtests", json=_build_create_payload())
    assert create_resp.status_code == 200
    record_id = create_resp.json()["record_id"]

    detail_resp = client.get(
        f"/api/signals/etf-backtests/{record_id}",
        params={"refresh": "true", "holding_days": 3},
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["constituents"]) > 0
    for row in detail["constituents"]:
        assert row.get("holding_period_days") == 3
        assert "holding_target_date" in row
        assert "return_pct_holding" in row


def test_signal_etf_backtest_detail_holding_days_uses_symbol_specific_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [
        "2026-02-03",
        "2026-02-04",
        "2026-02-05",
        "2026-02-06",
    ]

    def _build(opens: list[float], closes: list[float]) -> list[CandlePoint]:
        out: list[CandlePoint] = []
        for day, open_price, close_price in zip(dates, opens, closes):
            out.append(
                CandlePoint(
                    time=day,
                    open=round(open_price, 2),
                    high=round(max(open_price, close_price) * 1.02, 2),
                    low=round(min(open_price, close_price) * 0.98, 2),
                    close=round(close_price, 2),
                    volume=1_000_000,
                    amount=round(close_price * 1_000_000, 2),
                    price_source="vwap",
                )
            )
        return out

    candles_map = {
        # signal_date=2026-02-03 -> T+1 buy on 2026-02-04
        # holding_days=3 -> target date 2026-02-06
        # expected holding return:
        # sz300750: 130 / 100 - 1 = 0.30
        # sh600519: 11 / 10 - 1 = 0.10
        "sz300750": _build([99.0, 100.0, 120.0, 130.0], [99.0, 100.0, 120.0, 130.0]),
        "sh600519": _build([9.5, 10.0, 10.5, 11.0], [9.5, 10.0, 10.5, 11.0]),
        "sh000001": _build([3000.0, 3005.0, 3010.0, 3015.0], [3000.0, 3005.0, 3010.0, 3015.0]),
    }

    def _fake_load_signal_etf_candles(
        symbol: str,
        *,
        window_bars: int,
        candle_cache: dict[str, list[CandlePoint]],
    ) -> list[CandlePoint]:
        _ = window_bars
        key = symbol.strip().lower()
        if key in candle_cache:
            return candle_cache[key]
        rows = [item.model_copy(deep=True) for item in candles_map.get(key, [])]
        candle_cache[key] = rows
        return rows

    monkeypatch.setattr(store, "_load_signal_etf_candles", _fake_load_signal_etf_candles)

    create_resp = client.post("/api/signals/etf-backtests", json=_build_create_payload())
    assert create_resp.status_code == 200
    record_id = create_resp.json()["record_id"]

    detail_resp = client.get(
        f"/api/signals/etf-backtests/{record_id}",
        params={"refresh": "true", "holding_days": 3},
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    rows = {str(item.get("symbol")): item for item in detail.get("constituents", [])}

    assert rows["sz300750"]["holding_target_date"] == "2026-02-06"
    assert rows["sh600519"]["holding_target_date"] == "2026-02-06"
    assert rows["sz300750"]["return_pct_holding"] == pytest.approx(0.3, abs=1e-6)
    assert rows["sh600519"]["return_pct_holding"] == pytest.approx(0.1, abs=1e-6)


def test_signal_etf_backtest_list_supports_holding_days(patch_signal_etf_candles) -> None:
    _ = patch_signal_etf_candles
    create_resp = client.post("/api/signals/etf-backtests", json=_build_create_payload())
    assert create_resp.status_code == 200

    list_resp = client.get("/api/signals/etf-backtests", params={"holding_days": 4})
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert len(body["items"]) == 1
    summary = body["items"][0]["summary"]
    assert summary.get("holding_period_days") == 4
    assert "holding_target_date" in summary
    assert "holding_return_pct" in summary


def test_signal_etf_backtest_auto_create_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_dates: list[str] = []

    monkeypatch.setattr(
        store,
        "_require_backtest_trend_pool_run",
        lambda run_id: SimpleNamespace(run_id=run_id),
    )

    def _fake_get_signals(
        *,
        mode="trend_pool",
        run_id=None,
        trend_step="auto",
        strategy_id=None,
        strategy_params=None,
        board_filters=None,
        as_of_date=None,
        refresh=False,
        window_days=60,
        min_score=60,
        require_sequence=False,
        min_event_count=1,
        signal_age_min=0,
        signal_age_max=None,
        **kwargs,
    ) -> SignalsResponse:
        _ = (
            mode,
            trend_step,
            strategy_id,
            strategy_params,
            board_filters,
            refresh,
            window_days,
            min_score,
            require_sequence,
            min_event_count,
            signal_age_min,
            signal_age_max,
            kwargs,
        )
        assert str(run_id) == "run-test-001"
        day = str(as_of_date or "").strip()
        requested_dates.append(day)
        if day == "2026-02-04":
            return SignalsResponse(
                items=[],
                mode="trend_pool",
                as_of_date=day,
                generated_at="2026-02-04T10:00:00+08:00",
                cache_hit=False,
                degraded=False,
                source_count=0,
            )
        return SignalsResponse(
            items=[
                SignalResult(
                    symbol="sz300750",
                    name="宁德时代",
                    primary_signal="B",
                    secondary_signals=["A"],
                    trigger_date=day,
                    expire_date="2026-02-28",
                    trigger_reason="test",
                    priority=3,
                )
            ],
            mode="trend_pool",
            as_of_date=day,
            generated_at=f"{day}T10:00:00+08:00",
            cache_hit=False,
            degraded=False,
            source_count=1,
            strategy_id="wyckoff_trend_v1",
            strategy_version="1.0.0",
            strategy_params={},
            strategy_params_hash="test",
            notes=[],
        )

    monkeypatch.setattr(store, "get_signals", _fake_get_signals)

    resp = client.post(
        "/api/signals/etf-backtests/auto",
        json={
            "run_id": "run-test-001",
            "strategy_id": "wyckoff_trend_v1",
            "strategy_name": "维科夫趋势V1",
            "date_from": "2026-02-03",
            "date_to": "2026-02-05",
            "trend_step": "auto",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed_dates"] == 3
    assert body["created_count"] == 2
    assert body["skipped_count"] == 1
    assert body["failed_count"] == 0
    assert len(body["created"]) == 2
    assert requested_dates == ["2026-02-03", "2026-02-04", "2026-02-05"]
    assert len(store._signal_etf_backtest_store) == 2


def test_signal_etf_backtest_auto_create_supports_full_market(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_modes: list[str] = []

    def _fake_get_signals(
        *,
        mode="trend_pool",
        run_id=None,
        trend_step="auto",
        strategy_id=None,
        strategy_params=None,
        market_filters=None,
        board_filters=None,
        as_of_date=None,
        refresh=False,
        window_days=60,
        min_score=60,
        require_sequence=False,
        min_event_count=1,
        signal_age_min=0,
        signal_age_max=None,
        **kwargs,
    ) -> SignalsResponse:
        _ = (
            run_id,
            trend_step,
            strategy_id,
            strategy_params,
            market_filters,
            board_filters,
            refresh,
            window_days,
            min_score,
            require_sequence,
            min_event_count,
            signal_age_min,
            signal_age_max,
            kwargs,
        )
        requested_modes.append(str(mode))
        day = str(as_of_date or "").strip()
        return SignalsResponse(
            items=[
                SignalResult(
                    symbol="sz300750",
                    name="宁德时代",
                    primary_signal="A",
                    secondary_signals=[],
                    trigger_date=day,
                    expire_date="2026-02-28",
                    trigger_reason="wulong test",
                    priority=2,
                    wyckoff_signal="五龙聚首",
                )
            ],
            mode="full_market",
            as_of_date=day,
            generated_at=f"{day}T10:00:00+08:00",
            cache_hit=False,
            degraded=False,
            source_count=1,
            strategy_id="wulong_cluster_v1",
            strategy_version="2.0.0-alpha",
            strategy_params={},
            strategy_params_hash="test",
            notes=[],
        )

    monkeypatch.setattr(store, "get_signals", _fake_get_signals)

    resp = client.post(
        "/api/signals/etf-backtests/auto",
        json={
            "mode": "full_market",
            "strategy_id": "wulong_cluster_v1",
            "strategy_name": "五龙聚首",
            "date_from": "2026-02-03",
            "date_to": "2026-02-04",
            "market_filters": ["sh", "sz"],
            "min_event_count": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == ""
    assert body["created_count"] == 2
    assert requested_modes == ["full_market", "full_market"]
    assert len(store._signal_etf_backtest_store) == 2
