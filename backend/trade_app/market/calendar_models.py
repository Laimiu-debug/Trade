from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class LocalTradingCalendar(Base):
    __tablename__ = 'local_trading_calendars'

    market: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    start_date: Mapped[str] = mapped_column(String)
    end_date: Mapped[str] = mapped_column(String)
    days_json: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String)
