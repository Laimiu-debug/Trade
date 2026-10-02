from datetime import date, timedelta
from decimal import Decimal
import json

import pytest
from sqlalchemy import func, select, text

from app.models import CandlePoint
from app.store import InMemoryStore
from trade_app.market.service import import_dataset
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research.basket_domain import normalize_config
from trade_app.research.basket_models import SignalBasketEvaluation
from trade_app.research.basket_service import (
    create_basket, delete_baskets, evaluate_basket, get_basket, get_evaluation,
    list_audit, list_baskets, list_evaluations, update_basket,
)
from trade_app.research.models import ResearchRun
from trade_app.research.scan_service import create_scan
from trade_app.research.service import create_run
from trade_app.trading.domain import FeeRule, calculate_fees


def _bars():
    bars = []
    for index in range(45):
        day = (date(2025, 1, 10) - timedelta(days=44 - index)).isoformat()
        close = 10 + index * 0.2
        bars.append({'event_date': day, 'open': f'{close - 0.1:.4f}',
                     'high': f'{close + 0.1:.4f}', 'low': f'{close - 0.2:.4f}',
                     'close': f'{close:.4f}', 'volume': 1000 + index * 30,
                     'available_at': day + 'T08:00:00+00:00'})
    return bars


def _future():
    return [{'event_date': day, 'open': opening, 'high': str(max(float(opening), float(close)) + 1),
             'low': str(min(float(opening), float(close)) - 1), 'close': close, 'volume': 3000,
             'available_at': day + 'T08:00:00+00:00'} for day, opening, close in [
                 ('2025-01-13', '20.00', '22.00'), ('2025-01-14', '23.00', '24.00'),
                 ('2025-01-15', '24.50', '25.00'), ('2025-01-16', '25.00', '26.00')]]


def _write(factory, operation):
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        return operation(session)


def _import(factory, directory, symbol, bars):
    return _write(factory, lambda session: import_dataset(session, directory, {
        'symbol': symbol, 'bars': bars, 'adjustment': 'none'}))['id']


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        original = _import(factory, tmp_path, '600000', _bars())
        forward = _import(factory, tmp_path, '600000.SH', _bars() + _future())
        body = {'dataset_id': original, 'strategy_id': 'relative_strength_breakout_v1',
                'decision_at': '2025-01-10T18:00:00+00:00', 'strict': True, 'params': {}}
        signal = _write(factory, lambda session: create_run(session, tmp_path, body))
        assert signal['result']['signal'] is True
        basket = _write(factory, lambda session: create_basket(session, tmp_path, {
            'name': '待买信号篮子', 'notes': '固定观察样本', 'run_ids': [signal['id']], 'holding_bars': 3}))
        yield factory, tmp_path, signal, basket, original, forward
    finally:
        engine.dispose()


def _evaluate(scenario, **overrides):
    factory, directory, _signal, basket, _original, forward = scenario
    body = {'expected_revision': basket['revision'], 'as_of_date': '2025-01-16', 'strict': True,
            'forward_datasets': {'sh600000': forward}, **overrides}
    return _write(factory, lambda session: evaluate_basket(session, directory, basket['id'], body))


def _legacy_detail(bars, holding_days=3, as_of_date='2025-01-16'):
    candles = [CandlePoint(time=bar['event_date'], open=float(bar['open']), high=float(bar['high']),
                          low=float(bar['low']), close=float(bar['close']), volume=bar['volume'], amount=0)
               for bar in bars]
    store = InMemoryStore.__new__(InMemoryStore)
    store._now_date = lambda: as_of_date
    store._now_datetime = lambda: as_of_date + 'T12:00:00Z'
    store._signal_etf_window_bars = lambda _dates: len(candles)
    store._load_signal_etf_candles = lambda symbol, **_kwargs: candles if symbol == 'sh600000' else []
    return store._build_signal_etf_runtime(
        {'record_id': 'legacy-fixed', 'name': '固定对照', 'strategy_id': 'relative_strength_breakout_v1',
         'signal_date': '2025-01-10', 'constituents': [{'symbol': 'sh600000', 'signal_date': '2025-01-10'}]},
        candle_cache={}, as_of_date=as_of_date, holding_days=holding_days)['detail']


def test_t1_t2_raw_prices_and_legacy_holding_return_match_original(scenario):
    _factory, _directory, signal, basket, _original, _forward = scenario
    report = _evaluate(scenario)
    row = report['result']['constituents'][0]
    legacy = _legacy_detail(_bars() + _future()).constituents[0]
    assert basket['constituents'][0]['signals'] == [signal]
    assert row['anchor_date'] == '2025-01-11'  # 18:00Z decision rolls into Shanghai's next day.
    for mode in ('t1', 't2'):
        case = row[mode]
        assert case['status'] == 'completed'
        assert case['entry_date'] == getattr(legacy, 'buy_date_' + mode)
        assert float(case['entry_price']) == getattr(legacy, 'buy_price_' + mode)
        assert case['mark_to_market_return'] == getattr(legacy, 'return_pct_' + mode)
    assert row['t1']['target_date'] == legacy.holding_target_date == '2025-01-15'
    assert row['t1']['raw_return'] == legacy.return_pct_holding == 0.25
    rule = FeeRule(**{key: Decimal(value) for key, value in report['request']['basket_snapshot']['config']['fees'].items()})
    buy = calculate_fees(Decimal(20), 100, 'buy', rule).total
    sell = calculate_fees(Decimal(25), 100, 'sell', rule).total
    expected = round(float((Decimal(2500) - sell) / (Decimal(2000) + buy) - 1), 6)
    assert row['t1']['after_cost_return'] == expected < row['t1']['raw_return']
    assert report['result']['summary']['t1']['stock_win_rate'] == 1
    assert report['result']['benchmark_return'] is None
    assert _evaluate(scenario) == report


def test_missing_future_and_asof_cutoff_are_pending_not_zero(scenario):
    pending = _evaluate(scenario, forward_datasets={})
    assert pending['result']['constituents'][0]['t1']['status'] == 'pending_entry'
    assert pending['summary']['t1']['raw_return'] is None
    assert pending['summary']['t1']['stock_win_rate'] is None
    early = _evaluate(scenario, as_of_date='2025-01-13')
    row = early['result']['constituents'][0]
    assert row['t1']['status'] == 'pending_exit' and row['t1']['raw_return'] is None
    assert row['t2']['status'] == 'pending_entry' and row['t2']['entry_price'] is None
    assert row['t2']['entry_date'] is None and row['t1']['target_date'] is None
    # The old runtime's holding calculation ignored valuation_cutoff here.
    assert _legacy_detail(_bars() + _future(), as_of_date='2025-01-13').constituents[0].return_pct_holding == 0.25
    assert early['id'] != pending['id']


def test_same_day_raw_return_is_not_a_sellable_cost_estimate_and_overnights_are_explicit(scenario):
    factory, directory, _signal, basket, _original, _forward = scenario
    updated = _write(factory, lambda session: update_basket(session, directory, basket['id'], {
        'expected_revision': 1, 'holding_bars': 1}))
    first = _evaluate(scenario, expected_revision=updated['revision'])['result']['constituents'][0]
    assert first['t1']['status'] == 'untradeable_same_day'
    assert first['t1']['raw_return'] == 0.1 and first['t1']['after_cost_return'] is None
    assert first['t2']['status'] == 'target_before_entry'
    updated = _write(factory, lambda session: update_basket(session, directory, basket['id'], {
        'expected_revision': 2, 'holding_mode': 'complete_overnights'}))
    second = _evaluate(scenario, expected_revision=updated['revision'])['result']['constituents'][0]
    assert second['t1']['status'] == second['t2']['status'] == 'completed'
    assert second['t1']['target_date'] == '2025-01-14'
    assert second['t2']['target_date'] == '2025-01-15'


def test_forward_history_or_symbol_changes_are_rejected_and_late_entry_not_shifted(scenario):
    factory, directory, _signal, _basket, _original, _forward = scenario
    changed = _bars() + _future()
    changed[0]['open'] = '10.0500'
    bad = _import(factory, directory, '600000', changed)
    with pytest.raises(TradeError) as raised:
        _evaluate(scenario, forward_datasets={'600000': bad})
    assert raised.value.code == 'BASKET_HISTORY_CHANGED'
    foreign = _import(factory, directory, '600001', _bars() + _future())
    with pytest.raises(TradeError) as raised:
        _evaluate(scenario, forward_datasets={'sh600000': foreign})
    assert raised.value.code == 'BASKET_DATASET_SYMBOL_MISMATCH'
    late = _bars() + _future()
    late[45]['available_at'] = '2025-01-17T08:00:00+00:00'
    late_id = _import(factory, directory, '600000', late)
    report = _evaluate(scenario, forward_datasets={'sh600000': late_id})
    case = report['result']['constituents'][0]['t1']
    assert case['entry_date'] == '2025-01-13' and case['entry_price'] is None
    assert case['status'] == 'pending_entry'


@pytest.mark.parametrize('changed', [{'amount': '0.00'}, {'available_at': '2024-11-27T09:00:00Z'}])
def test_forward_prefix_preserves_amount_and_when_history_became_known(scenario, changed):
    factory, directory, _signal, _basket, _original, _forward = scenario
    bars = _bars() + _future()
    assert bars[0]['event_date'] == '2024-11-27'
    bars[0].update(changed)
    altered = _import(factory, directory, '600000', bars)
    with pytest.raises(TradeError) as raised:
        _evaluate(scenario, forward_datasets={'sh600000': altered})
    assert raised.value.code == 'BASKET_HISTORY_CHANGED'


def test_evaluation_cannot_precede_shanghai_signal_decision(scenario):
    factory, _directory, _signal, _basket, _original, _forward = scenario
    with pytest.raises(TradeError) as raised:
        _evaluate(scenario, as_of_date='2025-01-10')
    assert raised.value.code == 'BASKET_EVALUATION_BEFORE_SIGNAL'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SignalBasketEvaluation)) == 0
    allowed = _evaluate(scenario, as_of_date='2025-01-11')
    assert allowed['result']['constituents'][0]['t1']['status'] == 'pending_entry'


def test_decision_date_prevents_backdated_entry_for_old_saved_record(scenario):
    factory, directory, signal, _basket, original, forward = scenario
    with factory.begin() as session:
        session.add(ResearchRun(id='a' * 64, dataset_id=original, strategy_id=signal['strategy_id'],
                                strategy_version=signal['strategy_version'], decision_at='2025-01-13T18:00:00Z',
                                strict=1, params_json='{}', result_json=json.dumps(signal['result']),
                                created_at='2025-01-13T18:00:01Z'))
    later = _write(factory, lambda session: create_basket(session, directory, {
        'name': '迟确认旧记录', 'run_ids': ['a' * 64], 'holding_bars': 1, 'holding_mode': 'complete_overnights'}))
    # Prefix equality requires the original signal dataset to include every bar
    # already available by the later decision. A forward dataset cannot quietly
    # change that old record's known history.
    with pytest.raises(TradeError) as raised:
        _write(factory, lambda session: evaluate_basket(session, directory, later['id'], {
            'expected_revision': 1, 'as_of_date': '2025-01-16', 'forward_datasets': {'sh600000': forward}}))
    assert raised.value.code == 'BASKET_HISTORY_CHANGED'
    assert later['constituents'][0]['anchor_date'] == '2025-01-14'
    # A complete original history is valid; the delayed confirmation must move
    # T+1 beyond Jan 14 even though the saved raw event occurred on Jan 10.
    with factory.begin() as session:
        session.add(ResearchRun(id='b' * 64, dataset_id=forward, strategy_id=signal['strategy_id'],
                                strategy_version=signal['strategy_version'], decision_at='2025-01-13T18:00:00Z',
                                strict=1, params_json='{}', result_json=json.dumps(signal['result']),
                                created_at='2025-01-13T18:00:01Z'))
    complete = _write(factory, lambda session: create_basket(session, directory, {
        'name': '完整旧记录', 'run_ids': ['b' * 64], 'holding_bars': 1, 'holding_mode': 'complete_overnights'}))
    report = _write(factory, lambda session: evaluate_basket(session, directory, complete['id'], {
        'expected_revision': 1, 'as_of_date': '2025-01-16'}))
    case = report['result']['constituents'][0]['t1']
    assert case['entry_date'] == '2025-01-15' and case['target_date'] == '2025-01-16'
    assert case['status'] == 'completed'


def test_manual_duplicates_negative_signals_and_config_bounds(scenario):
    factory, directory, signal, _basket, original, _forward = scenario
    alias_id = _import(factory, directory, 'SH600000', _bars())
    alias = _write(factory, lambda session: create_run(session, directory, {
        'dataset_id': alias_id, 'strategy_id': signal['strategy_id'], 'decision_at': signal['decision_at'],
        'strict': True, 'params': {}}))
    with pytest.raises(TradeError) as duplicate:
        _write(factory, lambda session: create_basket(session, directory, {
            'name': '重复证券', 'run_ids': [signal['id'], alias['id']]}))
    assert duplicate.value.code == 'DUPLICATE_BASKET_SYMBOL'
    negative = _write(factory, lambda session: create_run(session, directory, {
        'dataset_id': original, 'strategy_id': signal['strategy_id'], 'decision_at': signal['decision_at'],
        'strict': True, 'params': {'min_ret40': '1.5'}}))
    assert negative['result']['signal'] is False
    with pytest.raises(TradeError) as rejected:
        _write(factory, lambda session: create_basket(session, directory, {
            'name': '负信号', 'run_ids': [negative['id']]}))
    assert rejected.value.code == 'BASKET_SIGNAL_NOT_POSITIVE'
    for config in ({'holding_bars': 0}, {'holding_bars': 61}, {'quantity': 101},
                   {'quantity': True}, {'holding_mode': 'unknown'}, {'fees': {}}):
        with pytest.raises(TradeError):
            normalize_config(config)


def test_scan_auto_creation_freezes_members_and_provenance(scenario):
    factory, directory, _signal, _basket, original, _forward = scenario
    scan = _write(factory, lambda session: create_scan(session, directory, {
        'dataset_ids': [original], 'strategies': [{'strategy_id': 'relative_strength_breakout_v1'}],
        'as_of_date': '2025-01-10', 'strict': True}))
    auto = _write(factory, lambda session: create_basket(session, directory, {
        'name': '扫描自动篮子', 'scan_id': scan['id'], 'selection': 'union'}))
    assert auto['source']['kind'] == 'scan' and auto['source']['scan_id'] == scan['id']
    assert auto['total_constituents'] == 1
    assert auto['constituents'][0]['run_ids'] == scan['result']['union'][0]['run_ids']
    assert auto['source']['groups'] == scan['result']['union']
    with pytest.raises(TradeError):
        _write(factory, lambda session: create_basket(session, directory, {
            'name': '空交集', 'scan_id': scan['id'], 'selection': 'intersection'}))


def test_scan_intersection_merges_strategy_evidence_without_double_weighting(scenario):
    from test_strategy_scans import _bars as multi_strategy_bars

    factory, directory, _signal, _basket, _original, _forward = scenario
    original = _import(factory, directory, '600001', multi_strategy_bars(wulong=True))
    scan = _write(factory, lambda session: create_scan(session, directory, {
        'dataset_ids': [original], 'strategies': [{'strategy_id': 'relative_strength_breakout_v1'},
                                                {'strategy_id': 'wulong_cluster_v1'}],
        'as_of_date': '2025-03-29', 'strict': True}))
    auto = _write(factory, lambda session: create_basket(session, directory, {
        'name': '多策略同股交集', 'scan_id': scan['id'], 'selection': 'intersection'}))
    assert auto['total_constituents'] == 1
    assert len(auto['constituents'][0]['signals']) == 2
    assert set(auto['constituents'][0]['run_ids']) == set(scan['result']['intersection'][0]['run_ids'])
    assert auto['strategy_ids'] == ['relative_strength_breakout_v1', 'wulong_cluster_v1']
    report = _write(factory, lambda session: evaluate_basket(session, directory, auto['id'], {
        'expected_revision': 1, 'as_of_date': '2025-03-29'}))
    assert report['summary']['t1']['total_constituents'] == 1
    assert report['summary']['t1']['pending_count'] == 1


def test_history_revision_audit_atomic_delete_and_reopen(scenario):
    factory, directory, signal, basket, _original, _forward = scenario
    report = _evaluate(scenario)
    updated = _write(factory, lambda session: update_basket(session, directory, basket['id'], {
        'expected_revision': 1, 'name': '改名篮子', 'notes': '留存旧评估'}))
    assert updated['revision'] == 2
    with pytest.raises(TradeError) as stale:
        _evaluate(scenario)
    assert stale.value.code == 'REVISION_CONFLICT'
    other = _write(factory, lambda session: create_basket(session, directory, {
        'name': '批量第二篮子', 'run_ids': [signal['id']]}))
    with factory.begin() as session:
        with pytest.raises(TradeError):
            delete_baskets(session, {'items': [{'id': basket['id'], 'expected_revision': 2},
                                               {'id': other['id'], 'expected_revision': 9}]})
        assert not get_basket(session, basket['id'])['deleted']
        assert len(list_baskets(session)) == 2
    _write(factory, lambda session: delete_baskets(session, {'items': [
        {'id': basket['id'], 'expected_revision': 2}, {'id': other['id'], 'expected_revision': 1}]}))
    engine, reopened = open_database(directory)
    try:
        with reopened() as session:
            assert list_baskets(session) == []
            assert len(list_baskets(session, include_deleted=True)) == 2
            assert get_basket(session, basket['id'], include_deleted=True)['deleted'] is True
            assert get_evaluation(session, basket['id'], report['id']) == report
            assert list_evaluations(session, basket['id'])[0]['basket_revision'] == 1
            assert [item['action'] for item in list_audit(session, basket['id'])] == ['create', 'evaluate', 'update', 'delete']
            assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
            assert session.scalar(select(func.count()).select_from(SignalBasketEvaluation)) == 1
    finally:
        engine.dispose()
