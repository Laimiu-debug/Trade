from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from trade_app.platform.db import Base


class IntradaySnapshot(Base):
    __tablename__ = 'intraday_snapshots'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    date: Mapped[str] = mapped_column(String)
    as_of_at: Mapped[str] = mapped_column(String)
    point_count: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
