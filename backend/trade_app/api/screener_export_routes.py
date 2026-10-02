from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import read_session
from trade_app.api.screener_export import build_export, render_export

router = APIRouter(prefix='/api/v1/research', tags=['screening-exports'])


class ExportSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: str | None = None
    dataset_ids: list[str] | None = Field(default=None, min_length=1, max_length=12000)
    columns: list[str] | None = Field(default=None, min_length=1, max_length=40)


def _export(session, kind, run_id, format, body):
    value = build_export(session, kind, run_id, body.model_dump())
    content = render_export(value, format)
    media = {'pdf': 'application/pdf', 'csv': 'text/csv', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}[format]
    return Response(content, media_type=media, headers={'Cache-Control': 'no-store',
        'Content-Disposition': f'attachment; filename="trade-{kind}-{run_id[:16]}-{value["metadata"]["stage"]}.{format}"',
        'X-Export-Selection-SHA256': value['metadata']['selection_sha256'], 'X-Export-Row-Count': str(len(value['rows']))})


@router.post('/screener-runs/{run_id}/exports/{format}')
def funnel(run_id: str, format: Literal['csv', 'xlsx', 'pdf'], body: ExportSelection, session: Session = Depends(read_session)):
    return _export(session, 'funnel', run_id, format, body)


@router.post('/b1-runs/{run_id}/exports/{format}')
def b1(run_id: str, format: Literal['csv', 'xlsx', 'pdf'], body: ExportSelection, session: Session = Depends(read_session)):
    return _export(session, 'b1', run_id, format, body)
