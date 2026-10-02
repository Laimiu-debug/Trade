from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class InspirationCard(Base):
    __tablename__ = 'inspiration_cards'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    tags_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)


class DailyInspirationSelection(Base):
    __tablename__ = 'daily_inspiration_selections'

    review_date: Mapped[str] = mapped_column(String, primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey('inspiration_cards.id'))
    created_at: Mapped[str] = mapped_column(String)
