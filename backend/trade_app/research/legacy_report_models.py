from sqlalchemy import Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class LegacyResearchReport(Base):
    __tablename__ = 'legacy_research_reports'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String, unique=True)
    mode: Mapped[str] = mapped_column(String)
    content_sha256: Mapped[str] = mapped_column(String)
    original_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_json: Mapped[str] = mapped_column(Text)
    preview_json: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
    deleted: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)
