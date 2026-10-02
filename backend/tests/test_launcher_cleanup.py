"""No installed app is touched: every process and data directory is owned here."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from trade_app.platform import launcher as module
from trade_app.platform.instance_lock import InstanceLock
from trade_app.platform.lifecycle import atomic_json, load_json
from trade_app.platform.process_lifetime import OwnedProcessTree


def wait_for(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (httpx.HTTPError, OSError, RuntimeError):
            pass
        time.sleep(.1)
    pytest.fail('Owned process did not reach the required state')


def test_discovery_requires_locked_publication_and_live_matching_identity(tmp_path, monkeypatch):
    lock = InstanceLock(tmp_path)
    identity = 'a' * 32
    with pytest.raises(RuntimeError):
        lock.publish(instance_id=identity, port=12345)
    lock.acquire()
    try:
        lock.publish(instance_id=identity, port=12345)
        monkeypatch.setattr(module, 'health_matches', lambda port, instance: port == 12345 and instance == identity)
        assert module.running_instance_url(tmp_path) == 'http://127.0.0.1:12345/rebuild.html'
        opened = []
        monkeypatch.setattr(module.webbrowser, 'open', opened.append)
        assert module.launch_or_reopen(tmp_path, no_browser=False,
                                      start=lambda _: pytest.fail('duplicate launched a child')) == 0
        assert opened == ['http://127.0.0.1:12345/rebuild.html']
        monkeypatch.setattr(module, 'health_matches', lambda *_: False)
        assert module.running_instance_url(tmp_path) is None
    finally:
        lock.release()
    assert not (tmp_path / '.trade-running.json').exists()


@pytest.mark.parametrize('change', [{'port': True}, {'port': 0}, {'port': '8011'},
                                   {'instance_id': 'unverified'}, {'data_dir': 'elsewhere'}])
def test_invalid_discovery_never_probes_another_service(tmp_path, monkeypatch, change):
    atomic_json(tmp_path / '.trade-running.json', {'data_dir': str(tmp_path), 'port': 12345,
                                                'instance_id': 'a' * 32, **change})
    monkeypatch.setattr(module, 'health_matches', lambda *_: pytest.fail('untrusted record probed'))
    assert module.running_instance_url(tmp_path) is None


def test_failed_start_releases_serialization_lock(tmp_path):
    def fail(_):
        raise OSError('failed before ready')
    with pytest.raises(OSError):
        module.launch_or_reopen(tmp_path, no_browser=True, start=fail)
    guard = InstanceLock(tmp_path, '.trade-launcher.lock')
    guard.acquire()
    guard.release()


def test_ready_releases_startup_guard_before_long_running_supervision(tmp_path):
    def start(ready):
        ready()
        guard = InstanceLock(tmp_path, '.trade-launcher.lock')
        guard.acquire()
        guard.release()
        return 0
    assert module.launch_or_reopen(tmp_path, no_browser=True, start=start) == 0


def test_child_cannot_start_database_if_process_ownership_attachment_fails(tmp_path):
    class FailedTree:
        def assign(self, _):
            raise OSError('ownership attachment failed')
        def close(self):
            pass
    backend = Path(__file__).resolve().parents[1]
    launcher = module.ManagedLauncher(backend, port=12345, state_path=tmp_path / 'state.json',
                                      no_browser=True, tree_factory=FailedTree)
    control = tmp_path / 'control'; control.mkdir()
    with pytest.raises(OSError, match='ownership attachment'):
        launcher._start(tmp_path / 'data', control)
    assert launcher.process.poll() is not None
    assert not (tmp_path / 'data/trade.sqlite').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows ownership is verified against the actual OS')
def test_windows_job_closes_owned_descendants_but_leaves_unrelated_process(tmp_path):
    child = unrelated = None
    tree = OwnedProcessTree()
    try:
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        # Do not allow a descendant to spawn before the parent is assigned.
        script = ('import sys,subprocess,time;sys.stdin.buffer.read(1);'
                  'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                  'print(p.pid,flush=True);time.sleep(60)')
        child = subprocess.Popen([sys.executable, '-u', '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        tree.assign(child)
        child.stdin.write(b'R'); child.stdin.flush()
        descendant = int(child.stdout.readline())
        # Keep an OS process handle so PID reuse cannot affect the assertion.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, descendant)
        assert handle
        try:
            tree.close()
            child.wait(timeout=10)
            assert kernel.WaitForSingleObject(handle, 10000) == 0
            assert unrelated.poll() is None
        finally:
            kernel.CloseHandle(handle)
    finally:
        tree.close()
        for process in (child, unrelated):
            if process and process.poll() is None:
                process.kill(); process.wait(timeout=10)


def test_actual_launcher_duplicate_and_abrupt_death_release_port_and_data(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    data_dir = tmp_path / 'data'
    state = tmp_path / 'state.json'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    command = [sys.executable, str(repo / 'scripts/run_trade_rebuild.py'), '--no-browser', '--no-tray',
               '--data-dir', str(data_dir), '--state-file', str(state), '--port', str(port)]
    log = (tmp_path / 'launcher.log').open('wb')
    first = second = restarted = None
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=1, trust_env=False) as client:
        def ready():
            response = client.get('/health')
            return response.json()['instance_id'] if response.status_code == 200 else None
        def released():
            lock = InstanceLock(data_dir)
            lock.acquire(); lock.release()
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
            return True
        try:
            first = subprocess.Popen(command, stdout=log, stderr=log)
            # Launch concurrently, before the first one has published discovery.
            second = subprocess.Popen(command, stdout=log, stderr=log)
            identity = wait_for(ready)
            wait_for(lambda: first.poll() is not None or second.poll() is not None)
            owner, duplicate = (first, second) if first.poll() is None else (second, first)
            assert duplicate.returncode == 0
            assert ready() == identity
            assert (load_json(data_dir / '.trade-running.json') or {})['instance_id'] == identity
            owner.kill(); owner.wait(timeout=10)  # Kill ONLY this test's launcher, not its children.
            wait_for(released)
            restarted = subprocess.Popen(command, stdout=log, stderr=log)
            assert wait_for(ready) != identity
            assert client.get('/rebuild.html').status_code == 200
            token = client.get('/api/v1/session').json()['data']['csrf_token']
            response = client.post('/api/v1/system/lifecycle/exit', json={},
                                   headers={'X-CSRF-Token': token, 'Idempotency-Key': 'test-clean-exit'})
            assert response.status_code == 202
            assert restarted.wait(timeout=20) == 0
            wait_for(released)
            assert not (data_dir / '.trade-running.json').exists()
        finally:
            for process in (first, second, restarted):
                if process and process.poll() is None:
                    process.kill(); process.wait(timeout=10)
            log.close()
