from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from trade_app.api.routes import _write, read_session
from trade_app.api import settings_service as service

router = APIRouter(prefix='/api/v1/settings/groups', tags=['settings'])


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0, strict=True)


class Save(Revision):
    value: dict


class Reset(Revision):
    preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Import(Save):
    preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.get('/{group}')
def get(group: str, account_id: str | None = None, session: Session = Depends(read_session)):
    return {'data': service.get_group(session, group, account_id)}


@router.get('/{group}/audit')
def audit(group: str, account_id: str | None = None, session: Session = Depends(read_session)):
    return {'data': service.history(session, group, account_id)}


@router.put('/{group}')
def save(request: Request, group: str, payload: Save, account_id: str | None = None):
    body = payload.model_dump()
    return _write(request, f'settings/{group}/{account_id}', body,
                  lambda session: service.save_group(session, group, account_id, payload.expected_revision, payload.value))


@router.post('/{group}/defaults-preview')
def preview_defaults(group: str, payload: Revision, account_id: str | None = None, session: Session = Depends(read_session)):
    return {'data': service.preview(session, group, account_id, payload.expected_revision)}


@router.post('/{group}/preview')
def preview_value(group: str, payload: Save, account_id: str | None = None, session: Session = Depends(read_session)):
    return {'data': service.preview(session, group, account_id, payload.expected_revision, payload.value)}


@router.post('/{group}/reset')
def reset(request: Request, group: str, payload: Reset, account_id: str | None = None):
    return _write(request, f'settings/{group}/{account_id}/reset', payload.model_dump(),
                  lambda session: service.apply_preview(session, group, account_id, payload.expected_revision, payload.preview_sha256))


@router.post('/market_sources/import')
def import_sources(request: Request, payload: Import):
    return _write(request, 'settings/market_sources/import', payload.model_dump(),
                  lambda session: service.apply_preview(session, 'market_sources', None, payload.expected_revision, payload.preview_sha256, value=payload.value))
