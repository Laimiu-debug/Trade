"""Durable scan attempts and private, bounded checkpoints (included in DB backups)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class StrategyScanJob(Base):
    __tablename__ = 'strategy_scan_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_json: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    cancel_requested: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer)
    total_count: Mapped[int] = mapped_column(Integer)
    checkpoint_bytes: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    attempt_id: Mapped[str | None] = mapped_column(String)
    attempt_number: Mapped[int] = mapped_column(Integer)
    scan_id: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class StrategyScanChunk(Base):
    __tablename__ = 'strategy_scan_chunks'
    job_id: Mapped[str] = mapped_column(ForeignKey('strategy_scan_jobs.id'), primary_key=True)
    start_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    end_index: Mapped[int] = mapped_column(Integer)
    attempt_id: Mapped[str] = mapped_column(String)
    content_sha256: Mapped[str] = mapped_column(String)
    byte_count: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
