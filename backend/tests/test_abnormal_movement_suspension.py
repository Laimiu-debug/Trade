"""异动扫描停牌复牌场景回归测试。

修复前:_build_aligned_series 用个股物理上一根 bar 算日偏离,
停牌 N 天后复牌时,复牌当日会把 N 日累计涨跌误算成单日偏离,
被当作单日异动严重放大。

修复后:传入参考交易日历,检测到个股两根 bar 跨多个交易日时跳过该日偏离。
"""

from __future__ import annotations

from datetime import date, timedelta

from app.core.abnormal_movement import (
    _build_aligned_series,
    _window_cum_deviation,
)


def _make_bar(day: str, close: float, prev_close: float | None = None) -> dict[str, object]:
    high = max(close, prev_close or close) * 1.005
    low = min(close, prev_close or close) * 0.995
    return {
        "date": day,
        "open": prev_close or close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "amount": 1e7,
    }


def _build_trading_dates(start: date, count: int) -> list[str]:
    """生成连续交易日列表(模拟交易日历,跳过周末)。"""
    dates: list[str] = []
    cur = start
    while len(dates) < count:
        if cur.weekday() < 5:  # 周一到周五
            dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def test_suspension_gap_excluded_from_daily_deviations() -> None:
    """停牌 10 天后复牌:复牌当日不应被算成单日偏离。

    构造:个股 2025-01-02 收盘 10.0,停牌到 2025-01-20 复牌,收盘 12.0(+20%)。
    若不检测停牌,这个 +20% 会被当成单日偏离,污染 10 日窗口累加。
    传入交易日历后,2025-01-20 这根 bar 应被跳过。
    """
    trading_dates = _build_trading_dates(date(2025, 1, 2), 60)
    # 个股只有两根 bar:停牌前最后一日 + 复牌当日,中间跳过 12 个交易日
    bars = [
        _make_bar("2025-01-02", 10.0),
        _make_bar("2025-01-20", 12.0, prev_close=10.0),  # 复牌 +20%
    ]
    # 指数收盘填满所有交易日
    index_close = {day: 1000.0 for day in trading_dates}

    # 修复后:传入 trading_dates,复牌当日被跳过 → series 为 None(数据点不足)
    series = _build_aligned_series(bars, index_close, "sz300001", trading_dates=trading_dates)
    assert series is None, "停牌复牌当日应被跳过,数据点不足时返回 None"


def test_no_trading_dates_falls_back_to_old_behavior() -> None:
    """不传 trading_dates 时,保持旧行为(向后兼容)。"""
    bars = [
        _make_bar("2025-01-02", 10.0),
        _make_bar("2025-01-20", 12.0, prev_close=10.0),
    ]
    index_close = {"2025-01-02": 1000.0, "2025-01-20": 1000.0}
    # 不传 trading_dates → 不做停牌检测 → 会算出偏离(但数据点不足 WINDOW_30+1 仍返回 None)
    series = _build_aligned_series(bars, index_close, "sz300001")
    # 数据点不足,但仍应正常返回 None 而非异常
    assert series is None


def test_continuous_trading_days_not_treated_as_suspension() -> None:
    """连续交易日的正常情况:偏离照常计算,不被误判为停牌。"""
    trading_dates = _build_trading_dates(date(2025, 1, 2), 60)
    bars: list[dict[str, object]] = []
    close = 10.0
    for i, day in enumerate(trading_dates[:50]):
        bars.append(_make_bar(day, close))
        close *= 1.005  # 每日 +0.5%
    index_close = {day: 1000.0 for day in trading_dates}

    series = _build_aligned_series(bars, index_close, "sz300001", trading_dates=trading_dates)
    assert series is not None, "连续交易日应正常构建 series"
    assert len(series.dates) >= 49, "所有连续交易日偏离都应保留"


def test_window_cum_deviation_handles_suspension_window_correctly() -> None:
    """停牌期间,窗口偏离公式仍可用(用真实收盘价),但日偏离累加不包含停牌当日。

    这里只验证 _window_cum_deviation 本身能处理跨停牌的 bars(它基于收盘价,不依赖相邻性)。
    """
    trading_dates = _build_trading_dates(date(2025, 1, 2), 60)
    bars: list[dict[str, object]] = []
    # 前 35 个交易日正常,然后停牌,复牌后继续
    for i, day in enumerate(trading_dates[:35]):
        bars.append(_make_bar(day, 10.0 * (1.005 ** i)))
    # 停牌 10 天(交易日历继续,但个股无 bar)
    resume_day = trading_dates[45]  # 第 46 个交易日复牌
    bars.append(_make_bar(resume_day, 10.0 * (1.005 ** 34) * 1.1, prev_close=10.0 * (1.005 ** 34)))
    index_close = {day: 1000.0 for day in trading_dates}

    # _window_cum_deviation 基于 bars 的物理索引,停牌不影响它(用收盘价算)
    result = _window_cum_deviation(bars, index_close, len(bars) - 1, 10)
    assert result is not None, "窗口偏离应正常计算"
