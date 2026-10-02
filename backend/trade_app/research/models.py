from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class ResearchRun(Base):
    __tablename__ = 'research_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey('market_datasets.id'))
    strategy_id: Mapped[str] = mapped_column(String)
    strategy_version: Mapped[str] = mapped_column(String)
    decision_at: Mapped[str] = mapped_column(String)
    strict: Mapped[int] = mapped_column(Integer)
    params_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
