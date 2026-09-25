"""JSON 全量备份恢复。"""

from datetime import date, datetime

from sqlalchemy.orm import Session

from ..models import CapitalFlow, DailyReview, FlashCard, MonthlyReview, PendingTrade, Setting, Snapshot, Trade, WeeklyReview

MODEL_KEYS: list[tuple[str, type]] = [
    ("capital_flows", CapitalFlow),
    ("snapshots", Snapshot),
    ("trades", Trade),
    ("pending_trades", PendingTrade),
    ("daily_reviews", DailyReview),
    ("weekly_reviews", WeeklyReview),
    ("monthly_reviews", MonthlyReview),
    ("flash_cards", FlashCard),
    ("settings", Setting),
]

_DATE_COLS = frozenset({
    "flow_date", "snap_date", "trade_date", "review_date", "event_date",
})
_DT_COLS = frozenset({"created_at", "updated_at"})


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.replace("Z", "")[:19]
        return datetime.fromisoformat(raw)
    return None


def _row_from_dict(model: type, raw: dict):
    cols = {c.name for c in model.__table__.columns}
    data: dict = {}
    for key, val in raw.items():
        if key not in cols:
            continue
        if key in _DATE_COLS:
            data[key] = _parse_date(val)
        elif key in _DT_COLS:
            data[key] = _parse_datetime(val)
        else:
            data[key] = val
    return model(**data)


def restore_backup(db: Session, payload: dict) -> dict[str, int]:
    if not isinstance(payload, dict) or "exported_at" not in payload:
        raise ValueError("无效的备份文件：缺少 exported_at 字段")

    counts: dict[str, int] = {}
    for _key, model in reversed(MODEL_KEYS):
        db.query(model).delete()
    db.flush()

    for key, model in MODEL_KEYS:
        rows = payload.get(key)
        if not isinstance(rows, list):
            rows = []
        for item in rows:
            if isinstance(item, dict):
                db.add(_row_from_dict(model, item))
        counts[key] = len(rows)

    db.commit()
    return counts
