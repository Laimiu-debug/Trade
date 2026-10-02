from typing import Literal
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from trade_app.api.routes import read_session
from trade_app.api.statistics_export import statistics_csv, statistics_xlsx
from trade_app.reviews.performance_pdf import export_performance_pdf

router = APIRouter(prefix='/api/v1/accounts', tags=['statistics-exports'])


@router.get('/{account_id}/exports/performance.pdf')
def export(account_id: str, kind: Literal['daily', 'weekly', 'monthly'] = 'monthly',
           limit: int = Query(default=24, ge=1, le=366), date_basis: Literal['buy', 'sell'] = 'sell',
           date_from: str | None = None, date_to: str | None = None,
           session: Session = Depends(read_session)):
    content = export_performance_pdf(session, account_id, kind=kind, limit=limit, date_basis=date_basis, date_from=date_from, date_to=date_to)
    return Response(content, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="trade-performance-{account_id}-{kind}-{date_basis}.pdf"',
        'Cache-Control': 'no-store'})


@router.get('/{account_id}/exports/performance.xlsx')
def excel(account_id: str, kind: Literal['daily', 'weekly', 'monthly'] = 'monthly',
          limit: int = Query(default=24, ge=1, le=366), date_basis: Literal['buy', 'sell'] = 'sell',
          date_from: str | None = None, date_to: str | None = None,
          session: Session = Depends(read_session)):
    content = statistics_xlsx(session, account_id, kind=kind, limit=limit, date_basis=date_basis, date_from=date_from, date_to=date_to)
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="trade-performance-{account_id}-{kind}-{date_basis}.xlsx"', 'Cache-Control': 'no-store'})


@router.get('/{account_id}/exports/performance/{table}.csv')
def csv(account_id: str, table: Literal['summary', 'metadata', 'rounds', 'nodes', 'fills', 'allocations', 'curve', 'unallocated'],
        kind: Literal['daily', 'weekly', 'monthly'] = 'monthly', limit: int = Query(default=24, ge=1, le=366),
        date_basis: Literal['buy', 'sell'] = 'sell', date_from: str | None = None, date_to: str | None = None,
        session: Session = Depends(read_session)):
    content = statistics_csv(session, account_id, table=table, kind=kind, limit=limit, date_basis=date_basis, date_from=date_from, date_to=date_to)
    return Response(content, media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="trade-performance-{account_id}-{kind}-{date_basis}-{table}.csv"', 'Cache-Control': 'no-store'})
