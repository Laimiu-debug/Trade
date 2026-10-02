from decimal import Decimal

from sqlalchemy import select

from trade_app.trading.domain import LotBalance, consume_fifo
from trade_app.trading.sim_models import SimLot
from test_wyckoff_research_api import client_for, data, write


def test_fifo_never_uses_random_id_to_reorder_same_day_acquisitions():
    changes, cost = consume_fifo([
        LotBalance('z-first', '2025-01-01', 100, 100_001),
        LotBalance('a-second', '2025-01-01', 100, 200_002),
    ], 100, '2025-01-02')
    assert changes == {'z-first': (0, 0)} and cost == 100_001


def test_persisted_allocations_match_cash_fees_fifo_and_partial_lots(tmp_path):
    with client_for(tmp_path) as client:
        account = data(write(client, '/sim-accounts', {'name': 'FIFO精确归属',
            'initial_capital': '100000', 'start_date': '2025-01-30'}))
        base = '/sim-accounts/' + account['id']
        def fill(day, side, qty, price):
            order = data(write(client, base + '/orders', {'symbol': '600000', 'side': side,
                'quantity': qty, 'limit_price': price, 'signal_date': day, 'submit_date': day}))
            return data(write(client, base + '/orders/' + order['id'] + '/fill', {
                'expected_revision': 1, 'fill_date': day, 'fill_price': price}))['fill']
        first, second = fill('2025-01-30', 'buy', 100, '10'), fill('2025-01-30', 'buy', 300, '20')
        with client.app.state.db_factory.begin() as session:
            lots = list(session.scalars(select(SimLot).where(SimLot.account_id == account['id'])
                         .order_by(SimLot.created_at, SimLot.id)))
            # IDs deliberately oppose execution time, the prior implementation failed this.
            lots[0].id, lots[1].id = 'z-first', 'a-second'
        data(write(client, base + '/settle', {'to_date': '2025-02-03'}))
        sale = fill('2025-02-03', 'sell', 200, '30')
        allocations = sale['buy_allocations']
        assert [row['buy_fill_id'] for row in allocations] == [first['id'], second['id']]
        assert [row['quantity'] for row in allocations] == [100, 100]
        assert sum(Decimal(row['realized_pnl']) for row in allocations) == Decimal(sale['realized_pnl'])
        assert sum(Decimal(row['sell_gross']) for row in allocations) == Decimal(sale['gross'])
        assert sum(Decimal(row['sell_fees']) for row in allocations) == sum(Decimal(sale[key]) for key in ('commission', 'stamp', 'transfer'))
        assert allocations[0]['cost_basis'] == '1005.01'
        with client.app.state.db_factory() as session:
            remaining = session.get(SimLot, 'a-second')
            assert remaining.remaining_qty == 200
            assert remaining.cost_minor + int(Decimal(allocations[1]['cost_basis']) * 100) == 600506
        data(write(client, base + '/settle', {'to_date': '2025-02-04'}))
        final = fill('2025-02-04', 'sell', 200, '15')
        assert len(final['buy_allocations']) == 1
        assert final['buy_allocations'][0]['buy_fill_id'] == second['id']
        assert data(client.get('/api/v1' + base + '/portfolio'))['positions'] == []
        buy_view = data(client.get('/api/v1' + base + '/performance?date_basis=buy'))
        sell_view = data(client.get('/api/v1' + base + '/performance'))
        assert buy_view['monthly'][0]['month'] == '2025-01'
        assert sell_view['monthly'][0]['month'] == '2025-02'
        assert buy_view['monthly'][0]['realized_pnl'] == sell_view['realized_pnl']
        assert buy_view['realized_curve'] == sell_view['realized_curve']
        assert buy_view['buy_attribution_unavailable_fill_ids'] == []
        assert client.get('/api/v1' + base + '/performance?date_basis=unknown').status_code == 400


def test_legacy_sales_across_purchase_dates_are_not_proportionally_guessed(tmp_path):
    from trade_app.trading.sim_models import SimFill
    with client_for(tmp_path) as client:
        account = data(write(client, '/sim-accounts', {'name': '旧成交来源',
            'initial_capital': '100000', 'start_date': '2025-01-31'}))
        base = '/sim-accounts/' + account['id']
        def fill(day, side, qty, price):
            order = data(write(client, base + '/orders', {'symbol': '600000', 'side': side,
                'quantity': qty, 'limit_price': price, 'signal_date': day, 'submit_date': day}))
            return data(write(client, base + '/orders/' + order['id'] + '/fill', {
                'expected_revision': 1, 'fill_date': day, 'fill_price': price}))['fill']
        fill('2025-01-31', 'buy', 100, '10')
        data(write(client, base + '/settle', {'to_date': '2025-02-03'}))
        fill('2025-02-03', 'buy', 100, '20')
        data(write(client, base + '/settle', {'to_date': '2025-02-04'}))
        sale = fill('2025-02-04', 'sell', 200, '15')
        before = data(client.get('/api/v1' + base + '/performance?date_basis=buy'))
        assert len(before['monthly']) == 2
        assert sum(Decimal(row['realized_pnl']) for row in before['monthly']) == Decimal(sale['realized_pnl'])
        with client.app.state.db_factory.begin() as session:
            session.get(SimFill, sale['id']).allocations_json = None
        legacy = data(client.get('/api/v1' + base + '/performance?date_basis=buy'))
        assert legacy['monthly'] == [] and legacy['buy_attribution_unavailable_fill_ids'] == [sale['id']]
        assert legacy['realized_pnl'] == sale['realized_pnl']
