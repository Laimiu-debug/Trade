from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.api.portfolio_experiment_routes import Preview as Sampling
from trade_app.research import portfolio_walk_forward_service as service
from trade_app.research.portfolio_domain import canonical

router = APIRouter(prefix='/api/v1/research/portfolio-walk-forwards', tags=['portfolio-walk-forwards'])


class Preview(Sampling):
    initial_train_bars: int = Field(default=60, ge=32, le=1800, strict=True)
    test_bars: int = Field(default=20, ge=10, le=1000, strict=True)
    gap_bars: int = Field(default=1, ge=0, le=60, strict=True)
    max_folds: int = Field(default=4, ge=1, le=12, strict=True)
    min_train_cycles: int = Field(default=1, ge=0, le=1000, strict=True)


class Create(Preview):
    name: str = Field(min_length=1, max_length=80)
    expected_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
def listing(session: Session = Depends(read_session)):
    return {'data': service.list_walk_forwards(session)}


@router.post('/preview')
def preview(body: Preview, session: Session = Depends(read_session)):
    return {'data': service.preview_walk_forward(session, body.model_dump())}


@router.post('')
def create(request: Request, body: Create):
    payload = body.model_dump()
    return _write(request, 'research/portfolio-walk-forwards', payload, lambda session: service.create_walk_forward(session, payload))


@router.get('/{identifier}')
def detail(identifier: str, session: Session = Depends(read_session)):
    return {'data': service.get_walk_forward(session, identifier)}


@router.get('/{identifier}/tasks/{task_id}')
def task(identifier: str, task_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_task(session, identifier, task_id)}


@router.get('/{identifier}/export.json')
def export(identifier: str, session: Session = Depends(read_session)):
    return Response(canonical(service.get_walk_forward(session, identifier)), media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="portfolio-walk-forward-{identifier}.json"'})


@router.post('/{identifier}/{action}')
def control(request: Request, identifier: str, action: Literal['pause', 'resume', 'cancel', 'retry']):
    return _write(request, f'research/portfolio-walk-forwards/{identifier}/{action}', {}, lambda session: service.control_walk_forward(session, identifier, action))


@router.delete('/{identifier}')
def delete(request: Request, identifier: str):
    return _write(request, f'research/portfolio-walk-forwards/{identifier}/delete', {}, lambda session: service.control_walk_forward(session, identifier, 'delete'))
