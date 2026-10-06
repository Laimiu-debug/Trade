from copy import deepcopy
from decimal import Decimal
import json
import math

import pytest

from trade_app.platform.types import TradeError
from trade_app.market.domain import eligible_bars
from trade_app.platform.compute_process import ComputeResult, run_json_process
from trade_app.research import portfolio_domain as domain, portfolio_service as service
from trade_app.research.portfolio_domain import canonical, digest, initial_checkpoint, matrix_signals, run_chunk
from trade_app.research.portfolio_models import PortfolioRun, PortfolioChunk
from trade_app.research.service import normalize_strategy_params
from trade_app.trading.simulation import DEFAULT_CONFIG
from test_strategy_backtests import flat_bars
from test_wyckoff_research_api import client_for, write, data


def context(symbols=('sh600000',), count=20, start=3, **config):
    datasets = []
    for symbol in symbols:
        bars = flat_bars(count)
        for bar in bars:
            bar.update(high='10.1', low='9.9')
        datasets.append({'symbol': symbol, 'dataset_id': symbol, 'bars': bars})
    calendar = [bar['event_date'] for bar in datasets[0]['bars']]
    return {'mode': 'traditional_runtime14', 'strategy_id': 'relative_strength_breakout_v1',
            'datasets': datasets, 'all_calendar': calendar, 'calendar': calendar[start:],
            'params': normalize_strategy_params('relative_strength_breakout_v1', {}),
            'config': service.normalize_config({'initial_capital': '10000', 'position_pct': '1',
                'max_positions': 1, 'max_holding_bars': 240, 'stop_loss_pct': '0', 'take_profit_pct': '0',
                'daily_weak_clear': False, 'fee_config': {key: '0' for key in DEFAULT_CONFIG}, **config})}


def signals(monkeypatch, predicate=lambda symbol, bars: len(bars) == 3):
    def evaluate(_strategy_id, *, symbol, bars, **kwargs):
        hit = predicate(symbol, bars)
        return {'status': 'computed', 'source_date': bars[-1]['event_date'], 'signal': hit, 'draft_eligible': hit,
                'score': 10, 'quality_flags': []}
    monkeypatch.setattr(domain, 'evaluate_strategy', evaluate)


def test_unknown_availability_uses_shanghai_day_end_and_keeps_strict_guard():
    rows = context(count=2, start=0)['datasets'][0]['bars']
    day = rows[-1]['event_date']
    for row in rows:
        row['available_at'] = None
    before, _ = eligible_bars(rows, day + 'T15:59:59.999998+00:00', False)
    assert before == rows[:-1]
    visible, quality = eligible_bars(rows, day + 'T15:59:59.999999+00:00', False)
    assert visible == rows
    assert quality == ['historical_availability_unknown']
    assert eligible_bars(rows, day + 'T23:59:59.999999+08:00', False)[0] == rows
    assert eligible_bars(rows, day + 'T15:59:59.999999+00:00', True)[0] == []
    rows[-1]['available_at'] = day + 'T16:00:00+00:00'
    assert eligible_bars(rows, day + 'T15:59:59.999999+00:00', False)[0] == rows[:-1]


def test_nonstrict_unknown_bar_marks_same_day_close(monkeypatch):
    ctx = context(count=7, execution_strict=False)
    rows = ctx['datasets'][0]['bars']
    for row in rows:
        row['available_at'] = None
    rows[-1].update(open='10', high='20', low='10', close='20')
    signals(monkeypatch)
    result = run_chunk(ctx, days=20)
    assert result['equity'][-1]['positions']['sh600000']['mark'] == '20'
    assert Decimal(result['checkpoint']['ending_assets']) == 20000
    assert 'historical_availability_unknown' in result['checkpoint']['quality_flags']


def test_private_signal_provider_receives_only_causal_prefix_and_rejects_future_source():
    ctx = context(count=8)
    seen = []
    def provider(prefixes, dates):
        seen.append((deepcopy(prefixes), list(dates)))
        return {symbol: {'in_pool': True, 'buy': True, 'sell': False, 'score': 1,
                         'source_date': bars[-1]['event_date'], 'components': {}, 'reasons': []}
                for symbol, bars in prefixes.items()}
    actual = run_chunk(ctx, days=1, signal_provider=provider)
    assert len(seen[0][0]['sh600000']) == 3
    assert seen[0][1][-1] < actual['trades'][0]['date']
    def invalid(prefixes, dates):
        result = provider(prefixes, dates)
        result['sh600000']['source_date'] = ctx['calendar'][0]
        return result
    with pytest.raises(TradeError) as error:
        run_chunk(ctx, days=1, signal_provider=invalid)
    assert error.value.code == 'INVALID_PORTFOLIO_SIGNALS'


def test_raw_s1_s9_match_original_matrix_clean_windows_and_stable_ties():
    import numpy as np
    from app.core.backtest_matrix_engine import MatrixBundle
    from app.core.backtest_signal_matrix import compute_backtest_signal_matrix
    prefixes = {}
    for stock in range(3):
        bars = flat_bars(110)
        for index, bar in enumerate(bars):
            close = 10 + stock * 3 + index * .013 + math.sin(index / 4) * .22
            bar.update(open=str(close - .01), close=str(close), high=str(close + .05), low=str(close - .06),
                       volume=1000 + (index * 101 + stock * 11) % 1400)
        prefixes[f'sh60000{stock}'] = bars
    dates = [row['event_date'] for row in bars]
    symbols = sorted(prefixes)
    columns = {key: np.array([[float(prefixes[symbol][i][key]) for symbol in symbols] for i in range(110)]) for key in ('open', 'close', 'high', 'low', 'volume')}
    original = compute_backtest_signal_matrix(MatrixBundle(dates, symbols, **columns, valid_mask=np.ones((110, 3), dtype=bool)), top_n=1)
    for at in range(1, 110):
        actual = matrix_signals({symbol: rows[:at+1] for symbol, rows in prefixes.items()}, dates[:at+1], 1)
        for col, symbol in enumerate(symbols):
            row = actual[symbol]
            assert row['components'] == {f'S{i}': bool(getattr(original, f's{i}')[at, col]) for i in range(1, 10)}
            assert row['score'] == pytest.approx(original.score[at, col])
            assert row['buy'] == original.buy_signal[at, col] and row['sell'] == original.sell_signal[at, col]
    tied = {'sh600002': bars, 'sh600000': bars, 'sh600001': bars}
    assert [symbol for symbol, row in matrix_signals(tied, dates, 1).items() if row['components']['S3']] == ['sh600000']


def test_matrix_missing_calendar_bar_does_not_compress_window():
    bars = flat_bars(61)
    for bar in bars:
        bar.update(high='10.1', low='9.9')
    dates = [bar['event_date'] for bar in bars]
    full = matrix_signals({'a': bars}, dates, 1)['a']
    missing = matrix_signals({'a': bars[:50] + bars[51:]}, dates, 1)['a']
    assert full['components']['S4'] and not missing['components']['S4']
    assert missing['return_40d'] == 0  # pct_change requires endpoints, not all intermediate bars.


def test_t1_ignores_same_day_barriers_and_dual_touch_is_conservative(monkeypatch):
    signals(monkeypatch)
    ctx = context(count=7, stop_loss_pct='.05', take_profit_pct='.1')
    for bar in ctx['datasets'][0]['bars'][3:5]:
        bar.update(low='8', high='12')
    result = run_chunk(ctx, days=20)
    buy, sell = result['trades']
    assert buy['date'] == ctx['calendar'][0] and sell['date'] == ctx['calendar'][1]
    assert sell['reason'] == 'DAILY_STOP_LOSS' and Decimal(sell['price']) == Decimal('9.5')
    assert sell['execution_at'] is None and sell['reason_metrics']['both_touched']
    assert result['checkpoint']['cash'] == '9500.00'
    optimistic = deepcopy(ctx)
    optimistic['config']['ambiguity_policy'] = 'optimistic'
    assert Decimal(run_chunk(optimistic, days=20)['trades'][1]['price']) == 11


def test_open_gap_uses_actual_adverse_open_and_opposite_later_extreme_cannot_rewrite(monkeypatch):
    signals(monkeypatch)
    ctx = context(count=7, stop_loss_pct='.05', take_profit_pct='.1')
    ctx['datasets'][0]['bars'][4].update(open='8', low='7', high='12', close='10')
    sell = run_chunk(ctx, days=20)['trades'][1]
    assert sell['phase'] == 'open' and sell['reason'] == 'OPEN_GAP_STOP_LOSS' and Decimal(sell['price']) == 8
    ctx['datasets'][0]['bars'][4].update(open='12', low='7', high='13', close='10')
    sell = run_chunk(ctx, days=20)['trades'][1]
    assert sell['reason'] == 'OPEN_GAP_TAKE_PROFIT' and Decimal(sell['price']) == 12


def test_intraday_exit_cash_cannot_fund_same_open_but_open_exit_can(monkeypatch):
    signals(monkeypatch, lambda symbol, bars: len(bars) == (3 if symbol.endswith('0') else 4))
    ctx = context(symbols=('sh600000', 'sh600001'), count=6, stop_loss_pct='.05')
    ctx['datasets'][0]['bars'][4]['low'] = '9'
    intraday = run_chunk(ctx, days=20)
    assert [trade['symbol'] for trade in intraday['trades'] if trade['side'] == 'buy'] == ['sh600000']
    assert intraday['decisions'][0]['reason'] == 'MAX_POSITIONS'
    ctx['datasets'][0]['bars'][4]['open'] = '9'
    opening = run_chunk(ctx, days=20)
    assert [(trade['symbol'], trade['side']) for trade in opening['trades']] == [('sh600000', 'buy'), ('sh600000', 'sell'), ('sh600001', 'buy')]
    assert opening['trades'][2]['quantity'] == 900


def test_strict_known_prefix_no_same_close_and_late_history_skips(monkeypatch):
    seen = []
    def predicate(symbol, bars):
        seen.append(len(bars))
        return True
    signals(monkeypatch, predicate)
    ctx = context(count=8)
    ctx['datasets'][0]['bars'][2]['available_at'] = ctx['calendar'][0] + 'T02:00:00+00:00'
    result = run_chunk(ctx, days=20)
    buy = result['trades'][0]
    assert buy['date'] == ctx['calendar'][1] and buy['signal_date'] < buy['date']
    assert buy['known_at'] < buy['execution_at'] and 3 not in seen
    for bar in ctx['datasets'][0]['bars']:
        bar['available_at'] = None
    assert not run_chunk(ctx, days=20)['trades']
    ctx['config']['execution_strict'] = False
    loose = run_chunk(ctx, days=20)
    assert loose['trades'][0]['known_at'] is None
    assert 'historical_availability_unknown' in loose['checkpoint']['quality_flags']


def test_delayed_entry_count_and_risk_only_observation_never_enters(monkeypatch):
    signals(monkeypatch)
    ctx = context(count=10, entry_delay_bars=3)
    result = run_chunk(ctx, days=20)
    assert result['trades'][0]['date'] == ctx['calendar'][2]
    monkeypatch.setattr(domain, 'evaluate_strategy', lambda *a, bars, **kw: {
        'status': 'computed', 'signal': True, 'draft_eligible': False, 'source_date': bars[-1]['event_date'],
        'evaluation': {'primary_event': 'UTAD', 'risk_events': ['UTAD']}})
    assert not run_chunk(ctx, days=20)['trades']


def test_partial_trailing_respects_t1_quantities_and_independent_close_clear(monkeypatch):
    signals(monkeypatch)
    ctx = context(count=9, trailing_stop_pct='.1', intraday_trailing=True, trailing_reduce_ratio='.5')
    bars = ctx['datasets'][0]['bars']
    bars[3].update(high='12', close='12')
    bars[4].update(high='11', low='10', close='10.5')
    result = run_chunk(ctx, days=20)
    assert result['trades'][1]['date'] == bars[5]['event_date']
    assert result['trades'][1]['quantity'] == 500 and result['trades'][1]['reason'] == 'OBSERVED_TRAILING_NEXT_OPEN'
    assert all(trade['quantity'] % 100 == 0 for trade in result['trades'])
    ctx['config']['intraday_trailing'] = False
    assert len(run_chunk(ctx, days=20)['trades']) == 1


def test_chunk_resume_equals_uninterrupted_and_future_shock_cannot_change_earlier_pool_or_cash(monkeypatch):
    signals(monkeypatch, lambda symbol, bars: len(bars) % 3 == 0)
    ctx = context(symbols=('sh600000', 'sh600001'), count=30, max_holding_bars=2, pool_roll='position')
    reference = run_chunk(ctx, days=2000)
    checkpoint, outputs = None, {key: [] for key in ('trades', 'equity', 'pool_history', 'decisions')}
    while checkpoint is None or checkpoint['cursor'] < len(ctx['calendar']):
        part = run_chunk(ctx, checkpoint)
        checkpoint = json.loads(canonical(part['checkpoint']))
        for key in outputs:
            outputs[key].extend(part[key])
    assert checkpoint == reference['checkpoint']
    for key in outputs:
        assert outputs[key] == reference[key]
    future = deepcopy(ctx)
    cutoff = future['calendar'][10]
    for item in future['datasets']:
        for bar in item['bars']:
            if bar['event_date'] >= cutoff:
                bar.update(open='1', close='1', high='1.1', low='.9')
    changed = run_chunk(future, days=2000)
    for key in outputs:
        assert [row for row in reference[key] if row['date'] < cutoff] == [row for row in changed[key] if row['date'] < cutoff]


def test_fee_slippage_decimal_hand_calculation_and_terminal_open_position(monkeypatch):
    signals(monkeypatch)
    fees = {**DEFAULT_CONFIG, 'commission_rate': '.001', 'minimum_commission': '1', 'sell_stamp_rate': '.001', 'transfer_rate': '0', 'slippage_rate': '.01'}
    ctx = context(count=5, fee_config=fees)
    result = run_chunk(ctx, days=20)
    buy = result['trades'][0]
    assert buy['quantity'] == 900 and buy['price'] == '10.1000' and buy['fees'] == '9.09'
    assert result['checkpoint']['cash'] == '900.91'
    assert result['checkpoint']['ending_assets'] == '9900.91'
    assert result['checkpoint']['positions']['sh600000']['quantity'] == 900
    assert not [trade for trade in result['trades'] if trade['side'] == 'sell']


def test_shanghai_eod_mark_cannot_use_following_local_morning_information(monkeypatch):
    signals(monkeypatch, lambda *_: False)
    ctx = context(count=6, start=4)
    checkpoint = initial_checkpoint(ctx)
    checkpoint.update(cash='0', peak_equity='1000', marks={'sh600000': '10'}, positions={'sh600000': {
        'quantity': 100, 'cost': '1000', 'entry_date': '2025-01-04', 'entry_price': '10', 'held_bars': 1,
        'peak': '10', 'weak_days': 0, 'last_observed_date': '2025-01-04'}})
    # UTC Jan5 20:00 is Shanghai Jan6 04:00, after Jan5's reporting day.
    ctx['datasets'][0]['bars'][4].update(close='20', high='20', available_at='2025-01-05T20:00:00+00:00')
    late = run_chunk(ctx, checkpoint, days=1)
    assert late['equity'][0]['date'] == '2025-01-05' and late['equity'][0]['total_assets'] == '1000.00'
    assert late['equity'][0]['positions']['sh600000']['mark'] == '10'
    ctx['datasets'][0]['bars'][4]['available_at'] = '2025-01-05T15:59:59+00:00'
    on_time = run_chunk(ctx, checkpoint, days=1)
    assert on_time['equity'][0]['total_assets'] == '2000.00'


@pytest.mark.parametrize('invalid', [{'enforce_t1': False}, {'entry_delay_bars': 0}, {'max_positions': 65},
    {'execution_strict': 'false'}, {'position_pct': 'NaN'}, {'ambiguity_policy': 'best_possible'}, {'trailing_reduce_ratio': '0'}])
def test_unapplied_or_unsafe_config_rejected(invalid):
    with pytest.raises(TradeError):
        service.normalize_config(invalid)


def create_run(client, path, *, mode='traditional_runtime14', count=75):
    ids = []
    for stock in range(2):
        bars = flat_bars(count)
        for index, bar in enumerate(bars):
            close = Decimal('10') + index * Decimal('.03') + stock
            bar.update(open=str(close - Decimal('.01')), high=str(close + Decimal('.01')),
                       low=str(close - Decimal('.02')), close=str(close), volume=5000 if index % 7 == 0 else 1000 + index)
        ids.append(data(write(client, '/market/datasets', {'symbol': f'sh60000{stock}', 'bars': bars}))['id'])
    body = {'dataset_ids': ids, 'mode': mode, 'strategy_id': 'relative_strength_breakout_v1' if mode == 'traditional_runtime14' else None,
            'params': {'min_ret40': '.01', 'min_vol_slope20': '0'} if mode == 'traditional_runtime14' else {},
            'start_date': bars[60]['event_date'], 'end_date': bars[-1]['event_date'],
            'config': {'max_holding_bars': 3, 'daily_weak_clear': False, 'stop_loss_pct': '0', 'take_profit_pct': '0'}}
    preview = data(write(client, '/research/portfolios/preview', body))
    payload = {**body, 'name': '真实组合验收', 'expected_preview_sha256': preview['preview_sha256']}
    result = data(write(client, '/research/portfolios', payload, key='portfolio-create-' + mode))
    assert data(write(client, '/research/portfolios', payload, key='portfolio-create-' + mode)) == result
    return result, body


@pytest.mark.parametrize('mode', ['matrix_raw_s1_s9', 'traditional_runtime14'])
def test_real_worker_checkpoints_api_history_resume_and_export(mode, tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        run, _ = create_run(client, tmp_path, mode=mode)
        factory = client.app.state.db_factory
        assert service.process_one_portfolio(factory, tmp_path)
        first = data(client.get('/api/v1/research/portfolios/' + run['id']))
        assert first['completed_days'] == 5 and first['chunk_count'] == 1 and first['state'] == 'queued'
        with factory() as session:
            frozen = json.loads(session.get(PortfolioRun, run['id']).input_json)
            expected = run_chunk(frozen, days=2000)
        # Frozen bars remain executable when the external market file disappears.
        for dataset_id in [item['dataset_id'] for item in frozen['datasets']]:
            (tmp_path / 'market' / (dataset_id + '.json')).unlink()
        service.recover_interrupted_portfolios(factory)
        paused = data(client.get('/api/v1/research/portfolios/' + run['id']))
        assert paused['state'] == 'paused' and paused['checkpoint_sha256'] == first['checkpoint_sha256']
        data(write(client, '/research/portfolios/' + run['id'] + '/resume', {}))
        while service.process_one_portfolio(factory, tmp_path):
            pass
        complete = data(client.get('/api/v1/research/portfolios/' + run['id'] + '/result'))
        assert complete['state'] == 'succeeded', complete['error']
        assert complete['chunk_count'] == 3 and complete['completed_days'] == 15
        assert complete['checkpoint'] == expected['checkpoint']
        for key in ('trades', 'equity', 'pool_history', 'decisions'):
            assert complete['result'][key] == expected[key]
        assert complete['result']['buy_count'] > 0
        assert all(item['signal_date'] < item['date'] for item in complete['result']['trades'] if item['side'] == 'buy')
        assert 'selection_membership_unverified' in complete['summary']['quality_flags']
        exported = client.get('/api/v1/research/portfolios/' + run['id'] + '/export.json').json()
        assert exported['result_sha256'] == complete['result_sha256']
        # Completed evidence must remain readable after a later protocol upgrade.
        monkeypatch.setattr(domain, 'VERSION', 'future-protocol-for-reading-regression')
        assert data(client.get('/api/v1/research/portfolios/' + run['id'] + '/result'))['result_sha256'] == complete['result_sha256']
        assert len(data(client.get('/api/v1/research/portfolios'))) == 1
        data(write(client, '/research/portfolios/' + run['id'], {}, method='DELETE'))
        assert data(client.get('/api/v1/research/portfolios')) == []
        with factory() as session:
            assert session.get(PortfolioRun, run['id']).deleted == 1
            assert len(list(session.query(PortfolioChunk).filter_by(run_id=run['id']))) == 3


def test_preview_alias_duplicates_unused_options_and_profile_freeze(tmp_path):
    with client_for(tmp_path) as client:
        _, body = create_run(client, tmp_path)
        alias = data(write(client, '/market/datasets', {'symbol': '600000.SH', 'bars': flat_bars(75)}))
        bad = write(client, '/research/portfolios/preview', {**body, 'dataset_ids': [body['dataset_ids'][0], alias['id']]})
        assert bad.status_code == 400 and bad.json()['error']['code'] == 'PORTFOLIO_DUPLICATE_SYMBOL'
        assert write(client, '/research/portfolios/preview', {**body, 'mode': 'matrix_raw_s1_s9'}).status_code == 400
        assert write(client, '/research/portfolios/preview', {**body, 'historical_universe_strict': True}).status_code == 422
        profile = data(client.get('/api/v1/research/event-profiles'))['profiles'][0]
        assert profile['revision'] == 0
        preview = data(write(client, '/research/portfolios/preview', {**body, 'strategy_id': 'wyckoff_trend_v1', 'params': {},
            'event_profile_id': profile['profile_id'], 'event_profile_revision': 0}))
        assert preview['frozen_context']['event_profile']['snapshot']
        assert write(client, '/research/portfolios/preview', {**body, 'strategy_id': 'wyckoff_trend_v1', 'params': {},
            'event_profile_id': profile['profile_id'], 'event_profile_revision': 1}).status_code == 409
        assert write(client, '/research/portfolios', {**body, 'name': '未预览变更', 'config': {'position_pct': '.9'},
            'expected_preview_sha256': preview['preview_sha256']}).status_code == 409


@pytest.mark.parametrize('action', ['pause', 'cancel', 'shutdown', 'code', 'attempt'])
def test_publication_control_races_keep_atomic_prior_checkpoint(action, tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        run, _ = create_run(client, tmp_path)
        factory = client.app.state.db_factory
        stopped = False
        original = service.run_json_process
        def raced(*args, **kwargs):
            nonlocal stopped
            value = original(*args, **kwargs)
            if action in ('pause', 'cancel'):
                with factory.begin() as session:
                    service.control_portfolio(session, run['id'], action)
            elif action == 'shutdown':
                stopped = True
            elif action == 'code':
                monkeypatch.setattr(service, 'code_sha256', lambda: 'changed')
            else:
                with factory.begin() as session:
                    session.get(PortfolioRun, run['id']).attempt_id = 'replacement'
            return value
        monkeypatch.setattr(service, 'run_json_process', raced)
        assert service.process_one_portfolio(factory, tmp_path, should_stop=lambda: stopped)
        with factory() as session:
            saved = session.get(PortfolioRun, run['id'])
            assert saved.chunk_count == (1 if action == 'pause' else 0)
            assert saved.state == {'pause': 'paused', 'cancel': 'cancelled', 'shutdown': 'paused', 'code': 'failed', 'attempt': 'running'}[action]


def test_checkpoint_tamper_budget_and_retry_do_not_republish_success(tmp_path):
    with client_for(tmp_path) as client:
        run, _ = create_run(client, tmp_path)
        factory = client.app.state.db_factory
        service.process_one_portfolio(factory, tmp_path)
        data(write(client, '/research/portfolios/' + run['id'] + '/cancel', {}))
        data(write(client, '/research/portfolios/' + run['id'] + '/retry', {}))
        service.process_one_portfolio(factory, tmp_path)
        row = data(client.get('/api/v1/research/portfolios/' + run['id']))
        assert row['chunk_count'] == 2 and row['completed_days'] == 10
        data(write(client, '/research/portfolios/' + run['id'] + '/pause', {}))
        with factory.begin() as session:
            saved = session.get(PortfolioRun, run['id'])
            saved.elapsed_ms = 3600000
        assert write(client, '/research/portfolios/' + run['id'] + '/resume', {}).json()['error']['code'] == 'PORTFOLIO_BUDGET_EXHAUSTED'
        with factory.begin() as session:
            saved = session.get(PortfolioRun, run['id'])
            payload = json.loads(saved.checkpoint_json)
            payload['cash'] = '900000000'
            saved.checkpoint_json = canonical(payload)
        assert write(client, '/research/portfolios/' + run['id'] + '/resume', {}).json()['error']['code'] == 'PORTFOLIO_CHECKPOINT_CORRUPT'


def test_measured_worker_resource_boundary_64_symbols_57600_bars():
    # Largest stock-count boundary under total-bar limit, at the end of long histories.
    ctx = context(symbols=tuple(f'sh600{index:03}' for index in range(64)), count=900, start=895)
    ctx['mode'] = 'matrix_raw_s1_s9'
    matrix = run_json_process('trade_app.research.portfolio_worker', {'attempt_id': 'matrix-cap', 'context': ctx}, budget=service.COMPUTE_BUDGET)
    assert matrix.value['ok'] and matrix.value['result']['checkpoint']['cursor'] == 5
    from trade_app.research.event_profiles import get_system_profile
    ctx.update(mode='traditional_runtime14', strategy_id='wyckoff_trend_v2',
               params=normalize_strategy_params('wyckoff_trend_v2', {}), event_profile={'snapshot': get_system_profile()})
    wyckoff = run_json_process('trade_app.research.portfolio_worker', {'attempt_id': 'wyckoff-cap', 'context': ctx}, budget=service.COMPUTE_BUDGET)
    assert wyckoff.value['ok'] and wyckoff.value['result']['checkpoint']['cursor'] == 5
    print('portfolio max boundary:', {'matrix': matrix.metrics, 'wyckoff': wyckoff.metrics})
