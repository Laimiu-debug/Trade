from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import ScreenerParams, ScreenerResult
from app.store import InMemoryStore


def _build_row(symbol: str, **overrides: object) -> ScreenerResult:
    payload: dict[str, object] = {
        "symbol": symbol,
        "name": symbol,
        "latest_price": 10.0,
        "day_change": 0.1,
        "day_change_pct": 0.01,
        "score": 75,
        "ret40": 0.28,
        "turnover20": 0.08,
        "amount20": 8e8,
        "amplitude20": 0.05,
        "retrace20": 0.12,
        "pullback_days": 2,
        "ma10_above_ma20_days": 8,
        "ma5_above_ma10_days": 6,
        "price_vs_ma20": 0.05,
        "vol_slope20": 0.08,
        "up_down_volume_ratio": 1.35,
        "pullback_volume_ratio": 0.8,
        "has_blowoff_top": False,
        "has_divergence_5d": False,
        "has_upper_shadow_risk": False,
        "ai_confidence": 0.7,
        "theme_stage": "Unknown",
        "trend_class": "A",
        "stage": "Mid",
        "labels": [],
        "reject_reasons": [],
        "degraded": False,
        "degraded_reason": None,
    }
    payload.update(overrides)
    return ScreenerResult(**payload)


def test_screener_filters_follow_step3_risk_flags() -> None:
    params = ScreenerParams(
        markets=["sh", "sz"],
        mode="strict",
        as_of_date="2025-01-31",
        return_window_days=40,
        top_n=500,
        turnover_threshold=0.05,
        amount_threshold=5e8,
        amplitude_threshold=0.03,
    )
    base = _build_row("sz300750")
    soft_risk = _build_row("sz300751", has_blowoff_top=True)
    hard_risk = _build_row(
        "sz300752",
        has_blowoff_top=True,
        has_divergence_5d=True,
        has_upper_shadow_risk=True,
    )
    step_configs = InMemoryStore._resolve_screener_step_configs(params)

    _step1, _step2, step3, _step4 = InMemoryStore._run_screener_filters_for_backtest(
        [base, soft_risk, hard_risk],
        mode=params.mode,
        step_configs=step_configs,
    )
    symbols = [row.symbol for row in step3]
    assert "sz300750" in symbols
    assert "sz300751" not in symbols
    assert "sz300752" not in symbols


def test_screener_step1_supports_rank_window_before_activity_filters() -> None:
    params = ScreenerParams(
        markets=["sh", "sz"],
        mode="strict",
        as_of_date="2025-01-31",
        return_window_days=40,
        top_n=500,
        turnover_threshold=0.05,
        amount_threshold=5e8,
        amplitude_threshold=0.03,
    )
    resolved = InMemoryStore._resolve_screener_step_configs(params)
    step_configs = resolved.model_copy(
        update={
            "step1": resolved.step1.model_copy(
                update={"rank_start": 2, "top_n": 3}
            )
        }
    )
    rows = [
        _build_row("rank1", ret40=0.90, turnover20=0.01),
        _build_row("rank2", ret40=0.80),
        _build_row("rank3", ret40=0.70),
        _build_row("rank4", ret40=0.60),
    ]

    step1, _step2, _step3, _step4 = InMemoryStore._run_screener_filters_for_backtest(
        rows,
        mode=params.mode,
        step_configs=step_configs,
    )

    assert [row.symbol for row in step1] == ["rank2", "rank3"]
