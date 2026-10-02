"""Asynchronous scan routes; preparation never holds SQLite's writer lock."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.api.schemas import StrategyScanCreate
from trade_app.research.scan_job_service import (cancel_scan_job, enqueue_scan_job, get_scan_job,
                                                 list_scan_jobs, prepare_scan_job, retry_scan_job)

router = APIRouter(prefix='/api/v1/research/scan-jobs')


@router.post('')
def create_job(request: Request, payload: StrategyScanCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    with request.app.state.db_factory() as session:
        prepared = prepare_scan_job(session, request.app.state.data_dir, body)
    return _write(request, 'research/scan-jobs', body, lambda session: enqueue_scan_job(session, prepared))


@router.get('')
def list_jobs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_scan_jobs(session)}


@router.get('/{job_id}')
def get_job(job_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_scan_job(session, job_id)}


@router.post('/{job_id}/cancel')
def cancel_job(request: Request, job_id: str) -> JSONResponse:
    return _write(request, f'research/scan-jobs/{job_id}/cancel', {},
                  lambda session: cancel_scan_job(session, job_id))


@router.post('/{job_id}/retry')
def retry_job(request: Request, job_id: str) -> JSONResponse:
    return _write(request, f'research/scan-jobs/{job_id}/retry', {},
                  lambda session: retry_scan_job(session, job_id))
