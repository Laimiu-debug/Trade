from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from trade_app.research.screener_domain import FunnelConfig


class BrowserSessionData(BaseModel):
    csrf_token: str
    expires_in_seconds: int = Field(ge=1)


class BrowserSessionResponse(BaseModel):
    data: BrowserSessionData


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TradeFields(BaseModel):
    trade_date: date
    symbol: str = Field(min_length=2, max_length=24)
    name: str = Field(default="", max_length=80)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=100_000_000)
    price: str
    fee: str = "0"
    fee_mode: Literal['manual', 'auto'] = 'manual'
    note: str = Field(default="", max_length=4000)


class TradeCreate(TradeFields):
    pass


class TradeUpdate(TradeFields):
    expected_revision: int = Field(ge=1)


class PendingTradeConfirm(BaseModel):
    expected_revision: int = Field(ge=1)
    acknowledge_duplicate: bool = False


class PendingTradeBatchItem(PendingTradeConfirm):
    id: str


class PendingTradeBatch(BaseModel):
    items: list[PendingTradeBatchItem] = Field(min_length=1, max_length=500)


class RealFeeConfigUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    config: dict[str, str]


class TargetConfigUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    multiplier: str
    node_count: int = Field(ge=1, le=100)


class InspirationCardCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class DailyInspirationRequest(BaseModel):
    day: date


class RoundNoteSave(BaseModel):
    expected_revision: int = Field(ge=0)
    summary: str = Field(default='', max_length=50_000)


class SimReviewTagCreate(BaseModel):
    type: Literal['emotion', 'reason']
    name: str = Field(min_length=1, max_length=80)


class SimFillTagsSave(BaseModel):
    expected_revision: int = Field(ge=0)
    emotion_tag_id: str | None = None
    reason_tag_ids: list[str] = Field(default_factory=list, max_length=16)


class CashFlowCreate(BaseModel):
    flow_date: date
    kind: Literal["initial", "deposit", "withdraw"]
    amount: str
    note: str = Field(default="", max_length=4000)


class SnapshotPositionInput(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    name: str = Field(default='', max_length=80)
    quantity: int = Field(gt=0, le=100_000_000)
    market_value: str


class SnapshotSave(BaseModel):
    snap_date: date
    total_assets: str
    available_cash: str | None = None
    position_value: str | None = None
    note: str = Field(default="", max_length=4000)
    expected_revision: int = Field(ge=0)
    positions: list[SnapshotPositionInput] | None = Field(default=None, max_length=500)


class DailyReviewSave(BaseModel):
    expected_revision: int = Field(ge=0)
    title: str = Field(default="", max_length=200)
    market_observation: str = Field(default="", max_length=50_000)
    decision_review: str = Field(default="", max_length=50_000)
    mistakes: str = Field(default="", max_length=50_000)
    tomorrow_plan: str = Field(default="", max_length=50_000)
    overall_summary: str = Field(default='', max_length=50_000)
    reflection: str = Field(default='', max_length=50_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    next_market_forecast: str = Field(default='', max_length=50_000)
    next_watchlist: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    next_position_plan: str = Field(default='', max_length=50_000)
    next_risk_plan: str = Field(default='', max_length=50_000)
    next_position_rehearsal: list[dict[str, str | int]] = Field(default_factory=list, max_length=100)
    next_target_date: date | None = None


class ScoreEntrySave(BaseModel):
    final: int | None = Field(default=None, ge=0, le=10, strict=True)
    comment: str = Field(default='', max_length=2000)


class ReviewScoresSave(BaseModel):
    scope: Literal['daily', 'trade', 't_group']
    trade_ids: list[str] = Field(default_factory=list, max_length=100)
    scores: dict[str, ScoreEntrySave] = Field(default_factory=dict)
    comment: str = Field(default='', max_length=5000)
    expected_revision: int = Field(ge=0)


class PeriodReviewSave(BaseModel):
    expected_revision: int = Field(ge=0)
    sections: dict[str, str] = Field(default_factory=dict)


class SimAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    initial_capital: str
    start_date: date
    config: dict[str, str] | None = None


class SimOrderCreate(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    side: Literal['buy', 'sell']
    quantity: int = Field(gt=0, le=100_000_000)
    limit_price: str
    signal_date: date
    submit_date: date


class SimFillCreate(BaseModel):
    expected_revision: int = Field(ge=1)
    fill_date: date
    fill_price: str


class SimOpenMatch(BaseModel):
    expected_revision: int = Field(ge=1)
    dataset_id: str = Field(pattern=r'^[0-9a-f]{64}$')


class SimMarketAdvance(BaseModel):
    expected_wallet_revision: int = Field(ge=1)
    to_date: date
    datasets: dict[str, str] = Field(default_factory=dict)


class SimSettle(BaseModel):
    to_date: date


class SimReset(BaseModel):
    expected_wallet_revision: int = Field(ge=1)
    start_date: date
    reset_config: bool = False


class SimRecoveryActivate(BaseModel):
    expected_wallet_revision: int = Field(ge=1)


class SimConfigUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    config: dict[str, str]


class MarketBarInput(BaseModel):
    event_date: date
    open: str
    high: str
    low: str
    close: str
    volume: int = Field(ge=0)
    amount: str | None = None
    available_at: str | None = None


class MarketDatasetImport(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    adjustment: Literal['none'] = 'none'
    bars: list[MarketBarInput] = Field(min_length=2, max_length=2000)


class StockAnnotationSave(BaseModel):
    expected_revision: int = Field(ge=0)
    start_date: date
    stage: Literal['Early', 'Mid', 'Late']
    trend_class: Literal['A', 'A_B', 'B', 'Unknown']
    decision: Literal['保留', '排除']
    notes: str = Field(default='', max_length=10000)


class TdxDayImport(BaseModel):
    symbol: str = Field(min_length=6, max_length=8)


class MarketCacheImport(BaseModel):
    provider: Literal['akshare', 'baostock']
    symbol: str = Field(min_length=6, max_length=8)


class AkShareDailySync(BaseModel):
    symbol: str = Field(min_length=6, max_length=8)
    start_date: date
    end_date: date
    mode: Literal['full', 'incremental'] = 'incremental'


class OnlineDailySync(AkShareDailySync):
    provider: Literal['auto', 'akshare', 'baostock'] = 'auto'
    provider_order: list[Literal['akshare', 'baostock']] = Field(default_factory=lambda: ['baostock', 'akshare'])

    @model_validator(mode='after')
    def validate_provider_order(self):
        if sorted(self.provider_order) != ['akshare', 'baostock']:
            raise ValueError('provider_order 必须恰好包含 akshare 和 baostock 各一次')
        return self


class OnlineBatchSync(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date
    mode: Literal['full', 'incremental'] = 'incremental'
    provider: Literal['auto', 'akshare', 'baostock'] = 'auto'
    provider_order: list[Literal['akshare', 'baostock']] = Field(default_factory=lambda: ['baostock', 'akshare'])

    @model_validator(mode='after')
    def validate_provider_order(self):
        if sorted(self.provider_order) != ['akshare', 'baostock']:
            raise ValueError('provider_order 必须恰好包含 akshare 和 baostock 各一次')
        return self


class ScreenerDatasetInput(BaseModel):
    dataset_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    float_shares: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    float_shares_as_of_date: date | None = None
    float_shares_source_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class ScreenerRunCreate(BaseModel):
    datasets: list[ScreenerDatasetInput] = Field(min_length=1, max_length=100)
    as_of_date: date
    return_window_days: int = Field(default=40, ge=5, le=120)
    config: FunnelConfig = Field(default_factory=FunnelConfig)


class TdxUniverseRunCreate(BaseModel):
    markets: list[Literal['sh', 'sz', 'bj']] = Field(default_factory=lambda: ['sh', 'sz', 'bj'], min_length=1, max_length=3)
    as_of_date: date
    return_window_days: int = Field(default=40, ge=5, le=120)
    max_bars: int = Field(default=360, ge=251, le=2000)
    config: FunnelConfig = Field(default_factory=FunnelConfig)

    @model_validator(mode='after')
    def unique_markets(self):
        if len(set(self.markets)) != len(self.markets):
            raise ValueError('markets 不能重复')
        return self


class B1ParamsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    vol_ratio: float = Field(default=0.8, ge=0.1, le=1.5, allow_inf_nan=False)
    chg_limit: float = Field(default=3.0, ge=0.5, le=10, allow_inf_nan=False)
    amp_limit_10cm: float = Field(default=5.0, ge=1, le=15, allow_inf_nan=False)
    amp_limit_20cm: float = Field(default=8.0, ge=1, le=20, allow_inf_nan=False)
    kdj_j_upper: float = Field(default=50.0, ge=10, le=90, allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def scanner_params(cls, value):
        from trade_app.research.b1_params import normalize_b1_params
        from trade_app.platform.types import TradeError
        try:
            return normalize_b1_params(value)
        except TradeError as exc:
            raise ValueError(exc.message) from exc


class B1RunCreate(BaseModel):
    dataset_ids: list[str] = Field(min_length=1, max_length=100)
    as_of_date: date
    b1_params: B1ParamsInput = Field(default_factory=B1ParamsInput)

    @model_validator(mode='after')
    def unique_datasets(self):
        if len(set(self.dataset_ids)) != len(self.dataset_ids):
            raise ValueError('dataset_ids 不能重复')
        if any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in self.dataset_ids):
            raise ValueError('dataset_ids 必须是完整的样本哈希')
        return self


class B1UniverseRunCreate(BaseModel):
    markets: list[Literal['sh', 'sz']] = Field(default_factory=lambda: ['sh', 'sz'], min_length=1, max_length=2)
    as_of_date: date
    max_bars: int = Field(default=1000, ge=900, le=2000)
    b1_params: B1ParamsInput = Field(default_factory=B1ParamsInput)

    @model_validator(mode='after')
    def unique_markets(self):
        if len(set(self.markets)) != len(self.markets):
            raise ValueError('markets 不能重复')
        return self


class TrendLeaderRunCreate(BaseModel):
    dataset_ids: list[str] = Field(min_length=1, max_length=100)
    date_from: date
    date_to: date
    window_days: int = Field(default=20, ge=5, le=120)
    daily_top_n: int = Field(default=5, ge=1, le=20)
    board_filters: list[Literal['main', 'gem', 'star', 'beijing', 'st']] = Field(
        default_factory=lambda: ['main', 'gem', 'star'])
    min_amount_avg: float = Field(default=5e7, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def validate_range_and_uniqueness(self):
        if self.date_from > self.date_to:
            raise ValueError('date_from 不能晚于 date_to')
        if len(set(self.dataset_ids)) != len(self.dataset_ids):
            raise ValueError('dataset_ids 不能重复')
        if any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in self.dataset_ids):
            raise ValueError('dataset_ids 必须是完整的样本哈希')
        if len(set(self.board_filters)) != len(self.board_filters):
            raise ValueError('board_filters 不能重复')
        return self


class TrendUniverseRunCreate(BaseModel):
    markets: list[Literal['sh', 'sz', 'bj']] = Field(default_factory=lambda: ['sh', 'sz'],
                                                    min_length=1, max_length=3)
    date_from: date
    date_to: date
    window_days: int = Field(default=20, ge=5, le=120)
    daily_top_n: int = Field(default=5, ge=1, le=10)
    board_filters: list[Literal['main', 'gem', 'star', 'beijing', 'st']] = Field(
        default_factory=lambda: ['main', 'gem', 'star'])
    min_amount_avg: float = Field(default=5e7, ge=0, allow_inf_nan=False)
    max_bars: int = Field(default=360, ge=251, le=1000)

    @model_validator(mode='after')
    def validate_range(self):
        if self.date_from > self.date_to or (self.date_to - self.date_from).days > 90:
            raise ValueError('全市场趋势扫描日期范围不得超过 90 天')
        if len(set(self.markets)) != len(self.markets):
            raise ValueError('markets 不能重复')
        if len(set(self.board_filters)) != len(self.board_filters):
            raise ValueError('board_filters 不能重复')
        return self


class LadderRunCreate(BaseModel):
    dataset_ids: list[str] = Field(min_length=1, max_length=100)
    date_from: date
    date_to: date
    recent_days: int = Field(default=5, ge=1, le=30)
    historical_min_boards: int = Field(default=3, ge=2, le=10)
    board_filters: list[Literal['main', 'gem', 'star', 'beijing', 'st']] = Field(
        default_factory=lambda: ['main', 'gem', 'star'])

    @model_validator(mode='after')
    def validate_range(self):
        if self.date_from > self.date_to:
            raise ValueError('date_from 不能晚于 date_to')
        if len(set(self.dataset_ids)) != len(self.dataset_ids):
            raise ValueError('dataset_ids 不能重复')
        if any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in self.dataset_ids):
            raise ValueError('dataset_ids 必须是完整的样本哈希')
        if len(set(self.board_filters)) != len(self.board_filters):
            raise ValueError('board_filters 不能重复')
        return self


class LadderUniverseRunCreate(BaseModel):
    markets: list[Literal['sh', 'sz', 'bj']] = Field(default_factory=lambda: ['sh', 'sz'],
                                                    min_length=1, max_length=3)
    date_from: date
    date_to: date
    recent_days: int = Field(default=5, ge=1, le=30)
    historical_min_boards: int = Field(default=3, ge=2, le=10)
    board_filters: list[Literal['main', 'gem', 'star', 'beijing', 'st']] = Field(
        default_factory=lambda: ['main', 'gem', 'star'])
    max_bars: int = Field(default=360, ge=251, le=1000)

    @model_validator(mode='after')
    def validate_range(self):
        if self.date_from > self.date_to or (self.date_to - self.date_from).days > 90:
            raise ValueError('全市场涨停梯队日期范围不得超过 90 天')
        if len(set(self.markets)) != len(self.markets):
            raise ValueError('markets 不能重复')
        if len(set(self.board_filters)) != len(self.board_filters):
            raise ValueError('board_filters 不能重复')
        return self


class SectorFlowRunCreate(BaseModel):
    date_from: date
    date_to: date
    daily_top_n: int = Field(default=5, ge=1, le=15)
    flow_window: int = Field(default=5, ge=3, le=60)
    max_bars: int = Field(default=1000, ge=251, le=2000)

    @model_validator(mode='after')
    def validate_range(self):
        if self.date_from > self.date_to or (self.date_to - self.date_from).days > 1096:
            raise ValueError('板块资金日期范围不得超过三年')
        return self


class AbnormalUniverseRunCreate(BaseModel):
    markets: list[Literal['sh', 'sz', 'bj']] = Field(default_factory=lambda: ['sh', 'sz'],
                                                    min_length=1, max_length=3)
    date_from: date
    date_to: date
    include_warnings: bool = True
    board_filters: list[Literal['main', 'gem', 'star', 'beijing', 'st']] = Field(default_factory=list)
    scan_mode: Literal['snapshot', 'full'] = 'snapshot'
    cooling_days: int = Field(default=3, ge=0, le=10)
    max_bars: int = Field(default=900, ge=251, le=900)

    @model_validator(mode='after')
    def validate_range(self):
        if self.date_from > self.date_to or (self.date_to - self.date_from).days > 365:
            raise ValueError('异动扫描日期范围不得超过一年')
        if len(set(self.markets)) != len(self.markets):
            raise ValueError('markets 不能重复')
        if len(set(self.board_filters)) != len(self.board_filters):
            raise ValueError('board_filters 不能重复')
        return self


class ValuationScenarioInput(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    earnings_yi: Decimal = Field(ge=Decimal('-10000000'), le=Decimal('10000000'))
    growth_rate_pct: float = Field(ge=-100, le=1000, allow_inf_nan=False)
    base_pe: float = Field(gt=0, le=500, allow_inf_nan=False)
    index_points: float = Field(gt=0, le=100000, allow_inf_nan=False)
    sentiment_coef: float = Field(gt=0, le=20, allow_inf_nan=False)
    actual_cap_yi: Decimal | None = Field(default=None, ge=0, le=Decimal('100000000'))


class ValuationRunCreate(BaseModel):
    symbol: str = Field(default='', max_length=8)
    scenarios: list[ValuationScenarioInput] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def unique_labels(self):
        if len({item.label for item in self.scenarios}) != len(self.scenarios):
            raise ValueError('情景名称不能重复')
        return self


class ResearchRunCreate(BaseModel):
    dataset_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_id: str
    decision_at: str
    strict: bool = True
    params: dict[str, str] = Field(default_factory=dict)
    event_profile_id: str | None = Field(default=None, max_length=100)
    event_profile_revision: int | None = Field(default=None, ge=0)


class EventProfileSave(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    profile: dict


class EventProfileAction(BaseModel):
    expected_revision: int = Field(ge=0)
    expected_active_revision: int = Field(ge=0)


class MatrixPoolRunCreate(BaseModel):
    source_run_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    strict: bool = True
    params: dict[str, str] = Field(default_factory=dict)


class StrategyScanItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy_id: str
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)


class StrategySignalContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_path: Literal['legacy_store', 'legacy_tdx'] = 'legacy_store'
    window_days: int = Field(default=60, ge=20, le=240, strict=True)
    universe_mode: Literal['strategy_filtered', 'full_market'] = 'strategy_filtered'


class StrategyScanCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_ids: list[str] = Field(min_length=1, max_length=100)
    strategies: list[StrategyScanItem] = Field(min_length=1, max_length=10)
    as_of_date: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    strict: bool = Field(default=True, strict=True)
    event_profile_id: str | None = Field(default=None, max_length=100)
    event_profile_revision: int | None = Field(default=None, ge=0, strict=True)
    signal_context: StrategySignalContext | None = None


class BasketCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    notes: str = Field(default='', max_length=1000)
    run_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    scan_id: str | None = None
    selection: Literal['union', 'intersection'] = 'union'
    scan_date: date | None = None
    holding_bars: int = Field(default=5, ge=1, le=60)
    holding_mode: Literal['legacy_signal_anchor', 'complete_overnights'] = 'legacy_signal_anchor'
    quantity: int = Field(default=100, ge=100, le=1000000, multiple_of=100)
    fees: dict[str, str] | None = None


class BasketUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=1000)
    run_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    holding_bars: int | None = Field(default=None, ge=1, le=60)
    holding_mode: Literal['legacy_signal_anchor', 'complete_overnights'] | None = None
    quantity: int | None = Field(default=None, ge=100, le=1000000, multiple_of=100)
    fees: dict[str, str] | None = None


class BasketDeleteItem(BaseModel):
    id: str
    expected_revision: int = Field(ge=1)


class BasketDeleteBatch(BaseModel):
    items: list[BasketDeleteItem] = Field(min_length=1, max_length=100)


class BasketEvaluationCreate(BaseModel):
    expected_revision: int = Field(ge=1)
    as_of_date: date
    strict: bool = True
    forward_datasets: dict[str, str] = Field(default_factory=dict)


class BacktestCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_id: str
    initial_capital: str = '100000.00'
    holding_bars: int = Field(default=5, ge=1, le=60)
    max_position_pct: str = '0.95'
    strict: bool = True
    params: dict[str, str] = Field(default_factory=dict)
    fee_config: dict[str, str] | None = None
    stop_loss_pct: str = '0'
    take_profit_pct: str = '0'
    trailing_stop_pct: str = '0'
    event_profile_id: str | None = Field(default=None, max_length=100)
    event_profile_revision: int | None = Field(default=None, ge=0)
    advanced_analysis: bool = False
    analysis_seed: int = Field(default=20260926, ge=0, le=4294967295, strict=True)
    analysis_iterations: int = Field(default=400, ge=100, le=2000, strict=True)


class SimDraftCreate(BaseModel):
    source_run_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    quantity: int = Field(gt=0, le=100_000_000)
    limit_price: str


class SimDraftSizing(BaseModel):
    mode: Literal['lots', 'amount', 'cash_percent']
    value: str
    limit_price: str


class SimDraftUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    quantity: int = Field(gt=0, le=100_000_000)
    limit_price: str


class SimDraftBatchItem(BaseModel):
    id: str
    expected_revision: int = Field(ge=1)


class SimDraftBatchSubmit(BaseModel):
    drafts: list[SimDraftBatchItem] = Field(min_length=1, max_length=50)
    expected_wallet_revision: int = Field(ge=1)
    expected_config_version: int = Field(ge=1)
