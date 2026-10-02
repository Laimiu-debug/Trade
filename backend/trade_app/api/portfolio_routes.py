from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.research import portfolio_service as service
from trade_app.research.portfolio_domain import canonical

router = APIRouter(prefix='/api/v1/research/portfolios', tags=['research-portfolios'])


class PortfolioPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_ids: list[str] = Field(min_length=1, max_length=64)
    mode: Literal['matrix_raw_s1_s9', 'traditional_runtime14', 'aligned_wyckoff_events']
    strategy_id: str | None = Field(default=None, max_length=80)
    params: dict = Field(default_factory=dict, max_length=100)
    config: dict = Field(default_factory=dict, max_length=30)
    start_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    event_profile_id: str | None = None
    event_profile_revision: int | None = Field(default=None, ge=0)


class PortfolioCreate(PortfolioPreview):
    name: str = Field(min_length=1, max_length=80)
    expected_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('')
def listing(session: Session = Depends(read_session)):
    return {'data': service.list_portfolios(session)}


@router.get('/schema')
def schema():
    return {'data': {'defaults': service.DEFAULTS, 'event_defaults': service.EVENT_DEFAULTS, 'event_schema': service.EVENT_PARAM_SCHEMA,
                     'max_symbols': service.MAX_SYMBOLS, 'max_bars': service.MAX_BARS,
                     'universe_scope': 'fixed_research_sample'}}


@router.post('/preview')
def preview(request: Request, body: PortfolioPreview, session: Session = Depends(read_session)):
    return {'data': service.preview_portfolio(session, request.app.state.data_dir, body.model_dump())}


@router.post('')
def create(request: Request, body: PortfolioCreate):
    payload = body.model_dump()
    return _write(request, 'research/portfolios', payload,
                  lambda session: service.create_portfolio(session, request.app.state.data_dir, payload))


@router.get('/{run_id}')
def detail(run_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_portfolio(session, run_id)}


@router.get('/{run_id}/result')
def result(run_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_portfolio(session, run_id, full=True)}


@router.get('/{run_id}/export.json')
def export(run_id: str, session: Session = Depends(read_session)):
    return Response(canonical(service.get_portfolio(session, run_id, full=True)), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="portfolio-{run_id}.json"'})


@router.post('/{run_id}/{action}')
def control(request: Request, run_id: str, action: Literal['pause', 'resume', 'cancel', 'retry']):
    return _write(request, f'research/portfolios/{run_id}/{action}', {},
                  lambda session: service.control_portfolio(session, run_id, action))


@router.delete('/{run_id}')
def delete(request: Request, run_id: str):
    return _write(request, f'research/portfolios/{run_id}/delete', {},
                  lambda session: service.control_portfolio(session, run_id, 'delete'))
