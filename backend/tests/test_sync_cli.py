import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn

from trade_app.main import create_app
from trade_app.market import sync_cli
from trade_app.market.models import MarketSyncJob
from trade_app.platform.local_api_client import LocalAPI
from trade_app.platform.types import TradeError
from sqlalchemy import select


@pytest.fixture
def server(tmp_path, monkeypatch):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    monkeypatch.setenv('TRADE_REBUILD_PORT', str(port))
    app = create_app(tmp_path / 'data', auto_rebuild=False)
    instance = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False))
    thread = threading.Thread(target=lambda: instance.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(200):
        if instance.started:
            break
        time.sleep(.025)
    assert instance.started
    try:
        yield app, f'http://127.0.0.1:{port}'
    finally:
        instance.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


INTENT = {'symbols': ['600000'], 'start_date': '2025-01-01', 'end_date': '2025-01-15', 'provider': 'akshare'}


@pytest.mark.parametrize('url', ['https://example.com:443', 'http://192.168.1.1:80', 'http://user:secret@127.0.0.1:8011',
                               'http://127.0.0.1', 'http://127.0.0.1:8011/path', 'http://127.0.0.1:8011?token=x'])
def test_cli_rejects_nonlocal_and_credential_urls(url):
    with pytest.raises(TradeError):
        LocalAPI(url)


def test_lost_response_can_resume_same_durable_job_with_new_session(server, tmp_path, monkeypatch):
    app, url = server
    client = LocalAPI(url)
    journal = tmp_path / 'job.json'
    original = client.request
    def drop(path, method='GET', payload=None, **kwargs):
        result = original(path, method, payload, **kwargs)
        if method == 'POST':
            raise TradeError('CLI_API_UNREACHABLE', '模拟回包中断')
        return result
    monkeypatch.setattr(client, 'request', drop)
    with pytest.raises(TradeError, match='中断'):
        sync_cli.submit(client, INTENT, journal)
    first = sync_cli.load(journal)
    assert first['job_id'] is None and 'csrf' not in journal.read_text(encoding='utf-8')
    result = sync_cli.submit(LocalAPI(url), INTENT, journal, resume=True)
    assert result['state'] == 'queued'
    with app.state.db_factory() as session:
        jobs = list(session.scalars(select(MarketSyncJob)))
        assert len(jobs) == 1 and jobs[0].id == result['id']
    assert sync_cli.load(journal)['request_id'] == first['request_id']


def test_cli_scope_and_input_changes_refuse_reuse(server, tmp_path, monkeypatch):
    _, url = server
    path = tmp_path / 'job.json'
    client = LocalAPI(url)
    sync_cli.submit(client, INTENT, path)
    with pytest.raises(TradeError, match='请求已变化'):
        sync_cli.submit(client, {**INTENT, 'provider': 'baostock'}, path, resume=True)
    value = sync_cli.load(path)
    value['data_dir'] = 'different directory'
    sync_cli.save(path, value)
    with pytest.raises(TradeError, match='数据目录已改变'):
        sync_cli.submit(client, INTENT, path, resume=True)


def test_retry_only_failed_symbols_and_repeated_confirmation_reuses_child(server, tmp_path):
    app, url = server
    client = LocalAPI(url)
    source = sync_cli.submit(client, {**INTENT, 'symbols': ['600000', '000001']}, tmp_path / 'parent.json')
    with app.state.db_factory.begin() as session:
        job = session.get(MarketSyncJob, source['id'])
        job.state = 'partial_failed'
        job.results_json = json.dumps([{'symbol': 'sh600000', 'state': 'done'}, {'symbol': 'sz000001', 'state': 'failed'}])
    journal = sync_cli.load(tmp_path / 'parent.json')
    source = sync_cli.status(client, journal)
    child = sync_cli.retry(client, journal, source, tmp_path / 'retry.json')
    repeated = sync_cli.retry(LocalAPI(url), journal, source, tmp_path / 'retry.json')
    assert child['symbols'] == ['sz000001'] and repeated['id'] == child['id']
    assert sync_cli.load(tmp_path / 'retry.json')['retry_parent_id'] == source['id']
    with app.state.db_factory() as session:
        assert len(list(session.scalars(select(MarketSyncJob)))) == 2


def test_real_cli_process_submits_inspects_and_cancels_without_network_provider(server, tmp_path):
    _, url = server
    source = tmp_path / 'request.json'
    source.write_text(sync_cli.encode(INTENT), encoding='utf-8')
    journal = tmp_path / 'job.json'
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(root / 'scripts/trade_rebuild_sync.py'), '--url', url]
    created = subprocess.run(command + ['sync', '--request', str(source), '--job-file', str(journal)], capture_output=True, timeout=20)
    assert created.returncode == 0, created.stderr
    identifier = json.loads(created.stdout)['id']
    state = subprocess.run(command + ['status', '--job-file', str(journal)], capture_output=True, timeout=20)
    assert state.returncode == 0 and json.loads(state.stdout)['id'] == identifier
    cancelled = subprocess.run(command + ['cancel', '--job-file', str(journal)], capture_output=True, timeout=20)
    assert cancelled.returncode == 2 and json.loads(cancelled.stdout)['state'] == 'cancelled'
