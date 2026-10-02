from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class StrategyScanRun(Base):
    """Immutable grouping of independently reproducible single-stock runs."""
    __tablename__ = 'strategy_scan_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
