from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from trade_app.api.routes import read_session
from trade_app.reviews.reminders import reminders

router = APIRouter(prefix='/api/v1', tags=['review-reminders'])


@router.get('/accounts/{account_id}/review-reminders')
def review_reminders(account_id: str, limit: int = Query(default=100, ge=1, le=100),
                     session: Session = Depends(read_session)):
    return {'data': reminders(session, account_id, limit)}
