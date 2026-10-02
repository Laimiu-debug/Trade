"""Report archive transport; uploads are validated data and never executed."""
import hashlib

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from trade_app.api.excel_export import backtest_report_xlsx
from trade_app.api.routes import _write, read_session
from trade_app.platform.types import TradeError
from trade_app.research import report_service as service
from trade_app.research.report_domain import MAX_PACKAGE_BYTES, package_report, render_html


router = APIRouter(prefix='/api/v1/research/reports', tags=['research-reports'])


def _workbook(report):
    return backtest_report_xlsx(report['payload']['run'], report_metadata={
        'report_title': report['title'], 'report_content_sha256': report['content_sha256'],
        'report_origin': '导入，未重新计算' if report['origin'] == 'import' else '本地冻结'})


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    backtest_run_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)


@router.get('')
def list_reports(session: Session = Depends(read_session)) -> dict:
    return {'data': service.list_reports(session)}


@router.post('')
def create_report(request: Request, payload: ReportCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'research/reports', body,
                  lambda session: service.create_report(session, request.app.state.data_dir, body))


@router.post('/import')
async def import_report(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    try:
        contents = await file.read(MAX_PACKAGE_BYTES + 1)
    finally:
        await file.close()
    if len(contents) > MAX_PACKAGE_BYTES:
        raise TradeError('REPORT_PACKAGE_TOO_LARGE', '报告包超过 16 MiB 限制')
    return await run_in_threadpool(lambda: _write(
        request, 'research/reports/import', {'package_sha256': hashlib.sha256(contents).hexdigest()},
        lambda session: service.import_report(session, contents)))


@router.get('/{report_id}')
def get_report(report_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': service.get_report(session, report_id)}


@router.delete('/{report_id}')
def delete_report(request: Request, report_id: str) -> JSONResponse:
    return _write(request, f'research/reports/{report_id}/delete', {},
                  lambda session: service.delete_report(session, report_id))


@router.get('/{report_id}/export.zip')
def export_package(report_id: str, session: Session = Depends(read_session)) -> Response:
    report = service.get_report(session, report_id)
    payload = report['payload']
    return Response(package_report(payload, _workbook(report), origin=report['origin']), media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="trade-report-{report_id}.zip"'})


@router.get('/{report_id}/report.html')
def export_html(report_id: str, session: Session = Depends(read_session)) -> Response:
    report = service.get_report(session, report_id)
    return Response(render_html(report['payload'], origin=report['origin']), media_type='text/html',
                    headers={'Content-Disposition': f'attachment; filename="trade-report-{report_id}.html"',
                             'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
                             'X-Content-Type-Options': 'nosniff'})


@router.get('/{report_id}/export.xlsx')
def export_xlsx(report_id: str, session: Session = Depends(read_session)) -> Response:
    report = service.get_report(session, report_id)
    return Response(_workbook(report),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="trade-report-{report_id}.xlsx"'})
