from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.research import portfolio_experiment_service as service
from trade_app.research.portfolio_domain import canonical

router = APIRouter(prefix='/api/v1/research/portfolio-experiments', tags=['portfolio-experiments'])


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_run_id: str = Field(min_length=1, max_length=80)
    sampling_mode: Literal['grid', 'lhs'] = 'grid'
    axes: dict = Field(min_length=1, max_length=8)
    seed: int = Field(default=0, ge=0, le=2147483647, strict=True)
    sample_points: int = Field(default=24, ge=1, le=400, strict=True)
    max_points: int = Field(default=120, ge=1, le=400, strict=True)


class Create(Preview):
    name: str = Field(min_length=1, max_length=80)
    expected_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
def listing(session: Session = Depends(read_session)):
    return {'data': service.list_experiments(session)}


@router.get('/schema/{source_id}')
def schema(source_id: str, session: Session = Depends(read_session)):
    context = service._source(session, source_id)
    return {'data': {'schema': service.supported_schema(context), 'mode': context['mode']}}


@router.post('/preview')
def preview(body: Preview, session: Session = Depends(read_session)):
    return {'data': service.preview_experiment(session, body.model_dump())}


@router.post('')
def create(request: Request, body: Create):
    payload = body.model_dump()
    return _write(request, 'research/portfolio-experiments', payload, lambda session: service.create_experiment(session, payload))


@router.get('/{identifier}')
def detail(identifier: str, session: Session = Depends(read_session)):
    return {'data': service.get_experiment(session, identifier)}


@router.get('/{identifier}/points/{point_id}')
def point(identifier: str, point_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_point(session, identifier, point_id)}


@router.get('/{identifier}/export.json')
def export(identifier: str, session: Session = Depends(read_session)):
    return Response(canonical(service.get_experiment(session, identifier)), media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="portfolio-experiment-{identifier}.json"'})


@router.post('/{identifier}/{action}')
def control(request: Request, identifier: str, action: Literal['pause', 'resume', 'cancel', 'retry']):
    return _write(request, f'research/portfolio-experiments/{identifier}/{action}', {}, lambda session: service.control_experiment(session, identifier, action))


@router.delete('/{identifier}')
def delete(request: Request, identifier: str):
    return _write(request, f'research/portfolio-experiments/{identifier}/delete', {}, lambda session: service.control_experiment(session, identifier, 'delete'))
