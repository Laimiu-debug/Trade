from copy import deepcopy
from decimal import Decimal

import pytest

from trade_app.platform.types import TradeError
from trade_app.research.portfolio_domain import run_chunk, initial_checkpoint
from trade_app.research.portfolio_service import normalize_config
from trade_app.research.portfolio_plan_domain import build_plan
from test_portfolio import context, signals


def test_new_exit_defaults_are_off_and_unused_time_threshold_rejected():
    config = normalize_config({})
    assert all(config[key] in ('0', 0, False) for key in ('trail_activate', 'time_stop_days', 'time_stop_min_gain',
        'breakeven_trigger', 'structural_box_stop', 'ma20_box_break'))
    for values in ({'time_stop_days': True}, {'time_stop_days': 241}, {'time_stop_min_gain': '.1'}, {'trail_activate': 'NaN'}, {'breakeven_trigger': '2.1'}):
        with pytest.raises(TradeError): normalize_config(values)


def test_same_bar_new_high_never_retroactively_activates_breakeven_low(monkeypatch):
    signals(monkeypatch)
    ctx = context(count=8, breakeven_trigger='.1')
    ctx['datasets'][0]['bars'][4].update(open='10', high='12', low='9.9', close='11')
    ctx['datasets'][0]['bars'][5].update(open='9', high='10', low='8.5', close='9.5')
    result = run_chunk(ctx, days=20)
    assert [row['side'] for row in result['trades']] == ['buy', 'sell']
    sell = result['trades'][1]
    assert sell['date'] == ctx['datasets'][0]['bars'][5]['event_date']
    assert sell['reason'] == 'OPEN_GAP_BREAKEVEN_STOP' and Decimal(sell['price']) == 9
    assert sell['reason_metrics']['band_exit']['observed_date'] == ctx['datasets'][0]['bars'][4]['event_date']
    late = deepcopy(ctx)
    late['datasets'][0]['bars'][4]['available_at'] = '2030-01-01T00:00:00+00:00'
    assert len(run_chunk(late, days=20)['trades']) == 1


@pytest.mark.parametrize('policy, reason, price', [('conservative', 'DAILY_BREAKEVEN_STOP', '10.02'), ('optimistic', 'DAILY_TAKE_PROFIT', '15')])
def test_previously_known_breakeven_respects_existing_dual_touch_policy(monkeypatch, policy, reason, price):
    signals(monkeypatch)
    ctx = context(count=7, breakeven_trigger='.1', take_profit_pct='.5', ambiguity_policy=policy)
    ctx['datasets'][0]['bars'][4].update(open='10', high='12', low='10.05', close='11')
    ctx['datasets'][0]['bars'][5].update(open='10.1', high='16', low='9.9', close='12')
    sell = run_chunk(ctx, days=20)['trades'][1]
    assert sell['reason'] == reason and Decimal(sell['price']) == Decimal(price)
    assert sell['reason_metrics']['both_touched']
    assert sell['reason_metrics']['band_observed_date'] == ctx['datasets'][0]['bars'][4]['event_date']


def test_time_stop_counts_completed_bars_after_entry_and_executes_next_open(monkeypatch):
    signals(monkeypatch)
    ctx = context(count=9, time_stop_days=2, time_stop_min_gain='.05')
    result = run_chunk(ctx, days=20)
    sell = result['trades'][1]
    assert sell['date'] == ctx['datasets'][0]['bars'][6]['event_date']
    assert sell['reason'] == 'BAND_TIME_STOP_NEXT_OPEN'
    assert sell['reason_metrics']['band_exit']['completed_post_entry_bars'] == 2
    reached = deepcopy(ctx)
    reached['datasets'][0]['bars'][4]['high'] = '11'
    assert len(run_chunk(reached, days=20)['trades']) == 1


def test_activated_ma10_close_exit_uses_later_open_and_plan_has_same_reason(monkeypatch):
    from trade_app.research import portfolio_domain, portfolio_plan_domain
    signals(monkeypatch, lambda symbol, bars: len(bars) == 80)
    monkeypatch.setattr(portfolio_plan_domain, 'evaluate_strategy', portfolio_domain.evaluate_strategy)
    ctx = context(count=85, start=80, trail_activate='.1')
    ctx['datasets'][0]['bars'][81].update(open='10', high='12', low='9', close='9.5')
    ctx['datasets'][0]['bars'][82].update(open='8.8', high='9.3', low='8.5', close='9')
    state = run_chunk(ctx, days=2)['checkpoint']
    before = deepcopy(state)
    plan = build_plan(ctx, state, ctx['datasets'][0]['bars'][81]['event_date'])
    assert state == before
    assert plan['open_signals'][0]['reason'] == 'BAND_MA10_TRAIL_NEXT_OPEN'
    sell = run_chunk(ctx, state, days=2)['trades'][0]
    assert sell['reason'] == plan['open_signals'][0]['reason'] and Decimal(sell['price']) == Decimal('8.8')


def provider(box='10', first=3):
    return lambda prefixes, dates: {symbol: {'in_pool': True, 'buy': len(rows) == first, 'sell': False, 'score': 1,
        'source_date': rows[-1]['event_date'], 'components': {}, 'reasons': [], 'ma10': 10,
        **({'entry_box_high': box} if box is not None else {})} for symbol, rows in prefixes.items()}


def test_frozen_entry_box_controls_structural_gap_not_future_box_estimate():
    ctx = context(count=7, structural_box_stop=True, stop_loss_pct='.2')
    ctx['datasets'][0]['bars'][4].update(open='9', high='10', low='8', close='9')
    result = run_chunk(ctx, days=20, signal_provider=provider())
    assert result['trades'][0]['reason_metrics']['entry_box_high'] == '10'
    sell = result['trades'][1]
    assert sell['reason'] == 'OPEN_GAP_BOX_STRUCTURE_STOP' and Decimal(sell['price']) == 9
    missing = run_chunk(ctx, days=20, signal_provider=provider(None))
    assert not missing['trades'] and missing['decisions'][0]['reason'] == 'ENTRY_BOX_REFERENCE_MISSING'
    with pytest.raises(TradeError): run_chunk(ctx, days=20, signal_provider=provider('NaN'))


def test_ma20_and_frozen_box_break_is_known_close_then_later_open():
    ctx = context(count=85, start=80, ma20_box_break=True)
    ctx['datasets'][0]['bars'][81].update(open='10', high='10.1', low='8.9', close='9')
    ctx['datasets'][0]['bars'][82].update(open='9.4', high='10', low='9', close='9.8')
    trades = run_chunk(ctx, days=20, signal_provider=provider('10.5', first=80))['trades']
    assert trades[1]['reason'] == 'BAND_MA20_BOX_BREAK_NEXT_OPEN' and Decimal(trades[1]['price']) == Decimal('9.4')


def test_new_engine_refuses_old_checkpoint_instead_of_upgrading_semantics():
    ctx = context(); state = initial_checkpoint(ctx)
    state['version'] = 'fixed-sample-causal-portfolio-shanghai-marks-v2'
    with pytest.raises(TradeError) as error: run_chunk(ctx, state)
    assert error.value.code == 'INVALID_PORTFOLIO_CHECKPOINT'
