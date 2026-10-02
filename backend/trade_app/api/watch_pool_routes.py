from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.research import watch_pool_service as service

router = APIRouter(prefix='/api/v1/research/watch-pool', tags=['watch-pool'])


class StateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state: dict


class SaveRequest(StateRequest):
    expected_revision: int = Field(ge=0, strict=True)


@router.get('')
def read_pool(session: Session = Depends(read_session)):
    return {'data': service.get_pool(session)}


@router.get('/audit')
def audit(session: Session = Depends(read_session)):
    return {'data': service.list_audit(session)}


@router.post('/preview')
def preview(request: Request, payload: StateRequest, session: Session = Depends(read_session)):
    return {'data': service.prepare_state(session, request.app.state.data_dir, payload.state)}


def save(request, payload, action):
    with request.app.state.db_factory() as session:
        prepared = service.prepare_state(session, request.app.state.data_dir, payload.state)
    return _write(request, f'research/watch-pool/{action}', payload.model_dump(),
                  lambda session: service.save_pool(session, prepared, payload.expected_revision, action=action))


@router.put('')
def update(request: Request, payload: SaveRequest):
    return save(request, payload, 'save')


@router.post('/import')
def import_legacy(request: Request, payload: SaveRequest):
    return save(request, payload, 'legacy_import')
