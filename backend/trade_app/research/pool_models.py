from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class StrategyPoolRun(Base):
    """Immutable strategy evaluation over an already frozen candidate pool."""
    __tablename__ = 'strategy_pool_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String)
    source_run_id: Mapped[str] = mapped_column(ForeignKey('screener_runs.id'))
    request_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
