import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from trade_app.market import news
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError

NOW = datetime(2026, 9, 26, 8, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(news, 'now_utc', lambda: NOW)


def request(**changes):
    return news.normalize_request({'as_of_at': NOW.isoformat(), **changes})


def rows(*hours):
    return [news._item(f'市场资讯 {hour}', '原始摘要', f'https://example.com/item/{i}',
                       (NOW - timedelta(hours=hour)).isoformat(), '来源', 'eastmoney')
            for i, hour in enumerate(hours)]


def snapshot(items, **changes):
    data = {'items': items, 'fetched_at': NOW.isoformat(), 'actual_provider': 'eastmoney',
            'source_url': news.EASTMONEY_URL, 'attempted_providers': ['eastmoney'], 'errors': [], **changes}
    return {**data, 'id': news.digest(data)}


@contextmanager
def news_client(path):
    from trade_app.main import create_app
    from trade_app.api.news_routes import router
    app = create_app(path, auto_rebuild=False)
    if not any(getattr(route, 'path', '') == '/api/v1/market/news' for route in app.routes):
        # This also permits isolated route tests before the application entry point is mounted.
        app.router.routes[0:0] = router.routes
    lifespan = app.router.lifespan_context(app)
    with asyncio.Runner() as runner:
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app) as client:
                yield app, client
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


def test_eastmoney_current_and_legacy_formats_plain_text_times_and_safe_links():
    item = {'title': '<b>标题</b>', 'summary': '摘要 &amp; 数据', 'showTime': '2026-09-26 16:00:00',
            'code': '20260926123456', 'source': '东方财富'}
    for field in ('fastNewsList', 'newsList', 'list'):
        result = news.parse_eastmoney(json.dumps({'data': {field: [item]}}).encode())
        assert result[0]['title'] == '标题'
        assert result[0]['snippet'] == '摘要 & 数据'
        assert result[0]['published_at'] == NOW.isoformat()
        assert result[0]['url'] == 'https://kuaixun.eastmoney.com/news/20260926123456'
    item.update(url='javascript:alert(1)', showTime='not a date')
    row = news.parse_eastmoney(json.dumps({'data': [item]}).encode())[0]
    assert row['url'] is None and row['published_at'] is None
    assert news.publication_time('2026-09-26') is None
    assert news.publication_time('2026-09-26T16:00:00+08:00') == NOW.isoformat()


def test_rss_timezone_html_entity_restriction_and_bad_format():
    raw = b'<rss><channel><item><title>&lt;b&gt;A&lt;/b&gt;</title><description>B</description><pubDate>Sat, 26 Sep 2026 08:00:00 GMT</pubDate><link>https://example.com/a</link><source>RSS</source></item></channel></rss>'
    result = news.parse_google(raw)
    assert result[0]['published_at'] == NOW.isoformat()
    assert result[0]['title'] == 'A' and result[0]['provider'] == 'google_rss'
    for raw in (b'<!DOCTYPE x><rss/>', b'<rss/>', b'broken'):
        with pytest.raises(TradeError, match='格式'):
            news.parse_google(raw)


@pytest.mark.parametrize('changes', [
    {'age_hours': 12}, {'age_hours': True}, {'provider': 'arbitrary'}, {'query': ' '},
    {'query': 'a' * 121}, {'as_of_at': '2026-09-26'}, {'as_of_at': '2027-01-01T00:00:00+00:00'},
    {'date_from': '2026-09-20'}, {'date_from': '2026-09-25', 'date_to': '2026-09-24'},
])
def test_invalid_news_request_is_rejected(changes):
    with pytest.raises(TradeError) as failure:
        request(**changes)
    assert failure.value.code == 'INVALID_NEWS_REQUEST'


def test_window_exact_boundaries_future_unknown_and_shanghai_period_intersection():
    items = rows(0, 24, 24.001, -0.001, 48, 72)
    items += [news._item('未知日期', '', None, '', '来源', 'eastmoney')]
    data = news.present(snapshot(items), request(age_hours=24))
    assert data['count'] == 2
    assert data['excluded'] == {'unknown_publication_time': 1, 'after_as_of': 1, 'outside_window': 3}
    assert news.present(snapshot(items), request(age_hours=48))['count'] == 4
    assert news.present(snapshot(items), request(age_hours=72))['count'] == 5
    past_day = news.present(snapshot(items), request(age_hours=72, date_from='2026-09-25', date_to='2026-09-25'))
    assert past_day['count'] == 2
    assert past_day['request']['window_end'] == '2026-09-25T15:59:59.999999+00:00'
    assert 'historical' in past_day['availability_quality']


def test_view_deduplicates_without_expanding_window_and_reports_empty_distinct_from_failure():
    item = rows(1)[0]
    result = news.present(snapshot([item, item]), request())
    assert result['count'] == 1
    assert news.present(snapshot(rows(90)), request())['status'] == 'empty'
    assert news.present(None, request())['status'] == 'not_loaded'
    assert news.present(None, request(), errors=[{'provider': 'eastmoney', 'code': 'NEWS_SOURCE_TIMEOUT'}])['status'] == 'unavailable'


def test_fallback_preserves_actual_source_then_offline_cache_and_stale_metadata(monkeypatch):
    def fetched(provider, query):
        if provider == 'eastmoney':
            raise TradeError('NEWS_SOURCE_TIMEOUT', 'provider secret error not surfaced')
        return [{**rows(1)[0], 'provider': 'google_rss'}]
    monkeypatch.setattr(news, 'fetch_provider', fetched)
    prepared = news.prepare_refresh(request(), None)
    assert prepared['new'] and prepared['snapshot']['actual_provider'] == 'google_rss'
    view = news.present(prepared['snapshot'], request(), cache_hit=False, errors=prepared['errors'])
    assert view['fallback_used'] and view['degraded']
    assert view['errors'] == [{'provider': 'eastmoney', 'code': 'NEWS_SOURCE_TIMEOUT'}]
    old = {**prepared['snapshot'], 'fetched_at': (NOW - timedelta(hours=2)).isoformat()}
    monkeypatch.setattr(news, 'fetch_provider', lambda *_: (_ for _ in ()).throw(TradeError('NEWS_SOURCE_UNAVAILABLE', 'not exposed')))
    failed = news.prepare_refresh(request(), old)
    assert failed['snapshot'] == old and not failed['new']
    view = news.present(old, request(), errors=failed['errors'])
    assert view['cache_hit'] and view['cache_stale'] and view['count'] == 1
    assert view['fetched_at'] == old['fetched_at']
    assert view['cache_age_seconds'] == 7200


def test_real_empty_response_replaces_cache_without_inventing_fallback_news(monkeypatch):
    monkeypatch.setattr(news, 'fetch_provider', lambda *_: [])
    prepared = news.prepare_refresh(request(), snapshot(rows(1)))
    assert prepared['new'] and prepared['snapshot']['items'] == []
    assert news.present(prepared['snapshot'], request(), cache_hit=False)['status'] == 'empty'


def test_cache_reopens_and_round_trips_through_existing_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(news, 'fetch_provider', lambda *_: rows(1, 25))
    original = tmp_path / 'original'
    engine, factory = open_database(original)
    config = request()
    with factory.begin() as session:
        saved = news.save_refresh(session, config, news.prepare_refresh(config, None))
    engine.dispose()
    archive = create_backup(original)
    restored = tmp_path / 'restored'
    restore_to_new_directory(archive, restored)
    engine, factory = open_database(restored)
    try:
        with factory() as session:
            cached = news.cached_snapshot(session, config)
            view = news.present(cached, request(age_hours=24))
            assert view['snapshot_id'] == saved['snapshot_id'] and view['count'] == 1
            assert view['cache_hit']
            assert news.cached_snapshot(session, request(query='another query')) is None
    finally:
        engine.dispose()


def test_network_request_is_fixed_bounded_and_errors_do_not_echo_provider_body(monkeypatch):
    original = httpx.Client
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={'data': {'fastNewsList': []}})
    monkeypatch.setattr(news.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert news.fetch_provider('eastmoney', 'A股 热点') == []
    assert requests[0].url.host == 'np-weblist.eastmoney.com'
    assert requests[0].url.params['req_trace']
    def oversize(_req):
        return httpx.Response(200, content=b'a' * (news.MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(news.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(oversize), **kwargs))
    with pytest.raises(TradeError) as failure:
        news.fetch_provider('eastmoney', '')
    assert failure.value.code == 'NEWS_SOURCE_LIMIT'


def test_http_cache_read_never_fetches_explicit_refresh_is_csrf_guarded(tmp_path, monkeypatch):
    calls = []
    def provider(*args):
        calls.append(args)
        # This succeeds only if the route released reads before network and has no write transaction.
        with app.state.db_factory.begin() as session:
            session.execute(text("UPDATE market_news_snapshots SET created_at=created_at WHERE 1=0"))
        return rows(1)
    monkeypatch.setattr(news, 'fetch_provider', provider)
    with news_client(tmp_path) as (app, client):
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        assert client.get('/api/v1/market/news').json()['data']['status'] == 'not_loaded'
        assert not calls
        body = {'as_of_at': NOW.isoformat()}
        assert client.post('/api/v1/market/news/refresh', json=body).status_code == 403
        assert not calls
        response = client.post('/api/v1/market/news/refresh', json=body,
                               headers={'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())})
        assert response.status_code == 200, response.text
        assert response.json()['data']['count'] == 1 and len(calls) == 1
        assert client.get('/api/v1/market/news?age_hours=24').json()['data']['cache_hit']
        assert len(calls) == 1
        assert client.get('/api/v1/market/news?age_hours=25').status_code == 400
