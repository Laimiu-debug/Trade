"""Explicit network refresh; reading the news panel only reads persisted cache."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from trade_app.api.routes import _write, read_session
from trade_app.market import news

router = APIRouter(prefix='/api/v1/market/news', tags=['market-news'])


class NewsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(default=news.DEFAULT_QUERY, min_length=1, max_length=120)
    age_hours: Literal[24, 48, 72] = 72
    provider: Literal['auto', 'eastmoney', 'google_rss'] = 'auto'
    as_of_at: str | None = Field(default=None, max_length=64)
    date_from: str | None = Field(default=None, max_length=10)
    date_to: str | None = Field(default=None, max_length=10)


@router.get('')
def read_news(query: str = news.DEFAULT_QUERY, age_hours: int = 72, provider: str = 'auto',
              as_of_at: str | None = None, date_from: str | None = None, date_to: str | None = None,
              session: Session = Depends(read_session)) -> dict:
    body = news.normalize_request({'query': query, 'age_hours': age_hours, 'provider': provider,
                                   'as_of_at': as_of_at, 'date_from': date_from, 'date_to': date_to})
    return {'data': news.present(news.cached_snapshot(session, body), body)}


@router.post('/refresh')
def refresh_news(request: Request, payload: NewsRequest) -> JSONResponse:
    body = payload.model_dump(mode='json')
    config = news.normalize_request(body)
    with request.app.state.db_factory() as session:
        cached = news.cached_snapshot(session, config)
    # Fetch before the short write transaction; do not hold SQLite while waiting for a provider.
    prepared = news.prepare_refresh(config, cached)
    return _write(request, 'market/news/refresh', body,
                  lambda session: news.save_refresh(session, config, prepared))
