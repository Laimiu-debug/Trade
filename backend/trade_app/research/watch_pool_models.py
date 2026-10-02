from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class WatchPool(Base):
    __tablename__ = 'research_watch_pool'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    state_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class WatchPoolAudit(Base):
    __tablename__ = 'research_watch_pool_audit'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
