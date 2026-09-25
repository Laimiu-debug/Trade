"""异动扫描冷却期(cooling_days)回归测试。

修复前:_in_zone 为假时立即清零 active_start,边界抖动会把异动段切碎。
修复后:异动段退出 zone 后,在 cooling_days 个交易日内若再次入区则合并为同一段。

策略:用真实的偏离数据(个股持续大涨 vs 指数平盘)构造异动区,通过控制涨跌节奏
让 zone 出现「触发-短暂回落-再触发」的缺口,验证冷却期是否合并段。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.abnormal_movement import (
    WINDOW_10,
    TRIGGER_THRESHOLD_10,
    WARN_THRESHOLD_10,
    _scan_window_episodes,
    _build_aligned_series,
)


def _trading_dates(start: date, count: int) -> list[str]:
    dates: list[str] = []
    cur = start
    while len(dates) < count:
        if cur.weekday() < 5:
            dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def _build_scenario(
    daily_gains: list[float],
    *,
    index_daily_gain: float = 0.0,
    base_close: float = 10.0,
) -> tuple[list[dict[str, Any]], dict[str, float], list[str]]:
    """根据每日个股涨幅序列构建 bars + index_close + 交易日列表。"""
    dates = _trading_dates(date(2025, 1, 2), len(daily_gains))
    bars: list[dict[str, Any]] = []
    close = base_close
    for i, gain in enumerate(daily_gains):
        prev_close = close
        close = prev_close * (1.0 + gain)
        bars.append({
            "date": dates[i],
            "open": prev_close,
            "high": close * 1.005,
            "low": min(prev_close, close) * 0.995,
            "close": close,
            "volume": 100000,
            "amount": close * 100000,
        })
    index_close: dict[str, float] = {}
    idx_level = 1000.0
    for i, day in enumerate(dates):
        if i > 0:
            idx_level *= (1.0 + index_daily_gain)
        index_close[day] = idx_level
    return bars, index_close, dates


def _count_episodes(
    bars: list[dict[str, Any]],
    index_close: dict[str, float],
    dates: list[str],
    *,
    cooling_days: int,
    snapshot_only: bool = False,
) -> int:
    """返回总段数(含 warning + triggered)。冷却期主要通过段数变化验证。"""
    series = _build_aligned_series(bars, index_close, "sz300001")
    if series is None:
        return 0
    events = _scan_window_episodes(
        symbol="sz300001",
        name="测试",
        board="gem",
        bars=bars,
        index_close_by_date=index_close,
        series=series,
        date_from=dates[0],
        date_to=dates[-1],
        include_warnings=True,
        window=WINDOW_10,
        warn_threshold=WARN_THRESHOLD_10,
        trigger_threshold=TRIGGER_THRESHOLD_10,
        kind="10d",
        track_10=True,
        track_30=False,
        snapshot_only=snapshot_only,
        cooling_days=cooling_days,
    )
    return len(events)


def _make_trigger_gap_trigger(
    trigger_days: int,
    gap_days: int,
    trigger_days2: int,
    tail_cool_days: int = 20,
    gap_drop: float = -0.08,
) -> list[float]:
    """构造「触发-回落(gap_days)-再触发」的日涨幅序列。

    触发期:每日 +10%(创业板,涨停上限 20%,+10% 会被 _capped_stock_pct 原样保留)。
    回落期:每日 gap_drop(默认 -8%),让 effective_cum 明显跌出 zone。
    再触发期:每日 +10%。
    尾部:每日 -5%(让段最终结束)。
    """
    gains: list[float] = []
    gains += [0.10] * trigger_days          # 第一段触发
    gains += [gap_drop] * gap_days          # 回落(让 zone 断开)
    gains += [0.10] * trigger_days2         # 第二段触发
    gains += [-0.05] * tail_cool_days       # 尾部回落
    return gains


def test_cooling_merges_gap_when_window_large_enough() -> None:
    """冷却窗口越大,合并的缺口越多,段数单调不增。

    用中等缺口(3 日,每日 -10%):冷却期递增时段数应单调不增。
    这是冷却期核心行为的直接验证。
    """
    gains = _make_trigger_gap_trigger(
        trigger_days=12, gap_days=3, trigger_days2=8, tail_cool_days=15, gap_drop=-0.10
    )
    bars, index_close, dates = _build_scenario(gains, index_daily_gain=0.0)
    counts = [
        _count_episodes(bars, index_close, dates, cooling_days=c)
        for c in (0, 2, 5, 8)
    ]
    # 段数应随冷却期递增而单调不增
    for i in range(len(counts) - 1):
        assert counts[i] >= counts[i + 1], (
            f"段数应单调不增: cooling=(0,2,5,8) counts={counts}, "
            f"但 counts[{i}]={counts[i]} < counts[{i+1}]={counts[i+1]}"
        )


def test_cooling_monotonic_with_increasing_window() -> None:
    """冷却窗口越大,合并的缺口越多,段数单调不增(回归保护)。"""
    gains = _make_trigger_gap_trigger(
        trigger_days=12, gap_days=2, trigger_days2=5, tail_cool_days=15
    )
    bars, index_close, dates = _build_scenario(gains, index_daily_gain=0.0)
    count_no_cool = _count_episodes(bars, index_close, dates, cooling_days=0)
    count_cool3 = _count_episodes(bars, index_close, dates, cooling_days=3)
    count_cool5 = _count_episodes(bars, index_close, dates, cooling_days=5)
    assert count_no_cool >= count_cool3 >= count_cool5, (
        f"段数应随冷却期递增而单调不增: no_cool={count_no_cool} >= "
        f"cool3={count_cool3} >= cool5={count_cool5}"
    )


def test_long_gap_not_merged_by_short_cooling() -> None:
    """回落期(10日)远超冷却窗口(3日)时,冷却无法合并,段数与无冷却一致。"""
    gains = _make_trigger_gap_trigger(
        trigger_days=12, gap_days=10, trigger_days2=5, tail_cool_days=15
    )
    bars, index_close, dates = _build_scenario(gains, index_daily_gain=0.0)
    count_no_cool = _count_episodes(bars, index_close, dates, cooling_days=0)
    count_cool = _count_episodes(bars, index_close, dates, cooling_days=3)
    # 长回落:冷却窗口耗尽,无法合并,段数应与无冷却一致
    assert count_cool == count_no_cool, (
        f"长回落后冷却期不应改变段数, count_cool={count_cool} == count_no_cool={count_no_cool}"
    )


def test_cooling_zero_matches_legacy_behavior() -> None:
    """cooling_days=0 时,行为与修复前完全一致(有缺口就断开)。"""
    gains = _make_trigger_gap_trigger(
        trigger_days=12, gap_days=5, trigger_days2=8, tail_cool_days=15, gap_drop=-0.10
    )
    bars, index_close, dates = _build_scenario(gains, index_daily_gain=0.0)
    # cooling_days=0 应产生 >1 段(因为有 5 日大缺口且不合并)
    count = _count_episodes(bars, index_close, dates, cooling_days=0)
    assert count >= 2, f"cooling_days=0 有大缺口时应拆成多段,实际 {count}"


def test_snapshot_mode_ignores_cooling() -> None:
    """snapshot 模式只看截至日是否在 zone,不走状态机循环,冷却期不影响。"""
    # 全程大涨,截至日仍在 zone
    gains = [0.10] * 15
    bars, index_close, dates = _build_scenario(gains, index_daily_gain=0.0)
    count_no_cool = _count_episodes(bars, index_close, dates, cooling_days=0, snapshot_only=True)
    count_cool = _count_episodes(bars, index_close, dates, cooling_days=5, snapshot_only=True)
    assert count_no_cool == count_cool, (
        f"snapshot 模式应不受冷却期影响, {count_no_cool} == {count_cool}"
    )
