"""Catalog consolidation preserves identities and advertises executable paths."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from legacy_oracle import oracle
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError, utc_now
from trade_app.research import registry_service as registry
from trade_app.research.catalog_presentation import present_strategy
from trade_app.research.classic_catalog import classic_catalog
from trade_app.research.registry_models import StrategyRegistrySettings
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES
from trade_app.research.service import strategy_catalog, normalize_strategy_params
from trade_app.research.signal_context import context_catalog, normalize_context_params
from trade_app.research.scan_service import _request


@pytest.fixture
def database(tmp_path):
    engine, factory = open_database(tmp_path)
    yield factory
    engine.dispose()


def test_original_16_identities_and_related_variants_survive(database):
    def legacy_identities():
        from app.core.strategy_registry import StrategyRegistry
        return {row.strategy_id: {'name': row.name, 'version': row.version} for row in StrategyRegistry().list()}
    original = oracle('original_identities', legacy_identities)
    with database() as session:
        rows = {row['id']: row for row in registry.get_registry(session)['strategies']}
    legacy = {key: row for key, row in rows.items() if row['origin'] == 'final_trade'}
    assert len(original) == 16 and set(legacy) == set(original)
    for identity, before in original.items():
        assert (legacy[identity]['name'], legacy[identity]['version']) == (before['name'], before['version'])
        assert legacy[identity]['availability'] == 'available'
        assert 'full_signal_context' in legacy[identity]['execution_paths']
    for prefix, count in [('trend_king', 4), ('wyckoff', 3), ('ths_volume', 2)]:
        members = [row for row in legacy.values() if row['family_id'] == prefix]
        assert len(members) == count
        assert len({row['variant_name'] for row in members}) == count
    # A/B/C remain separate fixed modes, not aliases that overwrite saved IDs.
    for identity, mode in [('trend_king_v1', 'all'), ('trend_king_limitup_v1', 'a'),
                           ('trend_king_rally_v1', 'b'), ('trend_king_pullback_v1', 'c')]:
        assert normalize_strategy_params(identity, {})['mode'] == mode


def test_advertised_paths_follow_actual_runners_and_catalog_is_not_mutated(database):
    before = deepcopy(strategy_catalog())
    with database() as session:
        rows = registry.get_registry(session)['strategies']
    single = {row['id'] for row in rows if 'single_backtest' in row['execution_paths']}
    assert single == set(SINGLE_SYMBOL_STRATEGIES)
    full = {row['id'] for row in rows if row['current_capabilities']['full_signal_context']}
    assert full == {row['id'] for row in context_catalog()}
    for row in rows:
        if row['id'] in ('b1_mtf_v1', 'matrix_signal_v1'):
            assert row['execution_entry'] != 'single_symbol'
            assert not row['current_capabilities']['single_backtest']
        if row['origin'] == 'final_trade':
            assert not any('待迁移' in text or '尚待' in text for text in row['limitations'])
            assert row['legacy_limitations']
    rows[0]['params_schema'].clear()
    rows[0]['capabilities']['fake'] = True
    assert strategy_catalog() == before
    unknown = present_strategy({**before[0], 'id': 'future_not_implemented'}, single_ids=set(), context_ids=set())
    assert unknown['availability'] == 'unavailable'
    assert unknown['execution_entry'] == 'unavailable' and unknown['execution_paths'] == []


def test_new_install_enables_classics_but_persisted_choices_are_not_expanded(database):
    classic_ids = {row['id'] for row in classic_catalog()}
    legacy_ids = {row['id'] for row in strategy_catalog() if row['enabled_in_legacy']}
    with database.begin() as session:
        fresh = registry.get_registry(session)
        assert classic_ids <= set(fresh['enabled_ids'])
        assert fresh['catalog_counts'] == {'total': 19, 'legacy': 16, 'classic': 3}
        saved = {'enabled_ids': sorted(legacy_ids), 'default_strategy_id': 'wyckoff_trend_v1'}
        session.add(StrategyRegistrySettings(id='global', revision=7, settings_json=json.dumps(saved), updated_at=utc_now()))
        session.flush()
        current = registry.get_registry(session)
        assert current['revision'] == 7 and set(current['enabled_ids']) == legacy_ids
        assert classic_ids <= set(current['defaults']['enabled_ids'])
        with pytest.raises(TradeError) as error:
            registry.require_enabled(session, sorted(classic_ids))
        assert error.value.code == 'STRATEGY_DISABLED'
        body = {**current['defaults'], 'expected_revision': 7}
        preview = registry.preview_registry(session, body)
        assert {row['strategy_id'] for row in preview['diff']} == classic_ids
        assert set(registry.get_registry(session)['enabled_ids']) == legacy_ids
        after = registry.apply_registry(session, {**body, 'expected_preview_sha256': preview['preview_sha256']})
        assert classic_ids <= set(after['enabled_ids']) and after['revision'] == 8


@pytest.mark.parametrize('identity', [row['id'] for row in classic_catalog()])
def test_new_single_strategy_cannot_masquerade_as_old_full_context(identity):
    assert identity not in {row['id'] for row in context_catalog()}
    body = {'dataset_ids': ['frozen-dataset'], 'strategies': [{'strategy_id': identity, 'params': {}}],
            'as_of_date': '2025-01-02', 'strict': True}
    assert _request(body)['strategies'][0]['strategy_id'] == identity
    with pytest.raises(TradeError) as error:
        _request({**body, 'signal_context': {'candidate_path': 'legacy_store', 'window_days': 60}})
    assert error.value.code == 'SIGNAL_CONTEXT_UNSUPPORTED' and error.value.status == 409
    with pytest.raises(TradeError) as normalized:
        normalize_context_params(identity, {})
    assert normalized.value.code == 'SIGNAL_CONTEXT_UNSUPPORTED'


def test_changed_executable_defaults_invalidate_registry_preview(database, monkeypatch):
    with database.begin() as session:
        current = registry.get_registry(session)
        body = {**current['defaults'], 'expected_revision': 0}
        preview = registry.preview_registry(session, body)
        changed = registry._catalog()
        item = next(row for row in changed if row['id'] == 'trend_king_v1')
        item['signal_params']['min_hist'] = '31'  # Schema/revision unchanged; executable base differs.
        monkeypatch.setattr(registry, '_catalog', lambda: changed)
        with pytest.raises(TradeError) as error:
            registry.apply_registry(session, {**body, 'expected_preview_sha256': preview['preview_sha256']})
        assert error.value.code == 'STRATEGY_REGISTRY_PREVIEW_CHANGED'
        assert registry.get_registry(session)['revision'] == 0


def test_revision_types_and_invalid_preview_are_explicit_errors(database):
    with database() as session:
        for body in (None, [], 'settings'):
            with pytest.raises(TradeError) as error:
                registry.preview_registry(session, body)
            assert error.value.code == 'INVALID_STRATEGY_REGISTRY'
        with pytest.raises(TradeError) as error:
            registry.require_enabled(session, ['wyckoff_trend_v1'], expected_revision=False)
        assert error.value.code == 'STRATEGY_REGISTRY_CONFLICT'


def test_registry_and_scan_api_expose_current_paths_and_reject_wrong_adapter(tmp_path):
    from trade_app.main import create_app
    from test_trade_rebuild import started_client
    with started_client(create_app(tmp_path, auto_rebuild=False)) as client:
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        rows = client.get('/api/v1/research/strategies').json()['data']
        classic = next(row for row in rows if row['id'] == 'classic_sma_trend_v1')
        assert classic['origin'] == 'classic_reference' and classic['current_capabilities']['single_backtest']
        assert not classic['current_capabilities']['full_signal_context']
        response = client.post('/api/v1/research/scan-jobs', json={'dataset_ids': ['missing'],
            'strategies': [{'strategy_id': classic['id'], 'params': {}}], 'as_of_date': '2025-01-02',
            'signal_context': {'candidate_path': 'legacy_store', 'window_days': 60}},
            headers={'X-CSRF-Token': token, 'Idempotency-Key': 'unsupported-classic-context'})
        assert response.status_code == 409, response.text
        assert response.json()['error']['code'] == 'SIGNAL_CONTEXT_UNSUPPORTED'


def test_generic_scan_api_preserves_full_context_and_rejects_silent_typos(tmp_path, monkeypatch):
    from trade_app.main import create_app
    from test_matrix_pool_api import started_client, writer, data
    from test_strategy_scans import _bars
    from test_strategy_scan_jobs import inline_child, finish
    app = create_app(tmp_path, auto_rebuild=False)
    with started_client(app) as client:
        post = writer(client)
        dataset = data(post('/api/v1/market/datasets', {'symbol': '600000', 'bars': _bars(wulong=True), 'adjustment': 'none'}))
        body = {'dataset_ids': [dataset['id']], 'strategies': [{'strategy_id': 'wulong_cluster_v1', 'params': {'min_score': 0, 'require_sequence': False}}],
            'as_of_date': '2025-03-29', 'strict': True,
            'signal_context': {'candidate_path': 'legacy_store', 'window_days': 40, 'universe_mode': 'full_market'}}
        endpoint = '/api/v1/research/scan-jobs'
        queued = data(post(endpoint, body))
        inline_child(monkeypatch)
        completed = finish(app.state.db_factory, tmp_path, queued['id'])
        assert completed['state'] == 'succeeded', completed
        scan = data(client.get('/api/v1/research/scans/' + completed['scan_id']))
        assert scan['request']['signal_context'] == body['signal_context']
        run = data(client.get('/api/v1/research/runs/' + scan['result']['run_ids'][0]))
        assert run['result']['signal_context'] == body['signal_context']
        assert run['result']['universe']['applied'] is False
        assert run['params']['require_sequence'] is False
        for bad in ({**body, 'candidate_path': 'legacy_tdx'},
                    {**body, 'signal_context': {**body['signal_context'], 'window_days': True}},
                    {**body, 'signal_context': {**body['signal_context'], 'universe_mod': 'full_market'}},
                    {**body, 'strategies': [{**body['strategies'][0], 'window_days': 20}]},
                    {**body, 'strict': 'false'}):
            assert post(endpoint, bad).status_code == 422


def test_legacy_manifest_is_still_original_only():
    path = Path(__file__).parents[1] / 'trade_app/research/legacy_catalog.json'
    rows = json.loads(path.read_text(encoding='utf-8'))['strategies']
    assert len(rows) == 16 and all(not row['strategy_id'].startswith('classic_') for row in rows)
