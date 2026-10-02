"""Legacy entry points preserve data on malformed input and keep async I/O responsive."""
import asyncio
from contextlib import contextmanager
from datetime import date
import io
import json
from pathlib import Path
import sqlite3
import threading
import time

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.sim_engine import SimAccountEngine, SimEngineError
from trading_ms import database
from trading_ms.main import app
from trading_ms.models import Base, CapitalFlow, DailyReview, FlashCard, RoundReview, Setting, Snapshot
from trading_ms.routers import capital, misc, reviews, trades
from trading_ms.services import backup
from trading_ms.services.storage import copy_data_directory
from trading_ms.services.uploads import read_screenshot


@pytest.fixture
def legacy(tmp_path):
    directory = tmp_path / 'source'
    directory.mkdir()
    engine = create_engine('sqlite:///' + str(directory / 'laimiutrade.db'),
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    def session_override():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session
    app.dependency_overrides[database.get_db] = session_override
    app.state.storage_copying = app.state.data_switch_pending = False
    app.state.active_writes = 0
    try:
        yield engine, directory
    finally:
        app.dependency_overrides.pop(database.get_db, None)
        app.state.storage_copying = app.state.data_switch_pending = False
        app.state.active_writes = 0
        engine.dispose()


def sim_engine(path):
    return SimAccountEngine(get_candles=lambda _: [], resolve_symbol_name=lambda symbol: symbol,
        now_date=lambda: '2026-01-05', now_datetime=lambda: '2026-01-05T12:00:00', state_path=str(path))


@pytest.mark.parametrize('damage', ['json', 'array', 'missing_account', 'missing_balance', 'version', 'nonfinite', 'collection'])
def test_corrupt_sim_state_is_preserved_instead_of_reinitialized(tmp_path, damage):
    path = tmp_path / 'sim.json'
    original = sim_engine(path)._state
    if damage == 'json': raw = b'{broken-json'
    else:
        if damage == 'array': original = []
        elif damage == 'missing_account': original = {}
        elif damage == 'missing_balance': original['account'] = {}
        elif damage == 'version': original['schema_version'] = 2
        elif damage == 'nonfinite': original['account']['cash'] = float('nan')
        elif damage == 'collection': original['fills'] = 'broken'
        raw = json.dumps(original).encode()
    path.write_bytes(raw)
    with pytest.raises(SimEngineError) as failure: sim_engine(path)
    assert failure.value.code == 'SIM_STATE_INVALID'
    assert path.read_bytes() == raw


def test_sim_persistence_failure_does_not_replace_loaded_account(tmp_path, monkeypatch):
    path = tmp_path / 'sim.json'
    original = sim_engine(path)._state
    original['account']['cash'] = 321
    raw = json.dumps(original).encode()
    path.write_bytes(raw)
    def fail(*_): raise PermissionError('write denied')
    monkeypatch.setattr(SimAccountEngine, '_write_state', fail)
    with pytest.raises(PermissionError): sim_engine(path)
    assert path.read_bytes() == raw


def test_legacy_data_directory_explicit_override_never_uses_location_file(tmp_path, monkeypatch):
    target = tmp_path / 'isolated'
    monkeypatch.setenv('TRADING_MS_DATA_DIR', str(target))
    monkeypatch.setattr(database, '_read_location_file', lambda _: pytest.fail('override read user location'))
    assert database._resolve_data_dir() == target
    assert not target.exists()


@pytest.mark.parametrize('damage', ['partial', 'row_type', 'bad_date', 'duplicate', 'nonfinite'])
def test_legacy_restore_validation_cannot_delete_existing_rows(legacy, damage):
    engine, _source = legacy
    with Session(engine) as session:
        session.add(FlashCard(id=1, content='preserved', tags=''))
        session.commit()
        payload = backup.export_backup(session)
        if damage == 'partial': payload = {'exported_at': '2026-01-05'}
        elif damage == 'row_type': payload['flash_cards'] = [1]
        elif damage == 'bad_date': payload['flash_cards'][0]['created_at'] = 'invalid-date'
        elif damage == 'duplicate': payload['flash_cards'].append(dict(payload['flash_cards'][0]))
        elif damage == 'nonfinite': payload['capital_flows'] = [{'id': 1, 'flow_date': '2026-01-05', 'kind': 'initial', 'amount': float('nan'), 'note': ''}]
        with pytest.raises(ValueError): backup.restore_backup(session, payload)
        assert session.get(FlashCard, 1).content == 'preserved'


def test_legacy_backup_omits_credentials_restores_round_notes_and_retains_local_key(legacy):
    engine, _source = legacy
    with Session(engine) as session:
        session.add_all([Setting(key='ai_api_key', value='fake-private-key'),
                         RoundReview(id=1, code='600000', start_date=date(2026, 1, 5), review_summary='original')])
        session.commit()
        payload = backup.export_backup(session)
        assert 'fake-private-key' not in json.dumps(payload)
        assert payload['round_reviews'][0]['review_summary'] == 'original'
        session.get(RoundReview, 1).review_summary = 'changed'
        session.add(RoundReview(id=2, code='600001', start_date=date(2026, 1, 5), review_summary='extra'))
        session.commit()
        backup.restore_backup(session, payload)
        assert session.query(RoundReview).count() == 1
        assert session.get(RoundReview, 1).review_summary == 'original'
        assert session.get(Setting, 'ai_api_key').value == 'fake-private-key'


def test_legacy_http_rejects_hostile_origin_and_partial_restore(legacy):
    engine, _source = legacy
    with Session(engine) as session:
        session.add(FlashCard(id=1, content='preserved', tags=''))
        session.commit()
    client = TestClient(app)
    rejected = client.get('/api/export/json', headers={'Origin': 'https://evil.example'})
    assert rejected.status_code == 403 and 'access-control-allow-origin' not in rejected.headers
    assert client.post('/api/import/json', json={'data': {'exported_at': '2026-01-05'}}).status_code == 400
    assert client.get('/api/cards').json()[0]['content'] == 'preserved'


def test_old_nine_table_json_keeps_round_notes_and_does_not_import_credentials(legacy):
    engine, _source = legacy
    with Session(engine) as session:
        session.add_all([Setting(key='ai_api_key', value='local-fake-key'),
                         RoundReview(id=1, code='600000', start_date=date(2026, 1, 5), review_summary='preserved')])
        session.commit()
        payload = backup.export_backup(session)
        for field in ('format', 'credentials_excluded', 'round_reviews'): payload.pop(field)
        payload['settings'].append({'key': 'ai_api_key', 'value': 'imported-fake-key'})
        backup.restore_backup(session, payload)
        assert session.get(Setting, 'ai_api_key').value == 'local-fake-key'
        assert session.get(RoundReview, 1).review_summary == 'preserved'


@pytest.mark.parametrize('kind', ['trades', 'account'])
def test_screenshot_provider_and_database_work_do_not_block_event_loop(legacy, monkeypatch, kind):
    engine, _source = legacy
    started = threading.Event()
    event_thread = threading.get_ident()
    def provider(session, *_):
        assert threading.get_ident() != event_thread
        assert session.bind is engine
        started.set()
        time.sleep(0.5)
        if kind == 'trades': return [{'code': '600000', 'name': 'mock', 'side': 'buy', 'price': 10, 'qty': 100}]
        return {'snap_date': '2026-01-05', 'positions': [], 'total_assets': 100}
    if kind == 'trades':
        monkeypatch.setattr(trades.ai_svc, 'parse_screenshot', provider)
        monkeypatch.setattr(trades.market_svc, 'resolve_stock', lambda code, name: (code, name))
        path = '/api/trades/import/screenshot'
    else:
        monkeypatch.setattr(capital.ai_svc, 'parse_account_screenshot', provider)
        path = '/api/capital/import/screenshot'
    async def check():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
            before = time.monotonic()
            pending = asyncio.create_task(client.post(path, files={'file': ('image.png', b'mock-image', 'image/png')}))
            while not started.is_set():
                if pending.done(): pytest.fail('request finished before provider started')
                await asyncio.sleep(0.005)
            assert time.monotonic() - before < 0.25
            assert (await client.get('/api/settings')).status_code == 200
            assert (await pending).status_code == 200
    asyncio.run(check())


def test_screenshot_size_rejection_closes_file_before_provider_call(monkeypatch):
    monkeypatch.setattr('trading_ms.services.uploads.MAX_SCREENSHOT_BYTES', 4)
    upload = UploadFile(io.BytesIO(b'12345'), filename='image.png', headers=Headers({'content-type': 'image/png'}))
    with pytest.raises(HTTPException) as failure: asyncio.run(read_screenshot(upload))
    assert failure.value.status_code == 413 and upload.file.closed


def test_legacy_directory_copy_includes_uncheckpointed_wal_and_preserves_source(legacy, tmp_path):
    engine, source = legacy
    with engine.connect() as connection: connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    with Session(engine) as session:
        session.add(FlashCard(content='wal snapshot', tags=''))
        session.commit()
    uploads = source / 'uploads'
    uploads.mkdir()
    (uploads / 'image.png').write_bytes(b'preserved image')
    target = tmp_path / 'copied'
    copy_data_directory(source, target)
    with sqlite3.connect(target / 'laimiutrade.db') as connection:
        assert connection.execute('SELECT content FROM flash_cards').fetchone()[0] == 'wal snapshot'
    assert (target / 'uploads/image.png').read_bytes() == b'preserved image'
    assert (source / 'uploads/image.png').read_bytes() == b'preserved image'
    with Session(engine) as session: assert session.query(FlashCard).count() == 1


def test_legacy_move_endpoint_keeps_current_engine_and_requires_explicit_restart(legacy, tmp_path, monkeypatch):
    engine, source = legacy
    program = tmp_path / 'program'
    program.mkdir()
    monkeypatch.setattr(misc, 'DATA_DIR', source)
    monkeypatch.setattr(misc, 'ROOT_DIR', program)
    client = TestClient(app)
    target = tmp_path / 'copied'
    result = client.post('/api/system/move-data', json={'target_dir': str(target)})
    assert result.status_code == 200, result.text
    assert result.json()['active_dir'] == str(source)
    assert result.json()['disposition'] == 'copied_restart_required'
    assert (program / 'data_location.txt').read_text() == str(target)
    assert (source / 'laimiutrade.db').is_file() and (target / 'laimiutrade.db').is_file()
    assert client.get('/api/cards').status_code == 200
    assert client.post('/api/cards', json={'content': 'must wait'}).status_code == 409
    with Session(engine) as session: assert session.query(FlashCard).count() == 0


@pytest.mark.parametrize('kind', ['same', 'parent', 'child', 'root', 'relative'])
def test_legacy_copy_rejects_unsafe_directory_boundaries(legacy, tmp_path, monkeypatch, kind):
    _engine, source = legacy
    monkeypatch.setattr(misc, 'DATA_DIR', source)
    targets = {'same': source, 'parent': source.parent, 'child': source / 'child',
               'root': Path(source.anchor), 'relative': Path('relative')}
    with pytest.raises(HTTPException) as failure: misc._resolve_migration_target(str(targets[kind]))
    assert failure.value.status_code == 400


def test_review_upload_verifies_bytes_and_retains_legacy_image_urls(legacy, monkeypatch):
    engine, source = legacy
    uploads = source / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(reviews, 'UPLOAD_DIR', uploads)
    client = TestClient(app)
    endpoint = '/api/reviews/daily/2026-01-05'
    rejected = client.post(endpoint + '/images', files={'file': ('fake.png', b'invalid image', 'image/png')})
    assert rejected.status_code == 400
    with Session(engine) as session: assert session.query(DailyReview).count() == 0
    assert list(uploads.iterdir()) == []
    image = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(image, format='PNG')
    accepted = client.post(endpoint + '/images', files={'file': ('valid.png', image.getvalue(), 'image/png')})
    assert accepted.status_code == 200, accepted.text
    url = accepted.json()['url']
    image_path = uploads / url.rsplit('/', 1)[1]
    assert image_path.read_bytes() == image.getvalue()
    with Session(engine) as session:
        review = session.query(DailyReview).one()
        review.images = json.dumps([url.replace('/journal-app/uploads/', '/uploads/')])
        session.commit()
    assert client.get(endpoint).json()['images'] == [url]
    assert client.delete(endpoint + '/images', params={'url': url}).status_code == 200
    assert not image_path.exists()


def test_review_image_delete_never_unlinks_windows_absolute_path(legacy, tmp_path, monkeypatch):
    engine, source = legacy
    uploads = source / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(reviews, 'UPLOAD_DIR', uploads)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'preserved outside uploads')
    hostile_url = '/uploads/' + str(outside)
    with Session(engine) as session:
        session.add(DailyReview(review_date=date(2026, 1, 5), images=json.dumps([hostile_url])))
        session.commit()
    result = TestClient(app).delete('/api/reviews/daily/2026-01-05/images', params={'url': hostile_url})
    assert result.status_code == 200
    assert outside.read_bytes() == b'preserved outside uploads'
    with Session(engine) as session: assert json.loads(session.query(DailyReview).one().images) == []


def test_review_image_shared_by_other_review_is_preserved(legacy, monkeypatch):
    engine, source = legacy
    uploads = source / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(reviews, 'UPLOAD_DIR', uploads)
    image = uploads / 'shared.png'
    image.write_bytes(b'preserved shared image')
    with Session(engine) as session:
        session.add_all([DailyReview(review_date=date(2026, 1, 5), images='["/uploads/shared.png"]'),
                         DailyReview(review_date=date(2026, 1, 6), images='["/journal-app/uploads/shared.png"]')])
        session.commit()
    response = TestClient(app).delete('/api/reviews/daily/2026-01-05/images', params={'url': '/journal-app/uploads/shared.png'})
    assert response.status_code == 200 and image.is_file()


def test_zero_nav_is_reported_without_division_error_and_later_flow_is_explicit_error(legacy):
    engine, _source = legacy
    with Session(engine) as session:
        session.add_all([CapitalFlow(flow_date=date(2026, 1, 5), kind='initial', amount=100),
                         Snapshot(snap_date=date(2026, 1, 6), total_assets=0)])
        session.commit()
    client = TestClient(app)
    result = client.get('/api/capital/nav')
    assert result.status_code == 200, result.text
    assert result.json()['state']['nav'] == 0
    assert result.json()['state']['next_gap_pct'] is None
    with Session(engine) as session:
        session.add(CapitalFlow(flow_date=date(2026, 1, 7), kind='deposit', amount=100))
        session.commit()
    result = client.get('/api/capital/nav')
    assert result.status_code == 400
    assert '归零' in result.json()['detail']
