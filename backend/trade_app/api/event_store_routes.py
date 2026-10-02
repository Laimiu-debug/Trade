from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.platform.types import TradeError
from trade_app.research import event_store_service as service

router = APIRouter(prefix='/api/v1/research/event-store', tags=['wyckoff-event-store'])


class BackfillPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_ids: list[str] = Field(min_length=1, max_length=100)
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    window_days: list[int] = Field(default_factory=lambda: [60], min_length=1, max_length=3)
    strict: bool = Field(default=True, strict=True)
    event_profile_id: str | None = Field(default=None, max_length=100)
    event_profile_revision: int | None = Field(default=None, ge=0, strict=True)


class BackfillCreate(BackfillPreview):
    expected_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/stats')
def stats(session: Session = Depends(read_session)):
    return {'data': service.statistics(session)}


@router.post('/preview')
def preview(request: Request, payload: BackfillPreview, session: Session = Depends(read_session)):
    prepared = service.prepare_backfill(session, request.app.state.data_dir, payload.model_dump())
    return {'data': service.preview_backfill(session, prepared)}


@router.post('/jobs')
def create_job(request: Request, payload: BackfillCreate):
    body = payload.model_dump()
    with request.app.state.db_factory() as session:
        prepared = service.prepare_backfill(session, request.app.state.data_dir,
            {key: value for key, value in body.items() if key != 'expected_preview_sha256'})
    if service.digest(prepared) != body['expected_preview_sha256']:
        raise TradeError('EVENT_STORE_PREVIEW_CHANGED', '行情、模板或算法版本已变化，请重新预览回填', 409)
    return _write(request, 'research/event-store/jobs', body, lambda session: service.enqueue_backfill(session, prepared))


@router.get('/jobs')
def jobs(session: Session = Depends(read_session)):
    return {'data': service.list_jobs(session)}


@router.get('/jobs/{job_id}')
def job(job_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_job(session, job_id)}


@router.post('/jobs/{job_id}/{action}')
def control(request: Request, job_id: str, action: Literal['cancel', 'resume']):
    return _write(request, f'research/event-store/jobs/{job_id}/{action}', {},
                  lambda session: service.control_job(session, job_id, action))


@router.get('/records')
def records(job_id: str | None = None, version_id: str | None = None, symbol: str | None = None,
            limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000),
            session: Session = Depends(read_session)):
    return {'data': service.list_records(session, job_id=job_id, version_id=version_id,
                                        symbol=symbol, limit=limit, offset=offset)}


@router.get('/records/{record_id}')
def record(record_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_record(session, record_id)}
