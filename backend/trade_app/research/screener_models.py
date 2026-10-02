from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class ScreenerRun(Base):
    __tablename__ = 'screener_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_json: Mapped[str] = mapped_column(String)
    result_json: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
