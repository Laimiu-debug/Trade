import time

from test_wyckoff_research_api import client_for, data


def test_second_tab_reuses_cookie_and_csrf_without_extending_lifetime(tmp_path):
    with client_for(tmp_path) as client:
        first = data(client.get('/api/v1/session'))
        sid = client.cookies.get('trade_rebuild_sid')
        expires = client.app.state.sessions[sid][1]
        # All tabs on this origin share the cookie, even though each has its own JS token.
        second_response = client.get('/api/v1/session')
        second = data(second_response)
        assert second['csrf_token'] == first['csrf_token']
        assert 'set-cookie' not in second_response.headers
        assert second_response.headers['cache-control'] == 'no-store'
        assert client.app.state.sessions[sid][1] == expires
        assert len(client.app.state.sessions) == 1
        assert data(client.post('/api/v1/accounts', json={'name': '旧标签仍可保存'}, headers={
            'X-CSRF-Token': first['csrf_token'], 'Idempotency-Key': 'tab-one-save'}))['name'] == '旧标签仍可保存'


def test_expired_or_unrecognized_session_is_replaced_and_old_csrf_rejected(tmp_path):
    with client_for(tmp_path) as client:
        first = data(client.get('/api/v1/session'))
        sid = client.cookies.get('trade_rebuild_sid')
        client.app.state.sessions[sid] = (first['csrf_token'], time.time() - 1)
        response = client.get('/api/v1/session')
        second = data(response)
        assert second['csrf_token'] != first['csrf_token'] and 'set-cookie' in response.headers
        assert client.post('/api/v1/accounts', json={'name': '旧令牌'}, headers={
            'X-CSRF-Token': first['csrf_token'], 'Idempotency-Key': 'expired-save'}).status_code == 403
        assert data(client.post('/api/v1/accounts', json={'name': '新会话'}, headers={
            'X-CSRF-Token': second['csrf_token'], 'Idempotency-Key': 'fresh-save'}))['name'] == '新会话'
