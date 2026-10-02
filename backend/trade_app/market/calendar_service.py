"""Explicit local schedules: absence or coverage gaps never imply a trading day."""
from datetime import date, timedelta
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.calendar_models import LocalTradingCalendar
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now


def _day(raw) -> str:
    try:
        if date.fromisoformat(raw).isoformat() != raw:
            raise ValueError(raw)
        return raw
    except (TypeError, ValueError) as exc:
        raise TradeError('INVALID_CALENDAR_DATE', '日历日期须为 YYYY-MM-DD') from exc


def calendar_data(row: LocalTradingCalendar | None, *, include_days=True) -> dict:
    if row is None:
        return {'market': 'CN_A', 'revision': 0, 'source': None, 'start_date': None,
                'end_date': None, 'sha256': None, 'updated_at': None, 'days': []}
    result = {key: getattr(row, key) for key in ('market', 'revision', 'source', 'start_date',
                                               'end_date', 'sha256', 'updated_at')}
    days = json.loads(row.days_json)
    if not days:
        # A default reset retains a versioned tombstone: no coverage is known.
        result.update(source=None, start_date=None, end_date=None, sha256=None)
    if include_days:
        result['days'] = days
    return result


def get_calendar(session: Session) -> dict:
    return calendar_data(session.get(LocalTradingCalendar, 'CN_A'))


def save_calendar(session: Session, body: dict) -> dict:
    if set(body) != {'source', 'start_date', 'end_date', 'days', 'expected_revision'}:
        raise TradeError('INVALID_CALENDAR', '日历字段不完整或包含额外字段')
    source = body['source']
    if not isinstance(source, str) or not 3 <= len(source.strip()) <= 2000:
        raise TradeError('INVALID_CALENDAR_SOURCE', '请填写 3 至 2000 字的日历来源说明')
    start, end = _day(body['start_date']), _day(body['end_date'])
    count = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if not 1 <= count <= 1096:
        raise TradeError('INVALID_CALENDAR_RANGE', '日历范围须为 1 至 1096 个自然日')
    days = body['days']
    if not isinstance(days, list) or len(days) != count:
        raise TradeError('INCOMPLETE_CALENDAR', '区间内每个自然日都须明确填写是否开市，不可缺日')
    expected = [(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(count)]
    normalized = []
    for day, item in zip(expected, days):
        if (not isinstance(item, dict) or set(item) != {'date', 'is_open'}
                or item['date'] != day or type(item['is_open']) is not bool):
            raise TradeError('INCOMPLETE_CALENDAR', '日历须按日期连续升序且 is_open 为布尔值')
        normalized.append({'date': day, 'is_open': item['is_open']})
    row = session.get(LocalTradingCalendar, 'CN_A')
    before = calendar_data(row)
    if type(body['expected_revision']) is not int or body['expected_revision'] != before['revision']:
        raise TradeError('REVISION_CONFLICT', '本地日历已变化，请重新核对', 409)
    content = {'source': source.strip(), 'start_date': start, 'end_date': end, 'days': normalized}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if row is None:
        row = LocalTradingCalendar(market='CN_A')
        session.add(row)
    row.source, row.start_date, row.end_date = source.strip(), start, end
    row.days_json = json.dumps(normalized)
    row.sha256, row.revision, row.updated_at = digest, before['revision'] + 1, utc_now()
    result = calendar_data(row)
    session.add(AuditEvent(id=new_id(), account_id=None, entity_type='local_trading_calendar',
        entity_id='CN_A', operation='import', before_json=json.dumps(before, ensure_ascii=False),
        after_json=json.dumps(result, ensure_ascii=False), created_at=utc_now()))
    session.flush()
    return result


def calendar_audit(session: Session) -> list[dict]:
    rows = session.scalars(select(AuditEvent).where(AuditEvent.entity_type == 'local_trading_calendar',
        AuditEvent.entity_id == 'CN_A').order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(50))
    return [{'id': row.id, 'created_at': row.created_at, 'snapshot': json.loads(row.after_json)} for row in rows]


def plan_dates(session: Session, written_on: str, target_date: str | None = None) -> dict:
    _day(written_on)
    if target_date:
        _day(target_date)
    row = session.get(LocalTradingCalendar, 'CN_A')
    by_day = {item['date']: item['is_open'] for item in json.loads(row.days_json)} if row else {}
    candidate = (date.fromisoformat(written_on) + timedelta(days=1)).isoformat()
    next_date = None
    if candidate in by_day:
        # Completeness was checked on import; no weekend or holiday inference.
        next_date = next((day for day, opened in by_day.items() if day >= candidate and opened), None)
        if next_date and (date.fromisoformat(next_date) - date.fromisoformat(written_on)).days > 30:
            next_date = None
    status = 'unknown' if not target_date or target_date not in by_day else ('open' if by_day[target_date] else 'closed')
    return {'written_on': written_on, 'target_date': target_date, 'target_status': status,
            'suggested_date': next_date, 'suggestion_status': 'local_calendar' if next_date else 'unknown',
            'calendar': calendar_data(row, include_days=False),
            'method': '仅按用户导入的完整本地日历判断；未覆盖时交易日未知，需手填。来源内容由用户核对。'}
