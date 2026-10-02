"""Shared strategy execution, point-in-time fills, and frozen backtest profiles."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from trade_app.research import backtest_domain
from trade_app.research.backtest_domain import run_single_symbol_backtest
from trade_app.research.backtest_service import process_one_backtest
from trade_app.research.domain import DEFAULT_PARAMS
from trade_app.research.event_profiles import get_system_profile
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES
from trade_app.research.classic_domain import CLASSIC_STRATEGY_IDS
from trade_app.research.service import strategy_catalog, normalize_strategy_params
from trade_app.trading.domain import calculate_fees
from trade_app.trading.simulation import DEFAULT_CONFIG, fee_rule
from test_wyckoff_research_api import client_for, write, data, dataset, custom_payload
from test_wyckoff_domain import event_rich_bars


def flat_bars(count=40):
    return [{'event_date': (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
             'open': '10', 'high': '12', 'low': '8', 'close': '10', 'volume': 1000,
             'available_at': (date(2025, 1, 1) + timedelta(days=index)).isoformat() + 'T07:00:00+00:00'}
            for index in range(count)]


def execute(bars, **overrides):
    return run_single_symbol_backtest(
        symbol='600000', bars=bars, params=DEFAULT_PARAMS, initial_minor=1_000_000,
        holding_bars=overrides.pop('holding_bars', 60), max_position_pct=Decimal('0.95'),
        fees=fee_rule(DEFAULT_CONFIG), strict=overrides.pop('strict', True), **overrides)


def one_signal(_strategy_id, *, symbol, bars, **_):
    signal = len(bars) == 3
    return {'status': 'computed', 'signal': signal, 'draft_eligible': signal,
            'source_date': bars[-1]['event_date'], 'quality_flags': [],
            'candidate': {'symbol': symbol, 'date': bars[-1]['event_date']}}


def test_all_fourteen_single_stock_strategies_queue_execute_and_agree_with_research(tmp_path):
    legacy_single = [key for key in SINGLE_SYMBOL_STRATEGIES if key not in CLASSIC_STRATEGY_IDS]
    assert len(legacy_single) == 14
    assert len(SINGLE_SYMBOL_STRATEGIES) == 17
    assert set(SINGLE_SYMBOL_STRATEGIES) == {item['id'] for item in strategy_catalog() if item['signal_params'] is not None}
    with client_for(tmp_path) as client:
        points = event_rich_bars()
        day = date.fromisoformat(points[-1]['event_date'])
        while len(points) < 140:
            day += timedelta(days=1)
            if day.weekday() >= 5:
                continue
            close = round(float(points[-1]['close']) + 0.02, 4)
            points.append({'event_date': day.isoformat(), 'open': round(close - 0.01, 4),
                           'high': round(close + 0.15, 4), 'low': round(close - 0.15, 4), 'close': close,
                           'volume': 1000, 'available_at': day.isoformat() + 'T08:00:00+00:00'})
        ds = dataset(client, points)
        for strategy_id in legacy_single:
            body = {'dataset_id': ds['id'], 'strategy_id': strategy_id, 'holding_bars': 3,
                    'initial_capital': '10000', 'strict': True}
            queued = data(write(client, '/backtests', body))
            assert queued['state'] == 'queued'
            assert data(write(client, '/backtests', body))['id'] == queued['id']
            assert process_one_backtest(client.app.state.db_factory, tmp_path)
            complete = data(client.get('/api/v1/backtests/' + queued['id']))
            assert complete['state'] == 'succeeded', (strategy_id, complete.get('error'))
            result = complete['result']
            assert result['strategy_id'] == strategy_id
            assert len(result['equity']) == len(points)
            assert result['execution_profile_version'] == 'next-open-known-prefix-slippage-v3'
            calculated = next(item for item in result['decisions'] if item['status'] == 'computed')
            research = data(write(client, '/research/runs', {
                'dataset_id': ds['id'], 'strategy_id': strategy_id, 'strict': True,
                'decision_at': calculated['decision_at'],
            }))
            assert calculated['signal'] == research['result']['signal']
            assert calculated['draft_eligible'] == research['result']['draft_eligible']
            assert calculated['source_date'] == research['result']['source_date']
            for trade in result['trades']:
                assert datetime.fromisoformat(trade['known_at']) <= datetime.fromisoformat(trade['decision_at'])
                assert datetime.fromisoformat(trade['decision_at']) < datetime.fromisoformat(trade['execution_at'])
                assert trade['execution_at'].endswith('T01:30:00+00:00')
                if trade['side'] == 'buy':
                    assert trade['signal_date'] < trade['date']
        assert len(data(client.get('/api/v1/backtests'))) == 14
        unsupported = write(client, '/backtests', {'dataset_id': ds['id'], 'strategy_id': 'matrix_signal_v1'})
        assert unsupported.status_code == 409


@pytest.mark.parametrize('available_suffix', ['T01:30:00+00:00', 'T02:00:00+00:00'])
def test_prior_bar_known_at_or_after_open_cannot_be_used_at_that_open(monkeypatch, available_suffix):
    rows = flat_bars()
    rows[2]['available_at'] = rows[3]['event_date'] + available_suffix
    seen_prefixes = []

    def signal(_strategy, *, symbol, bars, **_):
        seen_prefixes.append(deepcopy(bars))
        triggered = len(bars) >= 3
        return {'status': 'computed', 'signal': triggered, 'draft_eligible': triggered,
                'source_date': bars[-1]['event_date'], 'quality_flags': [], 'candidate': {'symbol': symbol}}

    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', signal)
    result = execute(rows)
    first_buy = next(trade for trade in result['trades'] if trade['side'] == 'buy')
    assert first_buy['date'] == rows[4]['event_date']
    assert first_buy['signal_date'] == rows[3]['event_date']
    skipped = next(item for item in result['decisions'] if item['date'] == rows[3]['event_date'])
    assert skipped['status'] == 'skipped_unavailable'
    assert 'LATEST_PRIOR_BAR_UNAVAILABLE' in skipped['reasons']
    assert not any(len(prefix) == 3 for prefix in seen_prefixes)
    assert datetime.fromisoformat(first_buy['known_at']) < datetime.fromisoformat(first_buy['execution_at'])


def test_missing_older_history_blocks_strict_and_unknown_history_is_explicit(monkeypatch):
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', one_signal)
    rows = flat_bars()
    rows[0]['available_at'] = '2030-01-01T00:00:00+00:00'
    strict = execute(rows)
    assert strict['trades'] == []
    assert 'incomplete_strict_history' in strict['quality_flags']
    assert any('INCOMPLETE_STRICT_HISTORY' in item['reasons'] for item in strict['decisions'])
    unknown = [{**bar, 'available_at': None} for bar in flat_bars()]
    assert execute(unknown)['trades'] == []
    relaxed = execute(unknown, strict=False)
    assert relaxed['trade_count'] == 1
    assert 'historical_availability_unknown' in relaxed['quality_flags']


def test_executor_does_not_feed_current_or_future_bars_into_event_confirmation(monkeypatch):
    rows = flat_bars()
    observed = []

    def observe(_strategy, *, bars, **_):
        observed.append(deepcopy(bars))
        return {'status': 'computed', 'signal': False, 'draft_eligible': False,
                'source_date': bars[-1]['event_date'], 'quality_flags': []}

    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', observe)
    result = execute(rows)
    assert len(observed) == len(result['decisions'])
    for prefix, decision in zip(observed, result['decisions']):
        assert all(bar['event_date'] < decision['date'] for bar in prefix)
        assert all(datetime.fromisoformat(bar['available_at']) <= datetime.fromisoformat(decision['decision_at']) for bar in prefix)
        assert prefix == rows[:len(prefix)]


def test_real_wyckoff_confirmation_enters_after_it_is_known_not_on_historical_event_date():
    points = event_rich_bars()
    config = {'symbol': '600000', 'params': normalize_strategy_params('wyckoff_trend_v2', {}),
              'strategy_id': 'wyckoff_trend_v2', 'event_profile': {'snapshot': get_system_profile()},
              'initial_minor': 1_000_000, 'holding_bars': 3, 'max_position_pct': Decimal('.95'),
              'fees': fee_rule(DEFAULT_CONFIG), 'strict': True}
    result = run_single_symbol_backtest(bars=points, **config)
    first = next(trade for trade in result['trades'] if trade['side'] == 'buy')
    historical_event = first['reason_metrics']['evaluation']['trigger_date']
    assert historical_event < first['signal_date'] < first['date']
    pending = [item for item in result['decisions'] if item.get('primary_event') == 'LPS'
               and item['date'] < first['date']]
    assert pending and all(not item['signal'] for item in pending)
    assert 'PRIMARY_KEY_EVENT_UNCONFIRMED' in pending[-1]['reasons']
    # Even after the event exists, a late confirmation bar cannot support that open.
    delayed = deepcopy(points)
    index = next(index for index, bar in enumerate(delayed) if bar['event_date'] == first['date'])
    delayed[index - 1]['available_at'] = first['date'] + 'T02:00:00+00:00'
    result = run_single_symbol_backtest(bars=delayed, **config)
    assert not any(trade['side'] == 'buy' and trade['date'] <= first['date'] for trade in result['trades'])
    skipped = next(item for item in result['decisions'] if item['date'] == first['date'])
    assert skipped['status'] == 'skipped_unavailable'


def test_risk_only_observation_never_opens_a_long_position(monkeypatch):
    def risk_only(_strategy, *, bars, **_):
        return {'status': 'computed', 'signal': True, 'draft_eligible': False,
                'source_date': bars[-1]['event_date'], 'quality_flags': [],
                'evaluation': {'primary_event': 'SOW'}, 'draft_block_reason': '风险事件'}
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', risk_only)
    result = execute(flat_bars())
    assert result['trades'] == [] and result['signal_count'] == 0
    assert result['blocked_observations'] > 0
    assert result['decisions'][0]['primary_event'] == 'SOW'
    assert result['decisions'][0]['draft_block_reason'] == '风险事件'


@pytest.mark.parametrize('config,prior_close,next_open,reason', [
    ({'stop_loss_pct': Decimal('.1')}, '8.5', '7', 'CLOSE_STOP_LOSS'),
    ({'take_profit_pct': Decimal('.15')}, '12', '13', 'CLOSE_TAKE_PROFIT'),
    ({'trailing_stop_pct': Decimal('.1')}, '10.5', '10', 'CLOSE_TRAILING_STOP'),
])
def test_close_based_exits_fill_next_open_and_preserve_gap_and_shared_fees(monkeypatch, config, prior_close, next_open, reason):
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', one_signal)
    rows = flat_bars()
    if 'trailing_stop_pct' in config:
        rows[4]['close'] = '12'
    rows[5]['close'] = prior_close
    rows[6]['open'] = next_open
    result = execute(rows, **config)
    buy, sell = result['trades']
    assert buy['date'] == rows[3]['event_date']
    assert sell['date'] == rows[6]['event_date']
    assert sell['price'] == next_open and sell['reason'] == reason
    assert sell['reason_metrics']['observed_close'] == prior_close
    assert sell['reason_metrics']['observed_date'] == rows[5]['event_date']
    assert sell['holding_bars'] == 3
    fee = calculate_fees(Decimal(next_open), sell['quantity'], 'sell', fee_rule(DEFAULT_CONFIG))
    assert Decimal(sell['fees']) == fee.total
    expected = Decimal('10000') - Decimal(buy['price']) * buy['quantity'] - Decimal(buy['fees']) + Decimal(next_open) * sell['quantity'] - fee.total
    assert Decimal(result['ending_assets']) == expected
    assert result['trade_count'] == 1


def test_intraday_high_low_does_not_invent_exit_order_and_zero_disables_exits(monkeypatch):
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', one_signal)
    rows = flat_bars()
    rows[5].update(high='50', low='1', close='10')
    stopped = execute(rows, stop_loss_pct=Decimal('.1'), take_profit_pct=Decimal('.1'), trailing_stop_pct=Decimal('.1'))
    assert stopped['trades'][-1]['reason'] == 'SAMPLE_END'
    assert stopped['trades'][-1]['date'] == rows[-1]['event_date']
    rows[5]['close'] = '1'
    disabled = execute(rows)
    assert disabled['trades'][-1]['reason'] == 'SAMPLE_END'
    timed = execute(rows, holding_bars=2)
    assert timed['trades'][-1]['reason'] == 'MAX_HOLDING_BARS'
    assert timed['trades'][-1]['date'] == rows[5]['event_date']


def test_late_close_cannot_backdate_stop_execution(monkeypatch):
    monkeypatch.setattr(backtest_domain, 'evaluate_strategy', one_signal)
    rows = flat_bars()
    rows[5].update(close='8', available_at=rows[6]['event_date'] + 'T02:00:00+00:00')
    result = execute(rows, stop_loss_pct=Decimal('.1'))
    assert result['trades'][-1]['reason'] == 'SAMPLE_END'
    assert result['trades'][-1]['date'] == rows[-1]['event_date']
    assert 'latest_prior_bar_unavailable' in result['quality_flags']


def test_backtest_profile_is_frozen_before_worker_and_survives_profile_update_delete(tmp_path):
    with client_for(tmp_path) as client:
        ds = dataset(client)
        profile = data(write(client, '/research/event-profiles', {'profile': custom_payload()}))
        body = {'dataset_id': ds['id'], 'strategy_id': 'wyckoff_trend_v1',
                'event_profile_id': profile['profile_id'], 'event_profile_revision': 1,
                'stop_loss_pct': '0.1', 'take_profit_pct': '0.2', 'trailing_stop_pct': '0.05'}
        queued = data(write(client, '/backtests', body))
        assert queued['config']['event_profile']['revision'] == 1
        profile_path = '/research/event-profiles/' + profile['profile_id']
        changed = custom_payload('修改后模板')
        changed['rule_values'][0]['value'] = changed['rule_values'][0]['value'] * 0.9
        updated = data(write(client, profile_path, {'expected_revision': 1, 'profile': changed}, 'PUT'))
        stale = write(client, '/backtests', body)
        assert stale.status_code == 409
        assert stale.json()['error']['code'] == 'EVENT_PROFILE_VERSION_CONFLICT'
        second = data(write(client, '/backtests', {**body, 'event_profile_revision': 2}))
        assert second['id'] != queued['id']
        data(write(client, profile_path, {'expected_revision': 2, 'expected_active_revision': 0}, 'DELETE'))
        assert process_one_backtest(client.app.state.db_factory, tmp_path)
        complete = data(client.get('/api/v1/backtests/' + queued['id']))
        assert complete['state'] == 'succeeded', complete.get('error')
        assert complete['result']['event_profile'] == queued['config']['event_profile']
        assert complete['result']['event_profile']['sha256'] == profile['sha256']
        assert complete['result']['exit_rules']['stop_loss_pct'] == '0.1'
        assert process_one_backtest(client.app.state.db_factory, tmp_path)
        revised = data(client.get('/api/v1/backtests/' + second['id']))
        assert revised['state'] == 'succeeded'
        assert revised['result']['event_profile']['sha256'] == updated['sha256']
    with client_for(tmp_path) as client:
        assert data(client.get('/api/v1/backtests/' + queued['id'])) == complete


@pytest.mark.parametrize('fields,error', [
    ({'stop_loss_pct': '-0.01'}, 'INVALID_BACKTEST_CONFIG'),
    ({'take_profit_pct': '1.51'}, 'INVALID_BACKTEST_CONFIG'),
    ({'trailing_stop_pct': '0.51'}, 'INVALID_BACKTEST_CONFIG'),
    ({'params': {'unused': '1'}}, 'UNKNOWN_STRATEGY_PARAM'),
    ({'event_profile_id': 'system_legacy_formula_v1'}, 'EVENT_PROFILE_UNSUPPORTED'),
])
def test_backtest_rejects_unsupported_or_out_of_range_inputs(tmp_path, fields, error):
    with client_for(tmp_path) as client:
        ds = dataset(client)
        response = write(client, '/backtests', {'dataset_id': ds['id'], 'strategy_id': 'relative_strength_breakout_v1', **fields})
        assert response.status_code == 400, response.text
        assert response.json()['error']['code'] == error
        assert data(client.get('/api/v1/backtests')) == []
