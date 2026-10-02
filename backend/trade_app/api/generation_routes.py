"""Structured AI drafts have separate generation, validation and acceptance steps."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from trade_app.ai import generation_service as service
from trade_app.api.ai_routes import _scope
from trade_app.api.routes import _write, read_session


router = APIRouter(prefix='/api/v1/ai/generations', tags=['ai-generations'])


@router.get('')
def list_generations(account_id: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_generations(session, account_id)}


@router.post('/preview')
def preview_generation(request: Request, payload: dict, account_id: str | None = None,
                       session: Session = Depends(read_session)) -> dict:
    return {'data': service.preview_generation(session, request.app.state.data_dir, payload, account_id)}


@router.post('')
def prepare_generation(request: Request, payload: dict, account_id: str | None = None) -> JSONResponse:
    return _write(request, _scope('generations', account_id), payload,
                  lambda session: service.prepare_generation(session, request.app.state.data_dir, payload, account_id))


@router.get('/{generation_id}')
def get_generation(generation_id: str, account_id: str | None = None,
                   session: Session = Depends(read_session)) -> dict:
    return {'data': service.get_generation(session, generation_id, account_id)}


@router.put('/{generation_id}')
def edit_generation(request: Request, generation_id: str, payload: dict,
                    account_id: str | None = None) -> JSONResponse:
    return _write(request, _scope(f'generations/{generation_id}', account_id), payload,
                  lambda session: service.edit_generation(session, generation_id, payload, account_id))


@router.delete('/{generation_id}')
def delete_generation(request: Request, generation_id: str, expected_revision: int,
                      account_id: str | None = None) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, _scope(f'generations/{generation_id}/delete', account_id), body,
                  lambda session: service.delete_generation(session, generation_id, body, account_id))


@router.get('/{generation_id}/audit')
def generation_audit(generation_id: str, account_id: str | None = None,
                     session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_generation_audit(session, generation_id, account_id)}


def _action(request, generation_id, account_id, payload, action):
    operation = {'finalize': service.finalize_generation, 'accept': service.accept_generation,
                 'reject': service.reject_generation, 'retract': service.retract_generation}[action]
    return _write(request, _scope(f'generations/{generation_id}/{action}', account_id), payload,
                  lambda session: operation(session, generation_id, payload, account_id))


@router.post('/{generation_id}/finalize')
def finalize_generation(request: Request, generation_id: str, payload: dict,
                        account_id: str | None = None) -> JSONResponse:
    return _action(request, generation_id, account_id, payload, 'finalize')


@router.post('/{generation_id}/accept')
def accept_generation(request: Request, generation_id: str, payload: dict,
                      account_id: str | None = None) -> JSONResponse:
    return _action(request, generation_id, account_id, payload, 'accept')


@router.post('/{generation_id}/reject')
def reject_generation(request: Request, generation_id: str, payload: dict,
                      account_id: str | None = None) -> JSONResponse:
    return _action(request, generation_id, account_id, payload, 'reject')


@router.post('/{generation_id}/retract')
def retract_generation(request: Request, generation_id: str, payload: dict,
                       account_id: str | None = None) -> JSONResponse:
    return _action(request, generation_id, account_id, payload, 'retract')
