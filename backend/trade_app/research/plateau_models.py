from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class PlateauExperiment(Base):
    __tablename__ = 'plateau_experiments'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    source_run_id: Mapped[str] = mapped_column(String)
    strategy_id: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    input_sha256: Mapped[str] = mapped_column(String)
    input_json: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    pause_requested: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    last_served_at: Mapped[str] = mapped_column(String)


class PlateauPoint(Base):
    __tablename__ = 'plateau_points'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    point_sha256: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(String)
