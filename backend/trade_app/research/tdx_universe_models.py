from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class TdxUniverseJob(Base):
    __tablename__ = 'tdx_universe_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String)
    request_json: Mapped[str] = mapped_column(String)
    total_count: Mapped[int] = mapped_column(Integer)
    processed_count: Mapped[int] = mapped_column(Integer)
    success_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    cancel_requested: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String)
    error_code: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class TdxUniverseItem(Base):
    __tablename__ = 'tdx_universe_items'
    __table_args__ = (UniqueConstraint('job_id', 'symbol'),)
    job_id: Mapped[str] = mapped_column(String, ForeignKey('tdx_universe_jobs.id'), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    float_shares: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String)
    dataset_id: Mapped[str | None] = mapped_column(String)
    candidate_json: Mapped[str | None] = mapped_column(String)
    error_code: Mapped[str | None] = mapped_column(String)
