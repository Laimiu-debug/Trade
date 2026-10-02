from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class DailyReview(Base):
    __tablename__ = "daily_reviews"
    __table_args__ = (UniqueConstraint("account_id", "review_date"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    review_date: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="")
    market_observation: Mapped[str] = mapped_column(Text, default="")
    decision_review: Mapped[str] = mapped_column(Text, default="")
    mistakes: Mapped[str] = mapped_column(Text, default="")
    tomorrow_plan: Mapped[str] = mapped_column(Text, default="")
    overall_summary: Mapped[str] = mapped_column(Text, default='')
    reflection: Mapped[str] = mapped_column(Text, default='')
    tags_json: Mapped[str] = mapped_column(Text, default='[]')
    next_market_forecast: Mapped[str] = mapped_column(Text, default='')
    next_watchlist_json: Mapped[str] = mapped_column(Text, default='[]')
    next_position_plan: Mapped[str] = mapped_column(Text, default='')
    next_risk_plan: Mapped[str] = mapped_column(Text, default='')
    next_position_rehearsal_json: Mapped[str] = mapped_column(Text, default='[]')
    next_target_date: Mapped[str | None] = mapped_column(String, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class PeriodReview(Base):
    __tablename__ = "period_reviews"
    __table_args__ = (UniqueConstraint("account_id", "kind", "period_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String)
    period_key: Mapped[str] = mapped_column(String)
    sections_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
