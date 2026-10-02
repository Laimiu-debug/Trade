from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from trade_app.api.routes import read_session
from trade_app.api import research_table_exports as export

router = APIRouter(prefix='/api/v1', tags=['research-table-exports'])
SAFE = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
        'Content-Security-Policy': "default-src 'none'; sandbox"}


def response(content, name, kind):
    media = {'csv': 'text/csv', 'html': 'text/html', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}[kind]
    return Response(content, media_type=media, headers={**SAFE, 'Content-Disposition': f'attachment; filename="{name}.{kind}"'})


def trades(table, name, kind):
    columns, rows, meta = table
    content = export.csv_bytes(columns, rows) if kind == 'csv' else export.table_html(name, columns, rows, meta)
    return response(content, name, kind)


@router.get('/backtests/{run_id}/trades.{kind}')
def single(run_id: str, kind: Literal['csv', 'html'], session: Session = Depends(read_session)):
    return trades(export.native_trades(session, run_id), 'backtest-trades-' + run_id, kind)


@router.get('/research/portfolios/{run_id}/trades.{kind}')
def portfolio(run_id: str, kind: Literal['csv', 'html'], session: Session = Depends(read_session)):
    return trades(export.native_trades(session, run_id, portfolio=True), 'portfolio-trades-' + run_id, kind)


@router.get('/research/plateaus/{experiment_id}/export.xlsx')
def plateau(experiment_id: str, session: Session = Depends(read_session)):
    return response(export.native_plateau_xlsx(session, experiment_id), 'plateau-all-points-' + experiment_id, 'xlsx')


@router.get('/research/legacy-reports/{report_id}/trades.{kind}')
def legacy_trades(report_id: str, kind: Literal['csv', 'html'], detail_key: str | None = None, session: Session = Depends(read_session)):
    return trades(export.legacy_trades(session, report_id, detail_key), 'legacy-trades-' + report_id, kind)


@router.get('/research/legacy-reports/{report_id}/plateau.xlsx')
def legacy_plateau(report_id: str, session: Session = Depends(read_session)):
    return response(export.legacy_plateau_xlsx(session, report_id), 'legacy-plateau-' + report_id, 'xlsx')


@router.get('/research/signal-workspace/reports/{report_id}/cross-validation.csv')
def cross_validation(report_id: str, session: Session = Depends(read_session)):
    return response(export.cross_validation_csv(session, report_id), 'cross-validation-' + report_id, 'csv')
