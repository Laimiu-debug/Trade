from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class LegacySimPromotion(Base):
    __tablename__ = 'legacy_sim_promotions'
    import_id: Mapped[str] = mapped_column(ForeignKey('legacy_imports.id'), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), unique=True)
    preview_sha256: Mapped[str] = mapped_column(String)
    preview_json: Mapped[str] = mapped_column(Text)
    mappings_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
