from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.platform.db import Base


class SimWallet(Base):
    __tablename__ = 'sim_wallets'
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), primary_key=True)
    initial_minor: Mapped[int] = mapped_column(Integer)
    cash_minor: Mapped[int] = mapped_column(Integer)
    as_of_date: Mapped[str] = mapped_column(String)
    config_json: Mapped[str] = mapped_column(Text)
    config_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    frozen: Mapped[int] = mapped_column(Integer, default=0)
    reset_group_id: Mapped[str | None] = mapped_column(String, nullable=True)


class SimOrder(Base):
    __tablename__ = 'sim_orders'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    symbol: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price_units: Mapped[int] = mapped_column(Integer)
    signal_date: Mapped[str] = mapped_column(String)
    submit_date: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    reserve_minor: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    config_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    legacy_origin_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SimFill(Base):
    __tablename__ = 'sim_fills'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey('sim_orders.id'))
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    fill_date: Mapped[str] = mapped_column(String)
    price_units: Mapped[int] = mapped_column(Integer)
    gross_minor: Mapped[int] = mapped_column(Integer)
    commission_minor: Mapped[int] = mapped_column(Integer)
    stamp_minor: Mapped[int] = mapped_column(Integer)
    transfer_minor: Mapped[int] = mapped_column(Integer)
    realized_pnl_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_source: Mapped[str] = mapped_column(String)
    allocations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)


class SimLot(Base):
    __tablename__ = 'sim_lots'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    symbol: Mapped[str] = mapped_column(String)
    acquired_date: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    remaining_qty: Mapped[int] = mapped_column(Integer)
    cost_minor: Mapped[int] = mapped_column(Integer)
    buy_fill_id: Mapped[str | None] = mapped_column(ForeignKey('sim_fills.id'), nullable=True)
    created_at: Mapped[str] = mapped_column(String)
