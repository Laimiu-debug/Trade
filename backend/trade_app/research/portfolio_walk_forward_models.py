from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class PortfolioWalkForward(Base):
    __tablename__ = 'portfolio_walk_forwards'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    source_run_id: Mapped[str] = mapped_column(String)
    input_json: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    total_tasks: Mapped[int] = mapped_column(Integer)
    active_fold: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    pause_requested: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    last_served_at: Mapped[str] = mapped_column(String)


class PortfolioWalkForwardFold(Base):
    __tablename__ = 'portfolio_walk_forward_folds'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    selection_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_sha256: Mapped[str | None] = mapped_column(String, nullable=True)


class PortfolioWalkForwardTask(Base):
    __tablename__ = 'portfolio_walk_forward_tasks'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String)
    fold_index: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    axis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    child_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
