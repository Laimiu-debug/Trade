from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class StockAnnotation(Base):
    __tablename__ = 'stock_annotations'
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    start_date: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    trend_class: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    notes: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String)
