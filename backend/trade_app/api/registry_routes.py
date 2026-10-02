from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.research import registry_service as service

router = APIRouter(prefix='/api/v1/research/registry', tags=['strategy-registry'])


@router.get('')
def registry(session: Session = Depends(read_session)):
    return {'data': service.get_registry(session)}


@router.post('/preview')
def preview(body: dict, session: Session = Depends(read_session)):
    return {'data': service.preview_registry(session, body)}


@router.put('')
def apply(request: Request, body: dict):
    return _write(request, 'research/registry', body, lambda session: service.apply_registry(session, body))


@router.get('/history')
def history(session: Session = Depends(read_session)):
    return {'data': service.registry_history(session)}
