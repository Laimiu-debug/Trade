from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class PortfolioExperiment(Base):
    __tablename__ = 'portfolio_experiments'
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
    completed_points: Mapped[int] = mapped_column(Integer, default=0)
    total_points: Mapped[int] = mapped_column(Integer)
    active_point: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    pause_requested: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    last_served_at: Mapped[str] = mapped_column(String)


class PortfolioExperimentPoint(Base):
    __tablename__ = 'portfolio_experiment_points'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    axis_json: Mapped[str] = mapped_column(Text)
    point_sha256: Mapped[str] = mapped_column(String)
    child_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
