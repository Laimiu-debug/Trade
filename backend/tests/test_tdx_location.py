from pathlib import Path

import pytest

from trade_app import main as main_module
from trade_app.api import provider_health_routes
from trade_app.main import create_app
from trade_app.market.tdx import DAY_RECORD
from trade_app.market.tdx_location import inspect_location, scan_locations
from trade_app.market.tdx_location import source_fingerprint
from trade_app.research.tdx_universe import process_one_universe_symbol, prepare_ladder_universe_job, create_universe_job
from trade_app.platform.types import TradeError, utc_now
from trade_app.research.tdx_universe_models import TdxUniverseJob
from test_matrix_pool_api import started_client
from test_wyckoff_research_api import client_for, data, write


def installation(root):
    directory = root / 'vipdoc/sh/lday'; directory.mkdir(parents=True)
    contents = b''.join(DAY_RECORD.pack(day, 1000, 1100, 900, 1050, 100000.0, 10000, 0)
                        for day in (20250102, 20250103))
    (directory / 'sh600000.day').write_bytes(contents)
    return root


def authorize(client):
    client.headers['X-CSRF-Token'] = client.get('/api/v1/session').json()['data']['csrf_token']


def test_bounded_discovery_finds_multiple_without_searching_deep_folders(tmp_path):
    first = installation(tmp_path / 'TDX')
    second = installation(tmp_path / 'broker-terminal')
    installation(tmp_path / 'arbitrary/nested/TDX')
    result = scan_locations([tmp_path])
    assert {row['path'] for row in result['candidates']} == {str(first), str(second)}
    assert len({row['vipdoc'] for row in result['candidates']}) == 2
    assert inspect_location(str(first / 'vipdoc'))['markets'] == ['sh']
    assert result['checked_folders'] <= 512


@pytest.mark.parametrize('kind', ['missing', 'relative', 'empty', 'wrong_level'])
def test_invalid_selection_explains_directory_error(tmp_path, kind):
    root = installation(tmp_path / 'TDX')
    target = {'missing': str(tmp_path / 'missing'), 'relative': 'TDX', 'empty': '',
              'wrong_level': str(root / 'vipdoc/sh/lday')}[kind]
    with pytest.raises(TradeError):
        inspect_location(target)


def test_selection_updates_probe_and_import_but_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.delenv('TRADE_TDX_ROOT', raising=False)
    tdx = installation(tmp_path / 'TDX')
    source = tdx / 'vipdoc/sh/lday/sh600000.day'; before = source.read_bytes()
    home = tmp_path / 'app'
    with client_for(home) as client:
        assert data(client.get('/api/v1/market/providers/tdx/locations'))['current_path'] is None
        selected = data(write(client, '/market/providers/tdx/select', {'path': str(tdx)}))
        assert selected['current_path'] == str(tdx) and selected['selection'] == 'manual'
        assert selected['persisted'] is False
        result = data(write(client, '/market/providers/tdx/probe',
                            {'symbol': '600000', 'start_date': '2025-01-01', 'end_date': '2025-01-15'}))
        assert result['status'] == 'readable' and result['first_date'] == '2025-01-02' and result['last_date'] == '2025-01-03'
        assert data(client.get('/api/v1/market/datasets')) == []
        imported = data(write(client, '/market/tdx-import', {'symbol': '600000'}))
        assert imported['provider'] == 'tdx_local' and imported['bar_count'] == 2
        assert source.read_bytes() == before
    with client_for(home) as client:
        assert data(client.get('/api/v1/market/providers/tdx/locations'))['current_path'] is None
        assert len(data(client.get('/api/v1/market/datasets'))) == 1


@pytest.mark.parametrize('count', [0, 1, 2])
def test_startup_auto_uses_only_unique_candidate(tmp_path, monkeypatch, count):
    monkeypatch.delenv('TRADE_TDX_ROOT', raising=False)
    roots = [installation(tmp_path / f'TDX{i}') for i in range(count)]
    discovery = scan_locations([tmp_path])
    monkeypatch.setattr(main_module, 'scan_locations', lambda: discovery)
    app = create_app(tmp_path / 'app', auto_rebuild=False, auto_detect_tdx=True)
    with started_client(app) as client:
        authorize(client)
        state = data(client.get('/api/v1/market/providers/tdx/locations'))
        assert state['current_path'] == (str(roots[0]) if count == 1 else None)
        assert state['selection'] == ('auto' if count == 1 else 'unset')


def test_scan_does_not_replace_manual_choice_and_invalid_choice_preserves_it(tmp_path, monkeypatch):
    monkeypatch.delenv('TRADE_TDX_ROOT', raising=False)
    first, second = installation(tmp_path / 'first'), installation(tmp_path / 'second')
    monkeypatch.setattr(provider_health_routes, 'scan_locations', lambda: scan_locations([tmp_path]))
    with client_for(tmp_path / 'app') as client:
        data(write(client, '/market/providers/tdx/select', {'path': str(first)}))
        scanned = data(write(client, '/market/providers/tdx/scan', {}))
        assert len(scanned['candidates']) == 2 and scanned['current_path'] == str(first)
        assert write(client, '/market/providers/tdx/select', {'path': str(tmp_path / 'bad')}).status_code == 400
        assert data(client.get('/api/v1/market/providers/tdx/locations'))['current_path'] == str(first)
        changed = data(write(client, '/market/providers/tdx/select', {'path': str(second)}))
        assert changed['current_path'] == str(second)


def test_pending_jobs_and_inflight_writes_prevent_mixing_sources(tmp_path, monkeypatch):
    monkeypatch.delenv('TRADE_TDX_ROOT', raising=False)
    root = installation(tmp_path / 'TDX')
    app = create_app(tmp_path / 'app', auto_rebuild=False)
    with started_client(app) as client:
        authorize(client)
        assert app.state.lifecycle.enter_write()
        try:
            response = write(client, '/market/providers/tdx/select', {'path': str(root)})
            assert response.status_code == 409 and response.json()['error']['code'] == 'TDX_SOURCE_BUSY'
        finally:
            app.state.lifecycle.leave_write()
        with app.state.db_factory.begin() as session:
            session.add(TdxUniverseJob(id='a' * 32, state='queued', request_json='{}', total_count=1,
                processed_count=0, success_count=0, error_count=0, cancel_requested=0,
                created_at=utc_now(), updated_at=utc_now()))
        response = write(client, '/market/providers/tdx/select', {'path': str(root)})
        assert response.status_code == 409 and app.state.tdx_root is None


def test_queued_job_waits_for_original_source_after_reopen(tmp_path, monkeypatch):
    monkeypatch.delenv('TRADE_TDX_ROOT', raising=False)
    original, other = installation(tmp_path / 'original'), installation(tmp_path / 'other')
    # Enough daily records for the real ladder universe preparation path.
    path = original / 'vipdoc/sh/lday/sh600000.day'
    with path.open('ab') as stream:
        stream.write(DAY_RECORD.pack(20250106, 1000, 1100, 900, 1050, 100000.0, 10000, 0))
    app = create_app(tmp_path / 'app', auto_rebuild=False)
    with started_client(app) as client:
        authorize(client)
        prepared = prepare_ladder_universe_job(original, {'markets': ['sh'], 'date_from': '2025-01-02',
            'date_to': '2025-01-06', 'max_bars': 251})
        assert prepared['request']['tdx_source_fingerprint'] == source_fingerprint(original / 'vipdoc')
        with app.state.db_factory.begin() as session:
            job = create_universe_job(session, prepared)
        assert process_one_universe_symbol(app.state.db_factory, app.state.data_dir, other) is False
        with app.state.db_factory() as session:
            row = session.get(TdxUniverseJob, job['id'])
            assert row.processed_count == 0 and row.error_code == 'TDX_SOURCE_SELECTION_REQUIRED'
        assert data(client.get('/api/v1/market/datasets')) == []
        selected = data(write(client, '/market/providers/tdx/select', {'path': str(original)}))
        assert selected['current_path'] == str(original)
        assert process_one_universe_symbol(app.state.db_factory, app.state.data_dir, app.state.tdx_root) is True
        with app.state.db_factory() as session:
            row = session.get(TdxUniverseJob, job['id'])
            assert row.processed_count == 1 and row.error_code is None
