from test_wyckoff_research_api import client_for, data, write


def test_review_and_snapshot_reminders_are_independent_bounded_and_account_scoped(tmp_path):
    with client_for(tmp_path) as client:
        account = data(write(client, '/accounts', {'name': '需补记录'}))
        other = data(write(client, '/accounts', {'name': '独立账户'}))
        base = '/accounts/' + account['id']
        data(write(client, base + '/trades', {'trade_date': '2025-01-02', 'symbol': '600000',
                                            'side': 'buy', 'quantity': 100, 'price': '10'}))
        data(write(client, base + '/daily-reviews/2025-01-02', {'expected_revision': 0, 'title': '已复盘'}, 'PUT'))
        data(write(client, base + '/daily-reviews/2025-01-04', {'expected_revision': 0, 'title': '观察'}, 'PUT'))
        data(write(client, base + '/snapshots/2025-01-03', {'snap_date': '2025-01-03',
                                  'total_assets': '0', 'expected_revision': 0}, 'PUT'))
        path = '/api/v1' + base + '/review-reminders'
        result = data(client.get(path))
        assert result['total_count'] == {'missing_reviews': 1, 'missing_snapshots': 2}
        assert [row['date'] for row in result['missing_snapshots']] == ['2025-01-04', '2025-01-02']
        assert [row['date'] for row in result['missing_reviews']] == ['2025-01-03']
        assert result['missing_snapshots'][1]['has_review']
        limited = data(client.get(path + '?limit=1'))
        assert len(limited['missing_snapshots']) == 1 and limited['total_count']['missing_snapshots'] == 2
        assert client.get(path + '?limit=101').status_code == 422
        assert data(client.get('/api/v1/accounts/' + other['id'] + '/review-reminders'))['missing_snapshots'] == []
        data(write(client, base + '/snapshots/2025-01-02', {'snap_date': '2025-01-02',
                                  'total_assets': '0', 'expected_revision': 0}, 'PUT'))
        assert [row['date'] for row in data(client.get(path))['missing_snapshots']] == ['2025-01-04']
        # Reads neither backfill blank snapshots nor fabricate absent trading dates.
        assert len(data(client.get('/api/v1' + base + '/snapshots'))) == 2
        assert client.get('/api/v1/accounts/missing/review-reminders').status_code == 404


def test_voided_transactions_do_not_leave_phantom_missing_snapshots(tmp_path):
    with client_for(tmp_path) as client:
        account = data(write(client, '/accounts', {'name': '更正记录'}))
        base = '/accounts/' + account['id']
        trade = data(write(client, base + '/trades', {'trade_date': '2025-01-02', 'symbol': '600000',
                                            'side': 'buy', 'quantity': 100, 'price': '10'}))
        flow = data(write(client, base + '/cash-flows', {'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'}))
        path = '/api/v1' + base + '/review-reminders'
        assert data(client.get(path))['total_count']['missing_snapshots'] == 2
        data(write(client, base + '/trades/' + trade['id'] + '?expected_revision=' + str(trade['revision']), {}, 'DELETE'))
        data(write(client, base + '/cash-flows/' + flow['id'] + '?expected_revision=' + str(flow['revision']), {}, 'DELETE'))
        assert data(client.get(path))['total_count'] == {'missing_reviews': 0, 'missing_snapshots': 0}
