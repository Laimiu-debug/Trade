import json

import pytest
from sqlalchemy import func, select

from trade_app.market.service import import_dataset
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research import registry_service as registry, scan_job_service as scans
from trade_app.research import b1_service, preset_service
from trade_app.research.backtest_service import create_backtest, process_one_backtest, get_backtest
from trade_app.research.b1_params import DEFAULTS, normalize_b1_params
from trade_app.research.models import ResearchRun
from trade_app.research.registry_models import StrategyRegistrySettings
from trade_app.research.service import create_run, get_run
from trade_app.research.tdx_universe import create_universe_job
from test_strategy_scans import _bars

STRATEGY = 'wulong_cluster_v1'


@pytest.fixture
def scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    with factory.begin() as session:
        dataset = import_dataset(session, tmp_path, {'symbol': '600000', 'adjustment': 'none', 'bars': _bars(wulong=True)})
    yield factory, tmp_path, dataset['id']
    engine.dispose()


def change(session, disabled=(), default=None):
    current = registry.get_registry(session)
    enabled = [item for item in current['enabled_ids'] if item not in disabled]
    body = {'enabled_ids': enabled, 'default_strategy_id': default or current['default_strategy_id'], 'expected_revision': current['revision']}
    preview = registry.preview_registry(session, body)
    return registry.apply_registry(session, {**body, 'expected_preview_sha256': preview['preview_sha256']})


def test_readonly_defaults_explicit_preview_conflict_restore_and_history(scenario):
    factory, path, _dataset_id = scenario
    with factory.begin() as session:
        current = registry.get_registry(session)
        assert current['revision'] == 0 and current['enabled_ids'] == current['defaults']['enabled_ids']
        assert current['catalog_counts']['legacy'] == 16
        assert current['default_strategy_id'] == 'wyckoff_trend_v1'
        assert session.scalar(select(func.count()).select_from(StrategyRegistrySettings)) == 0
        body = {'enabled_ids': [STRATEGY], 'default_strategy_id': STRATEGY, 'expected_revision': 0}
        preview = registry.preview_registry(session, body)
        assert len(preview['diff']) == len(current['enabled_ids']) - 1
        assert session.scalar(select(func.count()).select_from(StrategyRegistrySettings)) == 0
        with pytest.raises(TradeError, match='确认'): registry.apply_registry(session, body)
        saved = registry.apply_registry(session, {**body, 'expected_preview_sha256': preview['preview_sha256']})
        assert saved['revision'] == 1
        with pytest.raises(TradeError, match='版本'): registry.apply_registry(session, {**body, 'expected_preview_sha256': preview['preview_sha256']})
        default = {**saved['defaults'], 'expected_revision': 1}
        reset = registry.preview_registry(session, default)
        registry.apply_registry(session, {**default, 'expected_preview_sha256': reset['preview_sha256']})
        assert registry.get_registry(session)['revision'] == 2
        assert [item['revision'] for item in registry.registry_history(session)] == [2, 1]
    another_engine, another_factory = open_database(path)
    try:
        with another_factory() as session: assert registry.get_registry(session)['revision'] == 2
    finally: another_engine.dispose()


@pytest.mark.parametrize('ids,default', [(['unknown'], 'unknown'), ([STRATEGY, STRATEGY], STRATEGY), ([{}], None), ([STRATEGY], 'wyckoff_trend_v1'), ([], STRATEGY)])
def test_invalid_settings_never_apply(scenario, ids, default):
    with scenario[0].begin() as session:
        with pytest.raises(TradeError): registry.preview_registry(session, {'enabled_ids': ids, 'default_strategy_id': default, 'expected_revision': 0})
        assert registry.get_registry(session)['revision'] == 0


def test_new_runs_and_universe_submission_block_but_old_results_remain_readable(scenario):
    factory, path, dataset_id = scenario
    body = {'dataset_id': dataset_id, 'strategy_id': STRATEGY, 'decision_at': '2025-03-29T09:00:00Z', 'strict': True, 'params': {}}
    with factory.begin() as session:
        saved = create_run(session, path, body)
        assert saved['result']['signal']
        change(session, [STRATEGY, 'matrix_signal_v1', 'b1_mtf_v1'])
        with pytest.raises(TradeError) as error: create_run(session, path, body)
        assert error.value.code == 'STRATEGY_DISABLED'
        assert get_run(session, saved['id']) == saved
        from trade_app.research.pool_service import create_matrix_run
        with pytest.raises(TradeError, match='已停用'): create_matrix_run(session, {'source_run_id': 'unused'})
        with pytest.raises(TradeError, match='已停用'): b1_service.prepare_b1_run(session, path, {'dataset_ids': [dataset_id], 'as_of_date': '2025-03-29', 'b1_params': {}})
        with pytest.raises(TradeError, match='已停用'): create_universe_job(session, {'request': {'kind': 'b1'}})
        # Parameter editing is metadata and remains available when execution is disabled.
        assert preset_service.save_preset(session, {'name': '停用后仍可编辑', 'strategy_id': STRATEGY, 'params': {}})['params']


def test_queued_scan_finishes_and_cancelled_scan_resumes_after_disable(scenario):
    factory, path, dataset_id = scenario
    body = {'dataset_ids': [dataset_id], 'strategies': [{'strategy_id': STRATEGY, 'params': {}}], 'as_of_date': '2025-03-29', 'strict': True}
    with factory() as session: prepared = scans.prepare_scan_job(session, path, body)
    with factory.begin() as session:
        job = scans.enqueue_scan_job(session, prepared)
        scans.cancel_scan_job(session, job['id'])
        change(session, [STRATEGY])
        scans.retry_scan_job(session, job['id'])
    assert scans.process_one_scan_chunk(factory, path)
    with factory() as session:
        result = scans.get_scan_job(session, job['id'])
        assert result['state'] == 'succeeded', result
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        with pytest.raises(TradeError, match='停用'): scans.prepare_scan_job(session, path, body)


def test_preview_admission_revision_rechecked_at_scan_and_user_b1_save(scenario):
    factory, path, dataset_id = scenario
    body = {'dataset_ids': [dataset_id], 'strategies': [{'strategy_id': STRATEGY, 'params': {}}], 'as_of_date': '2025-03-29', 'strict': True}
    with factory() as session:
        prepared = scans.prepare_scan_job(session, path, body)
        b1 = b1_service.prepare_b1_run(session, path, {'dataset_ids': [dataset_id], 'as_of_date': '2025-03-29', 'b1_params': {}})
    with factory.begin() as session:
        change(session, [], default=STRATEGY)
        with pytest.raises(TradeError, match='设置已变化'): scans.enqueue_scan_job(session, prepared)
        with pytest.raises(TradeError, match='设置已变化'): b1_service.save_user_b1_run(session, b1)
        # TDX internal publication is a separate trusted boundary, unaffected by new admission policy.
        change(session, ['b1_mtf_v1'])
        assert b1_service.save_b1_run(session, b1)['total_scanned'] == 1


def test_queued_backtest_runs_with_frozen_admission_after_disable(scenario):
    factory, path, dataset_id = scenario
    body = {'dataset_id': dataset_id, 'strategy_id': STRATEGY, 'initial_capital': '10000', 'max_position_pct': '.2', 'holding_bars': 2, 'strict': True, 'params': {}}
    with factory.begin() as session:
        job = create_backtest(session, body)
        change(session, [STRATEGY])
        with pytest.raises(TradeError, match='停用'): create_backtest(session, body)
    assert process_one_backtest(factory, path)
    with factory() as session:
        result = get_backtest(session, job['id'])
        assert result['state'] == 'succeeded', result['error']
        assert result['config']['registry_admission']['revision'] == 0


def test_b1_preset_share_diff_scanner_normalizer_and_readonly_metadata(scenario):
    factory, path, dataset_id = scenario
    from trade_app.api.schemas import B1ParamsInput
    from pydantic import ValidationError
    with factory.begin() as session:
        raw = {'vol_ratio': '.6000', 'amp_limit_20cm': 9}
        preset = preset_service.save_preset(session, {'name': 'B1 参数验证', 'strategy_id': 'b1_mtf_v1', 'params': raw})
        assert preset['params'] == {'vol_ratio': '0.6', 'chg_limit': '3', 'amp_limit_10cm': '5', 'amp_limit_20cm': '9', 'kdj_j_upper': '50'}
        code = preset_service.export_preset(session, preset['id'])['share_code']
        preview = preset_service.preview_import(session, {'share_code': code, 'current_params': {}})
        assert {row['parameter'] for row in preview['diff']} == {'vol_ratio', 'amp_limit_20cm'}
        scan = b1_service.prepare_b1_run(session, path, {'dataset_ids': [dataset_id], 'as_of_date': '2025-03-29', 'b1_params': preview['params']})
        assert scan['request']['b1_params'] == normalize_b1_params(raw) == B1ParamsInput.model_validate(raw).model_dump()
        descriptor = next(row for row in registry.get_registry(session)['strategies'] if row['id'] == 'b1_mtf_v1')
        assert descriptor['params_schema']['min_score']['readonly']
        assert set(descriptor['scanner_params']) == set(DEFAULTS)
        for invalid in ({'min_score': 40}, {'min_total_bars': 1}, {'vol_ratio': True}, {'vol_ratio': 'NaN'}, {'vol_ratio': 99}, {'unknown': 1}):
            with pytest.raises(TradeError): normalize_b1_params(invalid)
            with pytest.raises(ValidationError): B1ParamsInput.model_validate(invalid)
            with pytest.raises(TradeError): preset_service.normalize_preset_params('b1_mtf_v1', invalid)


def test_registry_api_preset_import_and_disabled_b1_submission(scenario):
    from trade_app.main import create_app
    from test_trade_rebuild import started_client
    _factory, path, dataset_id = scenario
    with started_client(create_app(path, auto_rebuild=False)) as client:
        csrf = client.get('/api/v1/session').json()['data']['csrf_token']
        serial = 0
        def write(path, body, method='POST', status=200):
            nonlocal serial
            serial += 1
            response = client.request(method, '/api/v1' + path, json=body,
                headers={'X-CSRF-Token': csrf, 'Idempotency-Key': f'registry-{serial}'})
            assert response.status_code == status, response.text
            return response.json().get('data', response.json())
        current = client.get('/api/v1/research/registry').json()['data']
        body = {'enabled_ids': [item for item in current['enabled_ids'] if item != 'b1_mtf_v1'],
                'default_strategy_id': STRATEGY, 'expected_revision': 0}
        preview = write('/research/registry/preview', body)
        saved = write('/research/registry', {**body, 'expected_preview_sha256': preview['preview_sha256']}, method='PUT')
        assert saved['revision'] == 1
        catalog = client.get('/api/v1/research/strategies').json()['data']
        assert next(item for item in catalog if item['id'] == STRATEGY)['default_in_rebuild']
        assert not next(item for item in catalog if item['id'] == 'b1_mtf_v1')['enabled_in_rebuild']
        rejected = write('/research/b1-runs', {'dataset_ids': [dataset_id], 'as_of_date': '2025-03-29', 'b1_params': {}}, status=409)
        assert rejected['error']['code'] == 'STRATEGY_DISABLED'
        write('/research/b1-runs', {'dataset_ids': [dataset_id], 'as_of_date': '2025-03-29', 'b1_params': {'min_score': 99}}, status=422)
        preset = write('/research/presets', {'name': 'B1 分享验证', 'strategy_id': 'b1_mtf_v1', 'params': {'vol_ratio': '.6'}})
        exported = client.get('/api/v1/research/presets/' + preset['id'] + '/export').json()['data']
        imported = write('/research/presets/preview-import', {'share_code': exported['share_code'], 'current_params': {}})
        assert imported['diff'] == [{'parameter': 'vol_ratio', 'before': '0.8', 'after': '0.6'}]
        assert client.get('/api/v1/research/registry/history').json()['data'][0]['revision'] == 1
