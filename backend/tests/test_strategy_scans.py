from datetime import date, timedelta
import json

import pytest
from sqlalchemy import func, select, text

from trade_app.market.service import import_dataset
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.models import ResearchRun
from trade_app.research.scan_models import StrategyScanRun
from trade_app.research.scan_service import create_scan, delete_scan, get_scan, list_scans


RELATIVE = 'relative_strength_breakout_v1'
WULONG = 'wulong_cluster_v1'


def _bars(wulong=False):
    closes = ([round(8 + index * 2 / 59 - (0.1 if index % 7 == 3 else 0), 4)
               for index in range(60)] + [10] * 25 + [10.3, 10.6, 10.9]
              if wulong else [10 + index * 0.2 for index in range(88)])
    bars = []
    for index, close in enumerate(closes):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        volume = (3000 if index == 87 else 1200 if index == 0 or close >= closes[index - 1] else 1000)
        if not wulong:
            volume = 1000 + index * 30
        bars.append({'event_date': day, 'open': f'{close - 0.05:.4f}',
                     'high': f'{close + 0.1:.4f}', 'low': f'{close - 0.1:.4f}',
                     'close': f'{close:.4f}', 'volume': volume,
                     'available_at': day + 'T08:00:00+00:00'})
    return bars


def _import(factory, data_dir, symbol, bars):
    with factory.begin() as session:
        return import_dataset(session, data_dir, {'symbol': symbol, 'bars': bars, 'adjustment': 'none'})['id']


def _create(factory, data_dir, body):
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        return create_scan(session, data_dir, body)


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        both = _import(factory, tmp_path, '600000', _bars(wulong=True))
        relative = _import(factory, tmp_path, '600001', _bars())
        yield factory, tmp_path, {'dataset_ids': [both, relative],
                                'strategies': [{'strategy_id': RELATIVE, 'params': {}},
                                               {'strategy_id': WULONG, 'params': {}}],
                                'as_of_date': '2025-03-29', 'strict': True}
    finally:
        engine.dispose()


def test_real_multi_strategy_union_intersection_and_canonical_identity(scenario):
    factory, data_dir, body = scenario
    result = _create(factory, data_dir, body)
    assert result['dataset_count'] == 2 and result['strategy_count'] == 2
    assert result['evaluation_count'] == 4 and result['signal_count'] == 3
    assert result['union_count'] == 2 and result['intersection_count'] == 1
    assert result['result']['intersection'][0]['symbol'] == 'sh600000'
    assert result['result']['intersection'][0]['strategy_ids'] == [RELATIVE, WULONG]
    assert all(row['decision_at'] == '2025-03-29T15:59:59+00:00' for row in result['result']['rows'])
    assert len(result['result']['run_ids']) == len(set(result['result']['run_ids'])) == 4
    assert all(row['fresh_source'] for row in result['result']['rows'])
    reversed_request = {**body, 'dataset_ids': list(reversed(body['dataset_ids'])),
                        'strategies': list(reversed(body['strategies']))}
    assert _create(factory, data_dir, reversed_request) == result
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(StrategyScanRun)) == 1
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 4
        for row in result['result']['rows']:
            source = session.get(ResearchRun, row['run_id'])
            source_result = json.loads(source.result_json)
            assert row['run_signal'] is source_result['signal']
            assert row['code_sha256'] == source_result['code_sha256']


def test_range_preserves_actual_appearance_dates_and_same_day_intersection(scenario):
    factory, data_dir, body = scenario
    ranged = {**body, 'as_of_date': None, 'date_from': '2025-03-27', 'date_to': '2025-03-29'}
    scan = _create(factory, data_dir, ranged)
    assert scan['mode'] == 'date_range' and scan['evaluation_count'] == 12
    assert scan['result']['intersection'] == [
        item for item in scan['result']['union'] if item['matched_strategy_count'] == 2]
    assert all(item['decision_date'] == '2025-03-29' for item in scan['result']['intersection'])
    appearances = next(row for row in scan['result']['per_symbol'] if row['symbol'] == 'sh600001')
    relative = next(row for row in appearances['strategies'] if row['strategy_id'] == RELATIVE)
    assert relative['signal_dates'] == ['2025-03-27', '2025-03-28', '2025-03-29']
    assert relative['signal_count'] == relative['evaluation_count'] == 3
    assert all(score['value'] is None for score in relative['scores'])


def test_future_or_late_bars_do_not_create_stale_scan_hits(scenario):
    factory, data_dir, body = scenario
    bars = _bars()
    bars[-1]['available_at'] = '2025-03-30T08:00:00+00:00'
    bars.append({'event_date': '2025-03-30', 'open': '27.4000', 'high': '100.0000',
                 'low': '27.0000', 'close': '99.0000', 'volume': 1000000,
                 'available_at': '2025-03-30T08:00:00+00:00'})
    dataset_id = _import(factory, data_dir, '600002', bars)
    scan = _create(factory, data_dir, {**body, 'dataset_ids': [dataset_id],
                                     'strategies': [{'strategy_id': RELATIVE, 'params': {}}]})
    evidence = scan['result']['rows'][0]
    assert evidence['source_date'] == '2025-03-28'
    assert evidence['run_signal'] is True
    assert evidence['signal'] is False and evidence['fresh_source'] is False
    assert evidence['draft_eligible'] is False
    assert 'SOURCE_DATE_BEFORE_SCAN_DATE' in evidence['quality_flags']
    assert scan['result']['union'] == scan['result']['intersection'] == []
    assert scan['result']['intersection_supported'] is False
    assert scan['signal_count'] == 0


def test_symbol_alias_datasets_are_rejected_before_child_runs(scenario):
    factory, data_dir, body = scenario
    alias = _import(factory, data_dir, '600000.SH', _bars(wulong=True))
    with pytest.raises(TradeError, match='同一证券') as raised:
        _create(factory, data_dir, {**body, 'dataset_ids': [body['dataset_ids'][0], alias]})
    assert raised.value.code == 'DUPLICATE_SCAN_SYMBOL'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


@pytest.mark.parametrize('override,code', [
    ({'strategies': [{'strategy_id': RELATIVE}, {'strategy_id': RELATIVE}]}, 'DUPLICATE_SCAN_STRATEGY'),
    ({'strategies': [{'strategy_id': 'matrix_signal_v1'}]}, 'STRATEGY_NOT_SINGLE_RUN'),
    ({'strategies': [{'strategy_id': 'b1_mtf_v1'}]}, 'STRATEGY_NOT_SINGLE_RUN'),
    ({'strict': 'false'}, 'INVALID_SCAN_STRICT'),
    ({'date_from': '2025-01-01'}, 'INVALID_SCAN_DATE'),
    ({'as_of_date': None, 'date_from': '2025-01-01', 'date_to': '2025-04-01'}, 'INVALID_SCAN_RANGE'),
    ({'as_of_date': None, 'date_from': '2025-03-30', 'date_to': '2025-03-31'}, 'SCAN_RANGE_EMPTY'),
])
def test_invalid_scan_requests_are_rejected(scenario, override, code):
    factory, data_dir, body = scenario
    with pytest.raises(TradeError) as raised:
        _create(factory, data_dir, {**body, **override})
    assert raised.value.code == code


def test_limits_are_checked_before_evaluating_large_range(scenario):
    factory, data_dir, body = scenario
    ten_strategies = [RELATIVE, WULONG, 'ths_force_rhythm_v1', 'ths_main_force_flip_v1',
                      'ths_main_force_golden_cross_v1', 'trend_king_v1', 'trend_king_limitup_v1',
                      'trend_king_rally_v1', 'trend_king_pullback_v1', 'limit_up_arb_v1']
    with pytest.raises(TradeError) as raised:
        _create(factory, data_dir, {**body, 'as_of_date': None, 'date_from': '2025-01-01',
                                   'date_to': '2025-03-29',
                                   'strategies': [{'strategy_id': key} for key in ten_strategies]})
    assert raised.value.code == 'SCAN_TOO_LARGE'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


def test_single_date_cap_and_repeated_dataset_id_are_rejected(scenario):
    factory, data_dir, body = scenario
    with pytest.raises(TradeError) as repeated:
        _create(factory, data_dir, {**body, 'dataset_ids': [body['dataset_ids'][0]] * 2})
    assert repeated.value.code == 'DUPLICATE_SCAN_DATASET'
    datasets = list(body['dataset_ids']) + [
        _import(factory, data_dir, str(600100 + index), _bars()[:2]) for index in range(9)]
    strategies = [RELATIVE, WULONG, 'ths_force_rhythm_v1', 'ths_main_force_flip_v1',
                  'ths_main_force_golden_cross_v1', 'trend_king_v1', 'trend_king_limitup_v1',
                  'trend_king_rally_v1', 'trend_king_pullback_v1', 'limit_up_arb_v1']
    with pytest.raises(TradeError) as oversized:
        _create(factory, data_dir, {**body, 'dataset_ids': datasets,
                                   'strategies': [{'strategy_id': key} for key in strategies]})
    assert oversized.value.code == 'SCAN_TOO_LARGE'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


def test_later_bad_strategy_param_rolls_back_earlier_child_runs(scenario):
    factory, data_dir, body = scenario
    invalid = {**body, 'strategies': [{'strategy_id': RELATIVE},
                                     {'strategy_id': WULONG, 'params': {'min_ret40': '2'}}]}
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        with pytest.raises(TradeError):
            create_scan(session, data_dir, invalid)
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0
        assert session.scalar(select(func.count()).select_from(StrategyScanRun)) == 0


def test_profile_binding_is_frozen_in_request_and_each_research_evidence(scenario):
    factory, data_dir, body = scenario
    with factory() as session:
        expected = freeze_profile(session)
    scan = _create(factory, data_dir, {**body, 'strategies': [
        {'strategy_id': 'wyckoff_trend_v1'}, {'strategy_id': 'score_only_rank_v1'}]})
    assert scan['request']['event_profile'] == expected
    with factory() as session:
        for run_id in scan['result']['run_ids']:
            assert json.loads(session.get(ResearchRun, run_id).result_json)['event_profile'] == expected
    with pytest.raises(TradeError) as raised:
        _create(factory, data_dir, {**body, 'strategies': [{'strategy_id': 'wyckoff_trend_v1'}],
                                   'event_profile_id': expected['profile_id'],
                                   'event_profile_revision': expected['revision'] + 1})
    assert raised.value.code == 'EVENT_PROFILE_VERSION_CONFLICT'


def test_history_survives_reopen_delete_retains_research_evidence(scenario):
    factory, data_dir, body = scenario
    created = _create(factory, data_dir, body)
    engine, reopened_factory = open_database(data_dir)
    try:
        with reopened_factory() as session:
            assert get_scan(session, created['id']) == created
            history = list_scans(session)
            assert len(history) == 1 and history[0]['id'] == created['id']
            assert 'result' not in history[0]
        with reopened_factory.begin() as session:
            deleted = delete_scan(session, created['id'])
            assert deleted['deleted'] is True and deleted['preserved_research_run_count'] == 4
        with reopened_factory() as session:
            assert list_scans(session) == []
            assert session.scalar(select(func.count()).select_from(ResearchRun)) == 4
            with pytest.raises(TradeError) as raised:
                get_scan(session, created['id'])
            assert raised.value.status == 404
    finally:
        engine.dispose()
