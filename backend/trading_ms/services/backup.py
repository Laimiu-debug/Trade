"""JSON 全量备份恢复。"""

from datetime import date, datetime
import math

from sqlalchemy import create_engine, Float, Integer, String, Text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import Base
from ..models import CapitalFlow, DailyReview, FlashCard, MonthlyReview, PendingTrade, RoundReview, Setting, Snapshot, Trade, WeeklyReview

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
OPTIONAL_MODEL_KEYS = [('round_reviews', RoundReview)]
SECRET_KEYS = {'ai_api_key', 'ai_score_api_key', 'ai_ocr_api_key'}

_DATE_COLS = frozenset({
    "flow_date", "snap_date", "trade_date", "review_date", "event_date", "start_date",
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
    if not isinstance(raw, dict):
        raise ValueError('备份记录必须为对象')
    cols = {c.name: c for c in model.__table__.columns}
    if set(raw) - set(cols):
        raise ValueError('备份记录包含未知字段')
    for column in cols.values():
        if (column.primary_key or (not column.nullable and column.default is None)) and column.name not in raw:
            raise ValueError('备份记录缺少必要字段')
    data: dict = {}
    for key, val in raw.items():
        column = cols[key]
        if val is None:
            if not column.nullable:
                raise ValueError('备份必要字段不能为空')
            data[key] = None
            continue
        if key in _DATE_COLS:
            data[key] = _parse_date(val)
        elif key in _DT_COLS:
            data[key] = _parse_datetime(val)
        else:
            if isinstance(column.type, Float) and (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)):
                raise ValueError('备份金额必须为有限数值')
            if isinstance(column.type, Integer) and type(val) is not int:
                raise ValueError('备份整数字段无效')
            if isinstance(column.type, (String, Text)) and not isinstance(val, str):
                raise ValueError('备份文本字段无效')
            data[key] = val
    return model(**data)


def export_backup(db: Session) -> dict:
    payload = {'format': 'trading-ms-backup-v2', 'exported_at': date.today().isoformat(),
               'credentials_excluded': True}
    for key, model in MODEL_KEYS + OPTIONAL_MODEL_KEYS:
        payload[key] = [{column.name: (value.isoformat() if hasattr(value, 'isoformat') else value)
                         for column in model.__table__.columns
                         for value in [getattr(row, column.name)]}
                        for row in db.query(model).all()
                        if model is not Setting or row.key not in SECRET_KEYS]
    return payload


def restore_backup(db: Session, payload: dict) -> dict[str, int]:
    if not isinstance(payload, dict) or not isinstance(payload.get('exported_at'), str):
        raise ValueError('无效的备份文件：缺少导出日期')
    try:
        date.fromisoformat(payload['exported_at'])
    except ValueError as exc:
        raise ValueError('备份导出日期无效') from exc
    if payload.get('format') not in (None, 'trading-ms-backup-v2'):
        raise ValueError('备份格式不受支持')
    models = MODEL_KEYS + [(key, model) for key, model in OPTIONAL_MODEL_KEYS if key in payload]
    if payload.get('format') == 'trading-ms-backup-v2' and any(key not in payload for key, _ in OPTIONAL_MODEL_KEYS):
        raise ValueError('备份缺少回合复盘清单')
    for key, _model in models:
        if not isinstance(payload.get(key), list):
            raise ValueError(f'备份缺少完整数据清单: {key}')
    validation_engine = create_engine('sqlite:///:memory:')
    try:
        Base.metadata.create_all(validation_engine)
        with Session(validation_engine) as validation:
            for key, model in models:
                validation.add_all([_row_from_dict(model, item) for item in payload[key]])
            validation.flush()  # Check constraints and dates before touching existing rows.
    except (SQLAlchemyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('备份记录校验失败，原数据未修改') from exc
    finally:
        validation_engine.dispose()
    retained_secrets = [{'key': row.key, 'value': row.value} for row in db.query(Setting).all()
                        if row.key in SECRET_KEYS]
    payload = {**payload, 'settings': [item for item in payload['settings'] if item['key'] not in SECRET_KEYS]}
    try:
        for _key, model in reversed(models): db.query(model).delete()
        db.flush()
        for key, model in models:
            db.add_all([_row_from_dict(model, item) for item in payload[key]])
        incoming_keys = {item['key'] for item in payload['settings']}
        db.add_all([Setting(**item) for item in retained_secrets if item['key'] not in incoming_keys])
        db.commit()
    except Exception:
        db.rollback()  # A failing insert must never leave the preceding deletes committed.
        raise
    return {key: len(payload[key]) for key, _model in models}
