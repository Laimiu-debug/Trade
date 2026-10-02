import pytest

from trade_app.market.cache_csv import import_cache_csv
from trade_app.market.online_common import prepare_online_sync, save_online_sync
from trade_app.market.service import get_dataset, store_dataset
from trade_app.market.symbols import market_symbol_key, normalize_market_symbol
from trade_app.market.tdx import DAY_RECORD, import_tdx_day, normalize_tdx_symbol
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from test_wyckoff_research_api import client_for, data, write


@pytest.mark.parametrize('raw,expected', [
    ('920001', ('bj', '920001')), ('920001.BJ', ('bj', '920001')),
    ('BJ920001', ('bj', '920001')), ('600000.SH', ('sh', '600000')),
    ('300001.SZ', ('sz', '300001')), ('510300', ('sh', '510300')),
    ('510300.SH', ('sh', '510300')), ('159915.SZ', ('sz', '159915')),
    ('sh000001', ('sh', '000001')), ('000001.SH', ('sh', '000001')),
    ('000001', ('sz', '000001')), ('399001.SZ', ('sz', '399001')),
    ('sh881001', ('sh', '881001')), ('881001.SZ', ('sz', '881001')),
])
def test_tdx_stock_fund_and_explicit_index_identity(raw, expected):
    assert normalize_tdx_symbol(raw) == normalize_market_symbol(raw) == expected


@pytest.mark.parametrize('raw', ['SH920001', '600000.SZ', 'sh600000.sz', '../600000', '600000/SH'])
def test_invalid_exchange_or_path_is_rejected(raw):
    with pytest.raises(TradeError):
        normalize_tdx_symbol(raw)


def test_keys_merge_aliases_but_never_merge_same_code_index_and_stock():
    assert market_symbol_key('600000') == market_symbol_key(' SH600000 ') == market_symbol_key('600000.SH')
    assert market_symbol_key('000001') != market_symbol_key('sh000001')
    assert market_symbol_key('AAPL') == market_symbol_key('aapl') == 'raw:AAPL'


def test_full_universe_includes_new_beijing_codes_but_excludes_indices(tmp_path):
    from trade_app.research.tdx_universe import scan_tdx_universe

    for exchange, code in [('bj', '920001'), ('bj', '430047'), ('bj', '832000'),
                           ('sh', '600000'), ('sz', '000001'), ('sh', '000001'),
                           ('sz', '920001'), ('sh', '510300')]:
        folder = tmp_path / 'vipdoc' / exchange / 'lday'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{exchange}{code}.day').write_bytes(b'\0' * DAY_RECORD.size * 2)
    assert scan_tdx_universe(tmp_path, ['bj', 'sh', 'sz'], min_bars=2) == [
        'bj430047', 'bj832000', 'bj920001', 'sh600000', 'sz000001']


def test_import_beijing_and_index_keeps_source_and_exchange(tmp_path):
    root = tmp_path / 'tdx'
    raw = b''.join(DAY_RECORD.pack(day, 1000, 1100, 900, 1050, 12345.0, 1000, 0)
                   for day in (20250101, 20250102))
    for exchange, code in [('bj', '920001'), ('sh', '000001'), ('sz', '000001'), ('sh', '881001')]:
        folder = root / exchange / 'lday'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{exchange}{code}.day').write_bytes(raw)
    engine, factory = open_database(tmp_path / 'data')
    try:
        with factory.begin() as session:
            bj = import_tdx_day(session, tmp_path / 'data', '920001.BJ', root)
            sh = import_tdx_day(session, tmp_path / 'data', '000001.SH', root)
            sz = import_tdx_day(session, tmp_path / 'data', '000001', root)
            sector = import_tdx_day(session, tmp_path / 'data', 'sh881001', root)
            assert bj['symbol'] == '920001'
            assert sh['symbol'] == 'sh000001' and sz['symbol'] == '000001'
            assert sector['symbol'] == 'sh881001'
            assert sh['id'] != sz['id']
            assert get_dataset(session, tmp_path / 'data', bj['id'])['source']['filename'] == 'bj920001.day'
        assert (root / 'bj/lday/bj920001.day').read_bytes() == raw
    finally:
        engine.dispose()


def bars():
    return [{'event_date': day, 'open': '10', 'high': '11', 'low': '9', 'close': '10.5',
             'volume': 1000, 'available_at': day + 'T08:00:00Z'} for day in ('2025-01-01', '2025-01-02')]


def test_online_index_incremental_never_merges_same_code_stock_history(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as session:
            original = store_dataset(session, tmp_path, '000001', bars(), 'baostock', 'none')
        fetched = []
        with factory() as session:
            prepared = prepare_online_sync(session, tmp_path, {
                'symbol': '000001.SH', 'start_date': '2025-01-01', 'end_date': '2025-01-02', 'mode': 'incremental'},
                provider='baostock', fetcher=lambda symbol, a, b: fetched.append(symbol) or bars(),
                parser=lambda code, rows, a, b: rows, source={'format': 'fixture'})
        assert fetched == ['sh000001']
        assert prepared['existing_id'] is None and prepared['code'] == 'sh000001'
        with factory.begin() as session:
            index = save_online_sync(session, tmp_path, prepared)['dataset']
            assert index['symbol'] == 'sh000001' and index['id'] != original['id']
    finally:
        engine.dispose()


def test_csv_aliases_accept_suffix_and_preserve_index_identity(tmp_path):
    root = tmp_path / 'cache'
    root.mkdir()
    for code, symbol in [('sh600000', '600000.SH'), ('sh000001', '000001')]:
        (root / (code + '.csv')).write_text('date,open,high,low,close,volume,symbol\n' +
            ''.join(f'{day},10,11,9,10.5,1000,{symbol}\n' for day in ('2025-01-01', '2025-01-02')), encoding='utf-8')
    engine, factory = open_database(tmp_path / 'data')
    try:
        with factory.begin() as session:
            stock = import_cache_csv(session, tmp_path / 'data', 'akshare', '600000.SH', {'akshare': root})
            index = import_cache_csv(session, tmp_path / 'data', 'akshare', '000001.SH', {'akshare': root})
            assert stock['symbol'] == '600000' and index['symbol'] == 'sh000001'
    finally:
        engine.dispose()


def test_sim_alias_match_advance_valuation_and_duplicate_rejection(tmp_path):
    with client_for(tmp_path) as client:
        sample = data(write(client, '/market/datasets', {'symbol': '600000.SH', 'bars': bars()}))
        alias = data(write(client, '/market/datasets', {'symbol': 'SH600000', 'bars': bars()}))
        sim = data(write(client, '/sim-accounts', {'name': '别名撮合', 'initial_capital': '10000', 'start_date': '2025-01-01'}))
        root = '/sim-accounts/' + sim['id']
        order = data(write(client, root + '/orders', {'symbol': '600000', 'side': 'buy', 'quantity': 100,
            'limit_price': '11', 'signal_date': '2025-01-01', 'submit_date': '2025-01-01'}))
        wallet = data(client.get('/api/v1' + root + '/portfolio'))
        request = {'expected_wallet_revision': wallet['wallet_revision'], 'to_date': '2025-01-02',
                   'datasets': {'sh600000': sample['id']}}
        invalid = write(client, root + '/advance-market-day', {**request, 'datasets': {
            'sh600000': sample['id'], '600000.SH': alias['id']}})
        assert invalid.status_code == 400 and invalid.json()['error']['code'] == 'DUPLICATE_SYMBOL_DATASET'
        result = data(write(client, root + '/advance-market-day', request))
        assert result['outcomes'][0]['status'] == 'filled'
        assert result['outcomes'][0]['order']['symbol'] == order['symbol'] == '600000'
        value = data(client.get('/api/v1' + root + '/valuation', params={
            'dataset_id': sample['id'], 'decision_at': '2025-01-02T09:00:00Z'}))
        assert value['positions'][0]['market_value'] == '1050.00' and value['total_assets'] is not None
        duplicate = client.get('/api/v1' + root + '/valuation', params=[
            ('dataset_id', sample['id']), ('dataset_id', alias['id']), ('decision_at', '2025-01-02T09:00:00Z')])
        assert duplicate.status_code == 400 and duplicate.json()['error']['code'] == 'DUPLICATE_SYMBOL_DATASET'


def test_same_numeric_code_wrong_exchange_cannot_fill_order(tmp_path):
    with client_for(tmp_path) as client:
        sample = data(write(client, '/market/datasets', {'symbol': 'sh000001', 'bars': bars()}))
        sim = data(write(client, '/sim-accounts', {'name': '指数隔离', 'initial_capital': '10000', 'start_date': '2025-01-01'}))
        root = '/sim-accounts/' + sim['id']
        order = data(write(client, root + '/orders', {'symbol': '000001', 'side': 'buy', 'quantity': 100,
            'limit_price': '11', 'signal_date': '2025-01-01', 'submit_date': '2025-01-01'}))
        data(write(client, root + '/settle', {'to_date': '2025-01-02'}))
        response = write(client, root + '/orders/' + order['id'] + '/match-open', {
            'expected_revision': order['revision'], 'dataset_id': sample['id']})
        assert response.status_code == 409 and response.json()['error']['code'] == 'MARKET_SYMBOL_MISMATCH'
        assert data(client.get('/api/v1' + root + '/portfolio'))['positions'] == []


def test_real_estimate_uses_alias_without_rewriting_ledger(tmp_path):
    with client_for(tmp_path) as client:
        sample = data(write(client, '/market/datasets', {'symbol': '600000.SH', 'bars': bars()}))
        account = data(write(client, '/accounts', {'name': '估值别名'}))
        root = '/accounts/' + account['id']
        data(write(client, root + '/cash-flows', {'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'}))
        trade = data(write(client, root + '/trades', {'trade_date': '2025-01-01', 'symbol': '600000',
            'side': 'buy', 'quantity': 100, 'price': '10', 'fee': '0', 'fee_mode': 'manual'}))
        result = data(client.get('/api/v1' + root + '/asset-estimate/2025-01-02', params={
            'dataset_id': sample['id'], 'decision_at': '2025-01-02T09:00:00Z'}))
        assert result['total_assets'] == '10050.00'
        assert result['positions'][0]['symbol'] == trade['symbol'] == '600000'
