"""The final-trade four-stage funnel over frozen candidate metrics.

The caller supplies metrics with their provenance. Missing float shares leaves
turnover unknown and cannot satisfy the first-stage liquidity threshold.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Step1(BaseModel):
    rank_start: int = Field(default=1, ge=1, le=2000)
    top_n: int = Field(default=500, ge=10, le=2000)
    turnover_threshold: float = Field(default=0.05, ge=0.01, le=0.2)
    amount_threshold: float = Field(default=5e8, ge=5e7, le=5e9)
    amplitude_threshold: float = Field(default=0.03, ge=0.01, le=0.15)


class Step2(BaseModel):
    retrace_min: float = Field(default=0.05, ge=0, le=0.8)
    retrace_max: float = Field(default=0.25, ge=0, le=0.8)
    max_pullback_days: int = Field(default=3, ge=0, le=30)
    min_ma10_above_ma20_days: int = Field(default=5, ge=0, le=40)
    min_ma5_above_ma10_days: int = Field(default=3, ge=0, le=40)
    max_price_vs_ma20: float = Field(default=0.08, ge=0, le=0.5)
    require_above_ma20: bool = True
    allow_b_trend: bool = False


class Step3(BaseModel):
    min_vol_slope20: float = Field(default=0.05, ge=0, le=2)
    min_up_down_volume_ratio: float = Field(default=1.3, ge=0, le=20)
    max_pullback_volume_ratio: float = Field(default=0.9, ge=0, le=5)
    allow_blowoff_top: bool = False
    allow_divergence_5d: bool = False
    allow_upper_shadow_risk: bool = False
    allow_degraded: bool = False


class Step4(BaseModel):
    final_top_n: int = Field(default=8, ge=1, le=500)
    min_ai_confidence: float = Field(default=0.55, ge=0, le=1)
    allowed_theme_stages: list[Literal['发酵中', '高潮', '退潮', 'Unknown']] = Field(
        default_factory=lambda: ['发酵中', '高潮'], min_length=1)
    allow_degraded: bool = True


class FunnelConfig(BaseModel):
    mode: Literal['strict', 'loose'] = 'strict'
    step1: Step1 = Field(default_factory=Step1)
    step2: Step2 = Field(default_factory=Step2)
    step3: Step3 = Field(default_factory=Step3)
    step4: Step4 = Field(default_factory=Step4)

    @model_validator(mode='after')
    def check_rank_window(self):
        if self.step1.rank_start > self.step1.top_n:
            raise ValueError('rank_start 不能大于 top_n')
        return self


class ScreenerCandidate(BaseModel):
    symbol: str = Field(min_length=6, max_length=8)
    dataset_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    as_of_date: str
    name: str = ''
    score: int = Field(ge=0, le=100)
    ret40: float
    turnover20: float | None = None
    amount20: float | None = None
    amplitude20: float
    retrace20: float
    pullback_days: int
    ma10_above_ma20_days: int
    ma5_above_ma10_days: int
    price_vs_ma20: float
    vol_slope20: float
    up_down_volume_ratio: float
    pullback_volume_ratio: float
    has_blowoff_top: bool
    has_divergence_5d: bool
    has_upper_shadow_risk: bool
    ai_confidence: float
    theme_stage: Literal['发酵中', '高潮', '退潮', 'Unknown']
    trend_class: Literal['A', 'A_B', 'B', 'Unknown']
    degraded: bool = False
    quality_flags: list[str] = Field(default_factory=list)


def run_funnel(candidates: list[ScreenerCandidate], config: FunnelConfig) -> dict:
    """Mirror final-trade's four filters, preserving stable rank ties."""
    one, two, three, four = config.step1, config.step2, config.step3, config.step4
    ranked = sorted(candidates, key=lambda row: row.ret40, reverse=True)[one.rank_start - 1:one.top_n]
    step1 = [row for row in ranked if row.turnover20 is not None
             and row.amount20 is not None
             and row.turnover20 >= one.turnover_threshold
             and row.amount20 >= one.amount_threshold
             and row.amplitude20 >= one.amplitude_threshold][:400]
    pad = 0.02 if config.mode == 'loose' else 0.0
    days = 1 if config.mode == 'loose' else 0
    retrace_min = max(0.0, min(two.retrace_min, two.retrace_max) - pad)
    retrace_max = min(0.8, max(two.retrace_min, two.retrace_max) + pad)
    step2 = [row for row in step1
             if retrace_min <= row.retrace20 <= retrace_max
             and row.pullback_days <= two.max_pullback_days + days
             and row.ma10_above_ma20_days >= max(0, two.min_ma10_above_ma20_days - days)
             and row.ma5_above_ma10_days >= max(0, two.min_ma5_above_ma10_days - days)
             and abs(row.price_vs_ma20) <= two.max_price_vs_ma20
             and (not two.require_above_ma20 or row.price_vs_ma20 >= 0)
             and (two.allow_b_trend or row.trend_class != 'B')]
    step3 = [row for row in step2
             if row.vol_slope20 >= three.min_vol_slope20
             and row.up_down_volume_ratio >= three.min_up_down_volume_ratio
             and row.pullback_volume_ratio <= three.max_pullback_volume_ratio
             and (three.allow_blowoff_top or not row.has_blowoff_top)
             and (three.allow_divergence_5d or not row.has_divergence_5d)
             and (three.allow_upper_shadow_risk or not row.has_upper_shadow_risk)
             and (three.allow_degraded or not row.degraded)]
    source = step3 if four.allow_degraded else [row for row in step3 if not row.degraded]
    step4 = sorted((row for row in source
                    if row.ai_confidence >= four.min_ai_confidence
                    and row.theme_stage in four.allowed_theme_stages),
                   key=lambda row: row.score + row.ai_confidence * 20, reverse=True)[:four.final_top_n]
    pools = {'input': candidates, 'step1': step1, 'step2': step2,
             'step3': step3, 'step4': step4}
    ids = {key: {row.dataset_id for row in value} for key, value in pools.items()}
    ranked_ids = {row.dataset_id for row in ranked}
    passing_step1_ids = {row.dataset_id for row in ranked
                         if row.turnover20 is not None and row.amount20 is not None
                         and row.turnover20 >= one.turnover_threshold
                         and row.amount20 >= one.amount_threshold
                         and row.amplitude20 >= one.amplitude_threshold}
    rejections = {}
    for row in candidates:
        if row.dataset_id in ids['step4']:
            continue
        if row.dataset_id not in ids['step1']:
            reasons = []
            if row.dataset_id not in ranked_ids:
                reasons.append('RANK_WINDOW')
            elif row.dataset_id in passing_step1_ids:
                reasons.append('STEP1_CAP_400')
            if row.turnover20 is None:
                reasons.append('TURNOVER_MISSING')
            elif row.turnover20 < one.turnover_threshold:
                reasons.append('TURNOVER_BELOW_MIN')
            if row.amount20 is None:
                reasons.append('AMOUNT_MISSING')
            elif row.amount20 < one.amount_threshold:
                reasons.append('AMOUNT_BELOW_MIN')
            if row.amplitude20 < one.amplitude_threshold:
                reasons.append('AMPLITUDE_BELOW_MIN')
            rejections[row.dataset_id] = {'stage': 'step1', 'reasons': reasons}
        elif row.dataset_id not in ids['step2']:
            reasons = []
            if not retrace_min <= row.retrace20 <= retrace_max:
                reasons.append('RETRACE_OUTSIDE_RANGE')
            if row.pullback_days > two.max_pullback_days + days:
                reasons.append('PULLBACK_DAYS_ABOVE_MAX')
            if row.ma10_above_ma20_days < max(0, two.min_ma10_above_ma20_days - days):
                reasons.append('MA10_MA20_DAYS_BELOW_MIN')
            if row.ma5_above_ma10_days < max(0, two.min_ma5_above_ma10_days - days):
                reasons.append('MA5_MA10_DAYS_BELOW_MIN')
            if abs(row.price_vs_ma20) > two.max_price_vs_ma20:
                reasons.append('PRICE_VS_MA20_ABOVE_MAX')
            if two.require_above_ma20 and row.price_vs_ma20 < 0:
                reasons.append('PRICE_BELOW_MA20')
            if not two.allow_b_trend and row.trend_class == 'B':
                reasons.append('B_TREND_EXCLUDED')
            rejections[row.dataset_id] = {'stage': 'step2', 'reasons': reasons}
        elif row.dataset_id not in ids['step3']:
            reasons = []
            if row.vol_slope20 < three.min_vol_slope20:
                reasons.append('VOLUME_SLOPE_BELOW_MIN')
            if row.up_down_volume_ratio < three.min_up_down_volume_ratio:
                reasons.append('UP_DOWN_VOLUME_BELOW_MIN')
            if row.pullback_volume_ratio > three.max_pullback_volume_ratio:
                reasons.append('PULLBACK_VOLUME_ABOVE_MAX')
            if not three.allow_blowoff_top and row.has_blowoff_top:
                reasons.append('BLOWOFF_TOP_EXCLUDED')
            if not three.allow_divergence_5d and row.has_divergence_5d:
                reasons.append('DIVERGENCE_EXCLUDED')
            if not three.allow_upper_shadow_risk and row.has_upper_shadow_risk:
                reasons.append('UPPER_SHADOW_EXCLUDED')
            if not three.allow_degraded and row.degraded:
                reasons.append('DEGRADED_EXCLUDED')
            rejections[row.dataset_id] = {'stage': 'step3', 'reasons': reasons}
        else:
            reasons = []
            if not four.allow_degraded and row.degraded:
                reasons.append('DEGRADED_EXCLUDED')
            if row.ai_confidence < four.min_ai_confidence:
                reasons.append('CONFIDENCE_BELOW_MIN')
            if row.theme_stage not in four.allowed_theme_stages:
                reasons.append('THEME_STAGE_EXCLUDED')
            if not reasons:
                reasons.append('FINAL_TOP_N')
            rejections[row.dataset_id] = {'stage': 'step4', 'reasons': reasons}
    return {'summary': {key: len(value) for key, value in pools.items()},
            'pools': {key: [row.model_dump() for row in value] for key, value in pools.items()},
            'rejections': rejections}
