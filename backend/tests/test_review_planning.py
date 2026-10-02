from datetime import date, timedelta
from decimal import Decimal

import pytest

from trade_app.main import create_app
from trade_app.platform.backup import create_backup, restore_to_new_directory
from test_matrix_pool_api import started_client, data


@pytest.fixture
def client(tmp_path):
    with started_client(create_app(tmp_path, auto_rebuild=False)) as client:
        token = data(client.get('/api/v1/session'))['csrf_token']
        serial = 0
        def write(path, body, method='POST'):
            nonlocal serial
            serial += 1
            return client.request(method, path, json=body, headers={
                'X-CSRF-Token': token, 'Idempotency-Key': f'planning-{serial}'})
        client.write = write
        client.account_id = data(write('/api/v1/accounts', {'name': 'planning account'}))['id']
        client.root = f'/api/v1/accounts/{client.account_id}'
        yield client


def snapshot(client, *, cash='1000.00', positions=None, day='2025-01-03'):
    if not data(client.get(client.root + '/cash-flows')):
        data(client.write(client.root + '/cash-flows', {'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '2000'}))
    positions = positions if positions is not None else [{'symbol': '600000', 'name': '持仓',
                                                         'quantity': 100, 'market_value': '1000'}]
    total = str(sum(Decimal(item['market_value']) for item in positions) + Decimal(cash or '1000'))
    return data(client.write(client.root + f'/snapshots/{day}', {
        'snap_date': day, 'total_assets': total, 'available_cash': cash,
        'positions': positions, 'expected_revision': 0}, 'PUT'))


def baseline(client, source='snapshot', day='2025-01-03'):
    return data(client.get(client.root + f'/planning/{day}/baseline?source={source}'))


def preview(client, base, rows, datasets=None):
    return client.write(client.root + f"/planning/{base['as_of_date']}/preview", {
        'baseline_source': base['source'], 'expected_baseline_sha256': base['sha256'],
        'rows': rows, 'dataset_ids': datasets or [], 'target_date': '2025-01-06'})


def calendar_body(start='2025-01-01', opens=('2025-01-03',), days=6, revision=0):
    rows = [{'date': (date.fromisoformat(start) + timedelta(days=i)).isoformat(), 'is_open': False} for i in range(days)]
    for row in rows:
        row['is_open'] = row['date'] in opens
    return {'source': '测试隔离日历来源，不代表真实交易安排', 'start_date': start, 'end_date': rows[-1]['date'],
            'days': rows, 'expected_revision': revision}


def test_blank_new_target_stays_unknown_and_existing_saved_target_survives(client):
    path = client.root + '/daily-reviews/2025-01-03'
    blank = data(client.write(path, {'expected_revision': 0, 'title': 'no calendar'}, 'PUT'))
    assert blank['next_target_date'] is None
    assert data(client.get(client.root + '/plan-comparison/2025-01-06'))['plans'] == []
    date_info = data(client.get('/api/v1/market/trading-calendar/plan-date?written_on=2025-01-03'))
    assert date_info['suggested_date'] is None and date_info['target_status'] == 'unknown'
    explicit = data(client.write(path, {'expected_revision': 1, 'next_target_date': '2025-01-06'}, 'PUT'))
    assert explicit['next_target_date'] == '2025-01-06'
    preserved = data(client.write(path, {'expected_revision': 2, 'title': 'legacy client omits field'}, 'PUT'))
    assert preserved['next_target_date'] == '2025-01-06'
    data(client.write('/api/v1/market/trading-calendar', calendar_body(), 'PUT'))
    assert data(client.get(path))['next_target_date'] == '2025-01-06'
    cleared = data(client.write(path, {'expected_revision': 3, 'next_target_date': None}, 'PUT'))
    assert cleared['next_target_date'] is None


def test_complete_local_calendar_uses_closed_dates_without_weekday_guess(client):
    saved = data(client.write('/api/v1/market/trading-calendar', calendar_body(), 'PUT'))
    assert saved['revision'] == 1 and len(saved['sha256']) == 64
    info = data(client.get('/api/v1/market/trading-calendar/plan-date?written_on=2025-01-01&target_date=2025-01-02'))
    assert info['suggested_date'] == '2025-01-03' and info['target_status'] == 'closed'
    beyond = data(client.get('/api/v1/market/trading-calendar/plan-date?written_on=2025-01-03&target_date=2025-01-10'))
    assert beyond['suggested_date'] is None and beyond['target_status'] == 'unknown'
    assert client.write('/api/v1/market/trading-calendar', calendar_body(), 'PUT').status_code == 409
    changed = calendar_body(opens=('2025-01-06',), revision=1)
    data(client.write('/api/v1/market/trading-calendar', changed, 'PUT'))
    audit = data(client.get('/api/v1/market/trading-calendar/audit'))
    assert [item['snapshot']['revision'] for item in audit] == [2, 1]


@pytest.mark.parametrize('mutation', ['gap', 'duplicate', 'coerced_bool', 'oversize'])
def test_calendar_rejects_missing_dates_and_invalid_bounds(client, mutation):
    body = calendar_body()
    if mutation == 'gap':
        body['days'].pop(1)
    elif mutation == 'duplicate':
        body['days'][1]['date'] = body['days'][0]['date']
    elif mutation == 'coerced_bool':
        body['days'][0]['is_open'] = 0
    else:
        body['end_date'] = '2030-01-01'
    response = client.write('/api/v1/market/trading-calendar', body, 'PUT')
    assert response.status_code in (400, 422)
    assert data(client.get('/api/v1/market/trading-calendar'))['revision'] == 0


def test_copy_baseline_snapshot_cash_unknown_and_no_ledger_double_count(client):
    snapshot(client, cash=None)
    data(client.write(client.root + '/cash-flows', {'flow_date': '2025-01-01', 'kind': 'deposit', 'amount': '100000'}))
    base = baseline(client)
    assert base['cash'] is None and base['positions'][0]['qty'] == 100
    result = data(preview(client, base, [{'code': '600000.SH', 'qty': 200, 'price': '10'}]))
    assert result['cash_status'] == 'unknown' and result['remaining_cash'] is None
    assert result['rows'][0]['current_qty'] == 100 and result['estimated_fees'] == '5.01'
    assert result['baseline']['snapshot_revision'] == 1


def test_ledger_baseline_includes_all_booked_fees_flows_and_excludes_future(client):
    for kind, amount in [('initial', '10000'), ('deposit', '1000'), ('withdraw', '500')]:
        data(client.write(client.root + '/cash-flows', {'flow_date': '2025-01-01', 'kind': kind, 'amount': amount}))
    for day, symbol, side, qty, price, fee in [
        ('2025-01-02', '600000', 'buy', 100, '10', '7'),
        ('2025-01-03', '600000.SH', 'sell', 40, '12', '6'),
        ('2025-01-04', '600000', 'buy', 100, '100', '90')]:
        data(client.write(client.root + '/trades', {'trade_date': day, 'symbol': symbol, 'side': side,
                                                  'quantity': qty, 'price': price, 'fee': fee, 'fee_mode': 'manual'}))
    data(client.write(client.root + '/cash-flows', {'flow_date': '2025-01-04', 'kind': 'deposit', 'amount': '900000'}))
    snapshot(client, cash='888888')
    base = baseline(client, 'ledger')
    assert base['cash'] == '9967.00' and base['positions'][0]['qty'] == 60
    assert len(base['facts']) == 5 and base['snapshot_id'] is None


def test_fees_make_otherwise_affordable_buy_insufficient_and_preview_never_writes(client):
    snapshot(client)
    base = baseline(client)
    result = data(preview(client, base, [{'code': 'sh600000', 'qty': 200, 'price': '10'}]))
    assert result['cash_status'] == 'insufficient' and result['remaining_cash'] == '-5.01'
    assert result['rows'][0]['fee_breakdown'] == {'commission': '5.00', 'stamp': '0.00', 'transfer': '0.01'}
    assert result['projected_total'] == '1994.99'
    assert baseline(client)['sha256'] == base['sha256']
    assert data(client.get(client.root + '/trades')) == []
    assert data(client.get(client.root + '/daily-reviews/2025-01-03')) is None


def test_omitted_baseline_position_stays_and_sell_proceeds_are_explicit(client):
    snapshot(client, cash='0')
    base = baseline(client)
    result = data(preview(client, base, [{'code': '600000', 'qty': 0, 'price': '11'},
                                       {'code': '000001', 'qty': 100, 'price': '10'}]))
    assert result['requires_sell_proceeds'] is True and result['cash_status'] == 'sufficient'
    assert result['remaining_cash'] == '88.88'  # sell fee 6.11 + buy fee 5.01
    hold = data(preview(client, base, []))
    assert hold['rows'][0]['target_qty'] == 100 and hold['rows'][0]['quantity_delta'] == 0
    assert hold['remaining_cash'] == '0.00' and hold['position_value'] is None
    assert hold['estimated_fees'] == '0.00'


def test_missing_price_never_returns_zero_cash_or_fees(client):
    snapshot(client)
    result = data(preview(client, baseline(client), [{'code': '600000', 'qty': 200}]))
    assert result['cash_status'] == 'unknown'
    assert result['remaining_cash'] is None and result['estimated_fees'] is None
    assert 'execution_price_missing' in result['quality_flags']


def test_quote_snapshot_ignores_late_and_future_bars_and_keeps_exchange_identity(client):
    snapshot(client, positions=[{'symbol': '000001', 'quantity': 100, 'market_value': '1000'}])
    def dataset(symbol):
        bars = [{'event_date': day, 'open': price, 'high': price, 'low': price, 'close': price,
                 'volume': 1000, 'available_at': available} for day, price, available in [
            ('2025-01-02', '9', '2025-01-02T08:00:00Z'),
            ('2025-01-03', '5000', '2025-01-04T08:00:00Z'),
            ('2025-01-04', '9000', '2025-01-04T08:00:00Z')]]
        return data(client.write('/api/v1/market/datasets', {'symbol': symbol, 'adjustment': 'none', 'bars': bars}))['id']
    stock, index = dataset('000001.SZ'), dataset('sh000001')
    base = baseline(client)
    result = data(preview(client, base, [{'code': 'sz000001', 'qty': 200}], [stock, index]))
    assert result['rows'][0]['price'] == '9.0000'
    assert result['rows'][0]['price_evidence']['quote_date'] == '2025-01-02'
    assert result['remaining_cash'] == '94.99'
    missing = data(preview(client, base, [{'code': 'sz000001', 'qty': 200}], [index]))
    assert missing['remaining_cash'] is None


def test_rehearsal_rejects_alias_duplicates_boolean_quantities_and_stale_baseline(client):
    snapshot(client)
    base = baseline(client)
    assert preview(client, base, [{'code': '600000', 'qty': 100}, {'code': '600000.SH', 'qty': 0}]).status_code == 400
    assert preview(client, base, [{'code': '600000', 'qty': True}]).status_code == 422
    assert preview(client, base, [{'code': '600000', 'qty': 200, 'price': 'NaN'}]).status_code == 400
    data(client.write(client.root + '/cash-flows', {'flow_date': '2025-01-01', 'kind': 'deposit', 'amount': '10000'}))
    assert preview(client, base, []).status_code == 409
    other = data(client.write('/api/v1/accounts', {'name': 'isolated'}))['id']
    assert data(client.get(f'/api/v1/accounts/{other}/planning/2025-01-03/baseline'))['positions'] == []
    assert client.get('/api/v1/accounts/absent/planning/2025-01-03/baseline').status_code == 404


def test_comparison_aliases_and_unlisted_actual_positions_and_manual_price_roundtrip(client):
    data(client.write(client.root + '/daily-reviews/2025-01-02', {'expected_revision': 0,
        'next_target_date': '2025-01-03', 'next_position_rehearsal': [
            {'code': '600000.SH', 'name': '原计划', 'qty': 80, 'note': '', 'price': '9.5'}]}, 'PUT'))
    snapshot(client, positions=[{'symbol': 'sh600000', 'quantity': 100, 'market_value': '1000'},
                               {'symbol': '000001', 'quantity': 50, 'market_value': '500'}])
    saved = data(client.get(client.root + '/daily-reviews/2025-01-02'))
    assert saved['next_position_rehearsal'][0]['price'] == '9.5000'
    plan = data(client.get(client.root + '/plan-comparison/2025-01-03'))['plans'][0]
    assert plan['rehearsal'][0]['quantity_delta'] == 20
    assert plan['rehearsal'][1]['qty'] is None and plan['rehearsal'][1]['quality'] == 'not_in_plan'
    assert plan['calendar']['target_status'] == 'unknown'


def test_local_calendar_survives_backup_restore_and_restart(tmp_path):
    source, restored = tmp_path / 'source', tmp_path / 'restored'
    with started_client(create_app(source, auto_rebuild=False)) as client:
        token = data(client.get('/api/v1/session'))['csrf_token']
        saved = data(client.put('/api/v1/market/trading-calendar', json=calendar_body(), headers={
            'X-CSRF-Token': token, 'Idempotency-Key': 'calendar-import'}))
    restore_to_new_directory(create_backup(source), restored)
    with started_client(create_app(restored, auto_rebuild=False)) as client:
        data(client.get('/api/v1/session'))
        assert data(client.get('/api/v1/market/trading-calendar')) == saved
        assert len(data(client.get('/api/v1/market/trading-calendar/audit'))) == 1
