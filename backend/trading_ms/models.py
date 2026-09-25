from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class CapitalFlow(Base):
    """入金/出金/初始资金流水。"""

    __tablename__ = "capital_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # initial | deposit | withdraw
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")


class Snapshot(Base):
    """每日收盘账户快照（总资产 + 持仓明细）。"""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snap_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    total_assets: Mapped[float] = mapped_column(Float)
    available_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    positions: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str] = mapped_column(Text, default="")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(32), default="")
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer)
    fee_commission: Mapped[float] = mapped_column(Float, default=0.0)
    fee_stamp: Mapped[float] = mapped_column(Float, default=0.0)
    fee_transfer: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | import


class PendingTrade(Base):
    """截图识别后待人工确认的交易。"""

    __tablename__ = "pending_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(32), default="")
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DailyReview(Base):
    __tablename__ = "daily_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    market_observation: Mapped[str] = mapped_column(Text, default="")
    decision_review: Mapped[str] = mapped_column(Text, default="")
    mistakes: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [path, ...]
    # 打分: JSON {dim: {"ai": int|null, "final": int|null, "comment": str}}
    scores: Mapped[str] = mapped_column(Text, default="{}")
    trade_scores: Mapped[str] = mapped_column(Text, default="{}")  # { trade_id: { dim: {ai, final, comment} } }
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    # 次日预研
    next_market_forecast: Mapped[str] = mapped_column(Text, default="")
    next_watchlist: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{code,name,condition,action}]
    next_position_plan: Mapped[str] = mapped_column(Text, default="")
    next_risk_plan: Mapped[str] = mapped_column(Text, default="")
    next_position_rehearsal: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{code,name,qty,note?}]
    rehearsal_ai_analysis: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"
    __table_args__ = (UniqueConstraint("year", "week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)  # ISO 周号
    right_things: Mapped[str] = mapped_column(Text, default="")
    wrong_things: Mapped[str] = mapped_column(Text, default="")
    market_review: Mapped[str] = mapped_column(Text, default="")
    next_strategy: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MonthlyReview(Base):
    __tablename__ = "monthly_reviews"
    __table_args__ = (UniqueConstraint("year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    system_iteration: Mapped[str] = mapped_column(Text, default="")
    next_goal: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class FlashCard(Base):
    __tablename__ = "flash_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(String(128), default="")  # 逗号分隔
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class NodeEvent(Base):
    """节点点亮/熄灭事件史，由净值序列重算生成。"""

    __tablename__ = "node_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[int] = mapped_column(Integer, index=True)  # 1..50
    kind: Mapped[str] = mapped_column(String(16))  # lit | extinguished
    event_date: Mapped[date] = mapped_column(Date)
    net_value: Mapped[float] = mapped_column(Float)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class RoundReview(Base):
    """回合级复盘摘要（用户编辑或 AI 生成）。"""

    __tablename__ = "round_reviews"
    __table_args__ = (UniqueConstraint("code", "start_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    review_summary: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
