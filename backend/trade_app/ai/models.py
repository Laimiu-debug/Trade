"""Durable AI configuration, sessions, frozen calls and prompt revisions."""
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class AIConfig(Base):
    __tablename__ = 'ai_config_versions'
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class AISession(Base):
    __tablename__ = 'ai_sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer)
    deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AICall(Base):
    __tablename__ = 'ai_calls'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey('ai_sessions.id'), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    config_revision: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(String)
    output: Mapped[str] = mapped_column(Text)
    usage_json: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AIPromptTemplate(Base):
    __tablename__ = 'ai_prompt_templates'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AIPromptRevision(Base):
    __tablename__ = 'ai_prompt_revisions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(ForeignKey('ai_prompt_templates.id'))
    revision: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
