from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class SignalBasket(Base):
    __tablename__ = 'signal_baskets'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class SignalBasketAudit(Base):
    __tablename__ = 'signal_basket_audit'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    basket_id: Mapped[str] = mapped_column(ForeignKey('signal_baskets.id'))
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class SignalBasketEvaluation(Base):
    __tablename__ = 'signal_basket_evaluations'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    basket_id: Mapped[str] = mapped_column(ForeignKey('signal_baskets.id'))
    basket_revision: Mapped[int] = mapped_column(Integer)
    request_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
