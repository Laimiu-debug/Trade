"""FIFO projections retain exact costs through partial exits and anomalies."""
from trade_app.analytics.domain import project_account


def _trade(identifier, sequence, side, quantity, price, fee='0'):
    return {'id': identifier, 'trade_date': '2025-01-01', 'sequence': sequence,
            'symbol': '600000', 'name': '样本', 'side': side, 'quantity': quantity,
            'price': price, 'fee': fee}


def test_partial_fifo_sale_then_oversale_keeps_remaining_cost_and_round():
    trades = [
        _trade('buy-first', 1, 'buy', 100, '10', '5'),
        _trade('buy-second', 2, 'buy', 200, '20', '5'),
        _trade('partial', 3, 'sell', 150, '25', '5'),
        _trade('invalid-oversale', 4, 'sell', 200, '25', '5'),
    ]
    result = project_account([], [], trades)
    assert result['positions'] == [{
        'symbol': '600000', 'name': '样本', 'quantity': 150, 'cost_basis': '3003.75'}]
    assert result['anomalies'][0]['available_quantity'] == 150
    assert next(row for row in result['rounds'] if row['status'] == 'open')['trade_ids'] == [
        'buy-first', 'buy-second', 'partial']
    trades.append(_trade('close-first-round', 5, 'sell', 150, '25', '5'))
    trades.extend([_trade('new-buy', 6, 'buy', 100, '10'), _trade('new-sell', 7, 'sell', 100, '11')])
    result = project_account([], [], trades)
    assert result['positions'] == []
    closed = [row for row in result['rounds'] if row['status'] == 'closed']
    assert [(row['id'], row['pnl']) for row in closed] == [
        ('round:buy-first', '2480.00'), ('round:new-buy', '100.00')]
    assert result['trade_stats']['closed_rounds'] == 2
    assert result['trade_stats']['winning_rounds'] == 2
