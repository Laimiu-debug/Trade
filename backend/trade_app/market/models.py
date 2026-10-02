from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class MarketDataset(Base):
    __tablename__ = 'market_datasets'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    adjustment: Mapped[str] = mapped_column(String)
    first_date: Mapped[str] = mapped_column(String)
    last_date: Mapped[str] = mapped_column(String)
    bar_count: Mapped[int] = mapped_column(Integer)
    availability_quality: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)


class MarketSyncJob(Base):
    __tablename__ = 'market_sync_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_json: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    next_index: Mapped[int] = mapped_column(Integer)
    results_json: Mapped[str] = mapped_column(String)
    cancel_requested: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
