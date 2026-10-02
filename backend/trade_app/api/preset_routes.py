"""Versioned strategy parameter presets and explicit proposal application."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.ai.service import get_run as get_ai_run
from trade_app.api.routes import _write, read_session
from trade_app.research import preset_service as service


router = APIRouter(prefix='/api/v1/research', tags=['strategy-presets'])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PresetSave(StrictBody):
    name: str = Field(min_length=1, max_length=80)
    strategy_id: str = Field(min_length=1, max_length=128)
    params: dict = Field(max_length=128)
    favorite: bool = Field(default=False, strict=True)
    expected_revision: int | None = Field(default=None, ge=1, strict=True)


class PresetImport(StrictBody):
    share_code: str = Field(min_length=1, max_length=65536)
    current_params: dict = Field(default_factory=dict, max_length=128)


class ParameterProposal(StrictBody):
    preset_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=1, strict=True)
    params: dict | None = Field(default=None, max_length=128)
    source_ai_run_id: str | None = Field(default=None, min_length=1, max_length=80)
    account_id: str | None = Field(default=None, min_length=1, max_length=80)


class ProposalApply(StrictBody):
    expected_revision: int = Field(ge=1, strict=True)
    expected_proposal_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/presets')
def list_presets(strategy_id: str | None = None, favorites_only: bool = False,
                 session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_presets(session, strategy_id, favorites_only)}


@router.post('/presets')
def create_preset(request: Request, payload: PresetSave) -> JSONResponse:
    body = payload.model_dump(mode='json', exclude_none=True)
    return _write(request, 'research/presets', body, lambda session: service.save_preset(session, body))


@router.post('/presets/preview-import')
def preview_import(payload: PresetImport, session: Session = Depends(read_session)) -> dict:
    return {'data': service.preview_import(session, payload.model_dump(mode='json'))}


@router.get('/presets/{preset_id}')
def get_preset(preset_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': service.get_preset(session, preset_id)}


@router.put('/presets/{preset_id}')
def save_preset(request: Request, preset_id: str, payload: PresetSave) -> JSONResponse:
    body = payload.model_dump(mode='json', exclude_none=True)
    return _write(request, f'research/presets/{preset_id}', body,
                  lambda session: service.save_preset(session, body, preset_id))


@router.delete('/presets/{preset_id}')
def delete_preset(request: Request, preset_id: str, expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'research/presets/{preset_id}/delete', body,
                  lambda session: service.delete_preset(session, preset_id, body))


@router.get('/presets/{preset_id}/history')
def preset_history(preset_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': service.preset_history(session, preset_id)}


@router.get('/presets/{preset_id}/export')
def export_preset(preset_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': service.export_preset(session, preset_id)}


@router.post('/parameter-proposals')
def create_proposal(request: Request, payload: ParameterProposal) -> JSONResponse:
    body = payload.model_dump(mode='json', exclude_none=True)

    def operation(session):
        source = None
        if body.get('source_ai_run_id'):
            run = get_ai_run(session, body['source_ai_run_id'], body.get('account_id'))
            source = {key: run[key] for key in ('id', 'account_id', 'status', 'kind', 'output')}
        return service.create_parameter_proposal(session, body, ai_source=source)

    return _write(request, 'research/parameter-proposals', body, operation)


@router.get('/parameter-proposals/{proposal_id}')
def get_proposal(proposal_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': service.get_parameter_proposal(session, proposal_id)}


@router.post('/parameter-proposals/{proposal_id}/apply')
def apply_proposal(request: Request, proposal_id: str, payload: ProposalApply) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'research/parameter-proposals/{proposal_id}/apply', body,
                  lambda session: service.apply_parameter_proposal(session, proposal_id, body))
