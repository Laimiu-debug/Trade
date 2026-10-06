from decimal import Decimal

import pytest

from trade_app.platform.types import TradeError
from trade_app.research import backtest_domain
from trade_app.trading.domain import adverse_execution_price
from trade_app.trading.simulation import DEFAULT_CONFIG, normalized_config
from test_strategy_backtests import execute, flat_bars, one_signal
from test_wyckoff_research_api import client_for, data, dataset, write


@pytest.mark.parametrize('rate', ['-0.0001', '0.05001', 'NaN', 'Infinity'])
def test_invalid_slippage_rejected(rate):
    with pytest.raises(TradeError):
        adverse_execution_price(Decimal(10), 'buy', Decimal(rate))


def test_slippage_changes_execution_fees_and_equity_without_changing_raw_bars(monkeypatch):
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', one_signal)
    bars = flat_bars()
    normal = execute(bars)
    slipped = execute(bars, slippage_rate=Decimal('0.01'))
    buy, sale = slipped['trades']
    assert Decimal(buy['price']) == Decimal('10.1')
    assert Decimal(sale['price']) == Decimal('9.9')
    assert buy['reference_price'] == sale['reference_price'] == '10'
    assert buy['slippage_rate'] == sale['slippage_rate'] == '0.01'
    assert Decimal(slipped['ending_assets']) < Decimal(normal['ending_assets'])
    assert all(row['open'] == '10' for row in bars)
    assert normalized_config({key: value for key, value in DEFAULT_CONFIG.items() if key != 'slippage_rate'})['slippage_rate'] == '0'


def test_simulation_freezes_order_slippage_obeys_limit_and_manual_price(tmp_path):
    with client_for(tmp_path) as client:
        sample = dataset(client, flat_bars())
        sim = data(write(client, '/sim-accounts', {'name': '滑点对照', 'initial_capital': '10000',
                         'start_date': '2025-01-01', 'config': {**DEFAULT_CONFIG, 'slippage_rate': '0.01'}}))
        root = '/sim-accounts/' + sim['id']
        order = data(write(client, root + '/orders', {'symbol': sample['symbol'], 'side': 'buy', 'quantity': 100,
                          'limit_price': '11', 'signal_date': '2025-01-01', 'submit_date': '2025-01-01'}))
        data(write(client, root + '/config', {'expected_version': 1,
                         'config': {**DEFAULT_CONFIG, 'slippage_rate': '0.03'}}, 'PUT'))
        data(write(client, root + '/settle', {'to_date': '2025-01-02'}))
        outcome = data(write(client, root + '/orders/' + order['id'] + '/match-open',
                             {'expected_revision': order['revision'], 'dataset_id': sample['id']}))
        assert Decimal(outcome['fill']['fill_price']) == Decimal('10.1')
        assert outcome['slippage_rate'] == '0.01'
        assert outcome['fill']['price_source'].endswith(':slippage=0.01')
        limited = data(write(client, root + '/orders', {'symbol': sample['symbol'], 'side': 'buy', 'quantity': 100,
                            'limit_price': '10.05', 'signal_date': '2025-01-02', 'submit_date': '2025-01-02'}))
        data(write(client, root + '/settle', {'to_date': '2025-01-03'}))
        blocked = data(write(client, root + '/orders/' + limited['id'] + '/match-open',
                             {'expected_revision': limited['revision'], 'dataset_id': sample['id']}))
        assert blocked['status'] == 'limit_not_met' and blocked['fill'] is None
        manual = data(write(client, root + '/orders/' + limited['id'] + '/fill',
                            {'expected_revision': limited['revision'], 'fill_date': '2025-01-03', 'fill_price': '10.02'}))
        assert Decimal(manual['fill']['fill_price']) == Decimal('10.02')


@pytest.mark.parametrize('side', ['buy', 'sell'])
@pytest.mark.parametrize('batch', [False, True])
def test_zero_volume_open_preserves_pending_order_and_reservations_until_trading_resumes(tmp_path, side, batch):
    with client_for(tmp_path) as client:
        sample = dataset(client, [
            {'event_date': day, 'open': '10', 'high': '10', 'low': '10', 'close': '10', 'volume': volume}
            for day, volume in [('2025-01-01', 1000), ('2025-01-02', 1000),
                                ('2025-01-03', 0), ('2025-01-06', 1000)]])
        sim = data(write(client, '/sim-accounts', {'name': '零成交量回归', 'initial_capital': '10000',
                                                'start_date': '2025-01-01'}))
        root = '/sim-accounts/' + sim['id']
        if side == 'sell':
            purchase = data(write(client, root + '/orders', {'symbol': sample['symbol'], 'side': 'buy',
                'quantity': 100, 'limit_price': '10', 'signal_date': '2025-01-01', 'submit_date': '2025-01-01'}))
            data(write(client, root + '/orders/' + purchase['id'] + '/fill', {
                'expected_revision': 1, 'fill_date': '2025-01-01', 'fill_price': '10'}))
        data(write(client, root + '/settle', {'to_date': '2025-01-02'}))
        order = data(write(client, root + '/orders', {'symbol': sample['symbol'], 'side': side, 'quantity': 100,
            'limit_price': '11' if side == 'buy' else '9', 'signal_date': '2025-01-02', 'submit_date': '2025-01-02'}))
        before = data(client.get('/api/v1' + root + '/portfolio'))

        def match(day):
            if batch:
                wallet = data(client.get('/api/v1' + root + '/portfolio'))
                result = data(write(client, root + '/advance-market-day', {
                    'expected_wallet_revision': wallet['wallet_revision'], 'to_date': day,
                    'datasets': {sample['symbol']: sample['id']}}))
                return result['outcomes'][0]
            data(write(client, root + '/settle', {'to_date': day}))
            return data(write(client, root + '/orders/' + order['id'] + '/match-open', {
                'expected_revision': order['revision'], 'dataset_id': sample['id']}))

        blocked = match('2025-01-03')
        assert blocked['status'] == 'no_volume' and blocked['fill'] is None
        assert blocked['order'] == order  # No revision/status/reservation change.
        after = data(client.get('/api/v1' + root + '/portfolio'))
        assert {key: after[key] for key in ('cash', 'reserved_cash', 'positions')} == {
            key: before[key] for key in ('cash', 'reserved_cash', 'positions')}
        assert len(data(client.get('/api/v1' + root + '/fills'))) == (0 if side == 'buy' else 1)
        filled = match('2025-01-06')
        assert filled['status'] == 'filled' and filled['order']['status'] == 'filled'
        final = data(client.get('/api/v1' + root + '/portfolio'))
        assert final['reserved_cash'] == '0.00'
        if side == 'buy':
            assert final['positions'][0]['quantity'] == 100
        else:
            assert final['positions'] == []
