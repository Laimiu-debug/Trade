from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_app.platform.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String, default="CNY")
    input_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)


class RealFeeConfig(Base):
    __tablename__ = 'real_fee_configs'

    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), primary_key=True)
    config_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("account_id", "trade_date", "sequence"),
        CheckConstraint("quantity > 0"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    trade_date: Mapped[str] = mapped_column(String)
    sequence: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, default="")
    side: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    price_units: Mapped[int] = mapped_column(Integer)
    fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    calculated_fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    fee_source: Mapped[str] = mapped_column(String, default='manual')
    fee_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    voided_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class PendingTrade(Base):
    __tablename__ = "pending_trades"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    trade_date: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, default="")
    side: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    price_units: Mapped[int] = mapped_column(Integer)
    fee_minor: Mapped[int] = mapped_column(Integer)
    calculated_fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    fee_source: Mapped[str] = mapped_column(String, default='manual')
    fee_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String, default="manual")
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String)
    confirmed_trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class CashFlow(Base):
    __tablename__ = "cash_flows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    flow_date: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    amount_minor: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    voided_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AssetSnapshot(Base):
    __tablename__ = "asset_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "snap_date"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    snap_date: Mapped[str] = mapped_column(String)
    total_assets_minor: Mapped[int] = mapped_column(Integer)
    available_cash_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_value_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    positions: Mapped[list[SnapshotPosition]] = relationship(
        'SnapshotPosition', cascade='all, delete-orphan', order_by='SnapshotPosition.sequence')


class SnapshotPosition(Base):
    __tablename__ = 'asset_snapshot_positions'
    __table_args__ = (UniqueConstraint('snapshot_id', 'sequence'),
                      UniqueConstraint('snapshot_id', 'symbol'))

    id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('asset_snapshots.id', ondelete='CASCADE'))
    sequence: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, default='')
    quantity: Mapped[int] = mapped_column(Integer)
    market_value_minor: Mapped[int] = mapped_column(Integer)
