from __future__ import annotations

import json

import os
import sqlite3
import sys
import time
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
from app.models import ScreenerResult
from app.store import store
from app.tdx_loader import load_candles_for_symbol


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_sim_account() -> None:
    resp = client.post("/api/sim/reset")
    assert resp.status_code == 200


def _load_symbol_dates(symbol: str) -> list[str]:
    resp = client.get(f"/api/stocks/{symbol}/candles")
    assert resp.status_code == 200
    candles = resp.json()["candles"]
    assert len(candles) >= 10
    return [item["time"] for item in candles]


def _post_buy(symbol: str, submit_date: str, quantity: int = 100) -> dict[str, object]:
    payload = {
        "symbol": symbol,
        "side": "buy",
        "quantity": quantity,
        "signal_date": submit_date,
        "submit_date": submit_date,
    }
    resp = client.post("/api/sim/orders", json=payload)
    assert resp.status_code == 200
    return resp.json()["order"]


def _post_sell(symbol: str, submit_date: str, quantity: int = 100) -> dict[str, object]:
    payload = {
        "symbol": symbol,
        "side": "sell",
        "quantity": quantity,
        "signal_date": submit_date,
        "submit_date": submit_date,
    }
    resp = client.post("/api/sim/orders", json=payload)
    assert resp.status_code == 200
    return resp.json()["order"]


def test_load_candles_fallback_to_akshare_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_file = tmp_path / "sz300750.csv"
    cache_file.write_text(
        "\n".join(
            [
                "date,open,high,low,close,volume,amount,symbol",
                "2026-02-10,100,101,99,100.5,123400,123450000,sz300750",
                "2026-02-11,101,102,100,101.5,133400,135450000,sz300750",
                "2026-02-12,102,103,101,102.5,143400,147450000,sz300750",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AKSHARE_CACHE_DIR", str(tmp_path))
    candles = load_candles_for_symbol(r"Z:\not-exists\vipdoc", "sz300750", window=2)
    assert candles is not None
    assert len(candles) == 2
    assert candles[-1].time == "2026-02-12"
    assert candles[-1].price_source == "approx"


def test_load_candles_with_configured_data_source(tmp_path: Path) -> None:
    cache_file = tmp_path / "sz300750.csv"
    cache_file.write_text(
        "\n".join(
            [
                "date,open,high,low,close,volume,amount,symbol",
                "2026-02-12,102,103,101,102.5,143400,147450000,sz300750",
            ]
        ),
        encoding="utf-8",
    )
    candles = load_candles_for_symbol(
        r"Z:\not-exists\vipdoc",
        "sz300750",
        window=1,
        market_data_source="akshare_only",
        akshare_cache_dir=str(tmp_path),
    )
    assert candles is not None
    assert len(candles) == 1
    assert candles[-1].time == "2026-02-12"

    candles_tdx_only = load_candles_for_symbol(
        r"Z:\not-exists\vipdoc",
        "sz300750",
        window=1,
        market_data_source="tdx_only",
        akshare_cache_dir=str(tmp_path),
    )
    assert candles_tdx_only is None


def test_run_and_get_screener() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    payload = {
        "markets": ["sh", "sz"],
        "mode": "strict",
        "as_of_date": as_of_date,
        "return_window_days": 40,
        "top_n": 500,
        "turnover_threshold": 0.05,
        "amount_threshold": 500000000,
        "amplitude_threshold": 0.03,
    }

    run_resp = client.post("/api/screener/run", json=payload)
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]

    detail_resp = client.get(f"/api/screener/runs/{run_id}")
    assert detail_resp.status_code == 200
    body = detail_resp.json()
    assert body["as_of_date"] == as_of_date
    assert body["step_summary"]["input_count"] > 0
    assert body["step_pools"]["input"][0]["name"] != "绉戞妧鏍锋湰1"


def test_screener_result_cache_reuses_persisted_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TDX_TREND_SCREENER_RESULT_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_SCREENER_RESULT_CACHE_TTL_SEC", "3600")
    monkeypatch.setenv("TDX_TREND_SCREENER_RESULT_CACHE_DIR", str(tmp_path / "screener-result-cache"))

    call_counter = {"count": 0}

    def _fake_load_input_pool_rows(
        *,
        markets: list[str],
        return_window_days: int,
        as_of_date: str | None,
    ) -> tuple[list[ScreenerResult], str | None, bool]:
        _ = (markets, return_window_days)
        call_counter["count"] += 1
        day = str(as_of_date or "2025-01-02")
        row = ScreenerResult(
            symbol="sz300750",
            name="宁德时代",
            latest_price=10.0,
            day_change=0.1,
            day_change_pct=0.01,
            score=80,
            ret40=0.25,
            turnover20=0.08,
            amount20=8e8,
            amplitude20=0.05,
            retrace20=0.03,
            pullback_days=3,
            ma10_above_ma20_days=8,
            ma5_above_ma10_days=6,
            price_vs_ma20=0.06,
            vol_slope20=0.1,
            up_down_volume_ratio=1.4,
            pullback_volume_ratio=0.7,
            has_blowoff_top=False,
            has_divergence_5d=False,
            has_upper_shadow_risk=False,
            ai_confidence=0.7,
            theme_stage="发酵中",
            trend_class="A",
            stage="Mid",
            labels=[day],
            reject_reasons=[],
            degraded=False,
            degraded_reason=None,
        )
        return [row], None, False

    monkeypatch.setattr(store, "_load_input_pool_rows", _fake_load_input_pool_rows)

    payload = {
        "markets": ["sh", "sz"],
        "mode": "strict",
        "as_of_date": "2025-01-03",
        "return_window_days": 40,
        "top_n": 500,
        "turnover_threshold": 0.05,
        "amount_threshold": 500000000,
        "amplitude_threshold": 0.03,
    }
    run1 = client.post("/api/screener/run", json=payload)
    assert run1.status_code == 200
    run1_id = run1.json()["run_id"]
    detail1 = client.get(f"/api/screener/runs/{run1_id}")
    assert detail1.status_code == 200
    assert detail1.json()["step_summary"]["input_count"] == 1

    run2 = client.post("/api/screener/run", json=payload)
    assert run2.status_code == 200
    run2_id = run2.json()["run_id"]
    assert run2_id != run1_id
    detail2 = client.get(f"/api/screener/runs/{run2_id}")
    assert detail2.status_code == 200
    assert detail2.json()["step_summary"]["input_count"] == 1

    assert call_counter["count"] == 1


def test_screener_result_cache_reuses_payload_without_as_of_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TDX_TREND_SCREENER_RESULT_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_SCREENER_RESULT_CACHE_TTL_SEC", "3600")
    monkeypatch.setenv("TDX_TREND_SCREENER_RESULT_CACHE_DIR", str(tmp_path / "screener-result-cache-no-date"))

    call_counter = {"count": 0}

    def _fake_load_input_pool_rows(
        *,
        markets: list[str],
        return_window_days: int,
        as_of_date: str | None,
    ) -> tuple[list[ScreenerResult], str | None, bool]:
        _ = (markets, return_window_days, as_of_date)
        call_counter["count"] += 1
        row = ScreenerResult(
            symbol="sz300750",
            name="宁德时代",
            latest_price=10.0,
            day_change=0.1,
            day_change_pct=0.01,
            score=80,
            ret40=0.25,
            turnover20=0.08,
            amount20=8e8,
            amplitude20=0.05,
            retrace20=0.03,
            pullback_days=3,
            ma10_above_ma20_days=8,
            ma5_above_ma10_days=6,
            price_vs_ma20=0.06,
            vol_slope20=0.1,
            up_down_volume_ratio=1.4,
            pullback_volume_ratio=0.7,
            has_blowoff_top=False,
            has_divergence_5d=False,
            has_upper_shadow_risk=False,
            ai_confidence=0.7,
            theme_stage="发酵中",
            trend_class="A",
            stage="Mid",
            labels=["latest"],
            reject_reasons=[],
            degraded=False,
            degraded_reason=None,
        )
        return [row], None, False

    monkeypatch.setattr(store, "_load_input_pool_rows", _fake_load_input_pool_rows)

    payload = {
        "markets": ["sh", "sz"],
        "mode": "strict",
        "return_window_days": 40,
        "top_n": 500,
        "turnover_threshold": 0.05,
        "amount_threshold": 500000000,
        "amplitude_threshold": 0.03,
    }

    run1 = client.post("/api/screener/run", json=payload)
    assert run1.status_code == 200
    run2 = client.post("/api/screener/run", json=payload)
    assert run2.status_code == 200
    assert run2.json()["run_id"] != run1.json()["run_id"]
    assert call_counter["count"] == 1


def test_annotation_roundtrip() -> None:
    decision_keep = chr(0x4FDD) + chr(0x7559)
    payload = {
        "symbol": "sh600519",
        "start_date": "2026-01-01",
        "stage": "Mid",
        "trend_class": "A",
        "decision": decision_keep,
        "notes": "manual test",
        "updated_by": "manual",
    }

    save_resp = client.put("/api/stocks/sh600519/annotations", json=payload)
    assert save_resp.status_code == 200
    assert save_resp.json()["annotation"]["symbol"] == "sh600519"

    get_resp = client.get("/api/stocks/sh600519/analysis")
    assert get_resp.status_code == 200
    assert get_resp.json()["annotation"]["symbol"] == "sh600519"
    signal = get_resp.json()["signal"]
    assert signal["symbol"] == "sh600519"
    assert len(signal["wy_event_chain"]) >= 1


def test_analysis_signal_snapshot_uses_current_chart_window(monkeypatch: pytest.MonkeyPatch) -> None:
    symbol = "sh600519"
    candles_resp = client.get(f"/api/stocks/{symbol}/candles")
    assert candles_resp.status_code == 200
    expected_window_days = len(candles_resp.json()["candles"])
    assert expected_window_days >= 120

    original = store._calc_wyckoff_snapshot
    captured: dict[str, int] = {}

    def wrapped(row: ScreenerResult, window_days: int, *, as_of_date: str | None = None) -> dict[str, object]:
        captured["window_days"] = window_days
        return original(row, window_days=window_days, as_of_date=as_of_date)

    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", wrapped)

    resp = client.get(f"/api/stocks/{symbol}/analysis")
    assert resp.status_code == 200
    assert captured["window_days"] == expected_window_days


def test_config_update() -> None:
    config_resp = client.get("/api/config")
    assert config_resp.status_code == 200
    config = config_resp.json()

    config["ai_provider"] = "deepseek"
    config["backtest_default_execution_path_preference"] = "legacy"
    update_resp = client.put("/api/config", json=config)
    assert update_resp.status_code == 200
    assert update_resp.json()["ai_provider"] == "deepseek"
    assert update_resp.json()["backtest_default_execution_path_preference"] == "legacy"


def test_config_akshare_switch_affects_candles_endpoint(tmp_path: Path) -> None:
    cache_file = tmp_path / "sz300750.csv"
    cache_file.write_text(
        "\n".join(
            [
                "date,open,high,low,close,volume,amount,symbol",
                "2026-02-12,102,103,101,102.5,143400,147450000,sz300750",
            ]
        ),
        encoding="utf-8",
    )
    original_config = client.get("/api/config").json()
    changed_config = dict(original_config)
    changed_config["tdx_data_path"] = r"Z:\not-exists\vipdoc"
    changed_config["market_data_source"] = "akshare_only"
    changed_config["akshare_cache_dir"] = str(tmp_path)

    try:
        update_resp = client.put("/api/config", json=changed_config)
        assert update_resp.status_code == 200
        candles_resp = client.get("/api/stocks/sz300750/candles")
        assert candles_resp.status_code == 200
        candles = candles_resp.json()["candles"]
        assert len(candles) == 1
        assert candles[0]["time"] == "2026-02-12"
        assert candles[0]["price_source"] == "approx"
    finally:
        restore_resp = client.put("/api/config", json=original_config)
        assert restore_resp.status_code == 200


def test_system_storage_endpoint() -> None:
    resp = client.get("/api/system/storage")
    assert resp.status_code == 200
    body = resp.json()
    assert "app_state_path" in body
    assert "sim_state_path" in body
    assert "akshare_cache_candidates" in body
    assert isinstance(body["akshare_cache_candidates"], list)


def test_wyckoff_event_store_stats_endpoint() -> None:
    resp = client.get("/api/system/wyckoff-event-store/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    assert "db_record_count" in body
    assert "cache_hits" in body
    assert "cache_misses" in body
    assert "cache_hit_rate" in body
    assert "cache_miss_rate" in body
    assert "snapshot_reads" in body
    assert "avg_snapshot_read_ms" in body
    assert "quality_empty_events" in body
    assert "quality_score_outliers" in body
    assert "quality_date_misaligned" in body


def test_wyckoff_event_store_backfill_endpoint() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-12]
    date_to = dates[-10]
    payload = {
        "date_from": date_from,
        "date_to": date_to,
        "markets": ["sz"],
        "window_days_list": [60],
        "max_symbols_per_day": 40,
        "force_rebuild": False,
    }
    resp = client.post("/api/system/wyckoff-event-store/backfill", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["scan_dates"] >= 1
    assert body["symbols_scanned"] >= 0
    assert body["computed_count"] >= 0
    assert body["write_count"] >= 0
    assert body["quality_empty_events"] >= 0
    assert body["quality_score_outliers"] >= 0
    assert body["quality_date_misaligned"] >= 0


def test_market_data_sync_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sync_market_data(_payload):
        return {
            "ok": True,
            "provider": "baostock",
            "mode": "incremental",
            "message": "mock sync done",
            "out_dir": r"C:\\tmp\\market-data",
            "symbol_count": 10,
            "ok_count": 10,
            "fail_count": 0,
            "skipped_count": 6,
            "new_rows_total": 8,
            "started_at": "2026-02-13 10:00:00",
            "finished_at": "2026-02-13 10:00:02",
            "duration_sec": 2.0,
            "errors": [],
        }

    monkeypatch.setattr(store, "sync_market_data", fake_sync_market_data)
    resp = client.post(
        "/api/system/sync-market-data",
        json={
            "provider": "baostock",
            "mode": "incremental",
            "symbols": "",
            "all_market": True,
            "limit": 100,
            "start_date": "",
            "end_date": "",
            "initial_days": 180,
            "sleep_sec": 0.0,
            "out_dir": "",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "baostock"
    assert body["new_rows_total"] == 8


def test_intraday_endpoint_returns_points() -> None:
    resp = client.get("/api/stocks/sz000001/intraday", params={"date": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["points"]) > 0
    assert "date" in body


def test_ai_analyze_stock_endpoint() -> None:
    resp = client.post("/api/stocks/sz300750/ai-analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "sz300750"
    assert body["name"]
    assert "summary" in body and body["summary"]
    assert "breakout_date" in body and body["breakout_date"]
    assert "rise_reasons" in body and isinstance(body["rise_reasons"], list)


def test_ai_prompt_preview_endpoint() -> None:
    resp = client.get("/api/stocks/sz000070/ai-prompt-preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "sz000070"
    assert "prompt" in body and "rise_reasons" in body["prompt"]


def test_ai_provider_test_endpoint() -> None:
    payload = {
        "provider": {
            "id": "custom-test",
            "label": "Custom",
            "base_url": "https://example.com/v1",
            "model": "demo-model",
            "api_key": "",
            "api_key_path": "",
            "enabled": True,
        },
        "fallback_api_key": "",
        "fallback_api_key_path": "",
        "timeout_sec": 5,
    }
    resp = client.post("/api/ai/providers/test", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "AI_KEY_MISSING"


def test_delete_ai_record_endpoint() -> None:
    create_resp = client.post("/api/stocks/sz300750/ai-analyze")
    assert create_resp.status_code == 200
    record = create_resp.json()

    delete_resp = client.delete(
        "/api/ai/records",
        params={
            "symbol": record["symbol"],
            "fetched_at": record["fetched_at"],
            "provider": record["provider"],
        },
    )
    assert delete_resp.status_code == 200
    delete_body = delete_resp.json()
    assert delete_body["deleted"] is True


def test_signals_endpoint_full_market_fields() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    resp = client.get(
        "/api/signals",
        params={
            "mode": "full_market",
            "as_of_date": as_of_date,
            "window_days": 60,
            "min_score": 40,
            "require_sequence": False,
            "min_event_count": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "full_market"
    assert body["as_of_date"] == as_of_date
    assert "generated_at" in body
    assert "cache_hit" in body
    assert "source_count" in body
    assert isinstance(body["items"], list)
    assert all(item["trigger_date"] <= as_of_date for item in body["items"])
    if body["items"]:
        first = body["items"][0]
        assert "wyckoff_phase" in first
        assert "wy_events" in first
        assert "entry_quality_score" in first
        assert "health_score" in first
        assert "volatility_stability" in first
        assert "event_score" in first
        assert first.get("event_grade") in {"A", "B", "C"}
        assert "candle_quality_score" in first
        assert "cost_center_shift_score" in first
        assert "weekly_context_score" in first
        assert "weekly_context_multiplier" in first
        assert "phase_context_score" in first
        assert "event_recency_score" in first
        assert "risk_score" in first
        assert first.get("confirmation_status") in {"confirmed", "partial", "unconfirmed", "risk_blocked"}
        assert "scan_mode" in first


def test_signals_endpoint_trend_pool_mode() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    run_payload = {
        "markets": ["sh", "sz"],
        "mode": "strict",
        "as_of_date": as_of_date,
        "return_window_days": 40,
        "top_n": 500,
        "turnover_threshold": 0.05,
        "amount_threshold": 500000000,
        "amplitude_threshold": 0.03,
    }
    run_resp = client.post("/api/screener/run", json=run_payload)
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]

    resp = client.get(
        "/api/signals",
        params={
            "mode": "trend_pool",
            "run_id": run_id,
            "min_score": 40,
            "min_event_count": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "trend_pool"
    assert body["as_of_date"] == as_of_date
    assert body["source_count"] >= 0
    assert isinstance(body["items"], list)
    assert all(item["trigger_date"] <= as_of_date for item in body["items"])


def test_strategy_catalog_endpoint() -> None:
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("items"), list)
    ids = {str(item.get("strategy_id")) for item in body["items"]}
    assert "wyckoff_trend_v1" in ids
    assert "wyckoff_trend_v2" in ids
    assert "relative_strength_breakout_v1" in ids
    assert "score_only_rank_v1" in ids
    assert "ths_main_force_flip_v1" in ids
    assert "ths_main_force_golden_cross_v1" in ids
    assert "ths_force_rhythm_v1" in ids
    assert "emotion_limit_up_v1" in ids
    assert "wulong_cluster_v1" in ids
    assert "wulong_cluster_v2" not in ids


def test_strategy_update_endpoint_supports_default_and_version_switch() -> None:
    registry = store._strategy_registry
    before_registry = dict(registry._strategies)
    before_resp = client.get("/api/strategies")
    assert before_resp.status_code == 200
    before_items = before_resp.json().get("items") or []
    before_map = {str(item.get("strategy_id")): item for item in before_items}
    assert "wyckoff_trend_v1" in before_map and "wyckoff_trend_v2" in before_map

    try:
        update_resp = client.patch(
            "/api/strategies/wyckoff_trend_v2",
            json={"is_default": True, "version": "2.0.0-test"},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["strategy_id"] == "wyckoff_trend_v2"
        assert updated["is_default"] is True
        assert updated["version"] == "2.0.0-test"

        after_resp = client.get("/api/strategies")
        assert after_resp.status_code == 200
        after_items = after_resp.json().get("items") or []
        after_map = {str(item.get("strategy_id")): item for item in after_items}
        assert after_map["wyckoff_trend_v2"]["is_default"] is True
        assert after_map["wyckoff_trend_v1"]["is_default"] is False
    finally:
        registry._strategies = dict(before_registry)


def test_strategy_update_endpoint_rejects_empty_payload() -> None:
    resp = client.patch("/api/strategies/wyckoff_trend_v1", json={})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "STRATEGY_UPDATE_EMPTY"


def test_event_judgment_profiles_endpoint_supports_crud() -> None:
    list_resp = client.get("/api/event-judgment/profiles")
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert isinstance(list_body.get("profiles"), list)
    assert isinstance(list_body.get("rule_options"), list)
    assert len(list_body.get("rule_options") or []) > 0
    first_rule = (list_body.get("rule_options") or [])[0]
    assert isinstance(first_rule, dict)
    assert "risk_hint_low" in first_rule
    assert "risk_hint_high" in first_rule
    assert any(
        str(item.get("profile_id")) == "system_legacy_formula_v1"
        for item in list_body.get("profiles", [])
    )

    create_resp = client.post(
        "/api/event-judgment/profiles",
        json={
            "name": "测试模板-四维",
            "description": "用于接口回归测试",
            "dimensions": [
                {
                    "dimension_id": "dim_bg",
                    "label": "背景分",
                    "metric_key": "event_background_score",
                    "weight": 0.4,
                    "invert": False,
                    "enabled": True,
                },
                {
                    "dimension_id": "dim_pos",
                    "label": "位置分",
                    "metric_key": "event_position_score",
                    "weight": 0.3,
                    "invert": False,
                    "enabled": True,
                },
                {
                    "dimension_id": "dim_vol",
                    "label": "量价分",
                    "metric_key": "event_vol_price_score",
                    "weight": 0.2,
                    "invert": False,
                    "enabled": True,
                },
                {
                    "dimension_id": "dim_confirm",
                    "label": "确认分",
                    "metric_key": "event_confirmation_score",
                    "weight": 0.1,
                    "invert": False,
                    "enabled": True,
                },
            ],
            "rule_values": [
                {"rule_key": "sos_ret10_min", "value": 0.08},
                {"rule_key": "enable_sow", "value": False},
            ],
            "make_active": True,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    created_profile_id = str(created.get("profile_id") or "").strip()
    assert created_profile_id
    assert created["is_system"] is False
    assert created["score_mode"] == "dimension_weighted"
    assert isinstance(created.get("rule_values"), list)
    assert len(created.get("rule_values") or []) > 0
    created_rule_map = {
        str(item.get("rule_key")): item.get("value")
        for item in created.get("rule_values", [])
        if isinstance(item, dict)
    }
    assert float(created_rule_map.get("sos_ret10_min")) == pytest.approx(0.08)
    assert bool(created_rule_map.get("enable_sow")) is False

    try:
        apply_resp = client.post(
            "/api/event-judgment/profiles/apply",
            json={"profile_id": created_profile_id},
        )
        assert apply_resp.status_code == 200
        assert apply_resp.json().get("active_profile_id") == created_profile_id
    finally:
        delete_resp = client.delete(f"/api/event-judgment/profiles/{created_profile_id}")
        assert delete_resp.status_code == 200
        fallback_resp = client.get("/api/event-judgment/profiles")
        assert fallback_resp.status_code == 200
        assert fallback_resp.json().get("active_profile_id") != created_profile_id


def test_signals_cache_isolated_by_event_judgment_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_trade_date = dates[-1]
    trigger_date = dates[-3]
    row = store._build_row_from_candles("sz300750", as_of_date=as_of_trade_date)
    assert row is not None

    call_counter = {"count": 0}

    def fake_resolve_signal_candidates(
        *,
        mode: str,
        run_id: str | None,
        trend_step: str = "auto",
        as_of_date: str | None = None,
    ):
        _ = (mode, trend_step)
        return [row], None, run_id or "mock-run", as_of_date or as_of_trade_date

    def fake_calc_wyckoff_snapshot(
        candidate_row,
        window_days: int,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, object]:
        _ = (candidate_row, window_days, as_of_date)
        call_counter["count"] += 1
        return {
            "events": ["SC", "AR", "ST", "SOS"],
            "risk_events": [],
            "event_dates": {"SC": trigger_date, "AR": trigger_date, "ST": trigger_date, "SOS": trigger_date},
            "event_chain": [{"event": "SOS", "date": trigger_date, "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "phase": "吸筹D",
            "signal": "SOS",
            "trigger_date": trigger_date,
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_calc_wyckoff_snapshot)
    monkeypatch.setenv("TDX_TREND_SIGNALS_DISK_CACHE", "0")
    store._signals_cache.clear()

    base_params = {
        "mode": "trend_pool",
        "run_id": "mock-run",
        "as_of_date": as_of_trade_date,
        "window_days": "60",
        "min_score": "0",
        "require_sequence": "false",
        "min_event_count": "0",
    }

    apply_legacy_resp = client.post(
        "/api/event-judgment/profiles/apply",
        json={"profile_id": "system_legacy_formula_v1"},
    )
    assert apply_legacy_resp.status_code == 200

    resp1 = client.get("/api/signals", params=base_params)
    assert resp1.status_code == 200
    assert resp1.json().get("cache_hit") is False

    resp2 = client.get("/api/signals", params=base_params)
    assert resp2.status_code == 200
    assert resp2.json().get("cache_hit") is True

    create_resp = client.post(
        "/api/event-judgment/profiles",
        json={
            "name": "测试模板-缓存隔离",
            "dimensions": [
                {
                    "dimension_id": "dim_bg",
                    "label": "背景分",
                    "metric_key": "event_background_score",
                    "weight": 1.0,
                    "invert": False,
                    "enabled": True,
                },
            ],
            "make_active": True,
        },
    )
    assert create_resp.status_code == 200
    custom_profile_id = str(create_resp.json().get("profile_id") or "").strip()
    assert custom_profile_id

    try:
        resp3 = client.get("/api/signals", params=base_params)
        assert resp3.status_code == 200
        assert resp3.json().get("cache_hit") is False
        assert call_counter["count"] == 2
    finally:
        cleanup_apply = client.post(
            "/api/event-judgment/profiles/apply",
            json={"profile_id": "system_legacy_formula_v1"},
        )
        assert cleanup_apply.status_code == 200
        cleanup_delete = client.delete(f"/api/event-judgment/profiles/{custom_profile_id}")
        assert cleanup_delete.status_code == 200
        store._signals_cache.clear()


def test_signals_endpoint_rejects_unknown_strategy() -> None:
    resp = client.get("/api/signals", params={"strategy_id": "unknown_strategy"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "STRATEGY_NOT_FOUND"


def test_signals_endpoint_accepts_strategy_params_json() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    resp = client.get(
        "/api/signals",
        params={
            "mode": "full_market",
            "as_of_date": as_of_date,
            "min_score": 40,
            "min_event_count": 0,
            "strategy_id": "wyckoff_trend_v2",
            "strategy_params": json.dumps(
                {
                    "matrix_event_semantic_version": "aligned_wyckoff_v2",
                    "health_score_min": 0.0,
                    "event_score_min": 0.0,
                },
                ensure_ascii=False,
            ),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == "wyckoff_trend_v2"
    assert isinstance(body.get("strategy_params_hash"), str)
    assert len(body["strategy_params_hash"]) == 12


def test_signals_endpoint_applies_strategy_default_params() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    resp = client.get(
        "/api/signals",
        params={
            "mode": "full_market",
            "as_of_date": as_of_date,
            "min_score": 0,
            "min_event_count": 0,
            "strategy_id": "wyckoff_trend_v2",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == "wyckoff_trend_v2"
    strategy_params = body.get("strategy_params") or {}
    assert strategy_params.get("matrix_event_semantic_version") == "aligned_wyckoff_v2"
    assert strategy_params.get("event_grade_min") == "B"
    assert strategy_params.get("health_score_min") == 55.0
    assert strategy_params.get("event_score_min") == 55.0
    assert strategy_params.get("require_key_event_confirmation") is True


def test_signals_endpoint_supports_relative_strength_strategy_defaults() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    resp = client.get(
        "/api/signals",
        params={
            "mode": "full_market",
            "as_of_date": as_of_date,
            "min_score": 0,
            "min_event_count": 0,
            "strategy_id": "relative_strength_breakout_v1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == "relative_strength_breakout_v1"
    strategy_params = body.get("strategy_params") or {}
    assert strategy_params.get("min_ret40") == 0.12
    assert strategy_params.get("max_retrace20") == 0.22
    assert strategy_params.get("min_up_down_volume_ratio") == 1.15
    assert strategy_params.get("rank_weight_strength") == 0.3
    assert strategy_params.get("require_key_event_confirmation") is False


def test_signals_endpoint_supports_ths_force_strategy_defaults() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    resp = client.get(
        "/api/signals",
        params={
            "mode": "full_market",
            "as_of_date": as_of_date,
            "min_score": 0,
            "min_event_count": 0,
            "strategy_id": "ths_main_force_flip_v1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == "ths_main_force_flip_v1"
    strategy_params = body.get("strategy_params") or {}
    assert strategy_params.get("min_score") == 0.0
    assert strategy_params.get("min_event_count") == 0
    assert strategy_params.get("event_grade_min") == "C"


def test_signals_endpoint_supports_wulong_strategy_defaults() -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    resp = client.get(
        "/api/signals",
        params={
            "mode": "full_market",
            "as_of_date": as_of_date,
            "min_score": 0,
            "min_event_count": 0,
            "strategy_id": "wulong_cluster_v1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == "wulong_cluster_v1"
    strategy_params = body.get("strategy_params") or {}
    assert strategy_params.get("min_ret40") == 0.06
    assert strategy_params.get("convergence_threshold_pct") == 0.04
    assert strategy_params.get("min_spread_expansion_multiple") == 1.6
    assert strategy_params.get("min_volume_ratio20") == 1.2
    assert strategy_params.get("min_score") == 52.0


def test_signals_endpoint_supports_signal_age_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    as_of_trade_date = dates[-1]
    trigger_date = dates[-3]
    expected_age_days = 2
    row = store._build_row_from_candles("sz300750", as_of_date=as_of_trade_date)
    assert row is not None

    def fake_resolve_signal_candidates(
        *,
        mode: str,
        run_id: str | None,
        trend_step: str = "auto",
        as_of_date: str | None = None,
    ):
        _ = (mode, trend_step)
        return [row], None, run_id or "mock-run", as_of_date or as_of_trade_date

    def fake_calc_wyckoff_snapshot(
        candidate_row,
        window_days: int,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, object]:
        _ = (candidate_row, window_days, as_of_date)
        return {
            "events": ["SC", "AR", "ST", "SOS"],
            "risk_events": [],
            "event_dates": {"SC": trigger_date, "AR": trigger_date, "ST": trigger_date, "SOS": trigger_date},
            "event_chain": [{"event": "SOS", "date": trigger_date, "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "phase": "吸筹D",
            "signal": "SOS",
            "trigger_date": trigger_date,
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_calc_wyckoff_snapshot)
    store._signals_cache.clear()

    base_params = {
        "mode": "trend_pool",
        "run_id": "mock-run",
        "as_of_date": as_of_trade_date,
        "refresh": "true",
        "window_days": "60",
        "min_score": "0",
        "require_sequence": "false",
        "min_event_count": "0",
    }
    resp_kept = client.get(
        "/api/signals",
        params={
            **base_params,
            "signal_age_min": str(expected_age_days),
            "signal_age_max": str(expected_age_days),
        },
    )
    assert resp_kept.status_code == 200
    body_kept = resp_kept.json()
    assert len(body_kept["items"]) == 1
    assert int(body_kept["items"][0]["signal_age_days"]) == expected_age_days

    resp_filtered = client.get(
        "/api/signals",
        params={
            **base_params,
            "signal_age_min": str(expected_age_days + 1),
        },
    )
    assert resp_filtered.status_code == 200
    body_filtered = resp_filtered.json()
    assert body_filtered["items"] == []


def test_signals_endpoint_ignores_signal_age_filter_when_capability_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    registry = store._strategy_registry
    original = registry._strategies["wyckoff_trend_v1"]
    downgraded = replace(
        original,
        capabilities=replace(
            original.capabilities,
            supports_signal_age_filter=False,
        ),
    )
    monkeypatch.setitem(registry._strategies, "wyckoff_trend_v1", downgraded)
    store._signals_cache.clear()

    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    base_params = {
        "mode": "full_market",
        "as_of_date": as_of_date,
        "refresh": "true",
        "window_days": "60",
        "min_score": "0",
        "min_event_count": "0",
        "strategy_id": "wyckoff_trend_v1",
    }
    resp_base = client.get("/api/signals", params=base_params)
    assert resp_base.status_code == 200
    body_base = resp_base.json()
    base_count = len(body_base["items"])
    assert base_count > 0

    resp_filtered = client.get(
        "/api/signals",
        params={
            **base_params,
            "signal_age_min": "240",
            "signal_age_max": "240",
        },
    )
    assert resp_filtered.status_code == 200
    body_filtered = resp_filtered.json()
    assert len(body_filtered["items"]) == base_count
    notes = body_filtered.get("notes") or []
    assert any("不支持 signal_age 过滤" in str(note) for note in notes)


def test_signals_disk_cache_reuses_persisted_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TDX_TREND_SIGNALS_DISK_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_SIGNALS_DISK_CACHE_TTL_SEC", "3600")
    monkeypatch.setenv("TDX_TREND_SIGNALS_CACHE_DIR", str(tmp_path / "signals-cache"))

    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    run_payload = {
        "markets": ["sh", "sz"],
        "mode": "strict",
        "as_of_date": as_of_date,
        "return_window_days": 40,
        "top_n": 500,
        "turnover_threshold": 0.05,
        "amount_threshold": 500000000,
        "amplitude_threshold": 0.03,
    }
    run_resp = client.post("/api/screener/run", json=run_payload)
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]

    call_counter = {"count": 0}

    def _fake_snapshot(
        row,
        window_days: int,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, object]:
        _ = (row, window_days)
        call_counter["count"] += 1
        day = str(as_of_date or "2025-01-02")
        return {
            "events": ["SOS"],
            "risk_events": [],
            "event_dates": {"SOS": day},
            "event_chain": [{"event": "SOS", "date": day, "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 88.0,
            "trigger_date": day,
            "signal": "SOS",
            "phase": "吸筹D",
            "phase_hint": "test",
            "structure_hhh": "HH|HL|HQ",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 75.0,
            "volatility_score": 62.0,
        }

    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", _fake_snapshot)
    store._signals_cache = {}

    params = {
        "mode": "trend_pool",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "window_days": 60,
        "min_score": 0,
        "min_event_count": 0,
    }
    resp1 = client.get("/api/signals", params=params)
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["cache_hit"] is False
    assert call_counter["count"] > 0

    store._signals_cache = {}
    call_counter["count"] = 0
    resp2 = client.get("/api/signals", params=params)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["cache_hit"] is True
    assert call_counter["count"] == 0


def test_wyckoff_event_store_lazy_fill_and_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    original_calc = store.__class__._calc_wyckoff_snapshot.__get__(store, store.__class__)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", original_calc)
    monkeypatch.setattr(store._wyckoff_event_store, "_enabled", True)
    monkeypatch.setattr(store._wyckoff_event_store, "_read_only", False)
    # 用独立临时 db 文件,彻底隔离前面测试写入的残留数据(避免 sqlite WAL/连接可见性问题)
    import tempfile
    _tmp_dir = tempfile.mkdtemp(prefix="wyckoff_test_")
    db_path = Path(_tmp_dir) / "wyckoff_events.sqlite"
    monkeypatch.setattr(store._wyckoff_event_store, "_db_path", db_path)
    store._wyckoff_event_store._init_db()
    # 清空 runtime_cache 和 signals_cache,确保 lazy fill 重新计算并写入
    runtime_cache = getattr(store._wyckoff_event_store, "_runtime_cache", None)
    if isinstance(runtime_cache, dict):
        runtime_cache.clear()

    dates = _load_symbol_dates("sz300750")
    as_of_date = dates[-8]
    row_a = store._build_row_from_candles("sh600519", as_of_date=as_of_date)
    row_b = store._build_row_from_candles("sz300750", as_of_date=as_of_date)
    assert row_a is not None
    assert row_b is not None

    def fake_resolve_signal_candidates(
        *,
        mode: str,
        run_id: str | None,
        trend_step: str = "auto",
        as_of_date: str | None = None,
    ):
        return [row_a, row_b], None, run_id, as_of_date or dates[-8]

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    store._signals_cache.clear()

    query = {
        "mode": "full_market",
        "as_of_date": as_of_date,
        "refresh": "true",
        "window_days": 60,
        "min_score": 0,
        "min_event_count": 0,
    }
    first_resp = client.get("/api/signals", params=query)
    assert first_resp.status_code == 200
    assert db_path.exists()


    first_row = None
    deadline = time.time() + 8.0
    while first_row is None and time.time() < deadline:
        with sqlite3.connect(str(db_path)) as conn:
            first_row = conn.execute(
                """
                SELECT symbol, trade_date, window_days, created_at, updated_at
                FROM wyckoff_daily_events
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if first_row is None:
            time.sleep(0.1)
    assert first_row is not None
    symbol, trade_date, window_days, created_at, updated_at = first_row
    assert symbol
    assert trade_date
    assert int(window_days) == 60

    # Ensure rewritten rows would produce a different second-level timestamp.
    time.sleep(1.2)
    second_resp = client.get("/api/signals", params=query)
    assert second_resp.status_code == 200

    with sqlite3.connect(str(db_path)) as conn:
        second_row = conn.execute(
            """
            SELECT created_at, updated_at
            FROM wyckoff_daily_events
            WHERE symbol=? AND trade_date=? AND window_days=?
            LIMIT 1
            """,
            (symbol, trade_date, window_days),
        ).fetchone()
    assert second_row is not None
    assert second_row[0] == created_at
    assert second_row[1] == updated_at


def test_signals_endpoint_board_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    row_main = store._build_row_from_candles("sh600519")
    row_gem = store._build_row_from_candles("sz300750")
    assert row_main is not None
    assert row_gem is not None

    def fake_resolve_signal_candidates(
        *,
        mode: str,
        run_id: str | None,
        trend_step: str = "auto",
        as_of_date: str | None = None,
    ):
        return [row_main, row_gem], None, run_id or "mock-run", as_of_date or "2026-02-10"

    def fake_calc_wyckoff_snapshot(
        row, window_days: int, *, as_of_date: str | None = None
    ) -> dict[str, object]:
        trigger_date = as_of_date or "2026-02-10"
        return {
            "events": ["SC", "AR", "ST", "SOS"],
            "risk_events": [],
            "event_dates": {"SC": trigger_date, "AR": trigger_date, "ST": trigger_date, "SOS": trigger_date},
            "event_chain": [],
            "sequence_ok": True,
            "entry_quality_score": 80.0,
            "phase": "吸筹D",
            "signal": "SOS",
            "trigger_date": trigger_date,
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_calc_wyckoff_snapshot)
    store._signals_cache.clear()

    base_params = [
        ("mode", "trend_pool"),
        ("run_id", "mock-run"),
        ("refresh", "true"),
        ("window_days", "60"),
        ("min_score", "0"),
        ("require_sequence", "false"),
        ("min_event_count", "0"),
    ]

    resp_all = client.get("/api/signals", params=base_params)
    assert resp_all.status_code == 200
    body_all = resp_all.json()
    assert body_all["source_count"] == 2
    assert {item["symbol"] for item in body_all["items"]} == {"sh600519", "sz300750"}

    resp_main = client.get("/api/signals", params=base_params + [("board_filters", "main")])
    assert resp_main.status_code == 200
    body_main = resp_main.json()
    assert body_main["source_count"] == 1
    assert {item["symbol"] for item in body_main["items"]} == {"sh600519"}


def test_signals_endpoint_replay_position_uses_backtest_seed_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    row_gem = store._build_row_from_candles("sz300497")
    row_main = store._build_row_from_candles("sh600519")
    assert row_gem is not None
    assert row_main is not None

    def fake_resolve_signal_candidates(
        *,
        mode: str,
        run_id: str | None,
        trend_step: str = "auto",
        as_of_date: str | None = None,
    ):
        _ = (mode, trend_step)
        return [], None, run_id or "mock-run", as_of_date or "2026-01-20"

    def fake_calc_wyckoff_snapshot(
        row: ScreenerResult,
        window_days: int,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, object]:
        _ = (row, window_days)
        trigger_date = as_of_date or "2026-01-20"
        return {
            "events": ["SC", "AR", "SOS"],
            "risk_events": [],
            "event_dates": {"SC": trigger_date, "AR": trigger_date, "SOS": trigger_date},
            "event_chain": [],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "phase": "吸筹D",
            "signal": "SOS",
            "trigger_date": trigger_date,
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 75.0,
            "phase_score": 74.0,
            "structure_score": 70.0,
            "trend_score": 68.0,
            "volatility_score": 66.0,
        }

    fake_run = SimpleNamespace(run_id="mock-run", params=SimpleNamespace(), step_configs=object())

    def fake_require_backtest_run(requested_run_id: str | None):
        _ = requested_run_id
        return fake_run

    def fake_resolve_step_configs(params):  # noqa: ANN001
        _ = params
        return object()

    def fake_bind_step_configs(params, step_configs):  # noqa: ANN001
        _ = (params, step_configs)
        return SimpleNamespace(markets=["sh", "sz"], return_window_days=40, mode="strict")

    def fake_load_input_pool_rows(*, markets, return_window_days, as_of_date):  # noqa: ANN001
        _ = (markets, return_window_days, as_of_date)
        return [row_gem, row_main], None, True

    def fake_run_filters(rows: list[ScreenerResult], *, mode: str, step_configs):  # noqa: ANN001
        _ = (mode, step_configs)
        return list(rows), [], [], []

    def fake_select_step_source(*, trend_step, step1_pool, step2_pool, step3_pool, step4_pool):  # noqa: ANN001
        _ = (trend_step, step2_pool, step3_pool, step4_pool)
        return list(step1_pool)

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_calc_wyckoff_snapshot)
    monkeypatch.setattr(store, "_require_backtest_trend_pool_run", fake_require_backtest_run)
    monkeypatch.setattr(store, "_resolve_screener_step_configs", fake_resolve_step_configs)
    monkeypatch.setattr(store, "_bind_step_configs_to_screener_params", fake_bind_step_configs)
    monkeypatch.setattr(store, "_load_input_pool_rows", fake_load_input_pool_rows)
    monkeypatch.setattr(store, "_run_screener_filters_for_backtest", fake_run_filters)
    monkeypatch.setattr(store, "_select_step_source_for_backtest", fake_select_step_source)
    store._signals_cache.clear()

    resp = client.get(
        "/api/signals",
        params=[
            ("mode", "trend_pool"),
            ("run_id", "mock-run"),
            ("trend_step", "step1"),
            ("as_of_date", "2026-01-20"),
            ("window_days", "70"),
            ("min_score", "0"),
            ("require_sequence", "false"),
            ("min_event_count", "0"),
            ("refresh", "true"),
            ("board_filters", "gem"),
            ("backtest_date_from", "2026-01-01"),
            ("backtest_pool_roll_mode", "position"),
            ("backtest_max_symbols", "200"),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_count"] == 1
    assert {item["symbol"] for item in body["items"]} == {"sz300497"}
    assert any("回测复盘口径候选池" in str(note) for note in (body.get("notes") or []))


def test_signals_endpoint_market_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    row_sh = store._build_row_from_candles("sh600519")
    row_sz = store._build_row_from_candles("sz300750")
    assert row_sh is not None
    assert row_sz is not None

    def fake_resolve_signal_candidates(
        *,
        mode: str,
        run_id: str | None,
        trend_step: str = "auto",
        as_of_date: str | None = None,
    ):
        return [row_sh, row_sz], None, run_id, as_of_date or "2026-02-10"

    def fake_calc_wyckoff_snapshot(
        row, window_days: int, *, as_of_date: str | None = None
    ) -> dict[str, object]:
        trigger_date = as_of_date or "2026-02-10"
        return {
            "events": ["SC", "AR", "ST", "SOS"],
            "risk_events": [],
            "event_dates": {"SC": trigger_date, "AR": trigger_date, "ST": trigger_date, "SOS": trigger_date},
            "event_chain": [],
            "sequence_ok": True,
            "entry_quality_score": 80.0,
            "phase": "吸筹D",
            "signal": "SOS",
            "trigger_date": trigger_date,
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_calc_wyckoff_snapshot)
    store._signals_cache.clear()

    base_params = [
        ("mode", "full_market"),
        ("refresh", "true"),
        ("window_days", "60"),
        ("min_score", "0"),
        ("require_sequence", "false"),
        ("min_event_count", "0"),
    ]

    resp_all = client.get("/api/signals", params=base_params)
    assert resp_all.status_code == 200
    body_all = resp_all.json()
    assert body_all["source_count"] == 2
    assert {item["symbol"] for item in body_all["items"]} == {"sh600519", "sz300750"}

    resp_sh = client.get("/api/signals", params=base_params + [("market_filters", "sh")])
    assert resp_sh.status_code == 200
    body_sh = resp_sh.json()
    assert body_sh["source_count"] == 1
    assert {item["symbol"] for item in body_sh["items"]} == {"sh600519"}


def test_sim_order_buy_pending_then_settle_filled() -> None:
    dates = _load_symbol_dates("sz300750")
    submit_date = dates[-6]

    order_resp = client.post(
        "/api/sim/orders",
        json={
            "symbol": "sz300750",
            "side": "buy",
            "quantity": 100,
            "signal_date": submit_date,
            "submit_date": submit_date,
        },
    )
    assert order_resp.status_code == 200
    order = order_resp.json()["order"]
    assert order["status"] == "pending"

    settle_resp = client.post("/api/sim/settle")
    assert settle_resp.status_code == 200
    settle_body = settle_resp.json()
    assert settle_body["settled_count"] >= 1
    assert settle_body["filled_count"] >= 1

    orders = client.get("/api/sim/orders", params={"status": "filled"}).json()["items"]
    assert any(item["order_id"] == order["order_id"] for item in orders)

    fills = client.get("/api/sim/fills", params={"symbol": "sz300750"}).json()["items"]
    assert any(item["order_id"] == order["order_id"] for item in fills)


def test_sim_no_next_day_fallback_close() -> None:
    dates = _load_symbol_dates("sz300750")
    last_date = dates[-1]

    order = _post_buy("sz300750", last_date, quantity=100)
    assert order["status"] == "pending"

    settle_resp = client.post("/api/sim/settle")
    assert settle_resp.status_code == 200

    filled_orders = client.get("/api/sim/orders", params={"status": "filled"}).json()["items"]
    target = next(item for item in filled_orders if item["order_id"] == order["order_id"])
    assert target["status_reason"] == "NO_NEXT_DAY_FALLBACK_CLOSE"

    fills = client.get("/api/sim/fills", params={"symbol": "sz300750"}).json()["items"]
    fill = next(item for item in fills if item["order_id"] == order["order_id"])
    assert fill["warning"] == "NO_NEXT_DAY_FALLBACK_CLOSE"


def test_sim_sell_fifo_and_partial_lot_consumption() -> None:
    symbol = "sh601899"
    dates = _load_symbol_dates(symbol)
    buy_date_1 = dates[-8]
    buy_date_2 = dates[-7]
    sell_date = dates[-5]

    _post_buy(symbol, buy_date_1, quantity=100)
    _post_buy(symbol, buy_date_2, quantity=100)
    settle_resp = client.post("/api/sim/settle")
    assert settle_resp.status_code == 200

    sell_order = _post_sell(symbol, sell_date, quantity=100)
    assert sell_order["status"] == "pending"
    settle_resp2 = client.post("/api/sim/settle")
    assert settle_resp2.status_code == 200

    portfolio = client.get("/api/sim/portfolio").json()
    pos = next((item for item in portfolio["positions"] if item["symbol"] == symbol), None)
    assert pos is not None
    assert pos["quantity"] == 100

    review = client.get(
        "/api/review/stats",
        params={"date_from": dates[-15], "date_to": dates[-1], "date_axis": "sell"},
    ).json()
    assert review["trades"]
    filled_buys = client.get(
        "/api/sim/orders",
        params={"status": "filled", "side": "buy", "symbol": symbol},
    ).json()["items"]
    assert filled_buys
    earliest_buy_fill_date = min(item["filled_date"] for item in filled_buys if item.get("filled_date"))
    assert any(trade["buy_date"] == earliest_buy_fill_date for trade in review["trades"])


def test_sim_t_plus_one_buy_same_day_not_sellable() -> None:
    symbol = "sz300750"
    dates = _load_symbol_dates(symbol)
    buy_date = dates[-6]

    _post_buy(symbol, buy_date, quantity=100)
    client.post("/api/sim/settle")

    sell_same_day = client.post(
        "/api/sim/orders",
        json={
            "symbol": symbol,
            "side": "sell",
            "quantity": 100,
            "signal_date": buy_date,
            "submit_date": buy_date,
        },
    )
    assert sell_same_day.status_code == 400
    body = sell_same_day.json()
    assert body["code"] == "SIM_INSUFFICIENT_POSITION"


def test_sim_cancel_pending_and_filled_not_cancelable() -> None:
    symbol = "sz300750"
    dates = _load_symbol_dates(symbol)
    submit_date = dates[-6]

    pending = _post_buy(symbol, submit_date, quantity=100)
    cancel_resp = client.post(f"/api/sim/orders/{pending['order_id']}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["order"]["status"] == "cancelled"

    second = _post_buy(symbol, submit_date, quantity=100)
    client.post("/api/sim/settle")
    cancel_filled = client.post(f"/api/sim/orders/{second['order_id']}/cancel")
    assert cancel_filled.status_code == 400
    assert cancel_filled.json()["code"] == "SIM_ORDER_NOT_CANCELABLE"


def test_sim_reset_account_clears_positions_orders_and_fills() -> None:
    symbol = "sh601899"
    dates = _load_symbol_dates(symbol)
    _post_buy(symbol, dates[-6], quantity=100)
    client.post("/api/sim/settle")

    reset_resp = client.post("/api/sim/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["success"] is True

    orders = client.get("/api/sim/orders").json()
    fills = client.get("/api/sim/fills").json()
    portfolio = client.get("/api/sim/portfolio").json()
    config = client.get("/api/sim/config").json()

    assert orders["total"] == 0
    assert fills["total"] == 0
    assert portfolio["positions"] == []
    assert abs(portfolio["cash"] - config["initial_capital"]) < 1e-6


def test_sim_cost_calculation_min_commission_and_sell_stamp_tax() -> None:
    symbol = "sh601899"
    dates = _load_symbol_dates(symbol)
    buy_date = dates[-7]
    sell_date = dates[-5]

    buy = _post_buy(symbol, buy_date, quantity=100)
    assert buy["status"] == "pending"
    client.post("/api/sim/settle")

    buy_fill = next(
        item
        for item in client.get("/api/sim/fills", params={"side": "buy"}).json()["items"]
        if item["order_id"] == buy["order_id"]
    )
    assert buy_fill["fee_commission"] >= 5
    assert buy_fill["fee_stamp_tax"] == 0

    sell = _post_sell(symbol, sell_date, quantity=100)
    assert sell["status"] == "pending"
    client.post("/api/sim/settle")
    sell_fill = next(
        item
        for item in client.get("/api/sim/fills", params={"side": "sell"}).json()["items"]
        if item["order_id"] == sell["order_id"]
    )
    assert sell_fill["fee_stamp_tax"] > 0


def test_review_default_range_and_curve_fields() -> None:
    symbol = "sh601899"
    dates = _load_symbol_dates(symbol)
    _post_buy(symbol, dates[-8], quantity=100)
    client.post("/api/sim/settle")
    _post_sell(symbol, dates[-5], quantity=100)
    client.post("/api/sim/settle")

    resp = client.get("/api/review/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "range" in body
    assert body["range"]["date_axis"] == "sell"
    assert isinstance(body["equity_curve"], list)
    assert isinstance(body["drawdown_curve"], list)
    assert isinstance(body["monthly_returns"], list)
    assert "profit_factor" in body["stats"]


def test_review_supports_buy_date_axis() -> None:
    symbol = "sh601899"
    dates = _load_symbol_dates(symbol)
    _post_buy(symbol, dates[-8], quantity=100)
    client.post("/api/sim/settle")
    _post_sell(symbol, dates[-5], quantity=100)
    client.post("/api/sim/settle")

    filled_buys = client.get(
        "/api/sim/orders",
        params={"status": "filled", "side": "buy", "symbol": symbol},
    ).json()["items"]
    assert filled_buys
    buy_fill_date = min(item["filled_date"] for item in filled_buys if item.get("filled_date"))

    resp = client.get(
        "/api/review/stats",
        params={"date_from": buy_fill_date, "date_to": buy_fill_date, "date_axis": "buy"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_axis"] == "buy"
    assert body["stats"]["trade_count"] >= 1
    assert all(row["buy_date"] == buy_fill_date for row in body["trades"])


def test_daily_review_crud() -> None:
    date = "2026-02-17"
    client.delete(f"/api/review/daily/{date}")

    payload = {
        "title": "daily review test",
        "market_summary": "market was mixed and sentiment neutral",
        "operations_summary": "reduced high-beta positions",
        "reflection": "exit timing was slightly slow",
        "tomorrow_plan": "watch low-risk pullback entries",
        "summary": "capital protection first",
        "tags": ["discipline", "risk-control", "discipline"],
    }
    put_resp = client.put(f"/api/review/daily/{date}", json=payload)
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["date"] == date
    assert body["title"] == payload["title"]
    assert body["tags"] == ["discipline", "risk-control"]

    get_resp = client.get(f"/api/review/daily/{date}")
    assert get_resp.status_code == 200
    assert get_resp.json()["date"] == date

    list_resp = client.get("/api/review/daily", params={"date_from": "2026-02-01", "date_to": "2026-02-28"})
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert any(item["date"] == date for item in items)

    delete_resp = client.delete(f"/api/review/daily/{date}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    not_found_resp = client.get(f"/api/review/daily/{date}")
    assert not_found_resp.status_code == 404


def test_weekly_review_crud() -> None:
    week_label = "2026-W08"
    client.delete(f"/api/review/weekly/{week_label}")

    payload = {
        "start_date": "",
        "end_date": "",
        "core_goals": "focus on main trend and avoid noise",
        "achievements": "execution quality improved",
        "resource_analysis": "position concentration improved",
        "market_rhythm": "uptrend with periodic rotations",
        "next_week_strategy": "defensive and wait for pullbacks",
        "key_insight": "survival before return maximization",
        "tags": ["main-trend", "risk-control", "main-trend"],
    }
    put_resp = client.put(f"/api/review/weekly/{week_label}", json=payload)
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["week_label"] == week_label
    assert body["start_date"]
    assert body["end_date"]
    assert body["tags"] == ["main-trend", "risk-control"]

    get_resp = client.get(f"/api/review/weekly/{week_label}")
    assert get_resp.status_code == 200
    assert get_resp.json()["week_label"] == week_label

    list_resp = client.get("/api/review/weekly", params={"year": 2026})
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert any(item["week_label"] == week_label for item in items)

    delete_resp = client.delete(f"/api/review/weekly/{week_label}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    not_found_resp = client.get(f"/api/review/weekly/{week_label}")
    assert not_found_resp.status_code == 404


def test_review_tags_fill_assignment_and_stats() -> None:
    symbol = "sz300750"
    dates = _load_symbol_dates(symbol)
    order = _post_buy(symbol, dates[-6], quantity=100)
    client.post("/api/sim/settle")

    fills_resp = client.get("/api/sim/fills", params={"symbol": symbol})
    assert fills_resp.status_code == 200
    fills = fills_resp.json()["items"]
    fill = next(item for item in fills if item["order_id"] == order["order_id"])

    emotion_resp = client.post("/api/review/tags/emotion", json={"name": f"鎯呯华-{order['order_id'][-4:]}"})
    reason_resp = client.post("/api/review/tags/reason", json={"name": f"鍘熷洜-{order['order_id'][-4:]}"})
    assert emotion_resp.status_code == 200
    assert reason_resp.status_code == 200
    emotion_tag = emotion_resp.json()
    reason_tag = reason_resp.json()

    put_tag_resp = client.put(
        f"/api/review/fill-tags/{fill['order_id']}",
        json={
            "emotion_tag_id": emotion_tag["id"],
            "reason_tag_ids": [reason_tag["id"]],
        },
    )
    assert put_tag_resp.status_code == 200
    tagged = put_tag_resp.json()
    assert tagged["order_id"] == fill["order_id"]
    assert tagged["emotion_tag_id"] == emotion_tag["id"]
    assert reason_tag["id"] in tagged["reason_tag_ids"]

    get_tag_resp = client.get(f"/api/review/fill-tags/{fill['order_id']}")
    assert get_tag_resp.status_code == 200
    assert get_tag_resp.json()["order_id"] == fill["order_id"]

    stats_resp = client.get(
        "/api/review/tag-stats",
        params={"date_from": dates[-10], "date_to": dates[-1]},
    )
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    emotion_rows = stats["emotion"]
    reason_rows = stats["reason"]
    assert any(row["tag_id"] == emotion_tag["id"] and row["count"] >= 1 for row in emotion_rows)
    assert any(row["tag_id"] == reason_tag["id"] and row["count"] >= 1 for row in reason_rows)


def test_market_news_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_market_news(
        *,
        query: str = "",
        symbol: str | None = None,
        source_domains: list[str] | None = None,
        age_hours: int = 72,
        refresh: bool = False,
        limit: int = 20,
    ) -> dict[str, object]:
        assert query == "robotics"
        assert symbol == "sz300750"
        assert source_domains == ["finance.eastmoney.com", "cls.cn"]
        assert age_hours == 24
        assert refresh is False
        assert limit == 5
        return {
            "query": query,
            "age_hours": age_hours,
            "symbol": symbol,
            "symbol_name": "Ningde Times",
            "source_domains": source_domains or [],
            "items": [
                {
                    "title": "Robotics sector moves higher with rising volume",
                    "url": "https://example.com/news/robot",
                    "snippet": "Institutions expect improving upstream orders.",
                    "pub_date": "2026-02-18 10:00:00",
                    "source_name": "MockNews",
                }
            ],
            "fetched_at": "2026-02-18 10:00:01",
            "cache_hit": False,
            "fallback_used": False,
            "degraded": False,
            "degraded_reason": None,
        }

    monkeypatch.setattr(store, "get_market_news", fake_get_market_news)
    resp = client.get(
        "/api/market/news",
        params={
            "query": "robotics",
            "symbol": "sz300750",
            "source_domains": "finance.eastmoney.com,cls.cn",
            "age_hours": 24,
            "limit": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "robotics"
    assert body["age_hours"] == 24
    assert body["symbol"] == "sz300750"
    assert body["symbol_name"] == "Ningde Times"
    assert body["source_domains"] == ["finance.eastmoney.com", "cls.cn"]
    assert body["cache_hit"] is False
    assert body["fallback_used"] is False
    assert body["degraded"] is False
    assert len(body["items"]) == 1
