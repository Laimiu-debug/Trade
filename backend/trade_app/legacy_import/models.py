from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class LegacyImport(Base):
    __tablename__ = 'legacy_imports'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String, unique=True)
    source_kind: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    source_bytes: Mapped[int] = mapped_column(Integer)
    logical_sha256: Mapped[str] = mapped_column(String)
    archive_json: Mapped[str] = mapped_column(Text)
    preview_json: Mapped[str] = mapped_column(Text)
    mappings_json: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('accounts.id'), nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    promotion_json: Mapped[str | None] = mapped_column(Text, nullable=True)
