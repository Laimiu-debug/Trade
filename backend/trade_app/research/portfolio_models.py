from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class PortfolioRun(Base):
    __tablename__ = 'portfolio_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    strategy_id: Mapped[str] = mapped_column(String)
    input_json: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    checkpoint_json: Mapped[str] = mapped_column(Text)
    checkpoint_sha256: Mapped[str] = mapped_column(String)
    summary_json: Mapped[str] = mapped_column(Text)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_days: Mapped[int] = mapped_column(Integer, default=0)
    total_days: Mapped[int] = mapped_column(Integer)
    symbol_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    pause_requested: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    last_served_at: Mapped[str] = mapped_column(String)
    owner_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String, nullable=True)


class PortfolioChunk(Base):
    __tablename__ = 'portfolio_chunks'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    start_cursor: Mapped[int] = mapped_column(Integer)
    end_cursor: Mapped[int] = mapped_column(Integer)
    prior_sha256: Mapped[str] = mapped_column(String)
    result_json: Mapped[str] = mapped_column(Text)
    result_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
