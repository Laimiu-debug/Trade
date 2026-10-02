"""AI transport: local writes are idempotent; only explicit POST starts inference."""
from __future__ import annotations

from datetime import date
import json
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from trade_app.ai import config, service
from trade_app.api.routes import _write, read_session
from trade_app.platform.types import TradeError


router = APIRouter(prefix='/api/v1/ai', tags=['ai'])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProviderConfig(StrictBody):
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=128)
    secret_ref: str = Field(max_length=89)


class AIConfigSave(StrictBody):
    expected_revision: int = Field(ge=0, strict=True)
    text: ProviderConfig
    vision: ProviderConfig
    temperature: float = Field(ge=0, le=2, strict=True)
    max_tokens: int = Field(ge=16, le=8192, strict=True)
    timeout_seconds: int = Field(ge=5, le=120, strict=True)


class SessionCreate(StrictBody):
    title: str = Field(default='新会话', min_length=1, max_length=128)
    account_id: str | None = Field(default=None, max_length=80)


class Revision(StrictBody):
    expected_revision: int = Field(ge=1, strict=True)


class PromptContext(StrictBody):
    manual_text: str = Field(default='', max_length=12000)
    dataset_ids: list[str] = Field(default_factory=list, max_length=5)
    run_ids: list[str] = Field(default_factory=list, max_length=10)
    decision_at: str | None = Field(default=None, max_length=64)
    strict: bool = Field(default=True, strict=True)
    account_date: date | None = None
    artifact_sources: list[dict] = Field(default_factory=list, max_length=3)


class PromptRequest(StrictBody):
    message: str = Field(min_length=1, max_length=12000)
    channel: Literal['text', 'vision'] = 'text'
    config_revision: int = Field(ge=0, strict=True)
    context: PromptContext = Field(default_factory=PromptContext)
    template_id: str | None = Field(default=None, max_length=128)
    template_revision: int | None = Field(default=None, ge=0, strict=True)
    expected_input_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class ConnectionTest(StrictBody):
    channel: Literal['text', 'vision'] = 'text'
    config_revision: int = Field(ge=0, strict=True)


class TemplateCreate(StrictBody):
    name: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=16000)


class TemplateSave(TemplateCreate):
    expected_revision: int = Field(ge=1, strict=True)


def _scope(path: str, account_id: str | None) -> str:
    # A replay key from another account must never return the first account's data.
    return f'ai/{account_id or "unbound"}/{path}'


@router.get('/config')
def get_config(session: Session = Depends(read_session)) -> dict:
    return {'data': config.get_config(session)}


@router.put('/config')
def save_config(request: Request, payload: AIConfigSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'ai/config', body, lambda session: config.update_config(session, body))


@router.post('/test-connection')
def test_connection(request: Request, payload: ConnectionTest, account_id: str | None = None) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, _scope('test-connection', account_id), body,
                  lambda session: service.prepare_connection_test(session, body, account_id))


@router.get('/sessions')
def sessions(account_id: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_sessions(session, account_id)}


@router.post('/sessions')
def create_session(request: Request, payload: SessionCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, _scope('sessions', body['account_id']), body,
                  lambda session: service.create_session(session, body))


@router.get('/sessions/{session_id}')
def get_session(session_id: str, account_id: str | None = None,
                session: Session = Depends(read_session)) -> dict:
    return {'data': service.get_session(session, session_id, account_id)}


@router.delete('/sessions/{session_id}')
def delete_session(request: Request, session_id: str, expected_revision: int,
                   account_id: str | None = None) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, _scope(f'sessions/{session_id}/delete', account_id), body,
                  lambda session: service.delete_session(session, session_id, body, account_id))


@router.post('/sessions/{session_id}/preview')
def preview(request: Request, session_id: str, payload: PromptRequest, account_id: str | None = None,
            session: Session = Depends(read_session)) -> dict:
    return {'data': service.preview_prompt(session, request.app.state.data_dir, session_id,
                                           payload.model_dump(mode='json', exclude_none=True), account_id)}


@router.post('/sessions/{session_id}/runs')
def prepare_run(request: Request, session_id: str, payload: PromptRequest,
                account_id: str | None = None) -> JSONResponse:
    body = payload.model_dump(mode='json', exclude_none=True)
    return _write(request, _scope(f'sessions/{session_id}/runs', account_id), body,
                  lambda session: service.prepare_run(session, request.app.state.data_dir, session_id, body, account_id))


@router.get('/runs')
def runs(account_id: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_runs(session, account_id)}


@router.get('/runs/{run_id}')
def get_run(run_id: str, account_id: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': service.get_run(session, run_id, account_id)}


@router.post('/runs/{run_id}/cancel')
def cancel_run(request: Request, run_id: str, account_id: str | None = None) -> JSONResponse:
    return _write(request, _scope(f'runs/{run_id}/cancel', account_id), {},
                  lambda session: service.cancel_run(session, run_id, account_id))


@router.post('/runs/{run_id}/stream')
def stream_run(request: Request, run_id: str, account_id: str | None = None) -> StreamingResponse:
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    iterator = service.stream_run(request.app.state.db_factory, run_id, account_id,
                                  should_stop=request.app.state.compute_should_stop,
                                  data_dir=request.app.state.data_dir)
    # Claim before response headers; a repeated start returns 409 without another provider call.
    first = next(iterator)
    sentinel = object()

    def advance():
        return next(iterator, sentinel)

    async def events():
        try:
            yield 'data: ' + json.dumps(first, ensure_ascii=False) + '\n\n'
            while True:
                item = await anyio.to_thread.run_sync(advance)
                if item is sentinel:
                    break
                yield 'data: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
        finally:
            # StreamingResponse does not close a synchronous iterator on every disconnect.
            # A shielded close preserves interrupted state and releases provider resources.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(iterator.close)

    return StreamingResponse(events(), media_type='text/event-stream', background=BackgroundTask(iterator.close), headers={
        'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no', 'X-Content-Type-Options': 'nosniff'})


@router.get('/templates')
def templates(session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_templates(session)}


@router.post('/templates')
def create_template(request: Request, payload: TemplateCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'ai/templates', body, lambda session: service.create_template(session, body))


@router.put('/templates/{template_id}')
def update_template(request: Request, template_id: str, payload: TemplateSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'ai/templates/{template_id}', body,
                  lambda session: service.update_template(session, template_id, body))


@router.delete('/templates/{template_id}')
def delete_template(request: Request, template_id: str, expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'ai/templates/{template_id}/delete', body,
                  lambda session: service.delete_template(session, template_id, body))


@router.get('/usage')
def usage(account_id: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': service.usage_summary(session, account_id)}
