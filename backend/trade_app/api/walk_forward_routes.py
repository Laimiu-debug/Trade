from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from trade_app.api.plateau_routes import PlateauPreview
from trade_app.api.routes import _write, read_session
from trade_app.research.plateau_domain import canonical
from trade_app.research import walk_forward_service as service

router = APIRouter(prefix='/api/v1/research/walk-forwards', tags=['walk-forward'])


class WalkForwardPreview(PlateauPreview):
    initial_train_bars: int = Field(default=120, ge=32, le=1800, strict=True)
    test_bars: int = Field(default=40, ge=10, le=1000, strict=True)
    gap_bars: int = Field(default=1, ge=0, le=60, strict=True)
    warmup_bars: int = Field(default=0, ge=0, le=500, strict=True)
    max_folds: int = Field(default=4, ge=1, le=12, strict=True)
    min_train_trades: int = Field(default=3, ge=0, le=1000, strict=True)


class WalkForwardCreate(WalkForwardPreview):
    name: str = Field(min_length=1, max_length=80)
    expected_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
def listing(session: Session = Depends(read_session)):
    return {'data': service.list_walk_forwards(session)}


@router.post('/preview')
def preview(request: Request, body: WalkForwardPreview, session: Session = Depends(read_session)):
    return {'data': service.preview_walk_forward(session, request.app.state.data_dir, body.model_dump())}


@router.post('')
def create(request: Request, payload: WalkForwardCreate):
    body = payload.model_dump()
    return _write(request, 'research/walk-forwards', body,
                  lambda session: service.create_walk_forward(session, request.app.state.data_dir, body))


@router.get('/{job_id}')
def detail(job_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_walk_forward(session, job_id)}


@router.get('/{job_id}/export.json')
def export(job_id: str, session: Session = Depends(read_session)):
    return Response(canonical(service.get_walk_forward(session, job_id)), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="walk-forward-{job_id}.json"'})


@router.get('/{job_id}/tasks/{task_id}')
def task(job_id: str, task_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_task(session, job_id, task_id)}


@router.post('/{job_id}/{action}')
def control(request: Request, job_id: str, action: Literal['pause', 'resume', 'cancel', 'retry']):
    return _write(request, f'research/walk-forwards/{job_id}/{action}', {},
                  lambda session: service.control_walk_forward(session, job_id, action))


@router.delete('/{job_id}')
def delete(request: Request, job_id: str):
    return _write(request, f'research/walk-forwards/{job_id}/delete', {},
                  lambda session: service.control_walk_forward(session, job_id, 'delete'))
