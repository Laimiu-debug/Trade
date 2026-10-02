from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.ai import quick_prompts, context_sources

router = APIRouter(prefix='/api/v1/ai', tags=['ai-context'])


class Save(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0, strict=True)
    items: list[dict] = Field(max_length=100)


@router.get('/quick-prompts')
def list_prompts(session: Session = Depends(read_session)):
    return {'data': quick_prompts.get_prompts(session)}


@router.put('/quick-prompts')
def save(request: Request, body: Save):
    return _write(request, 'ai/quick-prompts', body.model_dump(),
                  lambda session: quick_prompts.save_prompts(session, body.expected_revision, body.items))


@router.get('/quick-prompts/history')
def history(session: Session = Depends(read_session)):
    return {'data': quick_prompts.history(session)}


@router.get('/context-sources')
def sources(session: Session = Depends(read_session)):
    return {'data': context_sources.catalog(session)}
