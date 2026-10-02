from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from trade_app.api.routes import _write
from trade_app.reviews.ai_scores import copy_ai_to_final


router = APIRouter(prefix='/api/v1/accounts', tags=['review-scores'])


class CopyScores(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    dimensions: list[str] = Field(min_length=1, max_length=6)


@router.post('/{account_id}/review-scores/{sheet_id}/copy-ai-to-final')
def copy_suggestions(request: Request, account_id: str, sheet_id: str, payload: CopyScores) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/review-scores/{sheet_id}/copy-ai-to-final', body,
                  lambda session: copy_ai_to_final(session, account_id, sheet_id, body))
