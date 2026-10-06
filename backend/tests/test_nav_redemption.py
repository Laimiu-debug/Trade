"""Redemption uses monetary precision and closes the unit-NAV segment exactly."""
from decimal import Decimal

import pytest

from trade_app.analytics.service import process_one
from trade_app.platform.types import TradeError
from trade_app.trading.nav import FlowFact, SnapshotFact, calculate_nav
from test_wyckoff_research_api import client_for, data, write


@pytest.mark.parametrize('initial,first_close,deposit,last_close', [
    (100000, 110000, 50000, 150000),
    (10000, 8000, 50000, 110000),
])
def test_full_redemption_clears_repeating_shares_and_allows_new_segment(
        initial, first_close, deposit, last_close):
    flows = [FlowFact('initial', '2025-01-01', 'initial', initial, '1'),
             FlowFact('deposit', '2025-01-02', 'deposit', deposit, '2'),
             FlowFact('withdraw', '2025-01-03', 'withdraw', last_close, '3')]
    snapshots = [SnapshotFact('2025-01-01', first_close),
                 SnapshotFact('2025-01-02', last_close)]
    result = calculate_nav(flows, snapshots)
    current = result['current']
    assert Decimal(current['shares']) == 0
    assert Decimal(current['assets']) == 0
    assert current['nav'] is None
    assert current['next_level'] is None and current['next_target_assets'] is None
    snapshots.append(SnapshotFact('2025-01-03', 0))
    flows.append(FlowFact('restart', '2025-01-04', 'initial', 50000, '4'))
    restarted = calculate_nav(flows, snapshots)
    assert restarted['points'][-2]['nav'] is None
    assert restarted['points'][-1]['segment'] == 2
    assert restarted['current']['nav'] == '1.00000000'
    assert Decimal(restarted['current']['shares']) == 500


def test_partial_redemption_preserves_nav_and_one_cent_overdraw_is_rejected():
    flows = [FlowFact('initial', '2025-01-01', 'initial', 100000, '1'),
             FlowFact('deposit', '2025-01-02', 'deposit', 50000, '2')]
    snapshots = [SnapshotFact('2025-01-01', 110000), SnapshotFact('2025-01-02', 150000)]
    flows.append(FlowFact('partial', '2025-01-03', 'withdraw', 50000, '3'))
    partial = calculate_nav(flows, snapshots)
    assert Decimal(partial['current']['assets']) == 1000
    assert Decimal(partial['current']['shares']) > 0
    assert partial['current']['nav'] == calculate_nav(flows[:2], snapshots)['current']['nav']
    with pytest.raises(TradeError) as error:
        calculate_nav(flows + [FlowFact('over', '2025-01-04', 'withdraw', 100001, '4')], snapshots)
    assert error.value.code == 'OVER_WITHDRAWAL'
    closed = calculate_nav(flows + [FlowFact('rest', '2025-01-04', 'withdraw', 100000, '4')], snapshots)
    assert closed['current']['nav'] is None


def test_full_redemption_can_be_saved_and_projected_through_api(tmp_path):
    with client_for(tmp_path) as client:
        account = data(write(client, '/accounts', {'name': '全额赎回'}))
        root = '/accounts/' + account['id']
        data(write(client, root + '/cash-flows', {
            'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '1000'}))
        data(write(client, root + '/snapshots/2025-01-01', {
            'snap_date': '2025-01-01', 'total_assets': '1100', 'expected_revision': 0}, 'PUT'))
        data(write(client, root + '/cash-flows', {
            'flow_date': '2025-01-02', 'kind': 'deposit', 'amount': '500'}))
        data(write(client, root + '/snapshots/2025-01-02', {
            'snap_date': '2025-01-02', 'total_assets': '1500', 'expected_revision': 0}, 'PUT'))
        data(write(client, root + '/cash-flows', {
            'flow_date': '2025-01-03', 'kind': 'withdraw', 'amount': '1500'}))
        data(write(client, root + '/snapshots/2025-01-03', {
            'snap_date': '2025-01-03', 'total_assets': '0', 'expected_revision': 0}, 'PUT'))
        while process_one(client.app.state.db_factory):
            pass
        current = data(client.get('/api/v1' + root + '/analytics'))['result']['nav']['current']
        assert current['nav'] is None
        assert Decimal(current['shares']) == 0
