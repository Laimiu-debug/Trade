from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class SimEquityReport(Base):
    __tablename__ = 'sim_equity_reports'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    request_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class SimEquityDraftEvidence(Base):
    __tablename__ = 'sim_equity_draft_evidence'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    draft_id: Mapped[str] = mapped_column(String)
    report_id: Mapped[str] = mapped_column(String)
    evidence_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
