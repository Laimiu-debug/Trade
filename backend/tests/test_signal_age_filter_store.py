from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CandlePoint, ScreenerResult
from app.store import store


def _build_row(symbol: str) -> ScreenerResult:
    return ScreenerResult(
        symbol=symbol,
        name="测试标的",
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
        labels=[],
        reject_reasons=[],
        degraded=False,
        degraded_reason=None,
    )


def test_store_get_signals_full_market_skips_strategy_universe_prefilter(monkeypatch) -> None:
    row_sh = _build_row("sh600519")
    row_sz = _build_row("sz300750")
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.4, low=10.0, close=10.3, volume=100000, amount=1_000_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row_sh, row_sz], None, None, "2026-01-06"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": ["SOS"],
            "risk_events": [],
            "event_dates": {"SOS": "2026-01-06"},
            "event_chain": [{"event": "SOS", "date": "2026-01-06", "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "trigger_date": "2026-01-06",
            "signal": "SOS",
            "phase": "吸筹D",
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    def fail_build_universe(**kwargs):
        raise AssertionError(f"full_market should skip strategy universe prefilter: {kwargs}")

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(
        store,
        "_ensure_candles",
        lambda raw_symbol: list(candles) if raw_symbol in {"sh600519", "sz300750"} else [],
    )
    monkeypatch.setattr(store._strategy_registry, "build_universe", fail_build_universe)
    store._signals_cache.clear()

    resp = store.get_signals(
        mode="full_market",
        as_of_date="2026-01-06",
        refresh=True,
        min_score=0.0,
        min_event_count=0,
    )

    assert resp.source_count == 2
    assert {item.symbol for item in resp.items} == {"sh600519", "sz300750"}


def test_store_get_signals_supports_signal_age_filter(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.4, low=10.0, close=10.3, volume=100000, amount=1_000_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-06"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": ["SOS"],
            "risk_events": [],
            "event_dates": {"SOS": "2026-01-02"},
            "event_chain": [{"event": "SOS", "date": "2026-01-02", "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "trigger_date": "2026-01-02",
            "signal": "SOS",
            "phase": "吸筹D",
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    matched = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        min_score=0.0,
        min_event_count=0,
        signal_age_min=2,
        signal_age_max=2,
    )
    assert len(matched.items) == 1
    assert matched.items[0].signal_age_days == 2

    filtered = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        min_score=0.0,
        min_event_count=0,
        signal_age_min=3,
    )
    assert filtered.items == []


def test_store_signal_age_uses_trading_day_diff_with_missing_calendar_days(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    # 2026-01-07 / 2026-01-08 缺失，模拟停牌/无交易日
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.4, low=10.0, close=10.3, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-09", open=10.3, high=10.5, low=10.1, close=10.4, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-12", open=10.4, high=10.6, low=10.2, close=10.5, volume=100000, amount=1_000_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-12"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": ["SOS"],
            "risk_events": [],
            "event_dates": {"SOS": "2026-01-06"},
            "event_chain": [{"event": "SOS", "date": "2026-01-06", "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 82.0,
            "trigger_date": "2026-01-06",
            "signal": "SOS",
            "phase": "鍚哥D",
            "phase_hint": "娴嬭瘯淇″彿",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 70.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-12",
        refresh=True,
        min_score=0.0,
        min_event_count=0,
        signal_age_min=2,
        signal_age_max=2,
    )
    assert len(resp.items) == 1
    # trading days: 2026-01-06 -> 2026-01-09 -> 2026-01-12 => age=2
    assert resp.items[0].signal_age_days == 2


def test_store_respects_key_event_confirmation_strategy_override(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.4, low=10.0, close=10.3, volume=100000, amount=1_000_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-06"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": ["SOS"],
            "risk_events": [],
            "event_dates": {"SOS": "2026-01-02"},
            "event_chain": [{"event": "SOS", "date": "2026-01-02", "category": "accumulation"}],
            "sequence_ok": True,
            "entry_quality_score": 85.0,
            "trigger_date": "2026-01-02",
            "signal": "SOS",
            "phase": "吸筹D",
            "phase_hint": "测试信号",
            "structure_hhh": "HH|HL|HC",
            "event_strength_score": 76.0,
            "event_score": 72.0,
            "event_grade": "A",
            "health_score": 78.0,
            "phase_score": 72.0,
            "structure_score": 68.0,
            "trend_score": 66.0,
            "volatility_score": 64.0,
            "event_confirmation_map": {"SOS": "pending"},
            "confirmation_status": "partial",
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    # v2 default requires key-event confirmation.
    strict_resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        strategy_id="wyckoff_trend_v2",
        min_score=0.0,
        min_event_count=0,
    )
    assert strict_resp.items == []

    # v1 keeps legacy behavior and does not enforce key-event confirmation by default.
    legacy_resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        strategy_id="wyckoff_trend_v1",
        min_score=0.0,
        min_event_count=0,
    )
    assert len(legacy_resp.items) == 1


def test_store_supports_wulong_strategy_custom_signal_context(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=120000, amount=1_200_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.8, low=10.1, close=10.7, volume=260000, amount=2_600_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-06"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": [],
            "risk_events": [],
            "event_dates": {},
            "event_chain": [],
            "sequence_ok": False,
            "entry_quality_score": 18.0,
            "trigger_date": "2026-01-06",
            "signal": "",
            "phase": "阶段未明",
            "phase_hint": "测试快照",
            "structure_hhh": "-",
            "event_strength_score": 12.0,
            "phase_score": 10.0,
            "structure_score": 8.0,
            "trend_score": 6.0,
            "volatility_score": 4.0,
            "wulong_cluster_signal": {
                "has_data": True,
                "trigger_date": "2026-01-06",
                "bullish_alignment": True,
                "close_above_all_mas": True,
                "rising_ma_count": 5,
                "current_spread_pct": 0.02,
                "convergence_spread_pct": 0.008,
                "pre_convergence_max_spread_pct": 0.05,
                "convergence_offset_days": 3,
                "spread_expansion_multiple": 2.5,
                "volume_ratio_20": 1.7,
                "breakout_ratio_pct": 0.02,
                "daily_return_pct": 0.03,
                "ma_values": {
                    "ma5": 10.3,
                    "ma10": 10.2,
                    "ma20": 10.1,
                    "ma30": 10.0,
                    "ma60": 9.9,
                },
            },
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        strategy_id="wulong_cluster_v1",
        min_score=50.0,
        min_event_count=0,
    )

    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.wyckoff_signal == "五龙聚首"
    assert item.entry_quality_score >= 50.0
    assert "五龙聚首" in item.trigger_reason


def _legacy_removed_wulong_v2_strategy_custom_signal_context(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=120000, amount=1_200_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.8, low=10.1, close=10.7, volume=260000, amount=2_600_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-06"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": [],
            "risk_events": [],
            "event_dates": {},
            "event_chain": [],
            "sequence_ok": False,
            "entry_quality_score": 18.0,
            "trigger_date": "2026-01-06",
            "signal": "",
            "phase": "闃舵鏈槑",
            "phase_hint": "娴嬭瘯蹇収",
            "structure_hhh": "-",
            "event_strength_score": 12.0,
            "phase_score": 10.0,
            "structure_score": 8.0,
            "trend_score": 6.0,
            "volatility_score": 4.0,
            "wulong_cluster_signal": {
                "has_data": True,
                "trigger_date": "2026-01-06",
                "bullish_alignment": True,
                "close_above_all_mas": True,
                "rising_ma_count": 5,
                "current_spread_pct": 0.02,
                "convergence_spread_pct": 0.008,
                "pre_convergence_max_spread_pct": 0.05,
                "convergence_offset_days": 3,
                "spread_expansion_multiple": 2.5,
                "volume_ratio_20": 1.7,
                "breakout_ratio_pct": 0.02,
                "daily_return_pct": 0.03,
                "ma_values": {
                    "ma5": 10.3,
                    "ma10": 10.2,
                    "ma20": 10.1,
                    "ma30": 10.0,
                    "ma60": 9.9,
                },
            },
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        strategy_id="wulong_cluster_v2",
        min_score=50.0,
        min_event_count=0,
    )

    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.wyckoff_signal == "浜旈緳鑱氶"
    assert item.entry_quality_score >= 50.0
    assert "浜旈緳鑱氶" in item.trigger_reason


def _legacy_removed_wulong_v2_strategy_custom_signal_context_2(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.1, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-05", open=10.1, high=10.3, low=9.9, close=10.2, volume=120000, amount=1_200_000.0),
        CandlePoint(time="2026-01-06", open=10.2, high=10.8, low=10.1, close=10.7, volume=260000, amount=2_600_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-06"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": [],
            "risk_events": [],
            "event_dates": {},
            "event_chain": [],
            "sequence_ok": False,
            "entry_quality_score": 18.0,
            "trigger_date": "2026-01-06",
            "signal": "",
            "phase": "phase-test",
            "phase_hint": "snapshot-test",
            "structure_hhh": "-",
            "event_strength_score": 12.0,
            "phase_score": 10.0,
            "structure_score": 8.0,
            "trend_score": 6.0,
            "volatility_score": 4.0,
            "wulong_cluster_signal": {
                "has_data": True,
                "trigger_date": "2026-01-06",
                "bullish_alignment": True,
                "close_above_all_mas": True,
                "rising_ma_count": 5,
                "current_spread_pct": 0.02,
                "convergence_spread_pct": 0.008,
                "pre_convergence_max_spread_pct": 0.05,
                "convergence_offset_days": 3,
                "spread_expansion_multiple": 2.5,
                "volume_ratio_20": 1.7,
                "breakout_ratio_pct": 0.02,
                "daily_return_pct": 0.03,
                "ma_values": {
                    "ma5": 10.3,
                    "ma10": 10.2,
                    "ma20": 10.1,
                    "ma30": 10.0,
                    "ma60": 9.9,
                },
            },
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-06",
        refresh=True,
        strategy_id="wulong_cluster_v2",
        min_score=50.0,
        min_event_count=0,
    )

    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.wyckoff_signal == "五龙聚首"
    assert item.entry_quality_score >= 50.0
    assert "五龙聚首" in item.trigger_reason


def test_store_signals_runtime_cache_prunes_stale_and_overflow(monkeypatch) -> None:
    monkeypatch.setenv("TDX_TREND_SIGNALS_RUNTIME_CACHE_TTL_SEC", "5")
    monkeypatch.setenv("TDX_TREND_SIGNALS_RUNTIME_CACHE_MAX_ITEMS", "2")

    oldest = object()
    newer = object()
    newest = object()
    latest = object()

    store._signals_cache.clear()
    store._signals_cache["oldest"] = (1.0, oldest)
    store._signals_cache["newer"] = (6.0, newer)
    store._signals_cache["newest"] = (9.0, newest)

    assert store._load_signals_runtime_cache("oldest", now_ts=10.0) is None
    assert set(store._signals_cache.keys()) == {"newer", "newest"}

    store._save_signals_runtime_cache("latest", latest, now_ts=11.0)
    assert set(store._signals_cache.keys()) == {"newest", "latest"}
    assert store._signals_cache["latest"][1] is latest
    store._signals_cache.clear()


def test_store_builds_custom_ths_force_signal(monkeypatch) -> None:
    symbol = "sz300750"
    row = _build_row(symbol)
    candles = [
        CandlePoint(time="2026-01-02", open=10.0, high=10.2, low=9.8, close=10.0, volume=100000, amount=1_000_000.0),
        CandlePoint(time="2026-01-03", open=10.0, high=11.1, low=9.9, close=11.0, volume=120000, amount=1_100_000.0),
        CandlePoint(time="2026-01-06", open=11.0, high=12.1, low=10.9, close=12.0, volume=120000, amount=1_200_000.0),
        CandlePoint(time="2026-01-07", open=12.0, high=12.0, low=10.8, close=11.0, volume=120000, amount=1_100_000.0),
        CandlePoint(time="2026-01-08", open=11.0, high=11.0, low=9.8, close=10.0, volume=120000, amount=1_000_000.0),
        CandlePoint(time="2026-01-09", open=10.0, high=11.2, low=9.9, close=11.0, volume=200000, amount=1_150_000.0),
    ]

    def fake_resolve_signal_candidates(
        *,
        mode,
        run_id,
        trend_step="auto",
        as_of_date=None,
    ):
        _ = (mode, run_id, trend_step, as_of_date)
        return [row], None, "mock-run", "2026-01-09"

    def fake_snapshot(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "events": [],
            "risk_events": [],
            "event_dates": {},
            "event_chain": [],
            "sequence_ok": True,
            "entry_quality_score": 10.0,
            "trigger_date": "2026-01-03",
            "signal": "",
            "phase": "阶段未明",
            "phase_hint": "测试快照",
            "structure_hhh": "-",
            "event_strength_score": 10.0,
            "phase_score": 10.0,
            "structure_score": 10.0,
            "trend_score": 10.0,
            "volatility_score": 10.0,
            "ths_main_retail_signal": {
                "trigger_date": "2026-01-09",
                "purple_to_yellow": True,
                "golden_cross": False,
                "signal_score": 80.0,
                "signal_name": "紫转黄",
            },
        }

    monkeypatch.setattr(store, "_resolve_signal_candidates", fake_resolve_signal_candidates)
    monkeypatch.setattr(store, "_calc_wyckoff_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_ensure_candles", lambda raw_symbol: list(candles) if raw_symbol == symbol else [])
    store._signals_cache.clear()

    resp = store.get_signals(
        mode="trend_pool",
        run_id="mock-run",
        as_of_date="2026-01-09",
        refresh=True,
        strategy_id="ths_main_force_flip_v1",
        min_score=0.0,
        min_event_count=0,
    )
    assert len(resp.items) == 1
    assert resp.items[0].trigger_date == "2026-01-09"
    assert resp.items[0].wyckoff_signal == "紫转黄"
    assert resp.items[0].wyckoff_phase == "主散量能"
    assert resp.items[0].entry_quality_score == 80.0
    assert "主力由紫转黄" in resp.items[0].trigger_reason
