from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class ReviewScoreSheet(Base):
    __tablename__ = 'review_score_sheets'
    __table_args__ = (UniqueConstraint('account_id', 'review_date', 'scope', 'subject_id'),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    review_date: Mapped[str] = mapped_column(String)
    scope: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str] = mapped_column(String)
    trade_ids_json: Mapped[str] = mapped_column(Text, default='[]')
    scores_json: Mapped[str] = mapped_column(Text, default='{}')
    comment: Mapped[str] = mapped_column(Text, default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
