from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class AIGeneration(Base):
    __tablename__ = 'ai_generations'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('ai_calls.id'), unique=True)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer)
    source_json: Mapped[str] = mapped_column(Text)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors_json: Mapped[str] = mapped_column(Text)
    acceptance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_sha256: Mapped[str] = mapped_column(String)
    deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AIGenerationAudit(Base):
    __tablename__ = 'ai_generation_audit'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey('ai_generations.id'))
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
