import hashlib

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from trade_app.api.routes import _write, read_session
from trade_app.api.portfolio_report_excel import portfolio_report_xlsx
from trade_app.platform.types import TradeError
from trade_app.research import portfolio_report_service as service
from trade_app.research.portfolio_report_domain import MAX_PACKAGE_BYTES, package_portfolio_report, render_portfolio_html

router = APIRouter(prefix='/api/v1/research/portfolio-reports', tags=['portfolio-reports'])


class PortfolioReportCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    portfolio_run_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    analysis_run_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


def _workbook(report):
    return portfolio_report_xlsx(report['payload'], metadata={'report_content_sha256': report['content_sha256'],
        'report_origin': '导入，未重新计算' if report['origin'] == 'import' else '本地冻结'})


@router.get('')
def listing(session: Session = Depends(read_session)):
    return {'data': service.list_reports(session)}


@router.post('')
def create(request: Request, payload: PortfolioReportCreate):
    body = payload.model_dump()
    return _write(request, 'research/portfolio-reports', body, lambda session: service.create_report(session, body))


@router.post('/import')
async def import_report(request: Request, file: UploadFile = File(...)):
    try:
        contents = await file.read(MAX_PACKAGE_BYTES + 1)
    finally:
        await file.close()
    if len(contents) > MAX_PACKAGE_BYTES:
        raise TradeError('REPORT_PACKAGE_TOO_LARGE', '完整组合报告包超过16MiB')
    return await run_in_threadpool(lambda: _write(request, 'research/portfolio-reports/import',
        {'package_sha256': hashlib.sha256(contents).hexdigest()}, lambda session: service.import_report(session, contents)))


@router.get('/{report_id}')
def detail(report_id: str, session: Session = Depends(read_session)):
    return {'data': service.get_report(session, report_id)}


@router.delete('/{report_id}')
def delete(request: Request, report_id: str):
    return _write(request, f'research/portfolio-reports/{report_id}/delete', {}, lambda session: service.delete_report(session, report_id))


@router.get('/{report_id}/export.zip')
def export_zip(report_id: str, session: Session = Depends(read_session)):
    report = service.get_report(session, report_id)
    return Response(package_portfolio_report(report['payload'], _workbook(report), origin=report['origin']),
        media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="portfolio-report-{report_id}.zip"'})


@router.get('/{report_id}/report.html')
def export_html(report_id: str, session: Session = Depends(read_session)):
    report = service.get_report(session, report_id)
    return Response(render_portfolio_html(report['payload'], origin=report['origin']), media_type='text/html',
        headers={'Content-Disposition': f'attachment; filename="portfolio-report-{report_id}.html"',
                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
                 'X-Content-Type-Options': 'nosniff'})


@router.get('/{report_id}/export.xlsx')
def export_xlsx(report_id: str, session: Session = Depends(read_session)):
    report = service.get_report(session, report_id)
    return Response(_workbook(report), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="portfolio-report-{report_id}.xlsx"'})
