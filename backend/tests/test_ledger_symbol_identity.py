"""Security aliases affect matching, never the original booked facts."""
import json

import pytest
from sqlalchemy import select

from trade_app.analytics.domain import project_account
from trade_app.analytics.models import ProjectionVersion
from trade_app.analytics.service import process_one, projection_status, recover_outdated_projections
from trade_app.platform.db import open_database
from trade_app.platform.symbols import market_symbol_aliases, market_symbol_key
from trade_app.platform.types import TradeError
from trade_app.research.real_valuation import estimate_real_assets
from trade_app.reviews.ai_scores import freeze_score_targets
from trade_app.reviews.plans import plan_comparison
from trade_app.reviews.scores import save_scores
from trade_app.reviews.service import save_review
from trade_app.reviews.sim_performance import sim_performance
from trade_app.trading.pending import create_pending_trade
from trade_app.trading.models import Trade
from trade_app.trading.service import create_account, create_flow, create_trade, list_trades, revise_trade, save_snapshot
from trade_app.trading.sim_models import SimLot, SimOrder
from trade_app.trading.simulation import create_order, create_sim_account, fill_order, portfolio, settle


@pytest.fixture
def database(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        yield factory
    finally:
        engine.dispose()


def trade_body(symbol, side, day='2025-01-01', price='10', quantity=100):
    return {'trade_date': day, 'symbol': symbol, 'side': side,
            'quantity': quantity, 'price': price, 'fee': '0', 'fee_mode': 'manual'}


def sim_order(session, account_id, symbol, side, day='2025-01-01', price='10', quantity=100):
    return create_order(session, account_id, {'symbol': symbol, 'side': side,
        'quantity': quantity, 'limit_price': price, 'signal_date': day, 'submit_date': day})


def fill(session, account_id, order, day='2025-01-01', price='10'):
    return fill_order(session, account_id, order['id'], {
        'expected_revision': order['revision'], 'fill_date': day, 'fill_price': price})


def test_historic_inconsistent_symbols_keep_raw_identity_and_upgrade(database, tmp_path):
    # Pre-v4 APIs required only a nonempty symbol. Model the facts those APIs
    # could persist; upgrading must neither discard nor silently correct them.
    with database.begin() as session:
        account = create_account(session, name='旧标记兼容')
        create_flow(session, account['id'], {
            'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'})
        old_buy = create_trade(session, account['id'], trade_body('600000', 'buy'))
        session.get(Trade, old_buy['id']).symbol = '600000.SZ'
        valid = create_trade(session, account['id'], trade_body('600000', 'buy', price='20'))
        old_sell = create_trade(session, account['id'], trade_body('600000', 'sell', '2025-01-02', '11'))
        session.get(Trade, old_sell['id']).symbol = '600000.SZ'
        session.flush()
        session.add(ProjectionVersion(id='old-inconsistent-symbols', account_id=account['id'],
            input_revision=4, calculation_version='nav-rounds-v3', payload_json='{}',
            state='current', created_at='2025-01-02T00:00:00Z'))
    recover_outdated_projections(database)
    assert process_one(database)
    with database() as session:
        status = projection_status(session, account['id'])
        assert status['status'] == 'fresh'
        assert status['result']['anomalies'] == []
        assert len(status['result']['positions']) == 1
        assert status['result']['positions'][0]['symbol'] == valid['symbol']
        closed = [row for row in status['result']['rounds'] if row['status'] == 'closed']
        assert len(closed) == 1
        assert closed[0]['id'] == 'round:' + old_buy['id']
        assert closed[0]['symbol'] == '600000.SZ' and closed[0]['pnl'] == '100.00'
        assert session.get(ProjectionVersion, 'old-inconsistent-symbols').state == 'historical'
        assert [row['symbol'] for row in list_trades(session, account['id'])] == [
            '600000.SZ', '600000', '600000.SZ']
        valuation = estimate_real_assets(session, tmp_path, account['id'], '2025-01-02',
                                        '2025-01-02T12:00:00Z', [], False)
        assert len(valuation['positions']) == 1
    assert market_symbol_key('600000.SZ') == 'raw:600000.SZ'
    assert market_symbol_aliases('600000.SZ') == ('600000.SZ',)
    assert market_symbol_key('600000.SZ') != market_symbol_key('600000')


def test_historic_simulation_lot_reads_without_merging_or_rewriting(database):
    with database.begin() as session:
        account = create_sim_account(session, {
            'name': '旧模拟标记', 'initial_capital': '10000', 'start_date': '2025-01-01'})
        old_order = sim_order(session, account['id'], '600000', 'buy')
        fill(session, account['id'], old_order)
        session.get(SimOrder, old_order['id']).symbol = '600000.SZ'
        old_lot = session.scalar(select(SimLot))
        old_lot.symbol = '600000.SZ'
        old_lot_id = old_lot.id
        fill(session, account['id'], sim_order(session, account['id'], '600000', 'buy', price='20'), price='20')
        session.flush()
        positions = portfolio(session, account['id'])['positions']
        assert {row['symbol'] for row in positions} == {'600000', '600000.SZ'}
        assert all(row['quantity'] == 100 for row in positions)
        assert len(sim_performance(session, account['id'])['closed_fills']) == 0
        assert session.get(SimLot, old_lot_id).symbol == '600000.SZ'


def test_new_writes_still_reject_inconsistent_exchange_marker(database):
    with database.begin() as session:
        account = create_account(session, name='新事实严格校验')
        valid = create_trade(session, account['id'], trade_body('600000', 'buy'))
        writes = (
            lambda: create_trade(session, account['id'], trade_body('600000.SZ', 'buy')),
            lambda: revise_trade(session, account['id'], valid['id'], {
                **trade_body('600000.SZ', 'buy'), 'expected_revision': 1}),
            lambda: create_pending_trade(session, account['id'], trade_body('600000.SZ', 'buy')),
            lambda: save_snapshot(session, account['id'], {
                'snap_date': '2025-01-01', 'total_assets': '1000', 'expected_revision': 0,
                'positions': [{'symbol': '600000.SZ', 'quantity': 100, 'market_value': '1000'}]}),
        )
        for write in writes:
            with pytest.raises(TradeError) as rejected:
                write()
            assert rejected.value.code == 'SYMBOL_EXCHANGE_MISMATCH'
        assert list_trades(session, account['id'])[0]['symbol'] == '600000'


@pytest.mark.parametrize('symbol,expected', [
    ('600000', {'600000', 'SH600000', '600000.SH', 'SH600000.SH'}),
    ('000001.SH', {'SH000001', '000001.SH', 'SH000001.SH'}),
    ('000001', {'000001', 'SZ000001', '000001.SZ', 'SZ000001.SZ'}),
    ('920001', {'920001', 'BJ920001', '920001.BJ', 'BJ920001.BJ'}),
    (' custom.ticker ', {'CUSTOM.TICKER'}),
])
def test_sql_aliases_preserve_instrument_exchange(symbol, expected):
    assert set(market_symbol_aliases(symbol)) == expected
    assert {market_symbol_key(item) for item in expected} == {market_symbol_key(symbol)}


def test_real_alias_rounds_valuation_and_old_projection_upgrade(database, tmp_path):
    with database.begin() as session:
        account = create_account(session, name='别名账本')
        create_flow(session, account['id'], {'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'})
        buy = create_trade(session, account['id'], trade_body('600000', 'buy'))
        sell = create_trade(session, account['id'], trade_body('600000.SH', 'sell', '2025-01-02', '11'))
        result = project_account([], [], list_trades(session, account['id']))
        assert result['positions'] == result['anomalies'] == []
        assert result['rounds'][0]['id'] == 'round:' + buy['id']
        assert result['rounds'][0]['trade_ids'] == [buy['id'], sell['id']]
        assert result['rounds'][0]['pnl'] == '100.00'
        estimate = estimate_real_assets(session, tmp_path, account['id'], '2025-01-02',
                                        '2025-01-02T12:00:00Z', [], False)
        assert estimate['total_assets'] == '10100.00'
        assert estimate['positions'] == [] and estimate['quality_flags'] == []
        session.add(ProjectionVersion(id='old-alias-projection', account_id=account['id'],
            input_revision=3, calculation_version='nav-rounds-v3', payload_json=json.dumps({'positions': []}),
            state='current', created_at='2025-01-02T12:00:00Z'))
    recover_outdated_projections(database)
    assert process_one(database)
    with database() as session:
        status = projection_status(session, account['id'])
        assert status['status'] == 'fresh' and status['calculation_version'] == 'nav-rounds-v4'
        assert status['result']['rounds'][0]['pnl'] == '100.00'
        assert session.get(ProjectionVersion, 'old-alias-projection').state == 'historical'
        assert [row['symbol'] for row in list_trades(session, account['id'])] == ['600000', '600000.SH']


def test_sim_alias_reservation_t_plus_one_fifo_and_fill_statistics(database):
    with database.begin() as session:
        account = create_sim_account(session, {'name': '别名模拟', 'initial_capital': '10000', 'start_date': '2025-01-01'})
        first = fill(session, account['id'], sim_order(session, account['id'], '600000', 'buy'))
        fill(session, account['id'], sim_order(session, account['id'], 'SH600000', 'buy', price='20'), price='20')
        state = portfolio(session, account['id'])
        assert len(state['positions']) == 1
        assert state['positions'][0]['quantity'] == 200
        assert state['positions'][0]['cost_basis'] == '3010.03'
        with pytest.raises(TradeError, match='可卖数量不足'):
            sim_order(session, account['id'], '600000.SH', 'sell')
        settle(session, account['id'], '2025-01-02')
        sell = sim_order(session, account['id'], '600000.SH', 'sell', '2025-01-02', '25')
        with pytest.raises(TradeError, match='可卖数量不足'):
            sim_order(session, account['id'], 'SH600000', 'sell', '2025-01-02', quantity=150)
        other = sim_order(session, account['id'], '600000', 'sell', '2025-01-02', '25')
        assert portfolio(session, account['id'])['positions'][0]['sellable_quantity'] == 0
        sold = fill(session, account['id'], sell, '2025-01-02', '25')
        assert sold['fill']['buy_allocations'][0]['buy_fill_id'] == first['fill']['id']
        assert sold['fill']['realized_pnl'] == '1487.46'
        assert sold['portfolio']['positions'][0]['cost_basis'] == '2005.02'
        assert sold['portfolio']['positions'][0]['sellable_quantity'] == 0  # Other alias reservation survives.
        statistics = sim_performance(session, account['id'])
        assert statistics['closed_fills'][0]['quality'] == 'complete'
        assert statistics['closed_fills'][0]['symbol'] == sell['symbol'] == '600000.SH'
        assert {lot.symbol for lot in session.scalars(select(SimLot))} == {'600000', 'SH600000'}
        assert other['status'] == 'pending'


@pytest.mark.parametrize('symbol', ['920001.BJ', 'CUSTOM.TICKER'])
def test_sim_beijing_and_custom_ticker_aliases_remain_sellable(database, symbol):
    with database.begin() as session:
        account = create_sim_account(session, {'name': '标识兼容', 'initial_capital': '10000', 'start_date': '2025-01-01'})
        bare = '920001' if symbol.endswith('.BJ') else 'custom.ticker'
        fill(session, account['id'], sim_order(session, account['id'], bare, 'buy'))
        settle(session, account['id'], '2025-01-02')
        sell = sim_order(session, account['id'], symbol, 'sell', '2025-01-02')
        assert fill(session, account['id'], sell, '2025-01-02')['portfolio']['positions'] == []


def test_identical_numeric_code_in_different_exchanges_has_separate_lots(database):
    with database.begin() as session:
        account = create_sim_account(session, {'name': '交易所隔离', 'initial_capital': '10000', 'start_date': '2025-01-01'})
        fill(session, account['id'], sim_order(session, account['id'], '000001', 'buy'))
        fill(session, account['id'], sim_order(session, account['id'], '000001.SH', 'buy', price='20'), price='20')
        assert len(portfolio(session, account['id'])['positions']) == 2
        settle(session, account['id'], '2025-01-02')
        sale = sim_order(session, account['id'], 'SH000001', 'sell', '2025-01-02', '25')
        assert fill(session, account['id'], sale, '2025-01-02', '25')['fill']['buy_allocations'][0]['cost_basis'] == '2005.02'
        position = portfolio(session, account['id'])['positions'][0]
        assert position['symbol'] == '000001' and position['quantity'] == 100
        with pytest.raises(TradeError) as rejected:
            sim_order(session, account['id'], '600000.SZ', 'buy', '2025-01-02')
        assert rejected.value.code == 'SYMBOL_EXCHANGE_MISMATCH'


def test_pending_duplicates_and_manual_ai_score_groups_use_same_identity(database):
    with database.begin() as session:
        account = create_account(session, name='重复与评分')
        buy = create_trade(session, account['id'], trade_body('600000', 'buy'))
        sell = create_trade(session, account['id'], trade_body('SH600000', 'sell'))
        pending = create_pending_trade(session, account['id'], trade_body('600000.SH', 'buy'))
        assert pending['duplicate_trade_ids'] == [buy['id']]
        assert pending['symbol'] == '600000.SH'
        target = {'scope': 't_group', 'key': '2025-01-01', 'trade_ids': [buy['id'], sell['id']]}
        assert freeze_score_targets(session, account['id'], target)['subjects'][0]['trade_ids'] == sorted(target['trade_ids'])
        scores = save_scores(session, account['id'], '2025-01-01', {
            'scope': 't_group', 'trade_ids': target['trade_ids'], 'scores': {}, 'comment': '', 'expected_revision': 0})
        assert scores['trade_ids'] == sorted(target['trade_ids'])
        wrong = create_trade(session, account['id'], trade_body('000001.SH', 'sell'))
        with pytest.raises(TradeError) as rejected:
            freeze_score_targets(session, account['id'], {**target, 'trade_ids': [buy['id'], wrong['id']]})
        assert rejected.value.code == 'INVALID_T_GROUP'


def test_snapshot_rejects_duplicate_aliases_and_plan_default_matches_alias(database):
    with database.begin() as session:
        account = create_account(session, name='计划标识')
        create_flow(session, account['id'], {'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'})
        body = {'snap_date': '2025-01-02', 'total_assets': '10000', 'available_cash': '9000',
                'expected_revision': 0, 'positions': [{'symbol': '600000.SH', 'quantity': 100, 'market_value': '1000'}]}
        with pytest.raises(TradeError) as rejected:
            save_snapshot(session, account['id'], {**body, 'positions': body['positions'] + [
                {'symbol': 'SH600000', 'quantity': 100, 'market_value': '1000'}]})
        assert rejected.value.code == 'DUPLICATE_SNAPSHOT_SYMBOL'
        save_snapshot(session, account['id'], body)
        save_review(session, account['id'], '2025-01-01', {'expected_revision': 0,
            'next_target_date': '2025-01-02', 'next_position_rehearsal': [{'code': '600000', 'qty': 100}]})
        result = plan_comparison(session, account['id'], '2025-01-02')
        assert len(result['plans'][0]['rehearsal']) == 1
        assert result['plans'][0]['rehearsal'][0]['quantity_delta'] == 0


def test_same_numeric_code_in_all_three_exchanges_never_merges(database):
    # SH/SZ 88xxxx sector indices and the BJ stock with the same digits are distinct.
    with database.begin() as session:
        real = create_account(session, name='three exchange real')
        sim = create_sim_account(session, {'name': 'three exchange sim', 'initial_capital': '10000',
                                          'start_date': '2025-01-01'})
        for symbol, price in [('SH881001', '10'), ('SZ881001', '20'), ('BJ881001', '30')]:
            create_trade(session, real['id'], trade_body(symbol, 'buy', price=price))
            fill(session, sim['id'], sim_order(session, sim['id'], symbol, 'buy', price=price), price=price)
        assert len(portfolio(session, sim['id'])['positions']) == 3
        settle(session, sim['id'], '2025-01-02')
        sale = sim_order(session, sim['id'], '881001.SH', 'sell', '2025-01-02', '15')
        sold = fill(session, sim['id'], sale, '2025-01-02', '15')
        assert sold['fill']['buy_allocations'][0]['cost_basis'] == '1005.01'
        assert {row['symbol'] for row in sold['portfolio']['positions']} == {'SZ881001', 'BJ881001'}
        create_trade(session, real['id'], trade_body('881001.SH', 'sell', '2025-01-02', '15'))
        result = project_account([], [], list_trades(session, real['id']))
        assert result['anomalies'] == []
        assert {row['symbol'] for row in result['positions']} == {'SZ881001', 'BJ881001'}
        closed = [row for row in result['rounds'] if row['status'] == 'closed']
        assert len(closed) == 1
        assert closed[0]['symbol'] == 'SH881001' and closed[0]['pnl'] == '500.00'
