from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.platform.types import TradeError
from trade_app.research import signal_workspace_service as service

router = APIRouter(prefix='/api/v1/research/signal-workspace', tags=['signal-workspace'])


@router.get('/sources')
def sources(session: Session = Depends(read_session)):
    return {'data': service.list_sources(session)}


@router.post('/source-preview')
def source_preview(body: dict, session: Session = Depends(read_session)):
    return {'data': service.resolve_source(session, body)}


@router.post('/scan-preview')
def scan_preview(request: Request, body: dict, session: Session = Depends(read_session)):
    prepared = service.prepare_workspace_scan(session, request.app.state.data_dir, body)
    return {'data': {'input_sha256': service.digest(prepared), 'source': prepared['source'],
                     'evaluation_count': len(prepared['scan']['plan']), 'request': prepared['scan']['request']}}


@router.post('/scan-jobs')
def create_scan(request: Request, body: dict):
    payload = {key: value for key, value in body.items() if key != 'expected_preview_sha256'}
    with request.app.state.db_factory() as session:
        prepared = service.prepare_workspace_scan(session, request.app.state.data_dir, payload)
    if body.get('expected_preview_sha256') != service.digest(prepared):
        raise TradeError('SIGNAL_PREVIEW_CHANGED', '来源、参数或算法版本已变化，请重新预览', 409)
    return _write(request, 'research/signal-workspace/scan-jobs', body,
                  lambda session: service.save_workspace_scan(session, prepared))


@router.post('/preview')
def preview(request: Request, body: dict, session: Session = Depends(read_session)):
    return {'data': service.prepare_report(session, request.app.state.data_dir, body)}


@router.post('/reports')
def create_report(request: Request, body: dict):
    payload = {key: value for key, value in body.items() if key != 'expected_preview_sha256'}
    with request.app.state.db_factory() as session:
        prepared = service.prepare_report(session, request.app.state.data_dir, payload)
    if body.get('expected_preview_sha256') != prepared['id']:
        raise TradeError('SIGNAL_PREVIEW_CHANGED', '报告内容发生变化，请重新预览', 409)
    return _write(request, 'research/signal-workspace/reports', body,
                  lambda session: service.save_report(session, prepared))


@router.get('/reports')
def reports(session: Session = Depends(read_session)):
    return {'data': service.list_reports(session)}


@router.get('/reports/{report_id}')
def report(report_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_report(session, report_id)}


@router.delete('/reports/{report_id}')
def delete(request: Request, report_id: str):
    return _write(request, f'research/signal-workspace/reports/{report_id}/delete', {},
                  lambda session: service.delete_report(session, report_id))
