"""Read-side composition keeps reviews independent of market storage/providers."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.market.calendar_service import calendar_audit, get_calendar, plan_dates, save_calendar
from trade_app.market.planning_quotes import planning_quotes
from trade_app.market.symbols import market_symbol_key
from trade_app.reviews.planning import digest, planning_baseline, preview_rehearsal
from trade_app.reviews.service import validate_day

router = APIRouter(prefix='/api/v1', tags=['review-planning'])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CalendarDay(StrictBody):
    date: str = Field(min_length=10, max_length=10)
    is_open: bool = Field(strict=True)


class CalendarImport(StrictBody):
    source: str = Field(min_length=3, max_length=2000)
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    days: list[CalendarDay] = Field(min_length=1, max_length=1096)
    expected_revision: int = Field(ge=0, strict=True)


class RehearsalRow(StrictBody):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(default='', max_length=2000)
    qty: int = Field(ge=0, le=100_000_000, strict=True)
    note: str = Field(default='', max_length=2000)
    price: str | None = Field(default=None, max_length=40)


class RehearsalPreview(StrictBody):
    baseline_source: Literal['snapshot', 'ledger'] = 'snapshot'
    expected_baseline_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    rows: list[RehearsalRow] = Field(default_factory=list, max_length=100)
    dataset_ids: list[str] = Field(default_factory=list, max_length=100)
    target_date: str | None = None


@router.get('/market/trading-calendar')
def calendar(session: Session = Depends(read_session)):
    return {'data': get_calendar(session)}


@router.put('/market/trading-calendar')
def import_calendar(request: Request, payload: CalendarImport):
    body = payload.model_dump()
    return _write(request, 'market/trading-calendar', body, lambda session: save_calendar(session, body))


@router.get('/market/trading-calendar/audit')
def audit_calendar(session: Session = Depends(read_session)):
    return {'data': calendar_audit(session)}


@router.get('/market/trading-calendar/plan-date')
def date_suggestion(written_on: str, target_date: str | None = None, session: Session = Depends(read_session)):
    return {'data': plan_dates(session, written_on, target_date)}


@router.get('/accounts/{account_id}/planning/{day}/baseline')
def baseline(account_id: str, day: str, source: Literal['snapshot', 'ledger'] = 'snapshot',
             session: Session = Depends(read_session)):
    return {'data': planning_baseline(session, account_id, day, source, symbol_key=market_symbol_key)}


@router.post('/accounts/{account_id}/planning/{day}/preview')
def preview(request: Request, account_id: str, day: str, payload: RehearsalPreview,
            session: Session = Depends(read_session)):
    validate_day(day)
    base = planning_baseline(session, account_id, day, payload.baseline_source, symbol_key=market_symbol_key)
    quotes = planning_quotes(session, request.app.state.data_dir, day, payload.dataset_ids)
    result = preview_rehearsal(base, [row.model_dump() for row in payload.rows], quotes,
        symbol_key=market_symbol_key, expected_baseline_sha256=payload.expected_baseline_sha256)
    result['calendar'] = plan_dates(session, day, payload.target_date)
    result.pop('sha256')
    result['sha256'] = digest(result)
    return {'data': result}
