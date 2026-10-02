from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from trade_app.api.routes import _write, read_session
from trade_app.platform.types import TradeError
from trade_app.research import legacy_report_service as service
from trade_app.research.legacy_report_domain import MAX_BYTES, prepare_import, encode, render_safe_html

router = APIRouter(prefix='/api/v1/research/legacy-reports', tags=['legacy-reports'])
SAFE = {'Content-Security-Policy': "default-src 'none'; sandbox", 'X-Content-Type-Options': 'nosniff'}


async def prepared_upload(file):
    try:
        contents = await file.read(MAX_BYTES + 1)
        filename = file.filename or 'legacy-report'
    finally:
        await file.close()
    return await run_in_threadpool(prepare_import, contents, filename)


@router.post('/preview')
async def preview(file: UploadFile = File(...)):
    prepared = await prepared_upload(file)
    return {'data': prepared['preview']}


@router.post('/import')
async def save(request: Request, file: UploadFile = File(...), expected_preview_sha256: str = Form(...),
               mode: str = Form(...), acknowledge_limitations: bool = Form(False)):
    prepared = await prepared_upload(file)
    identity = {'source_sha256': prepared['preview']['source_sha256'], 'expected_preview_sha256': expected_preview_sha256,
                'mode': mode, 'acknowledge_limitations': acknowledge_limitations}
    return await run_in_threadpool(lambda: _write(request, 'research/legacy-reports/import', identity,
        lambda session: service.save_report(session, prepared, expected_preview_sha256, mode, acknowledge_limitations)))


@router.get('')
def history(session: Session = Depends(read_session)):
    return {'data': service.list_reports(session)}


@router.get('/{identifier}')
def detail(identifier: str, session: Session = Depends(read_session)):
    return {'data': service.get_report(session, identifier)}


@router.delete('/{identifier}')
def delete(request: Request, identifier: str):
    return _write(request, f'research/legacy-reports/{identifier}/delete', {}, lambda session: service.delete_report(session, identifier))


@router.get('/{identifier}/original.bin')
def source(identifier: str, name: str | None = None, session: Session = Depends(read_session)):
    return Response(service.original(session, identifier, name), media_type='application/octet-stream',
                    headers={**SAFE, 'Content-Disposition': f'attachment; filename="legacy-original-{identifier}.bin"'})


@router.get('/{identifier}/export.json')
def export(identifier: str, session: Session = Depends(read_session)):
    report = service.get_report(session, identifier)
    return Response(encode(report), media_type='application/json',
                    headers={**SAFE, 'Content-Disposition': f'attachment; filename="legacy-readonly-{identifier}.json"'})


@router.get('/{identifier}/report.html')
def safe_html(identifier: str, session: Session = Depends(read_session)):
    report = service.get_report(session, identifier)
    return Response(render_safe_html(report), media_type='text/html',
                    headers={**SAFE, 'Content-Disposition': f'attachment; filename="legacy-readonly-{identifier}.html"'})
