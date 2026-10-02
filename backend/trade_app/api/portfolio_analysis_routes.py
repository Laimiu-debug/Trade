from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.research import portfolio_analysis_service as service
from trade_app.research.portfolio_domain import canonical

router = APIRouter(prefix='/api/v1/research/portfolio-analyses', tags=['portfolio-analyses'])


class AnalysisCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    name: str = Field(default='组合高级分析', min_length=1, max_length=80)
    seed: int = Field(default=20260926, ge=0, le=2**32 - 1, strict=True)
    iterations: int = Field(default=400, ge=100, le=2000, strict=True)
    block_size: int = Field(default=5, ge=1, le=60, strict=True)
    plan_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')


@router.get('')
def listing(source_run_id: str | None = None, session: Session = Depends(read_session)):
    return {'data': service.list_analyses(session, source_run_id)}


@router.post('')
def create(request: Request, body: AnalysisCreate):
    value = body.model_dump()
    return _write(request, 'research/portfolio-analyses', value, lambda session: service.create_analysis(session, value))


@router.get('/{run_id}')
def detail(run_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_analysis(session, run_id)}


@router.get('/{run_id}/export.json')
def export(run_id: str, session: Session = Depends(read_session)):
    return Response(canonical(service.get_analysis(session, run_id)), media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="portfolio-analysis-{run_id}.json"'})


@router.post('/{run_id}/{action}')
def control(request: Request, run_id: str, action: Literal['resume', 'retry', 'cancel']):
    return _write(request, f'research/portfolio-analyses/{run_id}/{action}', {},
        lambda session: service.control_analysis(session, run_id, action))


@router.delete('/{run_id}')
def delete(request: Request, run_id: str):
    return _write(request, f'research/portfolio-analyses/{run_id}/delete', {},
        lambda session: service.control_analysis(session, run_id, 'delete'))
