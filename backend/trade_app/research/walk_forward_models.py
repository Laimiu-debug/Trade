from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class WalkForwardJob(Base):
    __tablename__ = 'walk_forward_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    source_run_id: Mapped[str] = mapped_column(String)
    strategy_id: Mapped[str] = mapped_column(String)
    input_json: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(String)
    plan_json: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
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


class WalkForwardFold(Base):
    __tablename__ = 'walk_forward_folds'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String)
    fold_index: Mapped[int] = mapped_column(Integer)
    range_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    selection_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String)


class WalkForwardTask(Base):
    __tablename__ = 'walk_forward_tasks'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String)
    fold_index: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)
    candidate_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(String)
