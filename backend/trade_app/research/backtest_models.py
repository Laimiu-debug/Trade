from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class BacktestRun(Base):
    __tablename__ = 'backtest_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey('market_datasets.id'))
    strategy_id: Mapped[str] = mapped_column(String)
    strategy_version: Mapped[str] = mapped_column(String)
    execution_version: Mapped[str] = mapped_column(String)
    calculation_version: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    params_json: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    cancel_requested: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=0)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
