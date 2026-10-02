from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, price_text, price_units, utc_now
from trade_app.reviews.models import DailyReview
from trade_app.trading.models import AssetSnapshot, Trade
from trade_app.trading.service import account_or_error


def review_data(row: DailyReview) -> dict:
    return {"id": row.id, "account_id": row.account_id, "review_date": row.review_date,
            "title": row.title, "market_observation": row.market_observation,
            "decision_review": row.decision_review, "mistakes": row.mistakes,
            "tomorrow_plan": row.tomorrow_plan,
            'overall_summary': row.overall_summary, 'reflection': row.reflection,
            'tags': json.loads(row.tags_json),
            'next_market_forecast': row.next_market_forecast,
            'next_watchlist': json.loads(row.next_watchlist_json),
            'next_position_plan': row.next_position_plan,
            'next_risk_plan': row.next_risk_plan,
            'next_position_rehearsal': json.loads(row.next_position_rehearsal_json),
            'next_target_date': row.next_target_date,
            "revision": row.revision,
            "updated_at": row.updated_at}


def validate_day(day: str) -> None:
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError(day)
    except ValueError as exc:
        raise TradeError("INVALID_DATE", "日期必须使用 YYYY-MM-DD 格式") from exc


def next_weekday(day: str) -> str:
    candidate = date.fromisoformat(day) + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def get_review(session: Session, account_id: str, day: str) -> dict | None:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    row = session.scalar(select(DailyReview).where(
        DailyReview.account_id == account_id, DailyReview.review_date == day))
    return review_data(row) if row else None


def list_reviews(session: Session, account_id: str, limit: int = 100) -> list[dict]:
    account_or_error(session, account_id, real=True)
    if limit < 1 or limit > 500:
        raise TradeError('INVALID_LIMIT', '列表数量必须在 1 至 500 之间')
    rows = session.scalars(select(DailyReview).where(DailyReview.account_id == account_id)
                           .order_by(DailyReview.review_date.desc()).limit(limit))
    return [{'date': row.review_date, 'title': row.title,
             'tags': json.loads(row.tags_json), 'revision': row.revision,
             'updated_at': row.updated_at} for row in rows]


def review_gaps(session: Session, account_id: str) -> list[dict]:
    """Dates with confirmed trades or asset snapshots but no saved daily review."""
    account_or_error(session, account_id, real=True)
    trade_days = set(session.scalars(select(Trade.trade_date).where(
        Trade.account_id == account_id, Trade.voided_at.is_(None))))
    snapshot_days = set(session.scalars(select(AssetSnapshot.snap_date).where(
        AssetSnapshot.account_id == account_id)))
    reviewed_days = set(session.scalars(select(DailyReview.review_date).where(
        DailyReview.account_id == account_id)))
    return [{'date': day, 'has_trades': day in trade_days, 'has_snapshot': day in snapshot_days}
            for day in sorted((trade_days | snapshot_days) - reviewed_days, reverse=True)]


def save_review(session: Session, account_id: str, day: str, payload: dict) -> dict:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    row = session.scalar(select(DailyReview).where(
        DailyReview.account_id == account_id, DailyReview.review_date == day))
    expected_revision = payload["expected_revision"]
    before = review_data(row) if row else None
    if row:
        if row.revision != expected_revision:
            raise TradeError("REVISION_CONFLICT", "复盘已在其他页面修改，请核对后保存", 409)
        row.revision += 1
    else:
        if expected_revision != 0:
            raise TradeError("REVISION_CONFLICT", "复盘记录不存在，请刷新后重试", 409)
        now = utc_now()
        row = DailyReview(id=new_id(), account_id=account_id, review_date=day,
                          created_at=now, updated_at=now, revision=1)
        session.add(row)
    for field in ("title", "market_observation", "decision_review", "mistakes", "tomorrow_plan"):
        setattr(row, field, payload.get(field, ""))
    for field in ('overall_summary', 'reflection', 'next_market_forecast',
                  'next_position_plan', 'next_risk_plan'):
        setattr(row, field, payload.get(field, ''))
    tags = [tag.strip() for tag in payload.get('tags', [])]
    if any(not tag or len(tag) > 40 for tag in tags) or len(tags) != len(set(tag.casefold() for tag in tags)):
        raise TradeError('INVALID_REVIEW_TAGS', '标签不能为空、重复或超过 40 字')
    row.tags_json = json.dumps(tags, ensure_ascii=False)
    watchlist = payload.get('next_watchlist', [])
    if any(set(item) - {'code', 'name', 'condition', 'action'} or
           any(len(value) > 2000 for value in item.values()) or
           not item.get('code', '').strip() for item in watchlist):
        raise TradeError('INVALID_WATCHLIST', '关注股需填写代码，且字段内容不超过 2000 字')
    row.next_watchlist_json = json.dumps(watchlist, ensure_ascii=False)
    rehearsal = payload.get('next_position_rehearsal', [])
    if any(set(item) - {'code', 'name', 'qty', 'note', 'price'} or
           not str(item.get('code', '')).strip() or
           type(item.get('qty')) is not int or item['qty'] < 0 or item['qty'] > 100_000_000 or
           any(len(value) > 2000 for value in item.values() if isinstance(value, str))
           for item in rehearsal):
        raise TradeError('INVALID_REHEARSAL', '持仓预演需填写代码与非负数量')
    rehearsal = [{**item, 'price': price_text(price_units(item['price']))}
                 if item.get('price') else {key: value for key, value in item.items() if key != 'price'}
                 for item in rehearsal]
    row.next_position_rehearsal_json = json.dumps(rehearsal, ensure_ascii=False)
    # New blank targets stay unknown; existing saved dates are never migrated.
    # Calendar suggestions are an explicit UI action, not a save-time guess.
    raw_target = payload.get('next_target_date') if 'next_target_date' in payload else (before or {}).get('next_target_date')
    target = str(raw_target) if raw_target else None
    if target:
        validate_day(target)
        if target <= day or (date.fromisoformat(target) - date.fromisoformat(day)).days > 30:
            raise TradeError('INVALID_PLAN_TARGET', '计划执行日须晚于编写日且不超过 30 天')
    row.next_target_date = target
    row.updated_at = utc_now()
    session.flush()
    data = review_data(row)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type="daily_review",
                           entity_id=row.id, operation="update" if before else "create",
                           before_json=json.dumps(before, ensure_ascii=False) if before else None,
                           after_json=json.dumps(data, ensure_ascii=False), created_at=utc_now()))
    return data
