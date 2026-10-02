from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class EventStoreVersion(Base):
    __tablename__ = 'event_store_versions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    config_json: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String)
    profile_id: Mapped[str] = mapped_column(String)
    profile_revision: Mapped[int] = mapped_column(Integer)
    window_days: Mapped[int] = mapped_column(Integer)
    strict: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)


class EventStoreRecord(Base):
    __tablename__ = 'event_store_records'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey('event_store_versions.id'))
    dataset_id: Mapped[str] = mapped_column(ForeignKey('market_datasets.id'))
    symbol: Mapped[str] = mapped_column(String)
    decision_date: Mapped[str] = mapped_column(String)
    decision_at: Mapped[str] = mapped_column(String)
    source_date: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    event_count: Mapped[int] = mapped_column(Integer)
    risk_count: Mapped[int] = mapped_column(Integer)
    primary_event: Mapped[str] = mapped_column(String)
    observed_bars: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String)
    byte_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)


class EventStoreJob(Base):
    __tablename__ = 'event_store_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    cancel_requested: Mapped[int] = mapped_column(Integer)
    total_count: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer)
    cache_hits: Mapped[int] = mapped_column(Integer)
    written_count: Mapped[int] = mapped_column(Integer)
    result_bytes: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
