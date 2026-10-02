from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class SimReviewTag(Base):
    __tablename__ = 'sim_review_tags'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    tag_type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    active: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class SimFillTagAssignment(Base):
    __tablename__ = 'sim_fill_tag_assignments'

    fill_id: Mapped[str] = mapped_column(ForeignKey('sim_fills.id'), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    emotion_tag_id: Mapped[str | None] = mapped_column(ForeignKey('sim_review_tags.id'), nullable=True)
    reason_tag_ids_json: Mapped[str] = mapped_column(Text, default='[]')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String)
