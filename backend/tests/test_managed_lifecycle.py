"""All lifecycle actions use isolated directories and only processes started here."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from trade_app.main import create_app
from trade_app.platform.backup import verify_backup
from trade_app.platform.db import open_database
from trade_app.platform.instance_lock import InstanceLock
from trade_app.platform.launcher import ManagedLauncher, selected_directory
from trade_app.platform.lifecycle import (LifecycleController, LifecycleWriteGate, atomic_json,
                                          backup_details, load_json)
from trade_app.platform.types import TradeError
from trade_app.platform import lifecycle as lifecycle_module
from trade_app.trading.service import create_account
from test_matrix_pool_api import started_client, writer, data


def seed(path, name='source'):
    engine, factory = open_database(path)
    with factory.begin() as session:
        create_account(session, name=name)
    engine.dispose()


def context(tmp_path):
    control = tmp_path / 'control'
    control.mkdir(exist_ok=True)
    return {'instance_id': 'owned-test-instance', 'nonce': 'local-test-nonce',
            'control_dir': str(control), 'url': 'http://127.0.0.1:12345/rebuild.html'}


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    pytest.fail('bounded lifecycle wait timed out')


def test_external_uvicorn_exposes_no_lifecycle_authority(tmp_path):
    with started_client(create_app(tmp_path, auto_rebuild=False)) as client:
        post = writer(client)
        status = data(client.get('/api/v1/system/lifecycle'))
        assert status['supported'] is False and not status['capabilities']['exit']
        response = post('/api/v1/system/lifecycle/exit', {})
        assert response.status_code == 409 and response.json()['error']['code'] == 'MANAGED_LAUNCHER_REQUIRED'
        assert data(post('/api/v1/accounts', {'name': 'still writable'}))['name'] == 'still writable'


def test_switch_http_drains_writes_and_snapshots_without_changing_target(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    seed(source)
    seed(target, 'target')
    target_fingerprint = backup_details(target)[1]['fingerprint']
    exited = threading.Event()
    controller = LifecycleController(source, managed=context(tmp_path), request_exit=exited.set)
    app = create_app(source, auto_rebuild=False, lifecycle=controller)
    with started_client(app) as client:
        post = writer(client)
        preview = data(post('/api/v1/system/lifecycle/switch-preview', {'destination': str(target)}))
        assert preview['fingerprint'] == target_fingerprint
        assert controller.enter_write()
        try:
            response = post('/api/v1/system/lifecycle/switch', {'verification_id': preview['verification_id'],
                'expected_fingerprint': preview['fingerprint']}, key='switch-one')
            assert response.status_code == 202
            operation = response.json()['data']
            assert operation['state'] == 'draining' and not exited.is_set()
            assert post('/api/v1/accounts', {'name': 'blocked'}).status_code == 503
            repeat = post('/api/v1/system/lifecycle/switch', {'verification_id': preview['verification_id'],
                'expected_fingerprint': preview['fingerprint']}, key='switch-one')
            assert repeat.status_code == 202 and repeat.json()['data']['id'] == operation['id']
            assert client.get('/api/v1/system/lifecycle').status_code == 200
        finally:
            controller.leave_write()
        assert exited.wait(5)
        saved = controller.operation(operation['id'])
        assert saved['state'] == 'restarting'
        recovery = Path(saved['recovery_path'])
        assert recovery.parent == source / 'recovery'
        assert verify_backup(recovery.read_bytes())['database'] == 'trade.sqlite'
        assert backup_details(target)[1]['fingerprint'] == target_fingerprint
        assert load_json(Path(controller.managed['control_dir']) / 'intent.json')['destination'] == str(target)


def test_background_dispatch_pauses_and_timeout_reopens_writes(tmp_path):
    source = tmp_path / 'source'
    seed(source)
    controller = LifecycleController(source, managed=context(tmp_path), request_exit=lambda: pytest.fail('timed-out drain exited'),
                                     drain_seconds=0.08)
    entered, release = threading.Event(), threading.Event()
    def busy():
        entered.set()
        assert release.wait(3)
        return True
    worker = threading.Thread(target=lambda: controller.run_background('fixture', busy))
    worker.start()
    assert entered.wait(1)
    operation = controller.request('exit', {}, 'exit-timeout')
    assert controller.run_background('new-work', lambda: pytest.fail('new work ran')) is False
    failed = wait_for(lambda: (row if (row := controller.operation(operation['id']))['state'] == 'failed' else None))
    assert failed['error'] == 'LIFECYCLE_DRAIN_TIMEOUT'
    assert controller.enter_write()
    controller.leave_write()
    release.set()
    worker.join(3)
    assert controller.run_background('new-work', lambda: True) is True


@pytest.mark.parametrize('change', ['changed', 'locked'])
def test_stale_or_busy_target_does_not_exit_or_close_source(tmp_path, change):
    source, target = tmp_path / 'source', tmp_path / 'target'
    seed(source)
    seed(target)
    controller = LifecycleController(source, managed=context(tmp_path), request_exit=lambda: pytest.fail('invalid target exited'))
    preview = controller.preview_switch(str(target))
    lock = None
    if change == 'changed':
        seed(target, 'changed after preview')
    else:
        lock = InstanceLock(target)
        lock.acquire()
    try:
        operation = controller.request('switch', {'verification_id': preview['verification_id'],
            'expected_fingerprint': preview['fingerprint']}, 'switch-bad-target')
        failed = wait_for(lambda: (row if (row := controller.operation(operation['id']))['state'] == 'failed' else None))
        assert failed['error'] == ('LIFECYCLE_TARGET_CHANGED' if change == 'changed' else 'LIFECYCLE_TARGET_IN_USE')
        assert not controller.quiescing
        assert not list((source / 'recovery').glob('*.zip'))
    finally:
        if lock:
            lock.release()


def test_preview_rejects_unverified_paths_stale_token_and_idempotency_conflict(tmp_path):
    source = tmp_path / 'source'
    seed(source)
    exited = threading.Event()
    controller = LifecycleController(source, managed=context(tmp_path), request_exit=exited.set)
    for target in (str(source), str(source.parent), str(source / 'child'), 'relative', str(tmp_path / 'missing')):
        with pytest.raises(TradeError):
            controller.preview_switch(target)
    with pytest.raises(TradeError) as failure:
        controller.request('switch', {'verification_id': 'not-verified', 'expected_fingerprint': 'a' * 64}, 'x')
    assert failure.value.code == 'LIFECYCLE_PREVIEW_STALE'
    assert controller.enter_write()
    try:
        operation = controller.request('exit', {}, 'same-key')
        assert controller.request('exit', {}, 'same-key')['id'] == operation['id']
        with pytest.raises(TradeError) as failure:
            controller.request('switch', {}, 'same-key')
        assert failure.value.code == 'IDEMPOTENCY_CONFLICT'
    finally:
        controller.leave_write()
    assert exited.wait(3)


def test_startup_fingerprint_is_checked_before_migration(tmp_path):
    source = tmp_path / 'source'
    seed(source)
    before = backup_details(source)[1]['fingerprint']
    controller = LifecycleController(source, managed={**context(tmp_path), 'expected_fingerprint': 'f' * 64}, request_exit=lambda: None)
    with pytest.raises(TradeError, match='重启前'):
        with started_client(create_app(source, auto_rebuild=False, lifecycle=controller)):
            pass
    assert backup_details(source)[1]['fingerprint'] == before
    lock = InstanceLock(source)
    lock.acquire()
    lock.release()


def test_recovery_write_failure_reopens_source_and_never_requests_exit(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    seed(source)
    seed(target)
    before_source = backup_details(source)[1]['fingerprint']
    before_target = backup_details(target)[1]['fingerprint']
    controller = LifecycleController(source, managed=context(tmp_path),
        request_exit=lambda: pytest.fail('failed recovery must not exit'))
    preview = controller.preview_switch(str(target))
    real_write = lifecycle_module.atomic_bytes
    def disk_full(path, contents):
        if path.suffix == '.zip':
            raise OSError('simulated full disk')
        return real_write(path, contents)
    monkeypatch.setattr(lifecycle_module, 'atomic_bytes', disk_full)
    operation = controller.request('switch', {'verification_id': preview['verification_id'],
        'expected_fingerprint': preview['fingerprint']}, 'disk-full')
    failed = wait_for(lambda: (row if (row := controller.operation(operation['id']))['state'] == 'failed' else None))
    assert failed['error'] == 'LIFECYCLE_FAILED' and not controller.quiescing
    assert controller.enter_write()
    controller.leave_write()
    assert not (Path(controller.managed['control_dir']) / 'intent.json').exists()
    assert backup_details(source)[1]['fingerprint'] == before_source
    assert backup_details(target)[1]['fingerprint'] == before_target
    lock = InstanceLock(target)
    wait_for(lambda: not controller.status()['active_background'])
    # The worker publishes failure just before releasing the target lock.
    def released():
        try:
            lock.acquire()
            lock.release()
            return True
        except RuntimeError:
            return False
    wait_for(released)


def test_asgi_gate_keeps_stream_counted_until_final_body(tmp_path):
    seed(tmp_path / 'source')
    exited = threading.Event()
    controller = LifecycleController(tmp_path / 'source', managed=context(tmp_path), request_exit=exited.set)
    async def scenario():
        first, finish = asyncio.Event(), asyncio.Event()
        async def stream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'data: partial\n\n', 'more_body': True})
            first.set()
            await finish.wait()
            await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        sent = []
        async def send(message):
            sent.append(message)
        gate = LifecycleWriteGate(stream)
        scope = {'type': 'http', 'method': 'POST', 'path': '/api/v1/ai/run/stream',
                 'app': SimpleNamespace(state=SimpleNamespace(lifecycle=controller))}
        task = asyncio.create_task(gate(scope, receive, send))
        await first.wait()
        controller.request('exit', {}, 'stream-exit')
        await asyncio.sleep(0.04)
        assert controller.status()['active_writes'] == 1 and not exited.is_set()
        finish.set()
        await task
        assert sent[-1]['more_body'] is False and controller.status()['active_writes'] == 0
    asyncio.run(scenario())
    assert exited.wait(3)


class FakeProcess:
    def __init__(self, status=None, wait_action=None):
        self.status, self.wait_action = status, wait_action
        self.terminated = False
        self.stdin = io.BytesIO()
    def poll(self):
        return self.status
    def wait(self, timeout=None):
        if self.wait_action:
            self.status = self.wait_action()
            self.wait_action = None
        return self.status or 0
    def terminate(self):
        self.terminated, self.status = True, -1
    def kill(self):
        self.terminated, self.status = True, -9


class FakeTree:
    def assign(self, process):
        pass
    def close(self):
        pass


@pytest.mark.parametrize('intent_kind,valid_identity,exit_code,expected', [
    ('exit', True, 0, 0), ('exit', False, 0, 1),
    (None, True, 0, 1), ('exit', True, 1, 1), ('switch', True, 0, 1),
])
def test_exit_before_first_launcher_health_poll(tmp_path, monkeypatch, intent_kind, valid_identity, exit_code, expected):
    from trade_app.platform import launcher as launcher_module

    source = tmp_path / 'source'
    def spawn(_args, **kwargs):
        managed = json.loads(kwargs['env']['TRADE_MANAGED_CONTEXT'])
        if intent_kind:
            atomic_json(Path(managed['control_dir']) / 'intent.json', {
                'nonce': managed['nonce'] if valid_identity else 'stale',
                'instance_id': managed['instance_id'], 'source_data_dir': str(source),
                'kind': intent_kind, 'operation_id': 'early-exit',
            })
        return FakeProcess(status=exit_code)

    monkeypatch.setattr(launcher_module.webbrowser, 'open', lambda *_: pytest.fail('early exit opened a browser'))
    launcher = ManagedLauncher(tmp_path, port=12345, state_path=tmp_path / 'launcher.json',
                              spawn=spawn, tree_factory=FakeTree,
                              probe=lambda *_: pytest.fail('already exited server was probed'),
                              on_ready=lambda: pytest.fail('already exited server announced ready'))
    assert launcher.run(source) == expected


@pytest.mark.parametrize('failure', [None, 'start', 'commit'])
def test_supervisor_commits_only_ready_target_or_restarts_original_once(tmp_path, failure, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    seed(source, 'source facts')
    seed(target, 'target facts')
    before_source = backup_details(source)[1]['fingerprint']
    before_target = backup_details(target)[1]['fingerprint']
    recovery = source / 'recovery' / 'lifecycle-test.zip'
    recovery.parent.mkdir()
    recovery.write_bytes(backup_details(source)[0])
    contexts = []
    target_process = None
    def spawn(_args, **kwargs):
        nonlocal target_process
        managed = json.loads(kwargs['env']['TRADE_MANAGED_CONTEXT'])
        directory = Path(kwargs['env']['TRADE_REBUILD_DATA_DIR'])
        contexts.append(directory)
        if len(contexts) == 3:
            assert target_process.terminated or target_process.poll() is not None
        control = Path(managed['control_dir'])
        if len(contexts) == 1:
            def switch():
                atomic_json(control / 'operation.json', {'id': 'switch-operation', 'kind': 'switch', 'state': 'restarting'})
                atomic_json(control / 'intent.json', {'nonce': managed['nonce'], 'instance_id': managed['instance_id'],
                    'source_data_dir': str(source), 'operation_id': 'switch-operation', 'kind': 'switch',
                    'destination': str(target), 'expected_fingerprint': before_target, 'recovery_path': str(recovery),
                    'recovery_sha256': hashlib.sha256(recovery.read_bytes()).hexdigest()})
                return 42
            return FakeProcess(wait_action=switch)
        process = FakeProcess(status=1 if directory == target and failure == 'start' else None)
        if directory == target:
            target_process = process
        return process
    state_path = tmp_path / 'launcher-state.json'
    launcher = ManagedLauncher(tmp_path, port=12345, state_path=state_path, no_browser=True,
                               spawn=spawn, probe=lambda p, i: True, tree_factory=FakeTree)
    real_commit = launcher._commit_state
    def commit(directory, control, transition=None):
        if directory == target and failure == 'commit':
            raise OSError('target ready but launcher state could not be published')
        return real_commit(directory, control, transition)
    monkeypatch.setattr(launcher, '_commit_state', commit)
    assert launcher.run(source) == 0
    state = load_json(state_path)
    expected = source if failure else target
    assert state['active_data_dir'] == str(expected)
    assert state['last_transition']['state'] == ('rolled_back' if failure else 'completed')
    assert contexts == ([source, target, source] if failure else [source, target])
    assert backup_details(source)[1]['fingerprint'] == before_source
    assert backup_details(target)[1]['fingerprint'] == before_target
    assert selected_directory(None, state_path) == expected


def test_real_owned_server_switch_exit_and_persistent_pointer(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    seed(source, 'original facts')
    seed(target, 'copied target facts')
    with socket.socket() as binding:
        binding.bind(('127.0.0.1', 0))
        port = binding.getsockname()[1]
    backend = Path(__file__).resolve().parents[1]
    launcher = ManagedLauncher(backend, port=port, state_path=tmp_path / 'launcher.json', no_browser=True, startup_seconds=20)
    result = []
    thread = threading.Thread(target=lambda: result.append(launcher.run(source)), daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{port}'
    try:
        with httpx.Client(base_url=base, timeout=3, trust_env=False) as client:
            def ready():
                try:
                    response = client.get('/health')
                    return response.status_code == 200
                except httpx.HTTPError:
                    return False
            wait_for(ready, 20)
            wait_for(lambda: (load_json(tmp_path / 'launcher.json') or {}).get('active_data_dir') == str(source), 5)
            token = data(client.get('/api/v1/session'))['csrf_token']
            headers = {'X-CSRF-Token': token, 'Idempotency-Key': 'real-switch'}
            preview = data(client.post('/api/v1/system/lifecycle/switch-preview', json={'destination': str(target)}, headers=headers))
            source_instance = data(client.get('/api/v1/system/lifecycle'))['instance_id']
            response = client.post('/api/v1/system/lifecycle/switch', json={
                'verification_id': preview['verification_id'], 'expected_fingerprint': preview['fingerprint']}, headers=headers)
            assert response.status_code == 202, response.text
            def switched():
                try:
                    health = client.get('/health')
                    return health.status_code == 200 and health.json()['instance_id'] != source_instance
                except httpx.HTTPError:
                    return False
            wait_for(switched, 20)
            token = data(client.get('/api/v1/session'))['csrf_token']
            status = data(client.get('/api/v1/system/lifecycle'))
            assert status['data_dir'] == str(target.resolve())
            wait_for(lambda: (load_json(tmp_path / 'launcher.json') or {}).get('active_data_dir') == str(target), 5)
            assert data(client.get('/api/v1/accounts'))[0]['name'] == 'copied target facts'
            archives = list((source / 'recovery').glob('*.zip'))
            assert len(archives) == 1 and verify_backup(archives[0].read_bytes())
            response = client.post('/api/v1/system/lifecycle/exit', json={}, headers={
                'X-CSRF-Token': token, 'Idempotency-Key': 'real-exit'})
            assert response.status_code == 202, response.text
        thread.join(15)
        assert not thread.is_alive() and result == [0]
        assert load_json(tmp_path / 'launcher.json')['active_data_dir'] == str(target)
    finally:
        launcher._terminate_owned()
        thread.join(10)
