from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class StrategyRegistrySettings(Base):
    __tablename__ = 'strategy_registry_settings'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    settings_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class StrategyRegistryRevision(Base):
    __tablename__ = 'strategy_registry_revisions'
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
