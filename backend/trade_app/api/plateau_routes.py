"""Explicit sampling previews and durable single-symbol parameter experiments."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.research import plateau_service as service
from trade_app.research.plateau_domain import canonical

router = APIRouter(prefix='/api/v1/research/plateaus', tags=['research-plateaus'])


class PlateauPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_run_id: str = Field(min_length=1, max_length=80)
    sampling_mode: Literal['grid', 'lhs'] = 'grid'
    axes: dict = Field(min_length=1, max_length=8)
    seed: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    sample_points: int = Field(default=24, ge=1, le=400, strict=True)
    max_points: int = Field(default=120, ge=1, le=400, strict=True)


class PlateauCreate(PlateauPreview):
    name: str = Field(min_length=1, max_length=80)
    expected_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
def list_experiments(session: Session = Depends(read_session)):
    return {'data': service.list_plateaus(session)}


@router.get('/schema/{base_run_id}')
def schema(base_run_id: str, session: Session = Depends(read_session)):
    source = session.get(service.BacktestRun, base_run_id)
    if source is None:
        raise service.TradeError('BACKTEST_NOT_FOUND', '来源回测不存在', 404)
    return {'data': {'strategy_id': source.strategy_id, 'schema': service.supported_schema(source.strategy_id)}}


@router.post('/preview')
def preview(request: Request, body: PlateauPreview, session: Session = Depends(read_session)):
    return {'data': service.preview_plateau(session, request.app.state.data_dir, body.model_dump())}


@router.post('')
def create(request: Request, payload: PlateauCreate) -> JSONResponse:
    body = payload.model_dump()
    return _write(request, 'research/plateaus', body,
                  lambda session: service.create_plateau(session, request.app.state.data_dir, body))


@router.get('/{experiment_id}')
def detail(experiment_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_plateau(session, experiment_id)}


@router.get('/{experiment_id}/export.json')
def export(experiment_id: str, session: Session = Depends(read_session)):
    return Response(canonical(service.get_plateau(session, experiment_id)), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="plateau-{experiment_id}.json"'})


@router.get('/{experiment_id}/points/{point_id}')
def point(experiment_id: str, point_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_point(session, experiment_id, point_id)}


@router.post('/{experiment_id}/{action}')
def control(request: Request, experiment_id: str, action: Literal['pause', 'resume', 'cancel', 'retry-failed']) -> JSONResponse:
    return _write(request, f'research/plateaus/{experiment_id}/{action}', {},
                  lambda session: service.control_plateau(session, experiment_id, action))


@router.delete('/{experiment_id}')
def delete(request: Request, experiment_id: str) -> JSONResponse:
    return _write(request, f'research/plateaus/{experiment_id}/delete', {},
                  lambda session: service.control_plateau(session, experiment_id, 'delete'))
