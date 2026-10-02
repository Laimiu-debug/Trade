from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from typing import Literal

from trade_app.api.routes import _write, read_session
from trade_app.legacy_import import service
from trade_app.legacy_import import supplements
from trade_app.legacy_import import sim_service
from trade_app.legacy_import import attachment_service
from trade_app.legacy_import.reader import canonical, MAX_BYTES

router = APIRouter(prefix='/api/v1/legacy-imports', tags=['legacy-import'])


class Upload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=1, max_length=MAX_BYTES * 4 // 3 + 4)
    mode: Literal['archive_only', 'new_real_account'] = 'archive_only'
    account_name: str = Field(default='', max_length=80)


class Confirm(Upload):
    expected_preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_limitations: bool = False


class Promotion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    account_name: str = Field(min_length=1, max_length=80)


class PromotionConfirm(Promotion):
    expected_preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_limitations: bool = False


class SupplementSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    selected_keys: list[str] = Field(default_factory=list, max_length=500)
    secret_refs: dict[str, str] = Field(default_factory=dict)


class SupplementConfirm(SupplementSelection):
    expected_preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_limitations: bool = False


class AttachmentFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_key: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=1, max_length=8 * 1024 * 1024 * 4 // 3 + 4)


class AttachmentSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    files: list[AttachmentFile] = Field(min_length=1, max_length=20)


class AttachmentConfirm(AttachmentSelection):
    expected_preview_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_limitations: bool = False


@router.post('/preview')
def preview(payload: Upload):
    _, result = service.preview(payload.model_dump())
    return {'data': result}


@router.post('')
def save(request: Request, payload: Confirm):
    # Decode/redact/validate before entering the serialized writer transaction.
    upload, frozen = service.preview(payload.model_dump())
    # Idempotency only stores this digest identity, never encoded input or keys.
    identity = {key: getattr(payload, key) for key in ('mode', 'account_name', 'expected_preview_sha256', 'acknowledge_limitations')}
    identity['source_sha256'] = upload['source_sha256']
    return _write(request, 'legacy-imports', identity,
                  lambda session: service.save_import(session, upload, frozen, payload.expected_preview_sha256,
                                                       acknowledged=payload.acknowledge_limitations))


@router.get('')
def list_archives(session: Session = Depends(read_session)):
    return {'data': service.list_imports(session)}


@router.get('/{import_id}')
def detail(import_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_import(session, import_id)}


@router.post('/{import_id}/promotion-preview')
def promotion_preview(import_id: str, payload: Promotion, session: Session = Depends(read_session)):
    _, frozen = service.prepare_promotion(session, import_id, payload.expected_revision, payload.account_name)
    return {'data': frozen}


@router.post('/{import_id}/promote')
def promote(request: Request, import_id: str, payload: PromotionConfirm):
    with request.app.state.db_factory() as session:
        upload, frozen = service.prepare_promotion(session, import_id, payload.expected_revision, payload.account_name, payload.expected_preview_sha256)
    return _write(request, f'legacy-imports/{import_id}/promote', payload.model_dump(),
                  lambda session: service.save_promotion(session, import_id, upload, frozen, payload.expected_preview_sha256,
                                                         acknowledged=payload.acknowledge_limitations))


@router.get('/{import_id}/export.json')
def export(import_id: str, session: Session = Depends(read_session)):
    value = service.export_archive(session, import_id)
    return Response(canonical(value), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="legacy-sanitized-{value["source"]["id"]}.json"'})


@router.get('/{import_id}/supplements')
def supplement_catalog(import_id: str, session: Session = Depends(read_session)):
    return {'data': supplements.inspect(session, import_id)}


@router.post('/{import_id}/supplements/preview')
def supplement_preview(import_id: str, payload: SupplementSelection, session: Session = Depends(read_session)):
    return {'data': supplements.preview(session, import_id, payload.model_dump())}


@router.post('/{import_id}/supplements/apply')
def supplement_apply(request: Request, import_id: str, payload: SupplementConfirm):
    body = payload.model_dump()
    # Validate references before idempotency persistence; a pasted key is never saved.
    with request.app.state.db_factory() as session:
        supplements.inspect(session, import_id, secret_refs=body['secret_refs'])
    return _write(request, f'legacy-imports/{import_id}/supplements', body,
                  lambda session: supplements.apply(session, import_id, body))


@router.get('/{import_id}/supplements/history')
def supplement_history(import_id: str, session: Session = Depends(read_session)):
    return {'data': supplements.history(session, import_id)}


@router.post('/{import_id}/simulation/preview')
def simulation_preview(import_id: str, payload: Promotion, session: Session = Depends(read_session)):
    return {'data': sim_service.preview(session, import_id, payload.model_dump())}


@router.post('/{import_id}/simulation/apply')
def simulation_apply(request: Request, import_id: str, payload: PromotionConfirm):
    return _write(request, f'legacy-imports/{import_id}/simulation', payload.model_dump(),
                  lambda session: sim_service.apply(session, import_id, payload.model_dump()))


@router.get('/{import_id}/attachments')
def attachment_catalog(import_id: str, session: Session = Depends(read_session)):
    return {'data': attachment_service.catalog(session, import_id)}


@router.post('/{import_id}/attachments/preview')
def attachment_preview(import_id: str, payload: AttachmentSelection, session: Session = Depends(read_session)):
    body = payload.model_dump()
    return {'data': attachment_service.preview(session, import_id, body, attachment_service.decode(body['files']))}


@router.post('/{import_id}/attachments/apply')
def attachment_apply(request: Request, import_id: str, payload: AttachmentConfirm):
    body = payload.model_dump()
    decoded = attachment_service.decode(body['files'])
    identity = {**attachment_service.identity(body, decoded), 'expected_preview_sha256': payload.expected_preview_sha256,
                'acknowledge_limitations': payload.acknowledge_limitations}
    return _write(request, f'legacy-imports/{import_id}/attachments', identity,
                  lambda session: attachment_service.apply(session, request.app.state.data_dir, import_id, body, decoded))
