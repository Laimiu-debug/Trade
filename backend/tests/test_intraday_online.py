import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import text

from trade_app.market import intraday_online as online, intraday_service as service
from trade_app.market.intraday_models import IntradaySnapshot
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError

NOW = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)
DAY = '2026-09-25'


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(online, 'now_utc', lambda: NOW)


def source(*rows, market=0, code='000001'):
    return json.dumps({'rc': 0, 'data': {'code': code, 'market': market, 'name': '源证券',
                                      'trends': list(rows) or [DAY + ' 09:30,10,10.2,10.3,9.9,200,204000,10.2']}}).encode()


def parse(contents=None, symbol='sz000001', day=DAY):
    return online.parse_response(contents or source(), online.request_config(symbol, day))


def test_same_code_stock_and_index_identity_and_units():
    stock = parse()
    index = parse(source(market=1), '000001.SH')
    assert stock['symbol'] == 'sz000001' and stock['instrument_type'] == 'stock'
    assert index['symbol'] == 'sh000001' and index['instrument_type'] == 'index'
    assert stock['price_unit'] == 'CNY' and index['price_unit'] == 'index_points'
    assert stock['volume_unit'] == index['volume_unit'] == 'lots'
    assert stock['points'][0]['volume'] == '200'  # Preserve lots, not mistaken for shares.
    assert stock['points'][0]['amount'] == '204000'
    assert stock['points'][0]['event_at'] == DAY + 'T09:30:00+08:00'
    assert online.identity('000001')['secid'] == '0.000001'
    assert online.identity('600000.SH')['secid'] == '1.600000'
    assert online.identity('399006.SZ')['instrument_type'] == 'index'
    assert stock['availability_quality'] == 'historical_availability_unknown'
    assert 'available_at' not in stock['points'][0]


@pytest.mark.parametrize('symbol', ['bj920001', '510300.SH', 'sh000999', 'sz399999', '399001', 'SH600000.SZ'])
def test_unsupported_identity_never_networks(symbol, monkeypatch):
    monkeypatch.setattr(online, 'fetch_bytes', lambda *_: pytest.fail('unsupported instrument reached network'))
    with pytest.raises(TradeError): online.prepare(symbol, DAY)


def test_wrong_market_and_code_never_fallback():
    for contents in (source(market=1), source(code='000002'), source(market='0')):
        with pytest.raises(TradeError) as error: parse(contents)
        assert error.value.code == 'INTRADAY_IDENTITY_MISMATCH'


def test_zero_open_and_missing_values_are_null_but_actual_zero_volume_is_kept():
    result = parse(source(DAY + ' 09:30,0,10.2,0,0,0,-,0'))
    point = result['points'][0]
    assert point == {'time': '09:30', 'event_at': DAY + 'T09:30:00+08:00',
                     'open': None, 'high': None, 'low': None, 'close': '10.2',
                     'volume': '0', 'amount': None, 'average': None}
    assert result['quality_flags'] == ['source_fields_missing']


def test_future_observations_excluded_at_request_time_and_dates_not_replaced():
    result = parse(source(DAY + ' 09:30,10,10.2,10.3,9.9,200,204000,10.2',
                          DAY + ' 10:01,10,999,999,9,100,10000,10',
                          DAY + ' 10:00,10,999,999,9,100,10000,10',
                          '2026-09-24 09:30,10,10.1,10.3,9.9,200,204000,10.2'))
    assert len(result['points']) == 1 and result['points'][0]['close'] == '10.2'
    assert result['excluded_future_count'] == 2
    assert result['as_of_at'] == NOW.isoformat()
    with pytest.raises(TradeError) as error: parse(day='2026-09-23')
    assert error.value.code == 'INTRADAY_DATE_NOT_FOUND'
    with pytest.raises(TradeError) as error: online.request_config('000001', '2026-09-26')
    assert error.value.code == 'INTRADAY_FUTURE_DATE'
    # UTC yesterday is already the next day in Shanghai.
    assert online.request_config('000001', DAY, observed_at=datetime(2026, 9, 24, 18, tzinfo=timezone.utc))['date'] == DAY


def test_local_reader_preserves_same_code_exchange_and_index_unit(tmp_path):
    from trade_app.market.intraday import LC1_RECORD, read_tdx_intraday
    for market in ('sh', 'sz'):
        folder = tmp_path / 'vipdoc' / market / 'minline'
        folder.mkdir(parents=True)
        (folder / f'{market}000001.lc1').write_bytes(LC1_RECORD.pack(((2025 - 2004) << 11) + 101,
            571, 10, 11, 9, 10.5, 10000, 1000, 0))
    index = read_tdx_intraday(tmp_path, '000001.SH', '2025-01-01')
    stock = read_tdx_intraday(tmp_path, '000001', '2025-01-01')
    assert index['symbol'] == 'sh000001' and index['price_unit'] == 'index_points'
    assert stock['symbol'] == 'sz000001' and stock['price_unit'] == 'CNY'


@pytest.mark.parametrize('row', [
    '09:30,10,NaN,10.3,9.9,200,204000,10.2', '09:30,10,0,10.3,9.9,200,204000,10.2',
    '09:30,10,11,10.3,9.9,200,204000,10.2', '09:30,10,10,10.3,9.9,-1,204000,10.2',
    '09:30,10,10,10.3,9.9,2,Infinity,10.2', '25:30,10,10,10.3,9.9,2,200,10.2',
    '09:30,10,10,10.3,9.9,2,200',
])
def test_malformed_rows_never_saved(row):
    with pytest.raises(TradeError) as error: parse(source(DAY + ' ' + row))
    assert error.value.code == 'INTRADAY_SOURCE_FORMAT'


def test_duplicate_empty_missing_data_and_response_size():
    row = DAY + ' 09:30,10,10.2,10.3,9.9,200,204000,10.2'
    with pytest.raises(TradeError): parse(source(row, row))
    with pytest.raises(TradeError) as error: parse(b'{"rc":0,"data":null}')
    assert error.value.code == 'INTRADAY_SOURCE_EMPTY'
    with pytest.raises(TradeError) as error: parse(b'x' * (online.MAX_BYTES + 1))
    assert error.value.code == 'INTRADAY_RESPONSE_TOO_LARGE'
    with pytest.raises(TradeError): parse(b'[]')


def test_network_boundaries_redaction_and_slots(monkeypatch):
    actual_client = httpx.Client
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=source())
    monkeypatch.setattr(online.httpx, 'Client', lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    assert online.fetch_bytes('0.000001') == source()
    assert str(seen[0].url).startswith(online.URL)
    assert seen[0].url.params['secid'] == '0.000001'
    assert seen[0].url.params['ndays'] == '5'
    def timeout(_request): raise httpx.ReadTimeout('provider-private-data')
    monkeypatch.setattr(online.httpx, 'Client', lambda **kwargs: actual_client(transport=httpx.MockTransport(timeout), **kwargs))
    with pytest.raises(TradeError) as error: online.fetch_bytes('0.000001')
    assert error.value.code == 'INTRADAY_TIMEOUT' and 'provider-private-data' not in str(error.value)
    assert online._slots.acquire(blocking=False) and online._slots.acquire(blocking=False)
    try:
        with pytest.raises(TradeError) as error: online.fetch_bytes('0.000001')
        assert error.value.code == 'INTRADAY_BUSY'
    finally:
        online._slots.release(); online._slots.release()


def test_cache_hash_alias_isolation_restart_backup_and_delete(tmp_path):
    path = tmp_path / 'original'
    engine, factory = open_database(path)
    with factory.begin() as session:
        stock = service.save_snapshot(session, parse())
        again = service.save_snapshot(session, parse())
        index = service.save_snapshot(session, parse(source(market=1), 'sh000001'))
        assert stock['id'] == again['id'] != index['id']
        assert [row['id'] for row in service.list_snapshots(session, '000001.SZ')] == [stock['id']]
        assert [row['id'] for row in service.list_snapshots(session, 'sh000001')] == [index['id']]
    contents = create_backup(path)
    restored = tmp_path / 'restored'
    restore_to_new_directory(contents, restored)
    other_engine, other_factory = open_database(restored)
    try:
        with other_factory.begin() as session:
            assert service.get_snapshot(session, stock['id']) == stock
            service.delete_snapshot(session, stock['id'])
            assert service.list_snapshots(session, '000001') == []
            assert service.get_snapshot(session, index['id'])['symbol'] == 'sh000001'
        with factory.begin() as session:
            row = session.get(IntradaySnapshot, stock['id'])
            row.payload_json = '{}'
        with factory() as session:
            with pytest.raises(TradeError) as error: service.get_snapshot(session, stock['id'])
            assert error.value.code == 'INTRADAY_CACHE_CORRUPTED'
    finally:
        other_engine.dispose(); engine.dispose()


def test_api_explicit_network_idempotency_failed_refresh_keeps_cache_and_short_write(tmp_path, monkeypatch):
    from trade_app.main import create_app
    from test_trade_rebuild import started_client
    app = create_app(tmp_path, auto_rebuild=False)
    calls = []
    def fetch(secid):
        calls.append(secid)
        # The network phase must not hold SQLite's write lock.
        with app.state.db_factory.begin() as session: session.execute(text('BEGIN IMMEDIATE'))
        return source()
    monkeypatch.setattr(online, 'fetch_bytes', fetch)
    with started_client(app) as client:
        csrf = client.get('/api/v1/session').json()['data']['csrf_token']
        root = '/api/v1/market/intraday'
        assert client.get(root + '/capabilities').status_code == 200
        assert client.get(root + '/snapshots', params={'symbol': '000001'}).json()['data'] == []
        assert calls == []
        body = {'symbol': '000001', 'date': DAY}
        assert client.post(root + '/fetch', json=body).status_code == 403
        assert calls == []
        headers = {'X-CSRF-Token': csrf, 'Idempotency-Key': 'minute-once'}
        first = client.post(root + '/fetch', json=body, headers=headers)
        assert first.status_code == 200, first.text
        identifier = first.json()['data']['id']
        replay = client.post(root + '/fetch', json=body, headers=headers)
        assert replay.json() == first.json() and calls == ['0.000001']
        conflict = client.post(root + '/fetch', json={**body, 'symbol': 'sh000001'}, headers=headers)
        assert conflict.status_code == 409 and calls == ['0.000001']
        assert client.get(root + '/snapshots/' + identifier).json() == first.json()
        assert client.get(root + '/snapshots', params={'symbol': 'sh000001'}).json()['data'] == []
        monkeypatch.setattr(online, 'fetch_bytes', lambda *_: (_ for _ in ()).throw(TradeError('INTRADAY_TIMEOUT', '超时', 504)))
        failed = client.post(root + '/fetch', json=body, headers={**headers, 'Idempotency-Key': 'minute-failed'})
        assert failed.status_code == 504
        assert len(client.get(root + '/snapshots', params={'symbol': '000001.SZ'}).json()['data']) == 1
        assert client.get(root + '/snapshots/' + identifier).json() == first.json()
