"""Executable reference variants: exact rules, temporal isolation and real workers."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import pytest

from trade_app.platform.types import TradeError
from trade_app.research.classic_domain import (
    BOLLINGER, DONCHIAN, SMA, CLASSIC_STRATEGY_IDS, DEFAULTS, evaluate_classic,
    normalize_classic_params,
)
from trade_app.research.runtime import evaluate_strategy
from trade_app.research.backtest_domain import run_single_symbol_backtest
from trade_app.research.backtest_service import process_one_backtest
from trade_app.research.portfolio_domain import run_chunk
from trade_app.research.portfolio_service import process_one_portfolio
from trade_app.research.scan_job_service import process_one_scan_chunk
from trade_app.research.service import describe_strategy, normalize_strategy_params, strategy_catalog
from trade_app.trading.simulation import DEFAULT_CONFIG, fee_rule
from test_strategy_backtests import flat_bars
from test_portfolio import context
from test_wyckoff_research_api import client_for, write, data, dataset


PARAMS = {DONCHIAN: {'entry_period': '5', 'exit_period': '3'},
          SMA: {'period': '10', 'buffer_pct': '0'}, BOLLINGER: dict(DEFAULTS[BOLLINGER])}


def rows_for(strategy_id):
    rows = flat_bars(40)
    for row in rows:
        row.update(high='10.2', low='9.8')
    changes = {32: '12', 33: '12', 34: '8', 35: '8'}
    if strategy_id == BOLLINGER:
        changes = {31: '6', 32: '8', 33: '8', 34: '10', 35: '10'}
    for index, value in changes.items():
        price = Decimal(value)
        rows[index].update(open=value, close=value, high=str(price + Decimal('.2')),
                           low=str(price - Decimal('.2')))
    return rows


def single(strategy_id, rows):
    return run_single_symbol_backtest(symbol='sh600000', bars=rows, params=PARAMS[strategy_id],
        strategy_id=strategy_id, initial_minor=1_000_000, holding_bars=60,
        max_position_pct=Decimal('.95'), fees=fee_rule(DEFAULT_CONFIG), strict=True)


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_classic_catalog_and_normalization_are_one_contract(strategy_id):
    row = next(item for item in strategy_catalog() if item['id'] == strategy_id)
    assert row['enabled_by_default'] and not row['enabled_in_legacy']
    assert row['capabilities']['supports_exit_signal']
    assert not row['capabilities']['supports_full_signal_context']
    assert normalize_strategy_params(strategy_id, {}) == DEFAULTS[strategy_id]
    assert normalize_strategy_params(strategy_id, PARAMS[strategy_id]) == PARAMS[strategy_id]
    assert describe_strategy(strategy_id)['calculation_version'] == 'classic-confirmed-close-v1'


@pytest.mark.parametrize('strategy_id,params', [
    (DONCHIAN, {'entry_period': 5.5}), (DONCHIAN, {'entry_period': 5, 'exit_period': 5}),
    (DONCHIAN, {'exit_period': True}), (SMA, {'period': 501}), (SMA, {'buffer_pct': 'NaN'}),
    (SMA, {'buffer_pct': 'Infinity'}), (SMA, {'buffer_pct': -.01}),
    (BOLLINGER, {'stddev_multiplier': .49}), (BOLLINGER, {'period': 4}), (SMA, {'typo': 20}),
])
def test_invalid_parameters_are_not_silently_ignored(strategy_id, params):
    with pytest.raises(TradeError):
        normalize_classic_params(strategy_id, params)


def test_donchian_excludes_current_bar_and_threshold_equality_is_not_breakout():
    rows = rows_for(DONCHIAN)[:33]
    rows[-1]['high'] = '999'  # Must not raise today's entry threshold to 999.
    actual = evaluate_classic(DONCHIAN, rows, PARAMS[DONCHIAN])
    assert actual['signal'] and actual['metrics']['prior_entry_high'] == '10.2'
    assert actual['metrics']['channel_end'] == rows[-2]['event_date']
    rows[-1]['close'] = '10.2'
    assert not evaluate_classic(DONCHIAN, rows, PARAMS[DONCHIAN])['signal']
    rows[-1]['close'] = '9.8'
    assert not evaluate_classic(DONCHIAN, rows, PARAMS[DONCHIAN])['exit_signal']
    rows[-1]['close'] = '9.7999'
    assert evaluate_classic(DONCHIAN, rows, PARAMS[DONCHIAN])['exit_signal']


def test_sma_is_inclusive_regime_with_explicit_buffer_and_equality():
    rows = rows_for(SMA)[:33]
    actual = evaluate_classic(SMA, rows, PARAMS[SMA])
    assert actual['metrics']['sma'] == '10.2' and actual['signal']
    assert not evaluate_classic(SMA, rows, {'period': 10, 'buffer_pct': '.2'})['signal']
    flat = flat_bars(10)
    equal = evaluate_classic(SMA, flat, PARAMS[SMA])
    assert not equal['signal'] and not equal['exit_signal']


def test_bollinger_population_deviation_and_reentry_are_not_lower_band_touch():
    rows = rows_for(BOLLINGER)
    outside = evaluate_classic(BOLLINGER, rows[:32], PARAMS[BOLLINGER])
    assert not outside['signal']
    assert Decimal(outside['metrics']['middle']) == Decimal('9.8')
    assert float(outside['metrics']['population_stddev']) == pytest.approx(.76 ** .5)
    reentry = evaluate_classic(BOLLINGER, rows[:33], PARAMS[BOLLINGER])
    assert reentry['signal'] and not reentry['exit_signal']
    assert reentry['entry_reason'] == 'BOLLINGER_LOWER_REENTRY'
    exit_result = evaluate_classic(BOLLINGER, rows[:35], PARAMS[BOLLINGER])
    assert exit_result['exit_signal'] and not exit_result['signal']
    assert not evaluate_classic(BOLLINGER, flat_bars(21), PARAMS[BOLLINGER])['signal']


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_prefix_insufficient_and_no_volume_contract(strategy_id):
    rows = rows_for(strategy_id)
    assert evaluate_strategy(strategy_id, symbol='sh600000', bars=rows[:2],
        params=PARAMS[strategy_id])['signal'] is None
    baseline = evaluate_classic(strategy_id, rows[:33], PARAMS[strategy_id])
    assert baseline['signal']
    future = deepcopy(rows)
    for row in future[33:]:
        row.update(open='900', close='900', high='999', low='899')
    assert evaluate_classic(strategy_id, future[:33], PARAMS[strategy_id]) == baseline
    rows[32]['volume'] = 0
    illiquid = evaluate_classic(strategy_id, rows[:33], PARAMS[strategy_id])
    assert not illiquid['signal'] and not illiquid['exit_signal']
    assert illiquid['reasons'] == ['NO_CURRENT_VOLUME']


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_documented_default_periods_produce_entry_and_exit_without_shortening(strategy_id):
    rows = flat_bars(220)
    for row in rows:
        row.update(high='10.2', low='9.8')
    changes = {210: '12', 211: '12', 212: '8'}
    if strategy_id == BOLLINGER:
        changes = {209: '6', 210: '8', 211: '8', 212: '10'}
    for index, text in changes.items():
        price = Decimal(text)
        rows[index].update(open=text, close=text, high=str(price + Decimal('.2')), low=str(price - Decimal('.2')))
    buy = evaluate_classic(strategy_id, rows[:211], DEFAULTS[strategy_id])
    sell = evaluate_classic(strategy_id, rows[:213], DEFAULTS[strategy_id])
    assert buy['signal'] and not buy['exit_signal']
    assert sell['exit_signal'] and not sell['signal']
    if strategy_id == SMA:
        assert buy['required_bars'] == 200
        assert not evaluate_classic(strategy_id, rows[:199], DEFAULTS[strategy_id])['available']


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_real_entries_and_strategy_exits_execute_at_later_opens_with_fees(strategy_id):
    rows = rows_for(strategy_id)
    result = single(strategy_id, rows)
    buy, sell = result['trades'][:2]
    assert buy['side'] == 'buy' and buy['signal_date'] == rows[32]['event_date']
    assert buy['date'] == rows[33]['event_date'] and buy['price'] == rows[33]['open']
    assert sell['side'] == 'sell' and sell['date'] == rows[35]['event_date']
    assert sell['reason'] == 'CLASSIC_SIGNAL_NEXT_OPEN'
    assert sell['reason_metrics']['source_date'] == rows[34]['event_date']
    assert sell['reason_metrics']['evaluation']['exit_signal'] is True
    for trade in (buy, sell):
        assert Decimal(trade['fees']) > 0
        assert datetime.fromisoformat(trade['known_at']) <= datetime.fromisoformat(trade['decision_at'])
        assert datetime.fromisoformat(trade['decision_at']) < datetime.fromisoformat(trade['execution_at'])
    # The later sample shock cannot rewrite the already executed round trip.
    altered = deepcopy(rows)
    altered[37].update(open='500', high='501', low='499', close='500')
    assert single(strategy_id, altered)['trades'][:2] == [buy, sell]


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_late_availability_prevents_backdated_entry_and_exit(strategy_id):
    rows = rows_for(strategy_id)
    rows[32]['available_at'] = rows[33]['event_date'] + 'T02:00:00+00:00'
    assert not any(trade['side'] == 'buy' and trade['date'] <= rows[33]['event_date']
                   for trade in single(strategy_id, rows)['trades'])
    rows = rows_for(strategy_id)
    rows[34]['available_at'] = rows[35]['event_date'] + 'T02:00:00+00:00'
    assert not any(trade['side'] == 'sell' and trade['date'] <= rows[35]['event_date']
                   for trade in single(strategy_id, rows)['trades'])


def test_zero_volume_does_not_invent_execution_or_erase_unliquidated_position():
    rows = rows_for(SMA)
    rows[33]['volume'] = 0
    skipped = single(SMA, rows)
    assert not any(trade['date'] == rows[33]['event_date'] for trade in skipped['trades'])
    assert skipped['skipped_untradable'] >= 1
    rows = rows_for(SMA)
    for row in rows[35:]:
        row['volume'] = 0
    stuck = single(SMA, rows)
    assert [trade['side'] for trade in stuck['trades']] == ['buy']
    assert stuck['ending_quantity'] > 0 and 'open_position_at_sample_end' in stuck['quality_flags']
    expected = Decimal(stuck['ending_cash']) + Decimal(rows[-1]['close']) * stuck['ending_quantity']
    assert Decimal(stuck['ending_assets']) == expected
    assert Decimal(stuck['equity'][-1]['total_assets']) == expected


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_portfolio_uses_same_rules_and_chunk_boundaries_do_not_change_trades(strategy_id):
    rows = rows_for(strategy_id)
    ctx = context(count=40, start=31)
    ctx.update(strategy_id=strategy_id, params=PARAMS[strategy_id])
    ctx['datasets'][0]['bars'] = rows
    actual = run_chunk(ctx, days=40)
    buy, sell = actual['trades'][:2]
    assert buy['date'] == rows[33]['event_date'] and sell['date'] == rows[35]['event_date']
    assert sell['reason'] == 'CLASSIC_SIGNAL_NEXT_OPEN'
    first = run_chunk(ctx, days=4)
    rest = run_chunk(ctx, checkpoint=first['checkpoint'], days=40)
    assert first['trades'] + rest['trades'] == actual['trades']
    assert rest['checkpoint'] == actual['checkpoint']


@pytest.mark.parametrize('strategy_id', CLASSIC_STRATEGY_IDS)
def test_research_scan_single_and_portfolio_real_workers_share_classic_rules(tmp_path, strategy_id):
    with client_for(tmp_path) as client:
        rows = rows_for(strategy_id)
        ds = dataset(client, rows)
        request = {'dataset_id': ds['id'], 'strategy_id': strategy_id, 'params': PARAMS[strategy_id], 'strict': True}
        day = rows[32]['event_date']
        researched = data(write(client, '/research/runs', {**request, 'decision_at': day + 'T09:00:00+00:00'}))
        assert researched['result']['signal'] is True and researched['result']['exit_signal'] is False
        assert researched['result']['source_date'] == day
        # Importing a different file with arbitrary future prices cannot change
        # evidence before those bars have occurred/arrived.
        shocked = deepcopy(rows)
        for bar in shocked[33:]:
            bar.update(open='500', high='501', low='499', close='500')
        shock_ds = dataset(client, shocked)
        replay = data(write(client, '/research/runs', {**request, 'dataset_id': shock_ds['id'],
            'decision_at': day + 'T09:00:00+00:00'}))
        assert replay['result']['indicator'] == researched['result']['indicator']
        assert replay['result']['evaluation'] == researched['result']['evaluation']
        late = deepcopy(rows)
        late[32]['available_at'] = rows[33]['event_date'] + 'T08:00:00+00:00'
        late_ds = dataset(client, late)
        earlier = data(write(client, '/research/runs', {**request, 'dataset_id': late_ds['id'],
            'decision_at': day + 'T09:00:00+00:00'}))
        assert earlier['result']['signal'] is False
        assert earlier['result']['source_date'] == rows[31]['event_date']
        job = data(write(client, '/research/scan-jobs', {'dataset_ids': [ds['id']],
            'strategies': [{'strategy_id': strategy_id, 'params': PARAMS[strategy_id]}],
            'as_of_date': day, 'strict': True}))
        for _ in range(5):
            if not process_one_scan_chunk(client.app.state.db_factory, tmp_path):
                break
        job = data(client.get('/api/v1/research/scan-jobs/' + job['id']))
        assert job['state'] == 'succeeded', job
        scan = data(client.get('/api/v1/research/scans/' + job['scan_id']))
        assert scan['signal_count'] == 1
        scan_run = data(client.get('/api/v1/research/runs/' + scan['result']['run_ids'][0]))
        assert scan_run['result']['indicator'] == researched['result']['indicator']
        queued = data(write(client, '/backtests', {**request, 'holding_bars': 60, 'initial_capital': '10000'}))
        assert process_one_backtest(client.app.state.db_factory, tmp_path)
        done = data(client.get('/api/v1/backtests/' + queued['id']))
        assert done['state'] == 'succeeded', done.get('error')
        assert done['result']['trades'][1]['reason'] == 'CLASSIC_SIGNAL_NEXT_OPEN'
        body = {'dataset_ids': [ds['id']], 'mode': 'traditional_runtime14', 'strategy_id': strategy_id,
                'params': PARAMS[strategy_id], 'start_date': rows[31]['event_date'], 'end_date': rows[-1]['event_date'],
                'config': {'max_holding_bars': 60, 'daily_weak_clear': False, 'stop_loss_pct': '0', 'take_profit_pct': '0'}}
        preview = data(write(client, '/research/portfolios/preview', body))
        run = data(write(client, '/research/portfolios', {**body, 'name': '经典实际执行',
            'expected_preview_sha256': preview['preview_sha256']}))
        for _ in range(5):
            if not process_one_portfolio(client.app.state.db_factory, tmp_path):
                break
        complete = data(client.get('/api/v1/research/portfolios/' + run['id'] + '/result'))
        assert complete['state'] == 'succeeded', complete.get('error')
        assert complete['result']['trades'][1]['reason'] == 'CLASSIC_SIGNAL_NEXT_OPEN'
