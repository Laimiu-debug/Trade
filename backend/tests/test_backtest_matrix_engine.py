from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.backtest_matrix_engine import BacktestMatrixEngine, MatrixBundle
from app.core.backtest_signal_matrix import compute_backtest_signal_matrix
from app.models import CandlePoint


def _candle(day: str, close: float, volume: int = 1000) -> CandlePoint:
    return CandlePoint(
        time=day,
        open=close * 0.99,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=volume,
        amount=float(close * volume),
        price_source='vwap',
    )


def test_matrix_bundle_shape_and_valid_mask(tmp_path: Path) -> None:
    engine = BacktestMatrixEngine(cache_dir=tmp_path)

    candles_map = {
        'sh600000': [
            _candle('2025-12-29', 10.0),
            _candle('2025-12-30', 10.2),
            _candle('2025-12-31', 10.4),
            _candle('2026-01-02', 10.6),
        ],
        'sz000001': [
            _candle('2025-12-30', 8.0),
            _candle('2025-12-31', 8.1),
            _candle('2026-01-02', 8.2),
        ],
    }

    def _get_candles(symbol: str):
        return candles_map.get(symbol, [])

    key = engine.build_cache_key(
        symbols=['sh600000', 'sz000001'],
        date_from='2025-12-31',
        date_to='2026-01-02',
        data_version='tdx_only|bars=500',
        window_set=(10, 20, 60),
        algo_version='matrix-v1',
    )
    bundle, cache_hit = engine.build_bundle(
        symbols=['sh600000', 'sz000001'],
        get_candles=_get_candles,
        date_from='2025-12-31',
        date_to='2026-01-02',
        max_lookback_days=60,
        cache_key=key,
        use_cache=True,
    )

    assert cache_hit is False
    assert bundle.shape() == (4, 2)
    # sh600000 在 2025-12-29 有值；sz000001 在该日缺失。
    assert bool(bundle.valid_mask[0, 0]) is True
    assert bool(bundle.valid_mask[0, 1]) is False


def test_matrix_cache_hit_roundtrip(tmp_path: Path) -> None:
    engine = BacktestMatrixEngine(cache_dir=tmp_path)

    candles_map = {
        'sh600000': [_candle('2026-01-02', 10.0), _candle('2026-01-03', 10.1)],
    }

    def _get_candles(symbol: str):
        return candles_map.get(symbol, [])

    key = engine.build_cache_key(
        symbols=['sh600000'],
        date_from='2026-01-02',
        date_to='2026-01-03',
        data_version='tdx_only|bars=500',
        window_set=(10, 20, 60),
        algo_version='matrix-v1',
    )

    first_bundle, first_hit = engine.build_bundle(
        symbols=['sh600000'],
        get_candles=_get_candles,
        date_from='2026-01-02',
        date_to='2026-01-03',
        max_lookback_days=60,
        cache_key=key,
        use_cache=True,
    )
    second_bundle, second_hit = engine.build_bundle(
        symbols=['sh600000'],
        get_candles=_get_candles,
        date_from='2026-01-02',
        date_to='2026-01-03',
        max_lookback_days=60,
        cache_key=key,
        use_cache=True,
    )

    assert first_hit is False
    assert second_hit is True
    assert first_bundle.dates == second_bundle.dates
    assert first_bundle.symbols == second_bundle.symbols
    assert np.allclose(first_bundle.close, second_bundle.close, equal_nan=True)


def test_matrix_runtime_cache_hit_without_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE", "1")
    monkeypatch.setenv("TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE_TTL_SEC", "600")
    engine = BacktestMatrixEngine(cache_dir=tmp_path)
    call_counter = {"count": 0}

    candles_map = {
        "sh600000": [_candle("2026-01-02", 10.0), _candle("2026-01-03", 10.1)],
    }

    def _get_candles(symbol: str):
        call_counter["count"] += 1
        return candles_map.get(symbol, [])

    key = engine.build_cache_key(
        symbols=["sh600000"],
        date_from="2026-01-02",
        date_to="2026-01-03",
        data_version="tdx_only|bars=500",
        window_set=(10, 20, 60),
        algo_version="matrix-v1",
    )

    first_bundle, first_hit = engine.build_bundle(
        symbols=["sh600000"],
        get_candles=_get_candles,
        date_from="2026-01-02",
        date_to="2026-01-03",
        max_lookback_days=60,
        cache_key=key,
        use_cache=True,
    )
    assert first_hit is False
    assert call_counter["count"] == 1

    cache_file = tmp_path / f"{key}.npz"
    assert cache_file.exists()
    cache_file.unlink()

    second_bundle, second_hit = engine.build_bundle(
        symbols=["sh600000"],
        get_candles=_get_candles,
        date_from="2026-01-02",
        date_to="2026-01-03",
        max_lookback_days=60,
        cache_key=key,
        use_cache=True,
    )
    assert second_hit is True
    assert call_counter["count"] == 1
    assert first_bundle.dates == second_bundle.dates
    assert np.allclose(first_bundle.close, second_bundle.close, equal_nan=True)


def test_matrix_incremental_append_reuses_base_cache(tmp_path: Path) -> None:
    engine = BacktestMatrixEngine(cache_dir=tmp_path)
    symbols = ["sh600000", "sz000001"]
    windows = (10, 20, 60)
    data_version = "tdx_only|bars=500"

    candles_map = {
        "sh600000": [
            _candle("2025-12-29", 10.0),
            _candle("2025-12-30", 10.1),
            _candle("2025-12-31", 10.2),
            _candle("2026-01-02", 10.3),
            _candle("2026-01-03", 10.4),
            _candle("2026-01-04", 10.5),
            _candle("2026-01-05", 10.6),
        ],
        "sz000001": [
            _candle("2025-12-30", 8.0),
            _candle("2025-12-31", 8.1),
            _candle("2026-01-02", 8.2),
            _candle("2026-01-03", 8.3),
            _candle("2026-01-05", 8.5),
        ],
    }

    def _get_candles(symbol: str):
        return candles_map.get(symbol, [])

    signature = engine.build_incremental_signature(
        symbols=symbols,
        date_from="2026-01-02",
        max_lookback_days=60,
        data_version=data_version,
        window_set=windows,
        algo_version="matrix-v1",
    )
    key_first = engine.build_cache_key(
        symbols=symbols,
        date_from="2026-01-02",
        date_to="2026-01-03",
        data_version=data_version,
        window_set=windows,
        algo_version="matrix-v1",
    )
    first_bundle, first_hit = engine.build_bundle(
        symbols=symbols,
        get_candles=_get_candles,
        date_from="2026-01-02",
        date_to="2026-01-03",
        max_lookback_days=60,
        cache_key=key_first,
        incremental_signature=signature,
        use_cache=True,
    )
    assert first_hit is False

    key_second = engine.build_cache_key(
        symbols=symbols,
        date_from="2026-01-02",
        date_to="2026-01-05",
        data_version=data_version,
        window_set=windows,
        algo_version="matrix-v1",
    )
    second_bundle, second_hit = engine.build_bundle(
        symbols=symbols,
        get_candles=_get_candles,
        date_from="2026-01-02",
        date_to="2026-01-05",
        max_lookback_days=60,
        cache_key=key_second,
        incremental_signature=signature,
        use_cache=True,
    )
    assert second_hit is False
    assert len(second_bundle.dates) >= len(first_bundle.dates)
    assert np.allclose(
        first_bundle.close,
        second_bundle.close[: len(first_bundle.dates), :],
        equal_nan=True,
    )

    meta = engine.get_build_meta(key_second) or {}
    assert str(meta.get("mode") or "") in {"incremental_append", "incremental_reuse"}
    assert str(meta.get("base_cache_key") or "") == key_first
    assert int(meta.get("append_rows") or 0) >= 0


def test_signal_matrix_shape_and_missing_handling() -> None:
    dates = ['2026-01-01', '2026-01-02', '2026-01-03']
    symbols = ['sh600000', 'sz000001']

    close = np.asarray(
        [
            [10.0, 20.0],
            [10.1, np.nan],
            [10.2, 20.2],
        ],
        dtype=np.float64,
    )
    open_ = close * 0.99
    high = close * 1.02
    low = close * 0.98
    volume = np.asarray(
        [
            [1000.0, 2000.0],
            [1100.0, np.nan],
            [1200.0, 2100.0],
        ],
        dtype=np.float64,
    )
    valid_mask = np.asarray(
        [
            [True, True],
            [True, False],
            [True, True],
        ],
        dtype=bool,
    )

    bundle = MatrixBundle(
        dates=dates,
        symbols=symbols,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        valid_mask=valid_mask,
    )
    matrix = compute_backtest_signal_matrix(bundle, top_n=2)

    assert matrix.buy_signal.shape == (3, 2)
    assert matrix.sell_signal.shape == (3, 2)
    assert matrix.score.shape == (3, 2)
    assert bool(matrix.buy_signal[1, 1]) is False
    assert bool(matrix.sell_signal[1, 1]) is False
    assert float(matrix.score[1, 1]) == 0.0


def test_signal_matrix_accepts_readonly_arrays() -> None:
    dates = ['2026-01-01', '2026-01-02', '2026-01-03']
    symbols = ['sh600000']

    close = np.asarray([[10.0], [10.1], [10.2]], dtype=np.float64)
    open_ = close * 0.99
    high = close * 1.02
    low = close * 0.98
    volume = np.asarray([[1000.0], [1100.0], [1200.0]], dtype=np.float64)
    valid_mask = np.asarray([[True], [True], [True]], dtype=bool)

    for arr in (open_, high, low, close, volume, valid_mask):
        arr.setflags(write=False)

    bundle = MatrixBundle(
        dates=dates,
        symbols=symbols,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        valid_mask=valid_mask,
    )
    matrix = compute_backtest_signal_matrix(bundle, top_n=1)
    assert matrix.buy_signal.shape == (3, 1)
    assert matrix.sell_signal.shape == (3, 1)


def test_matrix_bundle_build_honors_control_callback(tmp_path: Path) -> None:
    engine = BacktestMatrixEngine(cache_dir=tmp_path)

    def _get_candles(symbol: str):
        _ = symbol
        return [_candle("2026-01-02", 10.0)]

    key = engine.build_cache_key(
        symbols=["sh600000"],
        date_from="2026-01-02",
        date_to="2026-01-02",
        data_version="tdx_only|bars=500",
        window_set=(10, 20, 60),
        algo_version="matrix-v1",
    )

    with pytest.raises(RuntimeError, match="stop matrix build"):
        engine.build_bundle(
            symbols=["sh600000"],
            get_candles=_get_candles,
            date_from="2026-01-02",
            date_to="2026-01-02",
            max_lookback_days=60,
            cache_key=key,
            use_cache=False,
            control_callback=lambda: (_ for _ in ()).throw(RuntimeError("stop matrix build")),
        )


def test_signal_matrix_honors_control_callback() -> None:
    bundle = MatrixBundle(
        dates=["2026-01-02"],
        symbols=["sh600000"],
        open=np.asarray([[9.9]], dtype=np.float64),
        high=np.asarray([[10.2]], dtype=np.float64),
        low=np.asarray([[9.8]], dtype=np.float64),
        close=np.asarray([[10.0]], dtype=np.float64),
        volume=np.asarray([[1000.0]], dtype=np.float64),
        valid_mask=np.asarray([[True]], dtype=bool),
    )

    with pytest.raises(RuntimeError, match="stop signal matrix"):
        compute_backtest_signal_matrix(
            bundle,
            top_n=1,
            control_callback=lambda: (_ for _ in ()).throw(RuntimeError("stop signal matrix")),
        )
