"""Frozen report bodies live in SQLite and are included in ordinary backups."""
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class ResearchReport(Base):
    __tablename__ = 'research_reports'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    source_run_id: Mapped[str] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String)
    content_sha256: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)
