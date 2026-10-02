"""Named parameter presets, immutable revisions, and explicitly applied proposals."""
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class StrategyPreset(Base):
    __tablename__ = 'strategy_presets'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    favorite: Mapped[int] = mapped_column(Integer)
    deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class StrategyPresetRevision(Base):
    __tablename__ = 'strategy_preset_revisions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    preset_id: Mapped[str] = mapped_column(ForeignKey('strategy_presets.id'))
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class StrategyParameterProposal(Base):
    __tablename__ = 'strategy_parameter_proposals'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    preset_id: Mapped[str] = mapped_column(ForeignKey('strategy_presets.id'))
    base_revision: Mapped[int] = mapped_column(Integer)
    proposal_sha256: Mapped[str] = mapped_column(String)
    snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    applied_at: Mapped[str | None] = mapped_column(String, nullable=True)
