from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from trade_app.api.routes import read_session
from trade_app.research.stock_history import stock_research_history

router = APIRouter(prefix='/api/v1/research', tags=['research-history'])


@router.get('/stock-history')
def history(symbol: str = Query(min_length=1, max_length=32), limit: int = Query(default=50, ge=1, le=100),
            session: Session = Depends(read_session)):
    return {'data': stock_research_history(session, symbol, limit)}
