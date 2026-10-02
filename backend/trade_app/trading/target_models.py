from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class TargetConfig(Base):
    __tablename__ = 'target_configs'

    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    multiplier: Mapped[str] = mapped_column(String)
    node_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
