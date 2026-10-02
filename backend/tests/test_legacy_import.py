import base64
import copy
import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import select, func

from trade_app.legacy_import import service
from trade_app.legacy_import.models import LegacyImport
from trade_app.legacy_import.reader import canonical, digest
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.models import AuditEvent, RebuildRequest
from trade_app.platform.types import TradeError
from trade_app.reviews.models import DailyReview
from trade_app.reviews.score_models import ReviewScoreSheet
from trade_app.trading.models import Account, Trade, CashFlow, AssetSnapshot, PendingTrade
from trade_app.trading.service import create_account, nav_inputs
from trade_app.trading.nav import calculate_nav


def sample():
    return {'exported_at': '2025-01-05T13:00:00',
            'capital_flows': [{'id': 1, 'flow_date': '2025-01-01', 'kind': 'initial', 'amount': 10000, 'note': ''},
                              {'id': 2, 'flow_date': '2025-01-01', 'kind': 'deposit', 'amount': 1000, 'note': ''}],
            'trades': [{'id': 2, 'trade_date': '2025-01-02', 'code': '600000.SH', 'side': 'sell', 'price': 12, 'qty': 100, 'fee_commission': 5, 'fee_stamp': '0.60', 'fee_transfer': '0.01'},
                       {'id': 1, 'trade_date': '2025-01-02', 'code': 'sh600000', 'side': 'buy', 'price': '10.1234', 'qty': 100, 'fee_commission': 5, 'fee_stamp': 0, 'fee_transfer': '0.01'}],
            'pending_trades': [{'id': 3, 'trade_date': '2025-01-03', 'code': '920001.BJ', 'side': 'buy', 'price': 2, 'qty': 100, 'raw_text': '待人工确认'}],
            'snapshots': [{'id': 1, 'snap_date': '2025-01-02', 'total_assets': 11000, 'available_cash': None, 'position_value': None,
                           'positions': '[{"code":"sh000001","name":"上证指数","qty":1,"market_value":3000}]'}],
            'daily_reviews': [{'id': 1, 'review_date': '2025-01-02', 'market_observation': '保留人工文字', 'scores': '{"discipline":{"ai":5,"final":8,"comment":"人工核对"}}',
                               'trade_scores': '{"1":{"timing":{"ai":6,"final":7,"comment":"买点"}}}', 'images': '["D:/private/chart.png"]', 'ai_summary': '旧AI摘要'}],
            'weekly_reviews': [{'id': 1, 'year': 2025, 'week': 1, 'right_things': '正确的事'}],
            'monthly_reviews': [{'id': 1, 'year': 2025, 'month': 1, 'system_iteration': '改进'}],
            'round_reviews': [{'id': 9, 'code': '600000', 'start_date': '2025-01-02', 'review_summary': '旧回合'}],
            'settings': [{'key': 'ai_api_key', 'value': 'never-persist-this-secret'}, {'key': 'extra_json', 'value': '{"apiKey":"nested-secret", "model":"test"}'}]}


def body(payload=None, **changes):
    raw = json.dumps(payload if payload is not None else sample(), ensure_ascii=False).encode()
    return {'filename': 'backup.json', 'content_base64': base64.b64encode(raw).decode(), 'mode': 'new_real_account', 'account_name': '旧账本独立副本', **changes}


@pytest.fixture
def db(tmp_path):
    engine, factory = open_database(tmp_path / 'data')
    try:
        yield tmp_path / 'data', factory
    finally:
        engine.dispose()


def save(session, request):
    source, preview = service.preview(request)
    return service.save_import(session, source, preview, preview['preview_sha256'], acknowledged=True)


def test_preview_is_deterministic_no_writes_secret_redaction_and_mapping(db):
    _, factory = db
    request = body()
    original = copy.deepcopy(request)
    source, result = service.preview(request)
    assert request == original and result == service.preview(request)[1]
    assert result['can_import'] and result['mapped_record_count'] == 9
    assert len(result['redacted_paths']) == 2
    assert 'never-persist' not in canonical(source) and 'nested-secret' not in canonical(source)
    assert any(item['section'] == 'round_reviews' and item['target'] == 'archive_only' for item in result['inventory'])
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 0
        assert session.scalar(select(func.count()).select_from(LegacyImport)) == 0


def test_import_new_account_exact_values_order_reference_scores_and_nulls(db):
    _, factory = db
    with factory.begin() as session:
        existing = create_account(session, name='现有账户')['id']
        result = save(session, body())
        assert result['account_id'] != existing
        trades = session.scalars(select(Trade).where(Trade.account_id == result['account_id']).order_by(Trade.sequence)).all()
        assert [row.side for row in trades] == ['buy', 'sell']
        assert trades[0].price_units == 101234 and trades[0].fee_minor == 501
        assert all(row.symbol == 'sh600000' for row in trades)
        pending = session.scalar(select(PendingTrade))
        assert pending.symbol == 'bj920001' and pending.status == 'pending'
        snap = session.scalar(select(AssetSnapshot))
        assert snap.available_cash_minor is None and snap.position_value_minor is None
        assert snap.positions[0].symbol == 'sh000001'
        scores = session.scalars(select(ReviewScoreSheet)).all()
        trade_score = next(row for row in scores if row.scope == 'trade')
        assert json.loads(trade_score.trade_ids_json) == [trades[0].id]
        assert json.loads(trade_score.scores_json)['timing']['final'] == 7
        assert json.loads(trade_score.scores_json)['timing']['ai'] == 6
        nav = calculate_nav(*nav_inputs(session, result['account_id']))
        assert nav['points'][-1]['nav'] is not None
        assert session.get(Account, existing).input_revision == 0
        assert session.scalar(select(func.count()).select_from(RebuildRequest)) == 1
        export = service.export_archive(session, result['id'])
        assert not export['original_bytes_included']
        assert export['logical_data']['round_reviews'][0]['review_summary'] == '旧回合'


@pytest.mark.parametrize('change', [
    lambda data: data['trades'][0].update(price='1.00001'),
    lambda data: data['capital_flows'][0].update(amount='1.005'),
    lambda data: data['trades'][0].update(trade_date='2025-01-02T00:00:00'),
    lambda data: data['trades'][0].update(code='sz600000'),
    lambda data: data['trades'][0].update(qty=True),
    lambda data: data['daily_reviews'][0].update(trade_scores='{"99":{"timing":{"final":6}}}'),
    lambda data: data['daily_reviews'][0].update(scores='{"discipline":{"final":11}}'),
    lambda data: data['snapshots'][0].update(available_cash=100, position_value=3000),
    lambda data: data['capital_flows'][0].update(kind='withdraw'),
    lambda data: data['trades'][0].update(id=1),
])
def test_invalid_facts_fail_preview_and_cannot_partially_import(db, change):
    _, factory = db
    data = sample()
    change(data)
    upload, preview = service.preview(body(data))
    assert not preview['can_import'] and preview['errors']
    with factory.begin() as session:
        with pytest.raises(TradeError, match='存在未解决'):
            service.save_import(session, upload, preview, preview['preview_sha256'], acknowledged=True)
        assert session.scalar(select(func.count()).select_from(Account)) == 0
    # The user can explicitly preserve even invalid legacy facts without promoting them.
    assert service.preview(body(data, mode='archive_only'))[1]['can_import']


def test_changed_preview_ack_and_duplicate_source_are_rejected(db):
    _, factory = db
    upload, preview = service.preview(body())
    with factory.begin() as session:
        with pytest.raises(TradeError) as exc:
            service.save_import(session, upload, preview, preview['preview_sha256'], acknowledged=False)
        assert exc.value.code == 'LEGACY_ACK_REQUIRED'
        altered = dict(preview, account_name='换账户')
        with pytest.raises(TradeError) as exc:
            service.save_import(session, upload, altered, preview['preview_sha256'], acknowledged=True)
        assert exc.value.code == 'LEGACY_PREVIEW_CHANGED'
        save(session, body())
        with pytest.raises(TradeError) as exc:
            save(session, body(account_name='不能重复'))
        assert exc.value.code == 'LEGACY_ALREADY_IMPORTED'
        assert session.scalar(select(func.count()).select_from(Account)) == 1


@pytest.mark.parametrize('payload,source', [
    ({'schema_version': 3, 'config': {'api_key': 'final-key'}, 'annotations': {'sh600000': {'stage': '观察'}}, 'daily_reviews': {'2025-01-02': {'summary': '旧模拟复盘'}}}, 'final_app_json'),
    ({'schema_version': 1, 'account': {'cash': 10000, 'as_of_date': '2025-01-03'}, 'orders': [], 'fills': [{'symbol': 'sh600000', 'fill_price': 10}], 'lots': []}, 'final_sim_json'),
])
def test_final_research_simulation_only_isolated_readonly_archive(db, payload, source):
    _, factory = db
    with pytest.raises(TradeError) as exc:
        service.preview(body(payload))
    assert exc.value.code == 'LEGACY_SOURCE_IS_NOT_REAL'
    with factory.begin() as session:
        result = save(session, body(payload, mode='archive_only'))
        assert result['source_kind'] == source and result['account_id'] is None
        assert 'final-key' not in canonical(service.get_import(session, result['id']))
        assert session.scalar(select(func.count()).select_from(Account)) == 0


def test_sqlite_logical_read_redacts_credentials_free_pages_never_archived(db, tmp_path):
    source_path = tmp_path / 'legacy.sqlite'
    with closing(sqlite3.connect(source_path)) as source:
        source.executescript('CREATE TABLE capital_flows (id INTEGER, flow_date TEXT, kind TEXT, amount REAL); CREATE TABLE trades (id INTEGER, trade_date TEXT, code TEXT, side TEXT, price REAL, qty INTEGER); CREATE TABLE snapshots (id INTEGER, snap_date TEXT, total_assets REAL); CREATE TABLE daily_reviews (id INTEGER, review_date TEXT); CREATE TABLE settings (key TEXT,value TEXT);')
        source.execute('INSERT INTO capital_flows VALUES (1,?,?,?)', ('2025-01-01', 'initial', 10000))
        source.execute('INSERT INTO settings VALUES (?,?)', ('ai_ocr_api_key', 'sqlite-private-key'))
        source.commit()
    raw = source_path.read_bytes()
    request = body(filename='legacy.sqlite', content_base64=base64.b64encode(raw).decode())
    upload, preview = service.preview(request)
    assert preview['can_import'] and upload['source'] == 'laimiu_sqlite'
    _, factory = db
    with factory.begin() as session:
        result = save(session, request)
        archive = service.export_archive(session, result['id'])
        assert 'sqlite-private-key' not in canonical(archive)
        assert archive['logical_data']['settings'][0]['value'].startswith('[REDACTED')
    assert source_path.read_bytes() == raw


def test_event_sqlite_is_archive_and_backup_roundtrip_has_no_known_secret(db, tmp_path):
    with closing(sqlite3.connect(':memory:')) as source:
        source.execute('CREATE TABLE wyckoff_daily_events(symbol TEXT,trade_date TEXT,events_json TEXT)')
        source.execute('INSERT INTO wyckoff_daily_events VALUES (?,?,?)', ('sh600000', '2025-01-02', '["spring"]'))
        source.commit()
        raw = source.serialize()
    path, factory = db
    with factory.begin() as session:
        first = save(session, body())
        event = save(session, body(filename='events.sqlite', content_base64=base64.b64encode(raw).decode(), mode='archive_only'))
        assert event['source_kind'] == 'final_event_sqlite'
    restored = tmp_path / 'restored'
    restore_to_new_directory(create_backup(path), restored)
    engine, recovered = open_database(restored)
    try:
        with recovered() as session:
            detail = service.get_import(session, first['id'])
            assert detail['account_id'] == first['account_id']
            assert detail['logical_sha256'] == digest(detail['archive'])
            assert 'never-persist-this-secret' not in canonical(detail)
            assert 'nested-secret' not in canonical(detail)
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == 'legacy_import')) >= 2
        assert b'never-persist-this-secret' not in (restored / 'trade.sqlite').read_bytes()
    finally:
        engine.dispose()


@pytest.mark.parametrize('raw', [b'{"exported_at":"x","trades":[],"trades":[]}', b'{"exported_at":"x","trades":NaN}', b'not-json', b'SQLite format 3\x00broken'])
def test_malformed_input_is_rejected(raw):
    with pytest.raises(TradeError):
        service.preview(body(content_base64=base64.b64encode(raw).decode()))


def test_server_paths_not_accepted():
    with pytest.raises(TradeError) as exc:
        service.preview(body(filename='C:\\secret\\backup.json'))
    assert exc.value.code == 'LEGACY_INVALID_FILENAME'


def test_credential_in_url_malformed_nested_json_and_http_bearer_is_redacted():
    data = {'schema_version': 3, 'annotations': {}, 'config': {
        'base_url': 'https://example.test/v1?api_key=query-secret',
        'broken_json': '{"api_key":"broken-secret"',
        'custom_header': 'Bearer bearer-secret-token',
        'nested': {'apikey': 'direct-secret'},
    }}
    source, preview = service.preview(body(data, mode='archive_only'))
    assert len(preview['redacted_paths']) == 4
    assert all(secret not in canonical(source) for secret in ('query-secret', 'broken-secret', 'bearer-secret', 'direct-secret'))


def test_api_preview_csrf_frozen_digest_idempotency_and_sanitized_export(tmp_path):
    from uuid import uuid4
    from test_watch_pool import client_at
    from trade_app.platform.models import IdempotencyKey
    with client_at(tmp_path) as (app, client):
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        request = body()
        assert client.post('/api/v1/legacy-imports/preview', json=request).status_code == 403
        response = client.post('/api/v1/legacy-imports/preview', json=request, headers=headers)
        assert response.status_code == 200, response.text
        frozen = response.json()['data']
        assert client.get('/api/v1/accounts').json()['data'] == []
        confirmed = {**request, 'expected_preview_sha256': frozen['preview_sha256'], 'acknowledge_limitations': True}
        changed = client.post('/api/v1/legacy-imports', json={**confirmed, 'account_name': '已变化'}, headers=headers)
        assert changed.status_code == 409
        first = client.post('/api/v1/legacy-imports', json=confirmed, headers=headers)
        assert first.status_code == 200, first.text
        assert client.post('/api/v1/legacy-imports', json=confirmed, headers=headers).json() == first.json()
        record = first.json()['data']
        assert len(client.get('/api/v1/accounts').json()['data']) == 1
        exported = client.get(f'/api/v1/legacy-imports/{record["id"]}/export.json')
        assert exported.status_code == 200 and not exported.json()['original_bytes_included']
        assert 'never-persist' not in exported.text and 'nested-secret' not in exported.text
        assert client.get('/api/v1/legacy-imports/absent').status_code == 404
        with app.state.db_factory() as session:
            saved = session.get(IdempotencyKey, ('legacy-imports', headers['Idempotency-Key']))
            assert 'content_base64' not in saved.response_json
            assert request['content_base64'] not in saved.response_json


def test_readonly_laimei_archive_promotion_is_explicit_once_and_keeps_logical_bytes(db):
    _, factory = db
    with factory.begin() as session:
        archived = save(session, body(mode='archive_only'))
        original = service.get_import(session, archived['id'])
        assert archived['revision'] == 1 and archived['account_id'] is None
        upload, frozen = service.prepare_promotion(session, archived['id'], 1, '显式转换')
        assert frozen['can_import'] and frozen['promotion_of'] == archived['id']
        assert session.scalar(select(func.count()).select_from(Account)) == 0
        with pytest.raises(TradeError) as error:
            service.save_promotion(session, archived['id'], upload, frozen, '0' * 64, acknowledged=True)
        assert error.value.code == 'LEGACY_PREVIEW_CHANGED'
        promoted = service.save_promotion(session, archived['id'], upload, frozen, frozen['preview_sha256'], acknowledged=True)
        assert promoted['revision'] == 2 and promoted['promoted'] and promoted['account_id']
        assert service.save_promotion(session, archived['id'], upload, frozen, frozen['preview_sha256'], acknowledged=True) == promoted
        assert session.scalar(select(func.count()).select_from(Account)) == 1
        current = service.get_import(session, archived['id'])
        assert current['archive'] == original['archive'] and current['original_preview'] == original['original_preview']
        assert current['logical_sha256'] == original['logical_sha256']
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == 'legacy_import', AuditEvent.operation == 'promote')) == 1
        with pytest.raises(TradeError) as error:
            service.prepare_promotion(session, archived['id'], 1, '第二账户')
        assert error.value.code == 'LEGACY_ALREADY_PROMOTED'


def test_promotion_rejects_bad_reference_final_source_and_wrong_revision(db):
    _, factory = db
    broken = sample()
    broken['daily_reviews'][0]['trade_scores'] = '{"999":{"timing":{"final":7}}}'
    with factory.begin() as session:
        archived = save(session, body(broken, mode='archive_only'))
        with pytest.raises(TradeError) as error:
            service.prepare_promotion(session, archived['id'], 2, '过期')
        assert error.value.code == 'LEGACY_REVISION_CONFLICT'
        upload, frozen = service.prepare_promotion(session, archived['id'], 1, '有坏引用')
        assert not frozen['can_import']
        with pytest.raises(TradeError) as error:
            service.save_promotion(session, archived['id'], upload, frozen, frozen['preview_sha256'], acknowledged=True)
        assert error.value.code == 'LEGACY_VALIDATION_FAILED'
        final = save(session, body({'schema_version': 3, 'annotations': {}}, mode='archive_only'))
        with pytest.raises(TradeError) as error:
            service.prepare_promotion(session, final['id'], 1, '不允许')
        assert error.value.code == 'LEGACY_SOURCE_IS_NOT_REAL'
        assert session.scalar(select(func.count()).select_from(Account)) == 0


def test_promotion_api_retry_and_backup_keep_revision_source_audit(tmp_path):
    from test_watch_pool import client_at
    from uuid import uuid4
    with client_at(tmp_path / 'source') as (app, client):
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        raw = body(mode='archive_only')
        preview = client.post('/api/v1/legacy-imports/preview', headers=headers, json=raw).json()['data']
        archived = client.post('/api/v1/legacy-imports', headers=headers, json={**raw, 'expected_preview_sha256': preview['preview_sha256'], 'acknowledge_limitations': True}).json()['data']
        path = '/api/v1/legacy-imports/' + archived['id']
        request = {'expected_revision': 1, 'account_name': '归档后转换'}
        preview = client.post(path + '/promotion-preview', headers=headers, json=request).json()['data']
        assert client.get('/api/v1/accounts').json()['data'] == []
        request.update(expected_preview_sha256=preview['preview_sha256'], acknowledge_limitations=True)
        headers['Idempotency-Key'] = str(uuid4())
        first = client.post(path + '/promote', headers=headers, json=request)
        assert first.status_code == 200, first.text
        assert client.post(path + '/promote', headers=headers, json=request).json() == first.json()
        headers['Idempotency-Key'] = str(uuid4())
        assert client.post(path + '/promote', headers=headers, json=request).json() == first.json()
        assert len(client.get('/api/v1/accounts').json()['data']) == 1
        archive = create_backup(tmp_path / 'source')
    restored = tmp_path / 'restore'
    restore_to_new_directory(archive, restored)
    engine, recovered = open_database(restored)
    try:
        with recovered() as session:
            detail = service.get_import(session, archived['id'])
            assert detail['revision'] == 2 and detail['promoted']
            assert detail['original_preview']['mode'] == 'archive_only'
            assert detail['promotion']['preview']['account_name'] == '归档后转换'
    finally:
        engine.dispose()
