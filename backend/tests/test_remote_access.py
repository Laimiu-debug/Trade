import asyncio
from contextlib import contextmanager

from fastapi.testclient import TestClient
import httpx
import pytest

from trade_app.main import create_app
from trade_app.platform.remote_access import RemoteAccess

ORIGIN = 'https://trade.example.com'
TOKEN = 'x' * 43


@contextmanager
def remote_client(path):
    app = create_app(path, auto_rebuild=False)
    lifespan = app.router.lifespan_context(app)
    with asyncio.Runner() as runner:
        runner.run(lifespan.__aenter__())
        try:
            with TestClient(app, base_url=ORIGIN) as client:
                yield client
        finally:
            runner.run(lifespan.__aexit__(None, None, None))


@pytest.mark.parametrize('origin', ['http://trade.example.com', 'https://user:pw@trade.example.com',
    'https://trade.example.com/path', 'https://trade.example.com?key=x', 'https://127.0.0.1',
    'https://localhost', 'https://*.example.com', 'https://trade.example.com:99999'])
def test_invalid_remote_origin_fails_before_disk_access(monkeypatch, origin):
    monkeypatch.setenv('TRADE_PUBLIC_ORIGIN', origin)
    monkeypatch.setenv('TRADE_PROXY_TOKEN', TOKEN)
    with pytest.raises(ValueError):
        RemoteAccess.from_environment()


def test_remote_requires_independent_proxy_token_and_never_exposes_it(monkeypatch):
    monkeypatch.setenv('TRADE_PUBLIC_ORIGIN', ORIGIN)
    monkeypatch.delenv('TRADE_PROXY_TOKEN', raising=False)
    with pytest.raises(ValueError, match='TRADE_PROXY_TOKEN'):
        RemoteAccess.from_environment()
    monkeypatch.setenv('TRADE_PROXY_TOKEN', TOKEN)
    assert TOKEN not in repr(RemoteAccess.from_environment())


def test_https_proxy_session_csrf_and_origin_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADE_PUBLIC_ORIGIN', ORIGIN)
    monkeypatch.setenv('TRADE_PROXY_TOKEN', TOKEN)
    with remote_client(tmp_path) as client:
        for url in ('/health', '/api/v1/session', '/rebuild.html', '/api/v1/accounts'):
            assert client.get(url).status_code == 403
        client.headers.update({'X-Trade-Proxy-Token': TOKEN, 'X-Forwarded-Proto': 'http'})
        assert client.get('/api/v1/session').status_code == 403
        client.headers['X-Forwarded-Proto'] = 'https'
        assert client.get('/api/v1/accounts').status_code == 401
        response = client.get('/api/v1/session')
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
        csrf = response.json()['data']['csrf_token']
        assert client.post('/api/v1/accounts', json={'name': '拒绝匿名写入'}).status_code == 403
        client.headers.update({'X-CSRF-Token': csrf, 'Origin': ORIGIN, 'Idempotency-Key': 'remote-create'})
        assert client.post('/api/v1/accounts', json={'name': '认证入口账户'}).status_code == 200
        for origin in ('https://evil.example', 'http://127.0.0.1:8011', 'null'):
            assert client.get('/api/v1/accounts', headers={'Origin': origin}).status_code == 403
        assert client.get('/api/v1/accounts', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert client.get('/api/v1/accounts', headers={'X-Trade-Proxy-Token': 'wrong'}).status_code == 403
        assert TOKEN not in client.get('/health').text


def test_default_local_mode_does_not_accept_proxy_headers_as_auth(tmp_path, monkeypatch):
    monkeypatch.delenv('TRADE_PUBLIC_ORIGIN', raising=False)
    with remote_client(tmp_path) as client:
        assert client.get('/api/v1/session', headers={'X-Trade-Proxy-Token': TOKEN,
            'X-Forwarded-Proto': 'https'}).status_code == 403


def test_even_forged_local_host_from_nonlocal_peer_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv('TRADE_PUBLIC_ORIGIN', raising=False)
    app = create_app(tmp_path, auto_rebuild=False)
    async def check():
        transport = httpx.ASGITransport(app=app, client=('203.0.113.10', 12345))
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8011') as client:
            return await client.get('/health')
    response = asyncio.run(check())
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'NON_LOOPBACK_PEER'
