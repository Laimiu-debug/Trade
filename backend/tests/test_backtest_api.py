from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEST_STATE_ROOT = Path(tempfile.mkdtemp(prefix="backtest-api-state-"))
os.environ.setdefault("TDX_TREND_APP_STATE_PATH", str(TEST_STATE_ROOT / "app_state.json"))
os.environ.setdefault("TDX_TREND_SIM_STATE_PATH", str(TEST_STATE_ROOT / "sim_state.json"))
os.environ.setdefault("TDX_TREND_BACKTEST_TASK_STATE_PATH", str(TEST_STATE_ROOT / "backtest_tasks.json"))
os.environ.setdefault("TDX_TREND_BACKTEST_PLATEAU_TASK_STATE_PATH", str(TEST_STATE_ROOT / "backtest_plateau_tasks.json"))
os.environ.setdefault("TDX_TREND_BACKTEST_PLATEAU_DETAIL_STORE_DIR", str(TEST_STATE_ROOT / "backtest_plateau_details"))
os.environ.setdefault("TDX_TREND_WYCKOFF_STORE_PATH", str(TEST_STATE_ROOT / "wyckoff_events.sqlite"))
os.environ.setdefault("TDX_TREND_WYCKOFF_STORE_ENABLED", "1")
os.environ.setdefault("TDX_TREND_WYCKOFF_STORE_READ_ONLY", "0")

from app.main import app
import app.store as store_module
from app.core.backtest_engine import CandidateTrade
from app.models import (
    BacktestPlateauParams,
    BacktestPlateauPoint,
    BacktestPlateauResponse,
    BacktestResponse,
    BacktestRiskMetrics,
    BacktestRunRequest,
    BacktestTrade,
    ReviewRange,
    ReviewStats,
    ScreenerParams,
    ScreenerResult,
)
from app.store import BacktestValidationError, store

client = TestClient(app)


def _wait_backtest_task(task_id: str, timeout_sec: float = 180.0) -> dict:
    deadline = time.time() + timeout_sec
    last_payload: dict | None = None
    while time.time() < deadline:
        resp = client.get(f"/api/backtest/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        last_payload = body
        if body.get("status") in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task timeout: task_id={task_id}, last={last_payload}")


def _wait_backtest_task_status(task_id: str, expected: set[str], timeout_sec: float = 30.0) -> dict:
    deadline = time.time() + timeout_sec
    last_payload: dict | None = None
    while time.time() < deadline:
        resp = client.get(f"/api/backtest/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        last_payload = body
        if body.get("status") in expected:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task status wait timeout: task_id={task_id}, expected={expected}, last={last_payload}")


def _build_custom_event_profile(profile_id: str, *, name: str, tweak: float = 1.0) -> dict[str, object]:
    default_rules = store._default_event_judgment_rule_values()
    custom_rules = [dict(item) for item in default_rules]
    if custom_rules:
        custom_rules[0]["value"] = round(float(custom_rules[0].get("value", 0.0) or 0.0) * tweak, 6)
    return store._normalize_event_judgment_profile(
        {
            "profile_id": profile_id,
            "name": name,
            "score_mode": "legacy_formula",
            "rule_values": custom_rules,
        },
        is_system=False,
        fallback_profile_id=profile_id,
        fallback_name=name,
        fallback_updated_at=store._now_datetime(),
        fallback_rule_values=default_rules,
    )


def _wait_backtest_plateau_task(task_id: str, timeout_sec: float = 180.0) -> dict:
    deadline = time.time() + timeout_sec
    last_payload: dict | None = None
    while time.time() < deadline:
        resp = client.get(f"/api/backtest/plateau/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        last_payload = body
        if body.get("status") in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task timeout: task_id={task_id}, last={last_payload}")


def _wait_backtest_plateau_task_status(task_id: str, expected: set[str], timeout_sec: float = 30.0) -> dict:
    deadline = time.time() + timeout_sec
    last_payload: dict | None = None
    while time.time() < deadline:
        resp = client.get(f"/api/backtest/plateau/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        last_payload = body
        if body.get("status") in expected:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task status wait timeout: task_id={task_id}, expected={expected}, last={last_payload}")


def _load_symbol_dates(symbol: str) -> list[str]:
    resp = client.get(f"/api/stocks/{symbol}/candles")
    assert resp.status_code == 200
    candles = resp.json()["candles"]
    assert len(candles) >= 40
    return [item["time"] for item in candles]


def _create_trend_pool_run(as_of_date: str) -> str:
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
    return str(run_resp.json()["run_id"])


def _detect_board(symbol: str) -> str:
    text = str(symbol).strip().lower()
    if len(text) < 8:
        return "unknown"
    market = text[:2]
    code = text[2:]
    if market == "bj":
        return "beijing"
    if market == "sh":
        if code.startswith("688") or code.startswith("689"):
            return "star"
        return "main"
    if market == "sz":
        if code.startswith("300") or code.startswith("301"):
            return "gem"
        return "main"
    return "unknown"


def test_backtest_run_trend_pool_smoke() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-35]
    date_to = dates[-8]
    run_id = _create_trend_pool_run(date_to)

    payload = {
        "mode": "trend_pool",
        "run_id": run_id,
        "trend_step": "auto",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.15,
        "max_hold_days": 60,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 0,
        "enforce_t1": True,
        "max_symbols": 120,
        "pool_roll_mode": "weekly",
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert body["candidate_count"] >= body["stats"]["trade_count"]
    assert 0.0 <= body["fill_rate"] <= 1.0
    assert isinstance(body["notes"], list)
    assert isinstance(body["trades"], list)
    assert isinstance(body["equity_curve"], list)
    assert isinstance(body.get("risk_metrics"), dict)
    assert isinstance(body.get("stability_diagnostics"), dict)
    assert isinstance(body.get("regime_breakdown"), list)
    assert isinstance(body.get("monte_carlo"), dict)
    assert "walk_forward" in body
    if body["trades"]:
        first = body["trades"][0]
        assert "candle_quality_score" in first
        assert "cost_center_shift_score" in first
        assert "weekly_context_score" in first
        assert "weekly_context_multiplier" in first


def test_backtest_plateau_endpoint_handles_truncation_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (progress_callback, control_callback)
        if float(payload.min_score) >= 58.0:
            raise ValueError("mock failure for plateau")
        total_return = float(payload.min_score) / 100.0
        win_rate = 0.3 if int(payload.window_days) <= 40 else 0.62
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=win_rate,
                total_return=total_return,
                max_drawdown=-0.12,
                avg_pnl_ratio=0.03,
                trade_count=12,
                win_count=7,
                loss_count=5,
                profit_factor=1.4,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=120,
            skipped_count=8,
            fill_rate=0.75,
            max_concurrent_positions=int(payload.max_positions),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-03-31",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "grid",
        "window_days_list": [40, 60],
        "min_score_list": [55, 58],
        "max_points": 3,
    }
    resp = client.post("/api/backtest/plateau", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_combinations"] == 4
    assert body["evaluated_combinations"] == 3
    assert len(body["points"]) == 3
    assert body["best_point"] is not None
    assert body["best_point"]["params"]["window_days"] == 60
    assert body["recommended_point"] is not None
    assert "plateau_score" in body["recommended_point"]
    assert "local_score" in body["recommended_point"]
    assert len(body["regions"]) >= 1
    assert body["regions"][0]["point_count"] >= 1
    assert body["regions"][0]["center_point"]["params"]["window_days"] in {40, 60}
    assert len(body["correlations"]) == 10
    assert any("参数组合总数" in note for note in body["notes"])
    assert any("参数评估失败" in note for note in body["notes"])
    assert any("稳定区域" in note for note in body["notes"])
    assert sum(1 for point in body["points"] if point.get("error")) >= 1


def test_backtest_plateau_endpoint_lhs_sampling_is_repeatable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (progress_callback, control_callback)
        score_raw = (
            float(payload.min_score) * 0.4
            + float(payload.take_profit) * 100.0
            - float(payload.stop_loss) * 120.0
            + float(payload.position_pct) * 20.0
        )
        total_return = round(score_raw / 100.0, 6)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.52,
                total_return=total_return,
                max_drawdown=-0.1,
                avg_pnl_ratio=0.025,
                trade_count=10,
                win_count=5,
                loss_count=5,
                profit_factor=1.2,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=80,
            skipped_count=6,
            fill_rate=0.7,
            max_concurrent_positions=int(payload.max_positions),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-02-28",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "lhs",
        "sample_points": 12,
        "random_seed": 20260221,
        "window_days_list": [40, 120],
        "min_score_list": [50, 70],
        "stop_loss_list": [0.03, 0.08],
        "take_profit_list": [0.1, 0.4],
        "max_positions_list": [3, 10],
        "position_pct_list": [0.1, 0.4],
        "max_symbols_list": [80, 300],
        "priority_topk_per_day_list": [0, 20],
    }

    resp1 = client.post("/api/backtest/plateau", json=payload)
    assert resp1.status_code == 200
    body1 = resp1.json()
    resp2 = client.post("/api/backtest/plateau", json=payload)
    assert resp2.status_code == 200
    body2 = resp2.json()

    assert body1["total_combinations"] == 12
    assert body1["evaluated_combinations"] == 12
    assert len(body1["points"]) == 12
    assert len(body1["correlations"]) == 10
    assert any(row["parameter"] == "min_score" for row in body1["correlations"])
    assert body1["points"] == body2["points"]
    assert body1["correlations"] == body2["correlations"]
    assert body1["recommended_point"] == body2["recommended_point"]
    assert body1["regions"] == body2["regions"]
    assert any("参数采样模式: lhs" in note for note in body1["notes"])
    assert any("LHS 随机种子: 20260221" in note for note in body1["notes"])
    assert any("稳定区域" in note for note in body1["notes"])


def test_backtest_plateau_endpoint_parallel_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_PLATEAU_WORKERS", "4")
    lock = threading.Lock()
    active_workers = 0
    max_active_workers = 0

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        nonlocal active_workers, max_active_workers
        _ = progress_callback
        if control_callback is not None:
            control_callback()
        with lock:
            active_workers += 1
            max_active_workers = max(max_active_workers, active_workers)
        try:
            time.sleep(0.03)
            return BacktestResponse(
                stats=ReviewStats(
                    win_rate=0.55,
                    total_return=float(payload.min_score) / 100.0,
                    max_drawdown=-0.1,
                    avg_pnl_ratio=0.03,
                    trade_count=10,
                    win_count=6,
                    loss_count=4,
                    profit_factor=1.3,
                ),
                trades=[],
                range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
                notes=[],
                candidate_count=40,
                skipped_count=3,
                fill_rate=0.75,
                max_concurrent_positions=int(payload.max_positions),
            )
        finally:
            with lock:
                active_workers -= 1

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-02-28",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "grid",
        "window_days_list": [40, 60],
        "min_score_list": [50, 60],
        "stop_loss_list": [0.03, 0.05],
        "take_profit_list": [0.2],
        "max_positions_list": [5],
        "position_pct_list": [0.2],
        "max_symbols_list": [100],
        "priority_topk_per_day_list": [10],
        "max_points": 8,
    }
    resp = client.post("/api/backtest/plateau", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["evaluated_combinations"] == 8
    assert max_active_workers >= 2


def test_backtest_plateau_task_supports_pause_resume_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = progress_callback
        if control_callback is not None:
            control_callback()
        time.sleep(0.03)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.5,
                total_return=0.1,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.02,
                trade_count=5,
                win_count=3,
                loss_count=2,
                profit_factor=1.2,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=12,
            skipped_count=2,
            fill_rate=0.8,
            max_concurrent_positions=int(payload.max_positions),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-02-28",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "lhs",
        "sample_points": 80,
        "random_seed": 20260221,
        "window_days_list": [40, 120],
        "min_score_list": [50, 70],
        "stop_loss_list": [0.03, 0.08],
        "take_profit_list": [0.1, 0.4],
        "max_positions_list": [3, 10],
        "position_pct_list": [0.1, 0.4],
        "max_symbols_list": [80, 300],
        "priority_topk_per_day_list": [0, 20],
    }

    start_resp = client.post("/api/backtest/plateau/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])
    assert task_id.startswith("bp_")

    running = _wait_backtest_plateau_task_status(task_id, {"running"})
    assert running["status"] == "running"

    pause_resp = client.post(f"/api/backtest/plateau/tasks/{task_id}/pause")
    assert pause_resp.status_code == 200
    paused = pause_resp.json()
    assert paused["status"] == "paused"

    paused_after = client.get(f"/api/backtest/plateau/tasks/{task_id}").json()
    assert paused_after["status"] == "paused"

    resume_resp = client.post(f"/api/backtest/plateau/tasks/{task_id}/resume")
    assert resume_resp.status_code == 200
    resumed = resume_resp.json()
    assert resumed["status"] in {"pending", "running"}

    cancel_resp = client.post(f"/api/backtest/plateau/tasks/{task_id}/cancel")
    assert cancel_resp.status_code == 200
    cancelled = cancel_resp.json()
    assert cancelled["status"] == "cancelled"

    final_task = _wait_backtest_plateau_task(task_id)
    assert final_task["status"] == "cancelled"


def test_backtest_plateau_task_delete_succeeded_task(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (progress_callback, prebuilt_universe)
        if control_callback is not None:
            control_callback()
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.5,
                total_return=0.1,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.02,
                trade_count=5,
                win_count=3,
                loss_count=2,
                profit_factor=1.2,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=12,
            skipped_count=2,
            fill_rate=0.8,
            max_concurrent_positions=int(payload.max_positions),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-02-28",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "lhs",
        "sample_points": 4,
        "random_seed": 20260227,
        "window_days_list": [40, 80],
        "min_score_list": [50, 70],
        "stop_loss_list": [0.03, 0.08],
        "take_profit_list": [0.1, 0.4],
        "trailing_stop_pct_list": [0.0, 0.08],
        "max_positions_list": [3, 10],
        "position_pct_list": [0.1, 0.4],
        "max_symbols_list": [80, 300],
        "priority_topk_per_day_list": [0, 20],
    }

    start_resp = client.post("/api/backtest/plateau/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    finished = _wait_backtest_plateau_task(task_id)
    assert finished["status"] == "succeeded"

    delete_resp = client.delete(f"/api/backtest/plateau/tasks/{task_id}")
    assert delete_resp.status_code == 200
    delete_body = delete_resp.json()
    assert delete_body["deleted"] is True
    assert delete_body["task_id"] == task_id

    get_resp = client.get(f"/api/backtest/plateau/tasks/{task_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["code"] == "BACKTEST_PLATEAU_TASK_NOT_FOUND"


def test_backtest_plateau_point_detail_endpoint_persists_and_cleans_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (progress_callback, prebuilt_universe)
        if control_callback is not None:
            control_callback()
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.6,
                total_return=float(payload.min_score) / 100.0,
                max_drawdown=-0.05,
                avg_pnl_ratio=0.04,
                trade_count=1,
                win_count=1,
                loss_count=0,
                profit_factor=2.0,
            ),
            trades=[
                BacktestTrade(
                    symbol="sz300750",
                    name="宁德时代",
                    signal_date=payload.date_from,
                    entry_date=payload.date_from,
                    exit_date=payload.date_to,
                    entry_signal="SOS",
                    entry_phase="吸筹D",
                    entry_quality_score=80.0,
                    exit_reason="take_profit",
                    quantity=100,
                    entry_price=10.0,
                    exit_price=11.0,
                    holding_days=5,
                    pnl_amount=100.0,
                    pnl_ratio=0.1,
                )
            ],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=["plateau detail persisted"],
            candidate_count=5,
            skipped_count=0,
            fill_rate=1.0,
            max_concurrent_positions=int(payload.max_positions),
            effective_run_request=payload.model_copy(deep=True),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "position",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-01-31",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS"],
        "exit_events": ["UTAD", "SOW"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.15,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 0,
        "enforce_t1": True,
        "max_symbols": 80,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "grid",
        "window_days_list": [40],
        "min_score_list": [52],
        "stop_loss_list": [0.03],
        "take_profit_list": [0.12],
        "trailing_stop_pct_list": [0.01],
        "max_positions_list": [3],
        "position_pct_list": [0.2],
        "max_symbols_list": [60],
        "priority_topk_per_day_list": [0],
        "sample_points": 1,
        "max_points": 1,
    }

    start_resp = client.post("/api/backtest/plateau/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    finished = _wait_backtest_plateau_task(task_id)
    assert finished["status"] == "succeeded"
    point = finished["result"]["points"][0]
    detail_key = str(point["detail_key"])
    assert detail_key.startswith("pt_")

    detail_resp = client.get(f"/api/backtest/plateau/tasks/{task_id}/points/{detail_key}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["task_id"] == task_id
    assert detail["detail_key"] == detail_key
    assert detail["run_request"]["pool_roll_mode"] == "position"
    assert detail["run_request"]["window_days"] == 40
    assert detail["run_result"]["notes"] == ["plateau detail persisted"]
    assert detail["run_result"]["trades"][0]["symbol"] == "sz300750"

    detail_path = store._backtest_plateau_point_detail_path(task_id, detail_key)
    detail_dir = store._backtest_plateau_task_detail_dir(task_id)
    assert detail_path.exists()
    assert detail_dir.exists()

    delete_resp = client.delete(f"/api/backtest/plateau/tasks/{task_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True
    assert not detail_dir.exists()

    detail_resp_after = client.get(f"/api/backtest/plateau/tasks/{task_id}/points/{detail_key}")
    assert detail_resp_after.status_code == 404
    assert detail_resp_after.json()["code"] == "BACKTEST_PLATEAU_POINT_DETAIL_NOT_FOUND"


def test_backtest_plateau_task_delete_running_task_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (progress_callback, prebuilt_universe)
        if control_callback is not None:
            control_callback()
        time.sleep(0.05)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.5,
                total_return=0.1,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.02,
                trade_count=5,
                win_count=3,
                loss_count=2,
                profit_factor=1.2,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=12,
            skipped_count=2,
            fill_rate=0.8,
            max_concurrent_positions=int(payload.max_positions),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    base_payload = {
        "mode": "full_market",
        "run_id": "",
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "board_filters": [],
        "date_from": "2025-01-02",
        "date_to": "2025-02-28",
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.2,
        "max_hold_days": 30,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 10,
        "enforce_t1": True,
        "max_symbols": 100,
    }
    payload = {
        "base_payload": base_payload,
        "sampling_mode": "lhs",
        "sample_points": 80,
        "random_seed": 20260227,
        "window_days_list": [40, 80],
        "min_score_list": [50, 70],
        "stop_loss_list": [0.03, 0.08],
        "take_profit_list": [0.1, 0.4],
        "trailing_stop_pct_list": [0.0, 0.08],
        "max_positions_list": [3, 10],
        "position_pct_list": [0.1, 0.4],
        "max_symbols_list": [80, 300],
        "priority_topk_per_day_list": [0, 20],
    }

    start_resp = client.post("/api/backtest/plateau/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    running = _wait_backtest_plateau_task_status(task_id, {"running"})
    assert running["status"] == "running"

    delete_resp = client.delete(f"/api/backtest/plateau/tasks/{task_id}")
    assert delete_resp.status_code == 400
    delete_body = delete_resp.json()
    assert delete_body["code"] == "BACKTEST_PLATEAU_TASK_CONTROL_INVALID"

    cancel_resp = client.post(f"/api/backtest/plateau/tasks/{task_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


def test_backtest_plateau_task_delete_returns_404_for_missing_task() -> None:
    missing_id = "bp_missing_20260227"
    delete_resp = client.delete(f"/api/backtest/plateau/tasks/{missing_id}")
    assert delete_resp.status_code == 404
    body = delete_resp.json()
    assert body["code"] == "BACKTEST_PLATEAU_TASK_NOT_FOUND"


def test_backtest_plateau_replay_reuses_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_PLATEAU_WORKERS", "4")
    run_calls = 0
    candidate_build_calls = 0
    replay_calls = 0

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        nonlocal run_calls
        _ = (progress_callback, prebuilt_universe)
        if control_callback is not None:
            control_callback()
        run_calls += 1
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.55,
                total_return=0.12,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.03,
                trade_count=6,
                win_count=4,
                loss_count=2,
                profit_factor=1.6,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=10,
            skipped_count=1,
            fill_rate=0.9,
            max_concurrent_positions=int(payload.max_positions),
        )

    def _fake_build_universe(payload, board_filters, *, control_callback=None):  # noqa: ANN001
        _ = (payload, board_filters)
        if control_callback is not None:
            control_callback()
        return (
            ["sz300750"],
            {"2025-01-02": {"sz300750"}},
            ["mock prebuilt universe"],
        )

    def _fake_run_candidates_only(  # noqa: ANN001
        self,
        *,
        payload,
        symbols,
        allowed_symbols_by_date=None,
        apply_priority_topk=True,
        control_callback=None,
    ):
        nonlocal candidate_build_calls
        _ = (self, payload, symbols, allowed_symbols_by_date, apply_priority_topk)
        if control_callback is not None:
            control_callback()
        candidate_build_calls += 1
        return [
            CandidateTrade(
                symbol="sz300750",
                signal_date="2025-01-02",
                entry_date="2025-01-03",
                exit_date="2025-01-10",
                entry_signal="SOS",
                entry_phase="吸筹D",
                entry_quality_score=78.0,
                candle_quality_score=78.0,
                cost_center_shift_score=78.0,
                weekly_context_score=60.0,
                weekly_context_multiplier=1.0,
                entry_phase_score=2.0,
                entry_events_weight=3.0,
                entry_structure_score=2,
                entry_trend_score=70.0,
                entry_volatility_score=55.0,
                health_score=70.0,
                event_score=68.0,
                risk_score=20.0,
                confirmation_status="confirmed",
                event_grade="A",
                phase_context_score=60.0,
                event_recency_score=50.0,
                delay_entry_days=1,
                delay_window_days=0,
                final_rank_score=72.0,
                entry_price=100.0,
                exit_price=105.0,
                holding_days=5,
                exit_reason="take_profit",
            )
        ]

    def _fake_replay_portfolio(self, *, candidates, payload):  # noqa: ANN001
        nonlocal replay_calls
        _ = self
        replay_calls += 1
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.5,
                total_return=0.08,
                max_drawdown=0.0,
                avg_pnl_ratio=0.02,
                trade_count=1,
                win_count=1,
                loss_count=0,
                profit_factor=2.0,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[],
            candidate_count=len(candidates),
            skipped_count=0,
            fill_rate=1.0,
            max_concurrent_positions=int(payload.max_positions),
        )

    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(store, "_build_backtest_universe_for_plateau", _fake_build_universe)
    monkeypatch.setattr(store_module.BacktestEngine, "run_candidates_only", _fake_run_candidates_only)
    monkeypatch.setattr(store_module.BacktestEngine, "replay_portfolio", _fake_replay_portfolio)

    payload = {
        "base_payload": {
            "mode": "full_market",
            "run_id": "",
            "trend_step": "auto",
            "pool_roll_mode": "daily",
            "board_filters": [],
            "date_from": "2025-01-02",
            "date_to": "2025-02-28",
            "window_days": 60,
            "min_score": 55,
            "require_sequence": False,
            "min_event_count": 1,
            "entry_events": ["Spring", "SOS", "JOC", "LPS"],
            "exit_events": ["UTAD", "SOW", "LPSY"],
            "initial_capital": 1_000_000,
            "position_pct": 0.2,
            "max_positions": 5,
            "stop_loss": 0.05,
            "take_profit": 0.2,
            "max_hold_days": 30,
            "fee_bps": 8.0,
            "prioritize_signals": True,
            "priority_mode": "balanced",
            "priority_topk_per_day": 10,
            "enforce_t1": True,
            "max_symbols": 100,
        },
        "sampling_mode": "grid",
        "window_days_list": [60],
        "min_score_list": [55],
        "stop_loss_list": [0.05],
        "take_profit_list": [0.2],
        "max_positions_list": [3, 5, 8],
        "position_pct_list": [0.2],
        "max_symbols_list": [100],
        "priority_topk_per_day_list": [10],
        "max_points": 3,
    }

    resp = client.post("/api/backtest/plateau", json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert body["evaluated_combinations"] == 3
    assert run_calls == 1
    assert candidate_build_calls == 1
    assert replay_calls == 2
    assert any("候选重放复用" in note for note in body["notes"])


def test_start_backtest_task_binds_event_judgment_profile_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}
    original_active = store._active_event_judgment_profile_id
    original_custom_profiles = dict(store._event_judgment_custom_profiles)
    profile_a = _build_custom_event_profile("ejp_task_bind_a", name="Task Bind A", tweak=1.01)
    profile_b = _build_custom_event_profile("ejp_task_bind_b", name="Task Bind B", tweak=1.02)

    try:
        store._event_judgment_custom_profiles[profile_a["profile_id"]] = dict(profile_a)
        store._event_judgment_custom_profiles[profile_b["profile_id"]] = dict(profile_b)
        store._active_event_judgment_profile_id = str(profile_a["profile_id"])

        monkeypatch.setattr(store, "_run_backtest_precheck_with_cache", lambda payload: None)

        def _fake_start_worker(task_id, payload, *, resumed, run_precheck):  # noqa: ANN001
            created["task_id"] = task_id
            created["payload"] = payload
            created["resumed"] = resumed
            created["run_precheck"] = run_precheck

        monkeypatch.setattr(store, "_start_backtest_task_worker", _fake_start_worker)

        task_id = store.start_backtest_task(
            BacktestRunRequest(
                mode="full_market",
                date_from="2025-01-02",
                date_to="2025-02-28",
                entry_events=["Spring"],
                exit_events=["UTAD"],
            )
        )
        stored = store._backtest_task_payloads[task_id]

        assert stored.event_judgment_profile_id == profile_a["profile_id"]
        assert stored.event_judgment_profile_hash == store._event_judgment_profile_hash(dict(profile_a))
        assert stored.event_judgment_profile_snapshot is not None
        assert stored.event_judgment_profile_snapshot["profile_id"] == profile_a["profile_id"]

        store._active_event_judgment_profile_id = str(profile_b["profile_id"])
        assert store._backtest_task_payloads[task_id].event_judgment_profile_snapshot["profile_id"] == profile_a["profile_id"]
        assert created["payload"].event_judgment_profile_hash == stored.event_judgment_profile_hash
    finally:
        store._active_event_judgment_profile_id = original_active
        store._event_judgment_custom_profiles = original_custom_profiles
        if "task_id" in created:
            with store._backtest_task_lock:
                store._backtest_task_payloads.pop(str(created["task_id"]), None)
                store._backtest_tasks.pop(str(created["task_id"]), None)


def test_start_backtest_plateau_task_binds_base_event_judgment_profile_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    original_active = store._active_event_judgment_profile_id
    original_custom_profiles = dict(store._event_judgment_custom_profiles)
    profile_a = _build_custom_event_profile("ejp_plateau_bind_a", name="Plateau Bind A", tweak=1.03)

    try:
        store._event_judgment_custom_profiles[profile_a["profile_id"]] = dict(profile_a)
        store._active_event_judgment_profile_id = str(profile_a["profile_id"])

        def _fake_start_worker(task_id, payload, *, resumed):  # noqa: ANN001
            created["task_id"] = task_id
            created["payload"] = payload
            created["resumed"] = resumed

        monkeypatch.setattr(store, "_start_backtest_plateau_task_worker", _fake_start_worker)

        task_id = store.start_backtest_plateau_task(
            store_module.BacktestPlateauRunRequest(
                base_payload=BacktestRunRequest(
                    mode="full_market",
                    date_from="2025-01-02",
                    date_to="2025-02-28",
                    entry_events=["Spring"],
                    exit_events=["UTAD"],
                ),
                sampling_mode="lhs",
                sample_points=3,
                max_points=3,
            )
        )
        stored = store._backtest_plateau_task_payloads[task_id].base_payload

        assert stored.event_judgment_profile_id == profile_a["profile_id"]
        assert stored.event_judgment_profile_hash == store._event_judgment_profile_hash(dict(profile_a))
        assert stored.event_judgment_profile_snapshot is not None
        assert stored.event_judgment_profile_snapshot["profile_id"] == profile_a["profile_id"]
        assert created["payload"].base_payload.event_judgment_profile_hash == stored.event_judgment_profile_hash
    finally:
        store._active_event_judgment_profile_id = original_active
        store._event_judgment_custom_profiles = original_custom_profiles
        if "task_id" in created:
            with store._backtest_plateau_task_lock:
                store._backtest_plateau_task_payloads.pop(str(created["task_id"]), None)
                store._backtest_plateau_tasks.pop(str(created["task_id"]), None)


def test_calc_wyckoff_snapshot_uses_bound_runtime_event_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    original_active = store._active_event_judgment_profile_id
    original_custom_profiles = dict(store._event_judgment_custom_profiles)
    profile_a = _build_custom_event_profile("ejp_runtime_bind_a", name="Runtime Bind A", tweak=1.04)
    captured_profile_ids: list[str] = []

    try:
        store._event_judgment_custom_profiles[profile_a["profile_id"]] = dict(profile_a)
        store._active_event_judgment_profile_id = str(profile_a["profile_id"])

        dates = _load_symbol_dates("sz300750")
        as_of_trade_date = dates[-1]
        row = store._build_row_from_candles("sz300750", as_of_date=as_of_trade_date)
        assert row is not None

        monkeypatch.setattr(store, "_slice_candles_as_of", lambda candles, as_of_date: (candles, as_of_trade_date))
        monkeypatch.setattr(store._wyckoff_event_store, "get_snapshot", lambda **kwargs: None)
        monkeypatch.setattr(store._wyckoff_event_store, "upsert_snapshot", lambda **kwargs: False)
        monkeypatch.setattr(store, "_inspect_wyckoff_snapshot_quality", lambda snapshot, *, trade_date: {})
        monkeypatch.setattr(store, "_record_wyckoff_snapshot_quality", lambda flags: None)

        def _fake_calc_snapshot(row_arg, candles, window_days, event_judgment_profile=None):  # noqa: ANN001
            _ = (row_arg, candles, window_days)
            captured_profile_ids.append(str((event_judgment_profile or {}).get("profile_id", "")))
            return {
                "events": ["SOS"],
                "event_dates": [as_of_trade_date],
            }

        monkeypatch.setattr(store_module.SignalAnalyzer, "calculate_wyckoff_snapshot", _fake_calc_snapshot)

        with store._backtest_runtime_event_judgment_binding(
            profile=dict(profile_a),
            profile_id=str(profile_a["profile_id"]),
            profile_hash=store._event_judgment_profile_hash(dict(profile_a)),
        ):
            store._calc_wyckoff_snapshot(row, 60, as_of_date=as_of_trade_date)

        assert captured_profile_ids == [str(profile_a["profile_id"])]
    finally:
        store._active_event_judgment_profile_id = original_active
        store._event_judgment_custom_profiles = original_custom_profiles


def test_maybe_trim_backtest_runtime_memory_respects_idle_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {
        "matrix": 0,
        "wyckoff": 0,
        "signal_runtime": 0,
        "input_runtime": 0,
        "precheck": 0,
        "candles_trim": 0,
        "gc": 0,
    }

    monkeypatch.setenv("TDX_TREND_BACKTEST_AUTO_TRIM_RUNTIME", "1")

    def _bump(key: str) -> None:
        calls[key] += 1

    monkeypatch.setattr(store._backtest_matrix_engine, "clear_runtime_cache", lambda: _bump("matrix"))
    monkeypatch.setattr(store._wyckoff_event_store, "clear_runtime_cache", lambda: _bump("wyckoff"))
    monkeypatch.setattr(store, "_clear_backtest_signal_matrix_runtime_cache", lambda: _bump("signal_runtime"))
    monkeypatch.setattr(store, "_clear_backtest_input_pool_runtime_cache", lambda: _bump("input_runtime"))
    monkeypatch.setattr(store, "_clear_backtest_precheck_cache", lambda: _bump("precheck"))
    monkeypatch.setattr(
        store,
        "_trim_candles_runtime_cache",
        lambda *, target_max_symbols: (_bump("candles_trim"), target_max_symbols)[1],
    )
    monkeypatch.setattr(store_module.gc, "collect", lambda: (_bump("gc"), 0)[1])

    with store._backtest_task_lock:
        store._backtest_running_worker_ids.clear()
    with store._backtest_plateau_task_lock:
        store._backtest_plateau_running_worker_ids.clear()

    store.maybe_trim_backtest_runtime_memory()
    assert calls["matrix"] == 1
    assert calls["wyckoff"] == 1
    assert calls["signal_runtime"] == 1
    assert calls["input_runtime"] == 1
    assert calls["precheck"] == 1
    assert calls["candles_trim"] == 1
    assert calls["gc"] == 1

    with store._backtest_task_lock:
        store._backtest_running_worker_ids.add("bt_running_demo")
    try:
        store.maybe_trim_backtest_runtime_memory()
    finally:
        with store._backtest_task_lock:
            store._backtest_running_worker_ids.discard("bt_running_demo")

    assert calls["matrix"] == 1
    assert calls["wyckoff"] == 1


def test_backtest_run_requires_existing_run_when_run_missing() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-35]
    date_to = dates[-8]

    payload = {
        "mode": "trend_pool",
        "run_id": "missing-run-id",
        "trend_step": "auto",
        "pool_roll_mode": "position",
        "date_from": date_from,
        "date_to": date_to,
    }
    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    message_text = str(body.get("message", ""))
    assert "筛选任务 missing-run-id 不存在或已失效" in message_text


def test_backtest_run_respects_board_filters() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-35]
    date_to = dates[-8]
    run_id = _create_trend_pool_run(date_to)

    detail_resp = client.get(f"/api/screener/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    source_rows = (
        detail.get("step_pools", {}).get("step4")
        or detail.get("step_pools", {}).get("step3")
        or detail.get("results")
        or []
    )
    assert source_rows
    source_symbols = [str(row["symbol"]) for row in source_rows if row.get("symbol")]
    assert source_symbols
    selected_board = next(
        (
            board
            for board in ("main", "gem", "star", "beijing")
            if any(_detect_board(symbol) == board for symbol in source_symbols)
        ),
        "main",
    )

    payload = {
        "mode": "trend_pool",
        "run_id": run_id,
        "trend_step": "auto",
        "board_filters": [selected_board],
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "require_sequence": False,
        "min_event_count": 1,
        "entry_events": ["Spring", "SOS", "JOC", "LPS"],
        "exit_events": ["UTAD", "SOW", "LPSY"],
        "initial_capital": 1_000_000,
        "position_pct": 0.2,
        "max_positions": 5,
        "stop_loss": 0.05,
        "take_profit": 0.15,
        "max_hold_days": 60,
        "fee_bps": 8.0,
        "prioritize_signals": True,
        "priority_mode": "balanced",
        "priority_topk_per_day": 0,
        "enforce_t1": True,
        "max_symbols": 120,
        "pool_roll_mode": "weekly",
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert any("候选池板块过滤" in note for note in body["notes"])
    for row in body["trades"]:
        assert _detect_board(row["symbol"]) == selected_board


def test_backtest_task_full_market_daily_smoke() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-18]
    date_to = dates[-14]

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
        "priority_topk_per_day": 0,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])
    assert task_id.startswith("bt_")

    task = _wait_backtest_task(task_id)
    assert task["status"] == "succeeded"
    progress = task["progress"]
    assert progress["total_dates"] >= 1
    assert progress["processed_dates"] >= progress["total_dates"]
    result = task["result"]
    assert result["range"]["date_from"] == date_from
    assert result["range"]["date_to"] == date_to
    assert any("全市场候选池构建" in note for note in result["notes"])


def test_backtest_task_list_includes_persisted_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_precheck(payload):  # noqa: ANN001
        _ = payload
        return None

    def _fake_scan_total_dates(payload):  # noqa: ANN001
        _ = payload
        return 2

    def _fake_total_dates(payload):  # noqa: ANN001
        _ = payload
        return 2

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (progress_callback, control_callback, prebuilt_universe)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.5,
                total_return=0.12,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.03,
                trade_count=2,
                win_count=1,
                loss_count=1,
                profit_factor=1.3,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=["persisted task result"],
            candidate_count=6,
            skipped_count=1,
            fill_rate=0.5,
            max_concurrent_positions=2,
            effective_run_request=payload.model_copy(deep=True),
        )

    monkeypatch.setattr(store, "_run_backtest_precheck_with_cache", _fake_precheck)
    monkeypatch.setattr(store, "_estimate_backtest_scan_progress_total_dates", _fake_scan_total_dates)
    monkeypatch.setattr(store, "_estimate_backtest_progress_total_dates", _fake_total_dates)
    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-02",
        "date_to": "2025-01-10",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
        "priority_topk_per_day": 0,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    finished = _wait_backtest_task(task_id)
    assert finished["status"] == "succeeded"
    assert finished["result"]["notes"] == ["persisted task result"]

    list_resp = client.get("/api/backtest/tasks", params={"include_result": "true"})
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    matched = next(item for item in items if item["task_id"] == task_id)
    assert matched["result"]["notes"] == ["persisted task result"]
    assert matched["result"]["effective_run_request"]["pool_roll_mode"] == "daily"

    persisted = json.loads(store._backtest_task_state_path.read_text(encoding="utf-8"))
    persisted_row = next(row for row in persisted["tasks"] if row["task"]["task_id"] == task_id)
    assert persisted_row["task"]["result"]["notes"] == ["persisted task result"]
    assert persisted_row["task"]["result"]["effective_run_request"]["pool_roll_mode"] == "daily"


def test_backtest_task_supports_pause_resume_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_precheck(payload):  # noqa: ANN001
        return None

    def _fake_total_dates(payload):  # noqa: ANN001
        return 120

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        total = 120
        for idx in range(total):
            if control_callback is not None:
                control_callback()
            if progress_callback is not None:
                progress_callback(
                    f"2025-01-{(idx % 28) + 1:02d}",
                    idx + 1,
                    total,
                    f"fake progress {idx + 1}/{total}",
                )
            time.sleep(0.01)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.0,
                total_return=0.0,
                max_drawdown=0.0,
                avg_pnl_ratio=0.0,
                trade_count=0,
                win_count=0,
                loss_count=0,
                profit_factor=0.0,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
        )

    monkeypatch.setattr(store, "_precheck_backtest_data_coverage_before_task", _fake_precheck)
    monkeypatch.setattr(store, "_estimate_backtest_progress_total_dates", _fake_total_dates)
    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    running = _wait_backtest_task_status(task_id, {"running"})
    assert running["status"] == "running"

    pause_resp = client.post(f"/api/backtest/tasks/{task_id}/pause")
    assert pause_resp.status_code == 200
    paused = pause_resp.json()
    assert paused["status"] == "paused"
    paused_processed = int(paused["progress"]["processed_dates"])
    time.sleep(0.15)
    paused_after = client.get(f"/api/backtest/tasks/{task_id}").json()
    assert paused_after["status"] == "paused"
    assert int(paused_after["progress"]["processed_dates"]) <= paused_processed + 1

    resume_resp = client.post(f"/api/backtest/tasks/{task_id}/resume")
    assert resume_resp.status_code == 200
    resumed = resume_resp.json()
    assert resumed["status"] in {"pending", "running"}

    _wait_backtest_task_status(task_id, {"running"})

    cancel_resp = client.post(f"/api/backtest/tasks/{task_id}/cancel")
    assert cancel_resp.status_code == 200
    cancelled = cancel_resp.json()
    assert cancelled["status"] == "cancelled"

    final = _wait_backtest_task_status(task_id, {"cancelled"})
    assert final["status"] == "cancelled"


def test_backtest_task_allows_new_task_when_previous_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_precheck(payload):  # noqa: ANN001
        return None

    def _fake_total_dates(payload):  # noqa: ANN001
        return 180

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        total = 180
        for idx in range(total):
            if control_callback is not None:
                control_callback()
            if progress_callback is not None:
                progress_callback(
                    f"2025-02-{(idx % 28) + 1:02d}",
                    idx + 1,
                    total,
                    f"fake progress {idx + 1}/{total}",
                )
            time.sleep(0.01)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.0,
                total_return=0.0,
                max_drawdown=0.0,
                avg_pnl_ratio=0.0,
                trade_count=0,
                win_count=0,
                loss_count=0,
                profit_factor=0.0,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
        )

    monkeypatch.setattr(store, "_precheck_backtest_data_coverage_before_task", _fake_precheck)
    monkeypatch.setattr(store, "_estimate_backtest_progress_total_dates", _fake_total_dates)
    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    start_a = client.post("/api/backtest/tasks", json=payload)
    assert start_a.status_code == 200
    task_a = str(start_a.json()["task_id"])
    _wait_backtest_task_status(task_a, {"running"})

    pause_resp = client.post(f"/api/backtest/tasks/{task_a}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    start_b = client.post("/api/backtest/tasks", json=payload)
    assert start_b.status_code == 200
    task_b = str(start_b.json()["task_id"])
    running_or_done = _wait_backtest_task_status(task_b, {"running", "succeeded"})
    assert running_or_done["status"] in {"running", "succeeded"}

    paused_a = client.get(f"/api/backtest/tasks/{task_a}")
    assert paused_a.status_code == 200
    assert paused_a.json()["status"] == "paused"

    cancel_a = client.post(f"/api/backtest/tasks/{task_a}/cancel")
    assert cancel_a.status_code == 200
    assert cancel_a.json()["status"] == "cancelled"
    _wait_backtest_task_status(task_a, {"cancelled"})

    task_b_now = client.get(f"/api/backtest/tasks/{task_b}")
    assert task_b_now.status_code == 200
    if task_b_now.json()["status"] not in {"succeeded", "failed", "cancelled"}:
        cancel_b = client.post(f"/api/backtest/tasks/{task_b}/cancel")
        assert cancel_b.status_code == 200
        _wait_backtest_task_status(task_b, {"cancelled"})


def test_backtest_task_fails_fast_when_worker_thread_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_precheck(payload):  # noqa: ANN001
        return None

    def _fake_total_dates(payload):  # noqa: ANN001
        return 10

    class _BrokenThread:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def start(self) -> None:
            raise RuntimeError("start-failed-for-test")

    monkeypatch.setattr(store, "_precheck_backtest_data_coverage_before_task", _fake_precheck)
    monkeypatch.setattr(store, "_estimate_backtest_progress_total_dates", _fake_total_dates)
    monkeypatch.setattr(store_module, "Thread", _BrokenThread)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-01-31",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])
    failed = _wait_backtest_task_status(task_id, {"failed"}, timeout_sec=6.0)
    assert failed["error_code"] == "BACKTEST_TASK_WORKER_START_FAILED"
    assert "线程启动失败" in str(failed.get("error") or "")


def test_backtest_task_full_market_weekly_smoke() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-28]
    date_to = dates[-12]

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "weekly",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    task = _wait_backtest_task(task_id)
    assert task["status"] == "succeeded"
    progress = task["progress"]
    assert progress["mode"] == "weekly"
    assert progress["processed_dates"] >= 1
    result = task["result"]
    assert result["range"]["date_from"] == date_from
    assert result["range"]["date_to"] == date_to
    assert any("全市场候选池构建: 每周滚动" in note for note in result["notes"])


def test_backtest_run_full_market_reports_system_limit_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-16]
    date_to = dates[-14]

    monkeypatch.setattr(store, "_FULL_MARKET_SYSTEM_PROTECT_LIMIT", 5)
    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 120,
    }
    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert any("触发系统保护上限" in note for note in body["notes"])


def test_backtest_run_full_market_position_mode() -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-20]
    date_to = dates[-14]

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "position",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert any("持仓触发滚动" in note for note in body["notes"])


def test_position_mode_refresh_dates_include_sell_signal_day() -> None:
    scan_dates = ["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"]
    refresh_dates = store._collect_position_mode_refresh_dates(
        scan_dates,
        seed_date=scan_dates[0],
        exit_dates=["2026-05-27"],
    )
    assert refresh_dates == ["2026-05-26", "2026-05-28"]


def test_backtest_task_rejects_when_candle_coverage_insufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_TASK_PRECHECK_ASYNC", "0")

    def _fake_scan_dates(_: str, __: str) -> list[str]:
        return ["2025-01-02", "2025-01-03"]

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        return ["sz300750", "sh600519"], {}, [], ["2025-01-02", "2025-01-03"], ["2025-01-02"]

    def _fake_candles(_: str):
        return [
            SimpleNamespace(time="2025-11-03"),
            SimpleNamespace(time="2026-02-13"),
        ]

    monkeypatch.setattr(store, "_build_backtest_scan_dates", _fake_scan_dates)
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)
    monkeypatch.setattr(store, "_ensure_candles", _fake_candles)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 120,
    }

    resp = client.post("/api/backtest/tasks", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "BACKTEST_DATA_COVERAGE_INSUFFICIENT"
    assert "2025-01-01" in body["message"]
    assert "2025-11-03" in body["message"]


def test_backtest_task_rejects_when_candles_window_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_TASK_PRECHECK_ASYNC", "0")

    def _fake_scan_dates(_: str, __: str) -> list[str]:
        return [f"2025-01-{(idx % 28) + 1:02d}" for idx in range(140)]

    monkeypatch.setattr(store, "_build_backtest_scan_dates", _fake_scan_dates)
    monkeypatch.setattr(store._config, "candles_window_bars", 120)
    store._clear_backtest_precheck_cache()

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 120,
    }

    resp = client.post("/api/backtest/tasks", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "BACKTEST_CANDLES_WINDOW_TOO_SHORT"
    assert "candles_window_bars=120" in body["message"]


def test_backtest_task_records_stage_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_precheck(payload):  # noqa: ANN001
        _ = payload
        return None

    def _fake_total_dates(payload):  # noqa: ANN001
        _ = payload
        return 3

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        if progress_callback is not None:
            progress_callback(payload.date_from, 1, 3, "mock progress")
            progress_callback(payload.date_to, 3, 3, "mock done")
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.0,
                total_return=0.0,
                max_drawdown=0.0,
                avg_pnl_ratio=0.0,
                trade_count=0,
                win_count=0,
                loss_count=0,
                profit_factor=0.0,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=[
                (
                    "矩阵引擎已启用：shape=12x3，windows=[20, 60]，cache=miss，signal_cache=miss，key=mock...；"
                    "耗时[建矩阵=0.11s, 算信号=0.22s, 撮合=0.33s, 总计=0.66s]"
                ),
                "执行细分耗时[候选=0.05s, 撮合=0.21s, 曲线=0.07s]",
            ],
        )

    monkeypatch.setattr(store, "_run_backtest_precheck_with_cache", _fake_precheck)
    monkeypatch.setattr(store, "_estimate_backtest_progress_total_dates", _fake_total_dates)
    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-01-03",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])
    final = _wait_backtest_task(task_id, timeout_sec=20.0)
    assert final["status"] == "succeeded"
    stage_timings = final["progress"].get("stage_timings") or []
    stage_keys = {str(row.get("stage_key")) for row in stage_timings if isinstance(row, dict)}
    assert "precheck" in stage_keys
    assert "matrix_build" in stage_keys
    assert "signal_compute" in stage_keys
    assert "candidate_build" in stage_keys
    assert "execution_match" in stage_keys
    assert "equity_curve" in stage_keys
    assert "run_total" in stage_keys


def test_backtest_task_async_precheck_fails_in_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    call_counter = {"precheck": 0, "run": 0}

    def _fake_precheck(payload):  # noqa: ANN001
        _ = payload
        call_counter["precheck"] += 1
        raise BacktestValidationError("BACKTEST_DATA_COVERAGE_INSUFFICIENT", "mock coverage insufficient")

    def _fake_run_backtest(payload, *, progress_callback=None, control_callback=None, prebuilt_universe=None):  # noqa: ANN001
        _ = (payload, progress_callback, control_callback)
        call_counter["run"] += 1
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.0,
                total_return=0.0,
                max_drawdown=0.0,
                avg_pnl_ratio=0.0,
                trade_count=0,
                win_count=0,
                loss_count=0,
                profit_factor=0.0,
            ),
            trades=[],
            range=ReviewRange(date_from="2025-01-01", date_to="2025-01-02", date_axis="sell"),
        )

    monkeypatch.setenv("TDX_TREND_BACKTEST_TASK_PRECHECK_ASYNC", "1")
    monkeypatch.setattr(store, "_run_backtest_precheck_with_cache", _fake_precheck)
    monkeypatch.setattr(store, "run_backtest", _fake_run_backtest)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": "2025-01-01",
        "date_to": "2025-01-02",
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    final = _wait_backtest_task(task_id, timeout_sec=20.0)
    assert final["status"] == "failed"
    assert final["error_code"] == "BACKTEST_DATA_COVERAGE_INSUFFICIENT"
    assert "mock coverage insufficient" in str(final.get("error") or "")
    assert call_counter["precheck"] == 1
    assert call_counter["run"] == 0


def test_backtest_run_full_market_daily_matrix_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        return ["sz300750"], allowed, ["mock matrix universe"], scan_dates, scan_dates

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert body["execution_path"] == "matrix"
    assert any("矩阵引擎已启用" in note for note in body["notes"])


def test_backtest_run_matrix_diff_guard_falls_back_to_legacy_when_deviation_exceeds_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]

    def _fake_matrix_run(
        *,
        payload: BacktestRunRequest,
        board_filters: list[str],
        progress_callback=None,  # noqa: ANN001
        control_callback=None,  # noqa: ANN001
    ) -> BacktestResponse:
        _ = (board_filters, progress_callback, control_callback)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.72,
                total_return=0.28,
                max_drawdown=-0.03,
                avg_pnl_ratio=0.04,
                trade_count=20,
                win_count=14,
                loss_count=6,
                profit_factor=2.0,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=["矩阵引擎已启用：mock"],
            candidate_count=50,
            skipped_count=10,
            fill_rate=0.4,
            max_concurrent_positions=4,
        )

    def _fake_legacy_shadow(*, payload: BacktestRunRequest) -> BacktestResponse:
        _ = payload
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.41,
                total_return=0.05,
                max_drawdown=-0.11,
                avg_pnl_ratio=0.01,
                trade_count=6,
                win_count=2,
                loss_count=4,
                profit_factor=0.9,
            ),
            trades=[],
            range=ReviewRange(date_from=date_from, date_to=date_to, date_axis="sell"),
            notes=[
                "已按参数强制使用旧路径执行（execution_path_preference=legacy）。",
                "legacy-shadow",
            ],
            candidate_count=30,
            skipped_count=12,
            fill_rate=0.2,
            max_concurrent_positions=2,
        )

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_GUARD", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_TRADE_COUNT_RATIO_MAX", "0.10")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_TOTAL_RETURN_ABS_MAX", "0.02")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_WIN_RATE_ABS_MAX", "0.02")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_MAX_DRAWDOWN_ABS_MAX", "0.02")
    monkeypatch.setattr(store, "_run_backtest_matrix", _fake_matrix_run)
    monkeypatch.setattr(store, "_run_backtest_legacy_shadow_for_matrix_diff_guard", _fake_legacy_shadow)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
        "enable_advanced_analysis": False,
    }
    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_path"] == "legacy"
    assert body["stats"]["trade_count"] == 6
    assert any("矩阵/旧路径偏差超阈值" in str(note) for note in body["notes"])
    assert all("execution_path_preference=legacy" not in str(note) for note in body["notes"])


def test_backtest_run_matrix_diff_guard_keeps_matrix_when_within_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]
    guard_call_counter = {"count": 0}

    def _fake_matrix_run(
        *,
        payload: BacktestRunRequest,
        board_filters: list[str],
        progress_callback=None,  # noqa: ANN001
        control_callback=None,  # noqa: ANN001
    ) -> BacktestResponse:
        _ = (board_filters, progress_callback, control_callback)
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.58,
                total_return=0.12,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.02,
                trade_count=10,
                win_count=6,
                loss_count=4,
                profit_factor=1.4,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=["矩阵引擎已启用：mock"],
            candidate_count=40,
            skipped_count=8,
            fill_rate=0.35,
            max_concurrent_positions=3,
        )

    def _fake_legacy_shadow(*, payload: BacktestRunRequest) -> BacktestResponse:
        _ = payload
        guard_call_counter["count"] += 1
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.57,
                total_return=0.11,
                max_drawdown=-0.085,
                avg_pnl_ratio=0.02,
                trade_count=9,
                win_count=5,
                loss_count=4,
                profit_factor=1.3,
            ),
            trades=[],
            range=ReviewRange(date_from=date_from, date_to=date_to, date_axis="sell"),
            notes=["legacy-shadow"],
            candidate_count=39,
            skipped_count=9,
            fill_rate=0.33,
            max_concurrent_positions=3,
        )

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_GUARD", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_TRADE_COUNT_RATIO_MAX", "1.00")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_TOTAL_RETURN_ABS_MAX", "1.00")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_WIN_RATE_ABS_MAX", "1.00")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_DIFF_MAX_DRAWDOWN_ABS_MAX", "1.00")
    monkeypatch.setattr(store, "_run_backtest_matrix", _fake_matrix_run)
    monkeypatch.setattr(store, "_run_backtest_legacy_shadow_for_matrix_diff_guard", _fake_legacy_shadow)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
        "enable_advanced_analysis": False,
    }
    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_path"] == "matrix"
    assert body["stats"]["trade_count"] == 10
    assert guard_call_counter["count"] == 1
    assert any("矩阵偏差守卫检查" in str(note) for note in body["notes"])
    assert any("在阈值内" in str(note) for note in body["notes"])


def test_backtest_run_full_market_daily_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        return ["sz300750"], allowed, ["mock legacy universe"], scan_dates, scan_dates

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert body["execution_path"] == "legacy"
    assert all("矩阵引擎已启用" not in note for note in body["notes"])


def test_backtest_run_forces_t1_when_strategy_disables_entry_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]
    registry = store._strategy_registry
    original = registry._strategies["wyckoff_trend_v1"]
    downgraded = replace(
        original,
        capabilities=replace(
            original.capabilities,
            supports_entry_delay=False,
        ),
    )
    monkeypatch.setitem(registry._strategies, "wyckoff_trend_v1", downgraded)
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
        "strategy_id": "wyckoff_trend_v1",
        "entry_delay_days": 3,
        "delay_invalidation_enabled": True,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_path"] == "legacy"
    assert any("不支持延迟入场" in str(note) for note in body["notes"])


def test_backtest_task_delete_removes_persisted_task(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-35]
    date_to = dates[-8]
    run_id = _create_trend_pool_run(date_to)
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_TASK_PRECHECK_ASYNC", "0")

    payload = {
        "mode": "trend_pool",
        "run_id": run_id,
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 120,
    }

    start_resp = client.post("/api/backtest/tasks", json=payload)
    assert start_resp.status_code == 200
    task_id = str(start_resp.json()["task_id"])

    finished = _wait_backtest_task(task_id)
    assert finished["status"] in {"succeeded", "failed", "cancelled"}

    delete_resp = client.delete(f"/api/backtest/tasks/{task_id}")
    assert delete_resp.status_code == 200
    delete_body = delete_resp.json()
    assert delete_body["deleted"] is True
    assert delete_body["task_id"] == task_id

    get_resp = client.get(f"/api/backtest/tasks/{task_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["code"] == "BACKTEST_TASK_NOT_FOUND"

    list_resp = client.get("/api/backtest/tasks")
    assert list_resp.status_code == 200
    listed_ids = {str(item.get("task_id")) for item in list_resp.json().get("items") or []}
    assert task_id not in listed_ids


def test_backtest_run_trend_king_rally_ignores_wyckoff_entry_events(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-35]
    date_to = dates[-8]
    run_id = _create_trend_pool_run(date_to)
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")

    payload = {
        "mode": "trend_pool",
        "run_id": run_id,
        "trend_step": "auto",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 0,
        "max_symbols": 120,
        "strategy_id": "trend_king_rally_v1",
        "entry_events": ["Spring", "SOS"],
        "min_event_count": 2,
        "require_sequence": True,
        "exit_events": [],
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["execution_path"] == "legacy"
    assert any("内置信号入场" in str(note) for note in body["notes"])


def test_backtest_run_matrix_signal_runtime_cache_reuses_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.backtest_signal_matrix import compute_backtest_signal_matrix as _real_compute

    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]
    call_counter = {"count": 0}
    bundle_callback_kwargs: list[bool] = []
    compute_callback_kwargs: list[bool] = []

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        return ["sz300750"], allowed, ["mock matrix universe"], scan_dates, scan_dates

    def _fake_compute(bundle, *, top_n=500, event_matrix_builder=None, **kwargs):  # noqa: ANN001
        call_counter["count"] += 1
        compute_callback_kwargs.append("control_callback" in kwargs)
        control_callback = kwargs.get("control_callback")
        return _real_compute(
            bundle,
            top_n=top_n,
            event_matrix_builder=event_matrix_builder,
            control_callback=control_callback,
        )

    original_build_bundle = store._backtest_matrix_engine.build_bundle

    def _fake_build_bundle(*args, **kwargs):  # noqa: ANN002, ANN003
        bundle_callback_kwargs.append("control_callback" in kwargs)
        return original_build_bundle(*args, **kwargs)

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_DISK_CACHE", "0")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)
    monkeypatch.setattr(store_module, "compute_backtest_signal_matrix", _fake_compute)
    monkeypatch.setattr(store._backtest_matrix_engine, "build_bundle", _fake_build_bundle)

    store._backtest_matrix_engine.clear_runtime_cache()
    store._clear_backtest_signal_matrix_runtime_cache()

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp1 = client.post("/api/backtest/run", json=payload)
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert any("signal_cache=miss" in note for note in body1["notes"])
    assert bundle_callback_kwargs == [True]
    assert compute_callback_kwargs == [True]

    resp2 = client.post("/api/backtest/run", json=payload)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert any("signal_cache=runtime" in note for note in body2["notes"])
    assert call_counter["count"] == 1


def test_backtest_run_matrix_signal_runtime_cache_isolated_by_event_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.backtest_signal_matrix import compute_backtest_signal_matrix as _real_compute

    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]
    call_counter = {"count": 0}
    profile_a = _build_custom_event_profile("profile_cache_a", name="Profile A", tweak=1.0)
    profile_b = _build_custom_event_profile("profile_cache_b", name="Profile B", tweak=1.2)

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        return ["sz300750"], allowed, ["mock matrix universe"], scan_dates, scan_dates

    def _fake_compute(bundle, *, top_n=500, event_matrix_builder=None, control_callback=None):  # noqa: ANN001
        call_counter["count"] += 1
        return _real_compute(
            bundle,
            top_n=top_n,
            event_matrix_builder=event_matrix_builder,
            control_callback=control_callback,
        )

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_DISK_CACHE", "0")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)
    monkeypatch.setattr(store_module, "compute_backtest_signal_matrix", _fake_compute)
    monkeypatch.setitem(store._event_judgment_custom_profiles, str(profile_a["profile_id"]), dict(profile_a))
    monkeypatch.setitem(store._event_judgment_custom_profiles, str(profile_b["profile_id"]), dict(profile_b))

    store._backtest_matrix_engine.clear_runtime_cache()
    store._clear_backtest_signal_matrix_runtime_cache()

    base_payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
        "matrix_event_semantic_version": "aligned_wyckoff_v2",
    }

    resp1 = client.post(
        "/api/backtest/run",
        json={**base_payload, "event_judgment_profile_id": str(profile_a["profile_id"])},
    )
    assert resp1.status_code == 200
    assert any("signal_cache=miss" in note for note in resp1.json()["notes"])

    resp2 = client.post(
        "/api/backtest/run",
        json={**base_payload, "event_judgment_profile_id": str(profile_b["profile_id"])},
    )
    assert resp2.status_code == 200
    assert any("signal_cache=miss" in note for note in resp2.json()["notes"])

    resp3 = client.post(
        "/api/backtest/run",
        json={**base_payload, "event_judgment_profile_id": str(profile_a["profile_id"])},
    )
    assert resp3.status_code == 200
    assert any("signal_cache=runtime" in note for note in resp3.json()["notes"])
    assert call_counter["count"] == 2


def test_backtest_run_matrix_signal_disk_cache_reuses_signals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core.backtest_signal_matrix import compute_backtest_signal_matrix as _real_compute

    dates = _load_symbol_dates("sz300750")
    date_from = dates[-10]
    date_to = dates[-6]
    call_counter = {"count": 0}

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        return ["sz300750"], allowed, ["mock matrix universe"], scan_dates, scan_dates

    def _fake_compute(bundle, *, top_n=500, event_matrix_builder=None, control_callback=None):  # noqa: ANN001
        call_counter["count"] += 1
        return _real_compute(
            bundle,
            top_n=top_n,
            event_matrix_builder=event_matrix_builder,
            control_callback=control_callback,
        )

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE", "0")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_DISK_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_CACHE_DIR", str(tmp_path / "signal-matrix-cache"))
    monkeypatch.setenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_CACHE_TTL_SEC", "3600")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)
    monkeypatch.setattr(store_module, "compute_backtest_signal_matrix", _fake_compute)

    store._clear_backtest_signal_matrix_runtime_cache()

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "daily",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp1 = client.post("/api/backtest/run", json=payload)
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert any("signal_cache=miss" in note for note in body1["notes"])

    resp2 = client.post("/api/backtest/run", json=payload)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert any("signal_cache=disk" in note for note in body2["notes"])
    assert call_counter["count"] == 1


def test_backtest_run_full_market_position_matrix_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-20]
    date_to = dates[-12]

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        refresh_dates = kwargs.get("refresh_dates")
        refresh_used = list(refresh_dates) if refresh_dates else [scan_dates[0]]
        return ["sz300750"], allowed, ["mock position matrix universe"], scan_dates, refresh_used

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "0")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "position",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert body["execution_path"] == "matrix"
    assert any("矩阵引擎已启用" in note for note in body["notes"])
    assert any("持仓触发滚动预演(轻量)" in note for note in body["notes"])


def test_backtest_run_full_market_weekly_matrix_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-20]
    date_to = dates[-12]

    def _fake_universe(*args, **kwargs):  # noqa: ANN002, ANN003
        scan_dates = store._build_backtest_scan_dates(date_from, date_to)
        allowed = {day: {"sz300750"} for day in scan_dates}
        refresh_dates = kwargs.get("refresh_dates")
        refresh_used = list(refresh_dates) if refresh_dates else store._build_weekly_refresh_dates(scan_dates)
        return ["sz300750"], allowed, ["mock weekly matrix universe"], scan_dates, refresh_used

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    monkeypatch.setattr(store, "_build_full_market_rolling_universe", _fake_universe)

    payload = {
        "mode": "full_market",
        "pool_roll_mode": "weekly",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert body["execution_path"] == "matrix"
    assert any("矩阵引擎已启用" in note for note in body["notes"])


def test_backtest_run_trend_pool_weekly_forces_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = _load_symbol_dates("sz300750")
    date_from = dates[-20]
    date_to = dates[-12]

    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_ENGINE", "1")
    run_id = _create_trend_pool_run(date_to)

    payload = {
        "mode": "trend_pool",
        "run_id": run_id,
        "pool_roll_mode": "weekly",
        "date_from": date_from,
        "date_to": date_to,
        "window_days": 60,
        "min_score": 55,
        "max_symbols": 20,
    }

    resp = client.post("/api/backtest/run", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"]["date_from"] == date_from
    assert body["range"]["date_to"] == date_to
    assert body["execution_path"] == "legacy"
    assert any("execution_path_preference=legacy" in note for note in body["notes"])
    assert any(f"使用筛选任务: {run_id}" in note for note in body["notes"])


def test_backtest_input_pool_disk_cache_reuses_refresh_day_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_INPUT_POOL_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_INPUT_POOL_CACHE_DIR", str(tmp_path / "input-pool-cache"))
    monkeypatch.setenv("TDX_TREND_BACKTEST_INPUT_POOL_CACHE_TTL_SEC", "3600")
    call_counter = {"count": 0}

    def _fake_loader(
        *,
        tdx_root: str,
        markets: list[str],
        return_window_days: int,
        as_of_date: str | None = None,
    ) -> tuple[list[ScreenerResult], str | None]:
        _ = (tdx_root, markets, return_window_days)
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
        return [row], None

    monkeypatch.setattr(store_module, "load_input_pool_from_tdx", _fake_loader)

    refresh_dates = ["2025-01-02", "2025-01-03"]
    rows1, stats1 = store._load_backtest_input_rows_by_dates(
        tdx_root="D:/new_tdx/vipdoc",
        markets=["sh", "sz"],
        return_window_days=40,
        refresh_dates=refresh_dates,
    )
    assert call_counter["count"] == 2
    assert stats1["cache_hit_days"] == 0
    assert stats1["cache_miss_days"] == 2
    assert stats1["cache_write_days"] == 2
    assert all(len(items[0]) == 1 for items in rows1.values())

    rows2, stats2 = store._load_backtest_input_rows_by_dates(
        tdx_root="D:/new_tdx/vipdoc",
        markets=["sh", "sz"],
        return_window_days=40,
        refresh_dates=refresh_dates,
    )
    assert call_counter["count"] == 2
    assert stats2["cache_hit_days"] == 2
    assert stats2["cache_miss_days"] == 0
    assert stats2["cache_write_days"] == 0
    assert all(len(items[0]) == 1 for items in rows2.values())


def test_trend_pool_rolling_universe_reuses_trend_filter_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_TREND_FILTER_CACHE", "1")
    monkeypatch.setenv(
        "TDX_TREND_BACKTEST_TREND_FILTER_CACHE_DIR",
        str(tmp_path / "trend-filter-cache"),
    )
    monkeypatch.setenv("TDX_TREND_BACKTEST_TREND_FILTER_CACHE_TTL_SEC", "3600")

    calls = {"loader": 0, "filters": 0}

    def _build_row(day: str) -> ScreenerResult:
        return ScreenerResult(
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

    def _fake_load_backtest_input_rows_by_dates(
        *,
        tdx_root: str,
        markets: list[str],
        return_window_days: int,
        refresh_dates: list[str],
        progress_callback=None,
    ) -> tuple[dict[str, tuple[list[object], str | None]], dict[str, int]]:
        _ = (tdx_root, markets, return_window_days, progress_callback)
        calls["loader"] += 1
        rows_by_date: dict[str, tuple[list[object], str | None]] = {}
        for day in refresh_dates:
            rows_by_date[day] = ([_build_row(day)], None)
        return rows_by_date, {"cache_hit_days": 0, "cache_miss_days": len(refresh_dates), "cache_write_days": 0}

    def _fake_run_filters(
        rows: list[ScreenerResult],
        *,
        mode: str,
        step_configs,
    ) -> tuple[list[ScreenerResult], list[ScreenerResult], list[ScreenerResult], list[ScreenerResult]]:
        _ = (mode, step_configs)
        calls["filters"] += 1
        return list(rows), list(rows), list(rows), list(rows)

    monkeypatch.setattr(store, "_load_backtest_input_rows_by_dates", _fake_load_backtest_input_rows_by_dates)
    monkeypatch.setattr(store, "_run_screener_filters_for_backtest", staticmethod(_fake_run_filters))

    payload = BacktestRunRequest(
        mode="trend_pool",
        trend_step="step1",
        pool_roll_mode="daily",
        date_from="2025-01-02",
        date_to="2025-01-03",
        max_symbols=30,
    )
    screener_params = ScreenerParams(
        markets=["sh", "sz"],
        mode="strict",
        as_of_date=None,
        return_window_days=40,
        top_n=500,
        turnover_threshold=0.05,
        amount_threshold=5e8,
        amplitude_threshold=0.03,
    )

    _, _, notes1, _, _ = store._build_trend_pool_rolling_universe(
        payload=payload,
        screener_params=screener_params,
        board_filters=[],
    )
    assert calls["loader"] == 1
    assert calls["filters"] == 2
    assert any("趋势快照缓存" in note for note in notes1)

    _, _, notes2, _, _ = store._build_trend_pool_rolling_universe(
        payload=payload,
        screener_params=screener_params,
        board_filters=[],
    )
    assert calls["loader"] == 1
    assert calls["filters"] == 2
    assert any("趋势快照缓存: hit 2 / miss 0 / write 0" in note for note in notes2)


def test_backtest_result_cache_reuses_persisted_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE_DIR", str(tmp_path / "backtest-result-cache"))
    monkeypatch.setenv("TDX_TREND_BACKTEST_RESULT_CACHE_TTL_SEC", "3600")

    call_counter = {"matrix": 0}

    def _fake_matrix_run(
        *,
        payload: BacktestRunRequest,
        board_filters: list[str],
        progress_callback=None,
        control_callback=None,
    ) -> BacktestResponse:
        _ = (board_filters, control_callback)
        call_counter["matrix"] += 1
        if progress_callback is not None:
            progress_callback(payload.date_to, 1, 1, "mock matrix")
        return BacktestResponse(
            stats=ReviewStats(
                win_rate=0.5,
                total_return=0.12,
                max_drawdown=-0.08,
                avg_pnl_ratio=0.03,
                trade_count=4,
                win_count=2,
                loss_count=2,
                profit_factor=1.2,
            ),
            trades=[],
            range=ReviewRange(date_from=payload.date_from, date_to=payload.date_to, date_axis="sell"),
            notes=["mock matrix result"],
            candidate_count=8,
            skipped_count=2,
            fill_rate=0.5,
            max_concurrent_positions=2,
        )

    monkeypatch.setattr(store, "_is_backtest_matrix_engine_enabled", lambda: True)
    monkeypatch.setattr(store, "_run_backtest_matrix", _fake_matrix_run)

    payload = BacktestRunRequest(
        mode="full_market",
        pool_roll_mode="daily",
        date_from="2025-01-02",
        date_to="2025-01-03",
        window_days=60,
        min_score=55,
        max_symbols=20,
    )

    first = store.run_backtest(payload)
    assert call_counter["matrix"] == 1
    assert first.execution_path == "matrix"
    assert all("回测结果缓存命中" not in note for note in first.notes)

    second = store.run_backtest(payload)
    assert call_counter["matrix"] == 1
    assert second.execution_path == "matrix"
    assert any("回测结果缓存命中" in note for note in second.notes)



def _build_backtest_report_build_payload(report_id: str) -> dict[str, object]:
    return {
        "report_id": report_id,
        "app_name": "Final Trade",
        "app_version": "test",
        "run_request": {
            "mode": "full_market",
            "trend_step": "auto",
            "pool_roll_mode": "daily",
            "date_from": "2025-01-02",
            "date_to": "2025-01-31",
            "window_days": 60,
            "min_score": 55,
            "require_sequence": False,
            "min_event_count": 1,
            "entry_events": ["Spring", "SOS", "JOC", "LPS"],
            "exit_events": ["UTAD", "SOW", "LPSY"],
            "initial_capital": 1000000,
            "position_pct": 0.2,
            "max_positions": 5,
            "stop_loss": 0.05,
            "take_profit": 0.15,
            "max_hold_days": 60,
            "fee_bps": 8,
            "prioritize_signals": True,
            "priority_mode": "balanced",
            "priority_topk_per_day": 0,
            "enforce_t1": True,
            "max_symbols": 120,
        },
        "run_result": {
            "stats": {
                "win_rate": 0.5,
                "total_return": 0.08,
                "max_drawdown": 0.05,
                "avg_pnl_ratio": 0.02,
                "trade_count": 2,
                "win_count": 1,
                "loss_count": 1,
                "profit_factor": 1.4,
            },
            "trades": [
                {
                    "symbol": "sz300750",
                    "name": "宁德时代",
                    "signal_date": "2025-01-03",
                    "entry_date": "2025-01-06",
                    "exit_date": "2025-01-10",
                    "entry_signal": "SOS",
                    "entry_phase": "吸筹D",
                    "entry_quality_score": 78.0,
                    "exit_reason": "event_exit:SOW",
                    "quantity": 100,
                    "entry_price": 150.0,
                    "exit_price": 158.0,
                    "holding_days": 4,
                    "pnl_amount": 780.0,
                    "pnl_ratio": 0.052,
                }
            ],
            "equity_curve": [
                {"date": "2025-01-02", "equity": 1000000, "realized_pnl": 0},
                {"date": "2025-01-10", "equity": 1000780, "realized_pnl": 780},
            ],
            "drawdown_curve": [
                {"date": "2025-01-02", "drawdown": 0},
                {"date": "2025-01-10", "drawdown": -0.01},
            ],
            "monthly_returns": [
                {"month": "2025-01", "return_ratio": 0.00078, "pnl_amount": 780, "trade_count": 1}
            ],
            "range": {"date_from": "2025-01-02", "date_to": "2025-01-31", "date_axis": "sell"},
            "notes": ["mock note"],
            "candidate_count": 6,
            "skipped_count": 2,
            "fill_rate": 0.333333,
            "max_concurrent_positions": 2,
        },
        "report_html": "<html><body>mock report</body></html>",
        "report_xlsx_base64": base64.b64encode(b"mock-xlsx-bytes").decode("ascii"),
    }


def _build_backtest_report_plateau_fixture(report_id: str) -> tuple[dict[str, object], dict[str, object]]:
    payload = _build_backtest_report_build_payload(report_id)
    detail_key = "pt_shared_detail_01"
    point_params = {
        "window_days": 40,
        "min_score": 58.0,
        "stop_loss": 0.04,
        "take_profit": 0.18,
        "trailing_stop_pct": 0.03,
        "max_positions": 4,
        "position_pct": 0.25,
        "max_symbols": 120,
        "priority_topk_per_day": 0,
    }
    point = {
        "params": point_params,
        "stats": payload["run_result"]["stats"],
        "candidate_count": 6,
        "skipped_count": 1,
        "fill_rate": 0.4,
        "max_concurrent_positions": 2,
        "annual_trades": 24.0,
        "score": 81.0,
        "point_score": 79.0,
        "local_score": 78.0,
        "plateau_score": 77.0,
        "passes_hard_filters": True,
        "cache_hit": True,
        "detail_key": detail_key,
        "error": None,
    }
    plateau_result = {
        "base_payload": payload["run_request"],
        "total_combinations": 1,
        "evaluated_combinations": 1,
        "points": [point],
        "best_point": point,
        "recommended_point": point,
        "peak_point": point,
        "regions": [],
        "correlations": [],
        "generated_at": "2025-01-31 10:00:00",
        "notes": ["mock plateau"],
    }
    detail = {
        "task_id": "plateau_task_src_001",
        "detail_key": detail_key,
        "saved_at": "2025-01-31 10:05:00",
        "params": point_params,
        "run_request": {
            **payload["run_request"],
            "window_days": 40,
            "min_score": 58.0,
            "stop_loss": 0.04,
            "take_profit": 0.18,
            "trailing_stop_pct": 0.03,
            "max_positions": 4,
            "position_pct": 0.25,
            "max_symbols": 120,
            "priority_topk_per_day": 0,
        },
        "run_result": {
            **payload["run_result"],
            "notes": ["plateau detail packaged"],
        },
    }
    payload["plateau_result"] = plateau_result
    payload["plateau_task_id"] = detail["task_id"]
    return payload, detail


def test_backtest_report_build_import_and_manage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_REPORT_STORE_DIR", str(tmp_path / "backtest-reports"))

    report_id = "bt_report_test_001"
    build_resp = client.post("/api/backtest/reports/build", json=_build_backtest_report_build_payload(report_id))
    assert build_resp.status_code == 200
    build_body = build_resp.json()
    assert build_body["report_id"] == report_id
    assert build_body["manifest"]["schema_version"] == "ftbt-1.0"
    assert any(item["path"] == "report.xlsx" for item in build_body["manifest"]["files"])

    package_bytes = base64.b64decode(build_body["file_base64"])
    import_resp = client.post(
        "/api/backtest/reports/import",
        files={"file": (f"{report_id}.ftbt", package_bytes, "application/octet-stream")},
    )
    assert import_resp.status_code == 200
    import_body = import_resp.json()
    assert import_body["summary"]["report_id"] == report_id

    list_resp = client.get("/api/backtest/reports")
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert any(item["report_id"] == report_id for item in list_body["items"])

    detail_resp = client.get(f"/api/backtest/reports/{report_id}")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    assert detail_body["run_request"]["mode"] == "full_market"
    assert detail_body["run_result"]["stats"]["trade_count"] == 2

    import_resp2 = client.post(
        "/api/backtest/reports/import",
        files={"file": (f"{report_id}.ftbt", package_bytes, "application/octet-stream")},
    )
    assert import_resp2.status_code == 200
    import_body2 = import_resp2.json()
    assert import_body2["summary"]["first_imported_at"] == import_body["summary"]["first_imported_at"]

    delete_resp = client.delete(f"/api/backtest/reports/{report_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    missing_resp = client.get(f"/api/backtest/reports/{report_id}")
    assert missing_resp.status_code == 404
    assert missing_resp.json()["code"] == "BACKTEST_REPORT_NOT_FOUND"


def test_backtest_report_import_recovers_effective_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_REPORT_STORE_DIR", str(tmp_path / "backtest-reports"))

    report_id = "bt_report_shared_runid_001"
    payload = _build_backtest_report_build_payload(report_id)
    payload["run_request"] = {
        **payload["run_request"],
        "mode": "trend_pool",
        "run_id": "",
        "board_filters": ["main", "gem", "star"],
    }
    payload["run_result"] = {
        **payload["run_result"],
        "effective_run_request": {
            **payload["run_request"],
            "run_id": "shared-run-20260301",
            "board_filters": ["main", "gem", "star"],
        },
    }

    build_resp = client.post("/api/backtest/reports/build", json=payload)
    assert build_resp.status_code == 200
    package_bytes = base64.b64decode(build_resp.json()["file_base64"])

    with zipfile.ZipFile(io.BytesIO(package_bytes), mode="r") as archive:
        exported_run_request = json.loads(archive.read("run_request.json").decode("utf-8"))
    assert exported_run_request["run_id"] == "shared-run-20260301"

    legacy_buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package_bytes), mode="r") as source_archive:
        archive_entries = {
            name: source_archive.read(name)
            for name in source_archive.namelist()
        }
        legacy_run_request = {
            **exported_run_request,
            "run_id": "",
        }
        legacy_run_request_bytes = json.dumps(legacy_run_request, ensure_ascii=False).encode("utf-8")
        archive_entries["run_request.json"] = legacy_run_request_bytes
        manifest_payload = json.loads(archive_entries["manifest.json"].decode("utf-8"))
        for row in manifest_payload["files"]:
            if row["path"] != "run_request.json":
                continue
            row["bytes"] = len(legacy_run_request_bytes)
            row["sha256"] = hashlib.sha256(legacy_run_request_bytes).hexdigest()
        archive_entries["manifest.json"] = json.dumps(manifest_payload, ensure_ascii=False).encode("utf-8")
        with zipfile.ZipFile(legacy_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as legacy_archive:
            for name, content in archive_entries.items():
                legacy_archive.writestr(name, content)
    legacy_package_bytes = legacy_buffer.getvalue()

    import_resp = client.post(
        "/api/backtest/reports/import",
        files={"file": (f"{report_id}.ftbt", legacy_package_bytes, "application/octet-stream")},
    )
    assert import_resp.status_code == 200

    detail_resp = client.get(f"/api/backtest/reports/{report_id}")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    assert detail_body["run_request"]["mode"] == "trend_pool"
    assert detail_body["run_request"]["run_id"] == "shared-run-20260301"
    assert detail_body["run_result"]["effective_run_request"]["run_id"] == "shared-run-20260301"


def test_backtest_report_import_restores_plateau_point_details(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_REPORT_STORE_DIR", str(tmp_path / "backtest-reports"))
    monkeypatch.setenv("TDX_TREND_BACKTEST_PLATEAU_DETAIL_STORE_DIR", str(tmp_path / "backtest-plateau-details"))

    report_id = "bt_report_plateau_detail_001"
    payload, detail_payload = _build_backtest_report_plateau_fixture(report_id)
    store._persist_backtest_plateau_point_detail(
        task_id=str(detail_payload["task_id"]),
        detail_key=str(detail_payload["detail_key"]),
        params=BacktestPlateauParams(**dict(detail_payload["params"])),
        run_request=BacktestRunRequest(**dict(detail_payload["run_request"])),
        run_result=BacktestResponse(**dict(detail_payload["run_result"])),
    )

    build_resp = client.post("/api/backtest/reports/build", json=payload)
    assert build_resp.status_code == 200
    build_body = build_resp.json()
    detail_file_name = f"plateau_point_detail__{detail_payload['detail_key']}.json"
    assert any(item["path"] == detail_file_name for item in build_body["manifest"]["files"])

    package_bytes = base64.b64decode(build_body["file_base64"])
    with zipfile.ZipFile(io.BytesIO(package_bytes), mode="r") as archive:
        packaged_detail = json.loads(archive.read(detail_file_name).decode("utf-8"))
    assert packaged_detail["detail_key"] == detail_payload["detail_key"]
    assert packaged_detail["run_result"]["notes"] == ["plateau detail packaged"]

    import_resp = client.post(
        "/api/backtest/reports/import",
        files={"file": (f"{report_id}.ftbt", package_bytes, "application/octet-stream")},
    )
    assert import_resp.status_code == 200

    imported_task_id = store._build_imported_local_task_id(report_id, "plateau")
    detail_resp = client.get(
        f"/api/backtest/plateau/tasks/{imported_task_id}/points/{detail_payload['detail_key']}"
    )
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    assert detail_body["task_id"] == imported_task_id
    assert detail_body["detail_key"] == detail_payload["detail_key"]
    assert detail_body["run_result"]["notes"] == ["plateau detail packaged"]

    delete_resp = client.delete(f"/api/backtest/reports/{report_id}")
    assert delete_resp.status_code == 200

    detail_resp_after = client.get(
        f"/api/backtest/plateau/tasks/{imported_task_id}/points/{detail_payload['detail_key']}"
    )
    assert detail_resp_after.status_code == 404
    assert detail_resp_after.json()["code"] == "BACKTEST_PLATEAU_POINT_DETAIL_NOT_FOUND"
