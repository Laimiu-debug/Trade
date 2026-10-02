from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class PortfolioAnalysis(Base):
    __tablename__ = 'portfolio_analysis_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_run_id: Mapped[str] = mapped_column(String)
    source_result_sha256: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    input_sha256: Mapped[str] = mapped_column(String)
    input_json: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
