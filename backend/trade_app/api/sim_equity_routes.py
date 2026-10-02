from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.platform.types import TradeError
from trade_app.research import sim_equity_service as service
from trade_app.reviews.equity_pdf import render_equity
from trade_app.reviews.print_settings import get_settings

router = APIRouter(prefix='/api/v1/sim-accounts/{account_id}/equity-reports', tags=['simulation-equity'])


@router.get('/inputs')
def inputs(account_id: str, session: Session = Depends(read_session)):
    return {'data': service.inputs(session, account_id)}


@router.post('/preview')
def preview(request: Request, account_id: str, body: dict, session: Session = Depends(read_session)):
    return {'data': service.prepare_report(session, request.app.state.data_dir, account_id, body)}


@router.post('')
def create_report(request: Request, account_id: str, body: dict):
    payload = {key: value for key, value in body.items() if key != 'expected_input_sha256'}
    with request.app.state.db_factory() as session:
        prepared = service.prepare_report(session, request.app.state.data_dir, account_id, payload)
    if prepared['input_sha256'] != body.get('expected_input_sha256'):
        raise TradeError('SIM_EQUITY_PREVIEW_STALE', '账户或行情版本在预览后变化，请重新预览', 409)
    return _write(request, f'sim-accounts/{account_id}/equity-reports', body,
                  lambda session: service.save_report(session, account_id, prepared))


@router.post('/size')
def size(account_id: str, body: dict, session: Session = Depends(read_session)):
    return {'data': service.size_from_assets(session, account_id, body)}


@router.post('/drafts')
def draft(request: Request, account_id: str, body: dict):
    return _write(request, f'sim-accounts/{account_id}/equity-reports/drafts', body,
                  lambda session: service.create_asset_sized_draft(session, account_id, body))


@router.get('')
def reports(account_id: str, session: Session = Depends(read_session)):
    return {'data': service.list_reports(session, account_id)}


@router.get('/{report_id}')
def report(account_id: str, report_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_report(session, account_id, report_id)}


@router.get('/{report_id}/export.pdf')
def export_pdf(account_id: str, report_id: str, session: Session = Depends(read_session)):
    report = service.get_report(session, account_id, report_id)
    return Response(render_equity(report, author=get_settings(session)['author']), media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="trade-equity-{report_id}.pdf"', 'Cache-Control': 'no-store'})


@router.delete('/{report_id}')
def delete(request: Request, account_id: str, report_id: str):
    return _write(request, f'sim-accounts/{account_id}/equity-reports/{report_id}/delete', {},
                  lambda session: service.delete_report(session, account_id, report_id))
