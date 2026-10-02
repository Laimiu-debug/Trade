from datetime import date

import pytest

from trade_app.market import provider_health as health, akshare_online
from trade_app.market.tdx import DAY_RECORD
from trade_app.platform.types import TradeError
from test_wyckoff_research_api import client_for, data, write


BODY = {'symbol': '600000', 'start_date': '2025-01-01', 'end_date': '2025-01-15'}


def remote_rows():
    return [{'日期': day, '股票代码': '600000', '开盘': 10, '最高': 11, '最低': 9, '收盘': 10.5,
             '成交量': 100, '成交额': 100000} for day in ('2025-01-02', '2025-01-03')]


def test_listing_never_fetches_or_claims_connectivity(tmp_path, monkeypatch):
    monkeypatch.setattr(health.akshare_online, 'fetch_akshare_rows', lambda *args: pytest.fail('GET cannot fetch'))
    with client_for(tmp_path) as client:
        result = data(client.get('/api/v1/market/providers'))
        assert len(result['sources']) == 5
        assert all(row['health'] == 'not_tested' and row['adjustment'] == ['none'] for row in result['sources'])
        assert data(client.get('/api/v1/market/datasets')) == []


def test_explicit_probe_validates_units_but_never_saves_dataset(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(health.akshare_online, 'fetch_akshare_rows', lambda *args: seen.append(args) or remote_rows())
    with client_for(tmp_path) as client:
        result = data(write(client, '/market/providers/akshare/probe', BODY))
        assert result['status'] == 'ok' and result['sample_count'] == 2 and result['persisted'] is False
        assert seen == [('600000', date(2025, 1, 1), date(2025, 1, 15))]
        assert data(client.get('/api/v1/market/datasets')) == []
        assert not list((tmp_path / 'market').glob('*.json'))


def test_stock_only_adapter_rejects_same_code_index_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(health.akshare_online, 'fetch_akshare_rows', lambda *args: pytest.fail('index must not fetch stock'))
    result = health.probe('akshare', {**BODY, 'symbol': 'sh000001'})
    assert result['status'] == 'failed' and result['error_code'] == 'MARKET_PROVIDER_UNSUPPORTED'
    from trade_app.platform.db import open_database
    engine, factory = open_database(tmp_path / 'isolated')
    try:
        with factory() as session:
            with pytest.raises(TradeError, match='仅支持 A 股'):
                akshare_online.prepare_akshare_sync(session, tmp_path / 'isolated',
                    {**BODY, 'symbol': 'sh000001', 'mode': 'replace'}, fetcher=lambda *args: pytest.fail('index fetch'))
    finally:
        engine.dispose()


def test_empty_failed_and_invalid_data_are_not_reported_healthy(monkeypatch):
    monkeypatch.setattr(health.akshare_online, 'fetch_akshare_rows', lambda *args: [])
    assert health.probe('akshare', BODY)['status'] == 'empty'
    def fail(*args):
        raise TradeError('MARKET_PROVIDER_TIMEOUT', '请求超时')
    monkeypatch.setattr(health.akshare_online, 'fetch_akshare_rows', fail)
    assert health.probe('akshare', BODY)['error_code'] == 'MARKET_PROVIDER_TIMEOUT'
    rows = remote_rows()
    rows[0]['最低'] = 100
    monkeypatch.setattr(health.akshare_online, 'fetch_akshare_rows', lambda *args: rows)
    assert health.probe('akshare', BODY)['status'] == 'failed'


def test_local_probe_preserves_original_and_explicitly_limits_scope(tmp_path):
    folder = tmp_path / 'vipdoc/sh/lday'
    folder.mkdir(parents=True)
    original = b''.join(DAY_RECORD.pack(day, 1000, 1100, 900, 1050, 100000.0, 10000, 0) for day in (20250102, 20250103))
    path = folder / 'sh600000.day'
    path.write_bytes(original)
    result = health.probe('tdx', BODY, tdx_root=tmp_path)
    assert result['status'] == 'readable' and result['scope'] == 'first_last_record'
    assert path.read_bytes() == original
    cache = tmp_path / 'cache'
    cache.mkdir()
    csv = cache / 'sh600000.csv'
    csv.write_text('date,open,high,low,close,volume\n2025-01-02,10,11,9,10.5,100\n', encoding='utf-8')
    assert health.probe('akshare_cache', BODY, cache_roots={'akshare': cache})['scope'] == 'header_only'
    csv.write_text('bad,header\n', encoding='utf-8')
    assert health.probe('akshare_cache', BODY, cache_roots={'akshare': cache})['status'] == 'failed'


def test_probe_window_and_concurrent_access_are_bounded(monkeypatch):
    with pytest.raises(TradeError):
        health.probe('akshare', {**BODY, 'end_date': '2025-03-01'})
    lock = health._locks['akshare']
    lock.acquire()
    try:
        with pytest.raises(TradeError, match='正在测试'):
            health.probe('akshare', BODY)
    finally:
        lock.release()
