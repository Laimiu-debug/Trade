from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class LegacySupplementBatch(Base):
    __tablename__ = 'legacy_supplement_batches'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey('legacy_imports.id'))
    preview_sha256: Mapped[str] = mapped_column(String)
    request_json: Mapped[str] = mapped_column(Text)
    preview_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class LegacySupplementItem(Base):
    __tablename__ = 'legacy_supplement_items'
    import_id: Mapped[str] = mapped_column(ForeignKey('legacy_imports.id'), primary_key=True)
    source_key: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('legacy_supplement_batches.id'))
    target_json: Mapped[str] = mapped_column(Text)
