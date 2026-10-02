from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class SimOrderDraft(Base):
    __tablename__ = 'sim_order_drafts'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    source_run_id: Mapped[str] = mapped_column(ForeignKey('research_runs.id'))
    symbol: Mapped[str] = mapped_column(String)
    signal_date: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price_units: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    order_id: Mapped[str | None] = mapped_column(ForeignKey('sim_orders.id'), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
