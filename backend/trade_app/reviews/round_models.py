from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class RoundNote(Base):
    __tablename__ = 'round_notes'
    __table_args__ = (UniqueConstraint('account_id', 'round_id'),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    round_id: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text, default='')
    trade_ids_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
