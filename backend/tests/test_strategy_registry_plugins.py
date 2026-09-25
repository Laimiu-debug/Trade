from __future__ import annotations

from app.core.strategy_registry import StrategyRegistry
from app.models import ScreenerResult, SignalResult


def _build_row(symbol: str, **overrides: object) -> ScreenerResult:
    payload: dict[str, object] = {
        "symbol": symbol,
        "name": symbol,
        "latest_price": 10.0,
        "day_change": 0.1,
        "day_change_pct": 0.01,
        "score": 75,
        "ret40": 0.20,
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
        "theme_stage": "发酵中",
        "trend_class": "A",
        "stage": "Mid",
        "labels": [],
        "reject_reasons": [],
        "degraded": False,
        "degraded_reason": None,
    }
    payload.update(overrides)
    return ScreenerResult(**payload)


def _build_signal(symbol: str, *, health: float, event: float, quality: float = 0.0) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        name=symbol,
        primary_signal="A",
        secondary_signals=[],
        trigger_date="2025-01-10",
        expire_date="2025-01-12",
        trigger_reason="test",
        priority=2,
        entry_quality_score=quality,
        health_score=health,
        event_score=event,
    )


def test_registry_contains_relative_strength_strategy() -> None:
    registry = StrategyRegistry()
    assert registry.get("relative_strength_breakout_v1") is not None
    assert registry.get("score_only_rank_v1") is not None
    assert registry.get("ths_main_force_flip_v1") is not None
    assert registry.get("ths_main_force_golden_cross_v1") is not None
    assert registry.get("ths_force_rhythm_v1") is not None
    assert registry.get("emotion_limit_up_v1") is not None
    assert registry.get("limit_up_arb_v1") is not None
    assert registry.get("wulong_cluster_v1") is not None
    assert registry.get("wulong_cluster_v2") is None


def test_wulong_strategy_defaults_are_loosened() -> None:
    registry = StrategyRegistry()
    v1_descriptor = registry.get("wulong_cluster_v1")
    assert v1_descriptor is not None
    defaults = dict(v1_descriptor.default_params)
    assert defaults["min_ret40"] == 0.06
    assert defaults["max_retrace20"] == 0.18
    assert defaults["min_up_down_volume_ratio"] == 1.08
    assert defaults["min_vol_slope20"] == 0.0
    assert defaults["convergence_threshold_pct"] == 0.04
    assert defaults["min_spread_expansion_multiple"] == 1.6
    assert defaults["min_volume_ratio20"] == 1.2
    assert defaults["min_score"] == 52.0


def test_relative_strength_plugin_filters_signal_universe() -> None:
    registry = StrategyRegistry()
    rows = [
        _build_row("sz300750", ret40=0.35, retrace20=0.10, up_down_volume_ratio=1.60, vol_slope20=0.12),
        _build_row("sz300751", ret40=0.08, retrace20=0.12, up_down_volume_ratio=1.30, vol_slope20=0.10),
    ]
    filtered = registry.build_universe(
        strategy_id="relative_strength_breakout_v1",
        candidates=rows,
        params={
            "min_ret40": 0.12,
            "max_retrace20": 0.22,
            "min_up_down_volume_ratio": 1.15,
            "min_vol_slope20": 0.02,
        },
        mode="signals",
    )
    symbols = [row.symbol for row in filtered]
    assert "sz300750" in symbols
    assert "sz300751" not in symbols


def test_relative_strength_plugin_rank_uses_strength_metrics() -> None:
    registry = StrategyRegistry()
    strong_row = _build_row("sz300750", ret40=0.36, up_down_volume_ratio=1.70, retrace20=0.10)
    weak_row = _build_row("sz300751", ret40=0.14, up_down_volume_ratio=1.12, retrace20=0.22)
    signal = _build_signal("sz300750", health=65.0, event=62.0)
    strong_rank = registry.rank_signals(
        strategy_id="relative_strength_breakout_v1",
        signal=signal,
        row=strong_row,
        params={
            "rank_weight_health": 0.25,
            "rank_weight_event": 0.25,
            "rank_weight_strength": 0.30,
            "rank_weight_volume": 0.10,
            "rank_weight_structure": 0.10,
        },
        fallback_score=63.5,
    )
    weak_rank = registry.rank_signals(
        strategy_id="relative_strength_breakout_v1",
        signal=signal,
        row=weak_row,
        params={
            "rank_weight_health": 0.25,
            "rank_weight_event": 0.25,
            "rank_weight_strength": 0.30,
            "rank_weight_volume": 0.10,
            "rank_weight_structure": 0.10,
        },
        fallback_score=63.5,
    )
    assert strong_rank > weak_rank


def test_score_only_plugin_rank_prefers_entry_quality_score() -> None:
    registry = StrategyRegistry()
    row = _build_row("sz300750")
    strong = _build_signal("sz300750", health=40.0, event=40.0, quality=92.0)
    weak = _build_signal("sz300750", health=90.0, event=90.0, quality=55.0)
    strong_rank = registry.rank_signals(
        strategy_id="score_only_rank_v1",
        signal=strong,
        row=row,
        params={"min_score": 50.0},
        fallback_score=70.0,
    )
    weak_rank = registry.rank_signals(
        strategy_id="score_only_rank_v1",
        signal=weak,
        row=row,
        params={"min_score": 50.0},
        fallback_score=70.0,
    )
    assert strong_rank > weak_rank


def test_resolve_backtest_overrides_does_not_map_trend_king_mode_to_payload() -> None:
    registry = StrategyRegistry()
    overrides = registry.resolve_backtest_overrides(
        "trend_king_rally_v1",
        {"mode": "b", "min_gain_3d_b": 8.0},
    )
    assert "mode" not in overrides
    assert overrides.get("min_gain_3d_b") == 8.0


def test_ths_main_retail_plugins_follow_exact_trigger_flags() -> None:
    registry = StrategyRegistry()
    row = _build_row("sz300750")
    base_snapshot = {
        "ths_main_retail_signal": {
            "purple_to_yellow": True,
            "golden_cross": False,
        }
    }
    assert registry.generate_signals(
        strategy_id="ths_main_force_flip_v1",
        row=row,
        snapshot=base_snapshot,
        params={},
    )
    assert not registry.generate_signals(
        strategy_id="ths_main_force_golden_cross_v1",
        row=row,
        snapshot=base_snapshot,
        params={},
    )

    cross_snapshot = {
        "ths_main_retail_signal": {
            "purple_to_yellow": False,
            "golden_cross": True,
        }
    }
    assert registry.generate_signals(
        strategy_id="ths_main_force_golden_cross_v1",
        row=row,
        snapshot=cross_snapshot,
        params={},
    )
