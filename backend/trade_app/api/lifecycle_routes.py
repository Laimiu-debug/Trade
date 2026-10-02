from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/v1/system/lifecycle', tags=['managed-lifecycle'])


class EmptyAction(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SwitchPreview(EmptyAction):
    destination: str = Field(min_length=1, max_length=2000)


class SwitchAction(EmptyAction):
    verification_id: str = Field(min_length=1, max_length=100)
    expected_fingerprint: str = Field(pattern='^[0-9a-f]{64}$')


@router.get('')
def status(request: Request):
    return {'data': request.app.state.lifecycle.status()}


@router.get('/operations/{operation_id}')
def operation(request: Request, operation_id: str):
    return {'data': request.app.state.lifecycle.operation(operation_id)}


@router.post('/switch-preview')
def preview(request: Request, payload: SwitchPreview):
    return {'data': request.app.state.lifecycle.preview_switch(payload.destination)}


@router.post('/switch')
def switch(request: Request, payload: SwitchAction):
    result = request.app.state.lifecycle.request('switch', payload.model_dump(), request.headers.get('Idempotency-Key', ''))
    return JSONResponse({'data': result}, status_code=202)


@router.post('/exit')
def exit_app(request: Request, payload: EmptyAction):
    result = request.app.state.lifecycle.request('exit', {}, request.headers.get('Idempotency-Key', ''))
    return JSONResponse({'data': result}, status_code=202)
