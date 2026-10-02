from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class SignalWorkspaceReport(Base):
    __tablename__ = 'signal_workspace_reports'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scan_id: Mapped[str] = mapped_column(String)
    request_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class SignalWorkspaceSource(Base):
    __tablename__ = 'signal_workspace_sources'
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
