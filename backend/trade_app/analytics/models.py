from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class ProjectionVersion(Base):
    __tablename__ = "projection_versions"
    __table_args__ = (UniqueConstraint("account_id", "input_revision", "calculation_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    input_revision: Mapped[int] = mapped_column(Integer)
    calculation_version: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
