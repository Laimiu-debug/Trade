from copy import deepcopy
from types import SimpleNamespace

import json

import pytest

from trade_app.platform.types import TradeError
from trade_app.research.portfolio_event_domain import (DEFAULTS, evaluate_event_bars,
    evaluate_event_snapshot, normalize_event_params)
from trade_app.research.event_profiles import get_system_profile
from trade_app.research.wyckoff_domain import WYCKOFF_ACC_EVENTS, calculate_snapshot
from test_wyckoff_domain import event_rich_bars


DAY = '2025-03-12'


def snapshot(**values):
    return {'events': ['SC', 'Spring', 'SOS'], 'risk_events': [],
            'event_dates': {'SC': '2025-03-01', 'Spring': '2025-03-10', 'SOS': DAY},
            'event_confirmation_map': {'Spring': 'confirmed', 'SOS': 'confirmed'},
            'event_chain': [], 'entry_quality_score': 65, 'health_score': 60, 'event_score': 70,
            'event_grade': 'B', 'confirmation_status': 'confirmed', 'sequence_ok': True, **values}


@pytest.mark.parametrize('patch,params', [({}, {}), ({'entry_quality_score': 54.99}, {}),
    ({'health_score': 0}, {'health_score_min': 1}), ({'event_score': 10}, {'event_score_min': 20}),
    ({'event_grade': 'C'}, {'event_grade_min': 'B'}), ({'sequence_ok': False}, {'require_sequence': True}),
    ({'confirmation_status': 'partial'}, {'require_key_event_confirmation': True}),
    ({'event_chain': [{'event': 'SC'}]}, {'min_event_count': 2}),
    ({'event_dates': {'Spring': '2025-03-11'}}, {}),
    ({'event_dates': {'UTAD': DAY}, 'risk_events': ['UTAD']}, {}),
    ({'health_score': None, 'event_score': None, 'event_grade': 'unknown'}, {})])
def test_legacy_snapshot_policy_matches_original_backtest_predicates(patch, params):
    from legacy_oracle import oracle
    case = json.dumps([patch, params], sort_keys=True)
    params = normalize_event_params({**params, 'confirmation_policy': 'legacy_snapshot'})
    snap = snapshot(**patch)
    result = evaluate_event_snapshot(snap, params, source_date=DAY)

    def legacy():
        from app.core.backtest_engine import BacktestEngine
        from app.models import BacktestRunRequest
        payload = BacktestRunRequest(date_from=DAY, date_to=DAY,
            **{key: value for key, value in params.items() if key != 'confirmation_policy'})
        dates = BacktestEngine._normalize_event_dates(snap.get('event_dates'))
        entries = [event for event in payload.entry_events if dates.get(event) == DAY]
        exits = [event for event in payload.exit_events if dates.get(event) == DAY]
        expected = bool(entries) and BacktestEngine._normalize_event_count(snap) >= payload.min_event_count
        expected &= not payload.require_sequence or bool(snap.get('sequence_ok'))
        expected &= snap['entry_quality_score'] >= payload.min_score
        expected &= BacktestEngine._passes_semantic_score_gates(payload=payload, health_score=result['health_score'],
            event_score=result['event_score'], event_grade=result['event_grade'], confirmation_status=snap.get('confirmation_status'))
        return {'expected': bool(expected), 'exits': bool(exits)}
    legacy_result = oracle('snapshot_policy:' + case, legacy)
    expected, exits = legacy_result['expected'], legacy_result['exits']
    assert result['buy'] == bool(expected and not exits)
    assert result['sell'] == bool(exits)
    assert result['score'] == (snap['entry_quality_score'] if expected else 0)


def test_backtest_defaults_not_scan_defaults_and_supported_old_accumulation_events():
    assert DEFAULTS['min_score'] == 55 and DEFAULTS['window_days'] == 60
    for event in WYCKOFF_ACC_EVENTS:
        result = evaluate_event_snapshot(snapshot(event_dates={event: DAY}, events=[event]),
                                        {'entry_events': [event]}, source_date=DAY)
        assert result['buy'] and result['entry_events'] == [event]
    assert not evaluate_event_snapshot(snapshot(), {'entry_events': []}, source_date=DAY)['buy']


def test_event_confirmation_cannot_borrow_another_events_confirmation_and_never_backdates():
    snap = snapshot(event_confirmation_map={'Spring': 'confirmed', 'SOS': 'pending'})
    modern = evaluate_event_snapshot(snap, {'require_key_event_confirmation': True}, source_date=DAY)
    legacy = evaluate_event_snapshot(snap, {'require_key_event_confirmation': True, 'confirmation_policy': 'legacy_snapshot'}, source_date=DAY)
    assert not modern['buy'] and legacy['buy']
    assert modern['entry_events'] == ['SOS'] and not modern['accepted_entry_events']
    snap['event_confirmation_map']['SOS'] = 'confirmed'
    later = evaluate_event_snapshot(snap, {'require_key_event_confirmation': True}, source_date='2025-03-13')
    assert not later['buy'] and later['event_dates']['SOS'] == DAY
    assert 'NO_ENTRY_EVENT_ON_SOURCE_DATE' in later['reasons']


def test_exit_independent_of_entry_gates_and_priority_is_explicit_intentional_difference():
    snap = snapshot(event_dates={'SOS': DAY, 'UTAD': DAY}, risk_events=['UTAD'])
    result = evaluate_event_snapshot(snap, {}, source_date=DAY)
    assert not result['buy'] and result['sell'] and result['in_pool']
    assert result['components']['S8'] and not result['components']['S5']
    assert 'EXIT_OVERRIDES_SIMULTANEOUS_ENTRY' in result['quality_flags']
    rejected_entry = evaluate_event_snapshot(snap, {'min_score': 99}, source_date=DAY)
    assert rejected_entry['sell'] and not rejected_entry['in_pool']


def test_zero_health_preserved_and_future_invalid_event_dates_excluded():
    snap = snapshot(health_score=0, event_dates={'SOS': DAY, 'UTAD': '2025-03-13', 'LPSY': 'invalid'})
    result = evaluate_event_snapshot(snap, {'health_score_min': 1}, source_date=DAY)
    assert result['health_score'] == 0 and not result['buy'] and not result['sell']
    assert result['event_dates'] == {'SOS': DAY}
    assert result['quality_flags'] == ['FUTURE_EVENT_DATE_EXCLUDED', 'INVALID_EVENT_DATE_EXCLUDED']


@pytest.mark.parametrize('params', [{'entry_events': ['UTAD']}, {'entry_events': ['SOS', 'SOS']},
    {'entry_events': 'SOS'}, {'exit_events': ['made_up']}, {'rank_weight_health': .5},
    {'window_days': 19}, {'min_score': float('nan')}, {'require_sequence': 'yes'}, {'min_event_count': 1.1}])
def test_unused_ranking_fields_unsafe_entries_and_invalid_params_rejected(params):
    with pytest.raises(TradeError):
        normalize_event_params(params)


def test_real_snapshot_uses_frozen_prefix_and_profile_without_future_confirmation():
    bars, profile = event_rich_bars(), {'snapshot': get_system_profile()}
    params = normalize_event_params({})
    for count in (40, 60, 85, len(bars)):
        prefix = bars[:count]
        result = evaluate_event_bars(prefix, params, event_profile=profile)
        original = calculate_snapshot(prefix, 60, profile=profile['snapshot'])
        expected = evaluate_event_snapshot(original['snapshot'], params, source_date=prefix[-1]['event_date'])
        assert result['buy'] == expected['buy'] and result['sell'] == expected['sell']
        assert all(day <= prefix[-1]['event_date'] for day in result['event_dates'].values())
    with pytest.raises(TradeError):
        evaluate_event_bars(bars, params, event_profile=None)


def test_actual_legacy_aligned_matrix_builder_matches_legacy_policy():
    from legacy_oracle import oracle
    snap = snapshot()

    def legacy():
        import numpy as np
        from app.store import InMemoryStore
        from app.core.backtest_engine import BacktestEngine
        from app.core.backtest_matrix_engine import MatrixBundle
        from app.models import BacktestRunRequest
        store = SimpleNamespace(_build_backtest_engine=lambda: BacktestEngine,
            _build_row_from_candles=lambda *_: object(), _calc_wyckoff_snapshot=lambda *_, **kw: snap)
        values = np.ones((1, 1))
        bundle = MatrixBundle(dates=[DAY], symbols=['sh600000'], open=values, high=values, low=values,
                              close=values, volume=values, valid_mask=np.ones((1, 1), dtype=bool))
        payload = BacktestRunRequest(date_from=DAY, date_to=DAY, require_key_event_confirmation=True)
        matrix = InMemoryStore._build_aligned_backtest_signal_matrix(store, bundle=bundle, payload=payload)
        return {'buy': bool(matrix.buy_signal[0, 0]), 'sell': bool(matrix.sell_signal[0, 0]),
                'in_pool': bool(matrix.in_pool[0, 0]), 'score': float(matrix.score[0, 0]),
                'components': {f'S{i}': bool(getattr(matrix, f's{i}')[0, 0]) for i in range(1, 10)}}
    original = oracle('aligned_matrix_builder', legacy)
    actual = evaluate_event_snapshot(snap, {'require_key_event_confirmation': True, 'confirmation_policy': 'legacy_snapshot'}, source_date=DAY)
    assert actual['buy'] == original['buy'] and actual['sell'] == original['sell']
    assert actual['in_pool'] == original['in_pool'] and actual['score'] == original['score']
    assert actual['components'] == original['components']


def test_aligned_actual_job_freezes_profile_events_and_has_entry_exit_evidence(tmp_path):
    from test_wyckoff_research_api import client_for, write, data, dataset
    from trade_app.research import portfolio_service as service
    bars = event_rich_bars()
    with client_for(tmp_path) as client:
        ds = dataset(client, bars)
        profile = data(client.get('/api/v1/research/event-profiles'))['profiles'][0]
        body = {'mode': 'aligned_wyckoff_events', 'dataset_ids': [ds['id']], 'start_date': bars[60]['event_date'],
            'params': {'entry_events': list(WYCKOFF_ACC_EVENTS), 'min_score': 0, 'min_event_count': 0},
            'event_profile_id': profile['profile_id'], 'event_profile_revision': 0,
            'config': {'stop_loss_pct': '0', 'take_profit_pct': '0', 'daily_weak_clear': False, 'entry_delay_bars': 2}}
        preview = data(write(client, '/research/portfolios/preview', body))
        assert preview['frozen_context']['params']['confirmation_policy'] == 'event'
        run = data(write(client, '/research/portfolios', {**body, 'name': '事件矩阵实测', 'expected_preview_sha256': preview['preview_sha256']}))
        factory = client.app.state.db_factory
        while service.process_one_portfolio(factory, tmp_path):
            pass
        result = data(client.get('/api/v1/research/portfolios/' + run['id'] + '/result'))
        assert result['state'] == 'succeeded', result['error']
        assert result['frozen_context']['event_profile']['revision'] == 0
        buys = [trade for trade in result['result']['trades'] if trade['side'] == 'buy']
        assert buys, result['result']['pool_history']
        assert all(trade['reason_metrics']['components']['entry_events'] for trade in buys)
        assert all(trade['signal_date'] < trade['date'] for trade in buys)
        assert result['result']['pool_history'][0]['rows'][0]['components']['confirmation_policy'] == 'event'
