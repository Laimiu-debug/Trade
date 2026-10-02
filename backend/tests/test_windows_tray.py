"""Native tray commands and owned shutdown, always with temporary data and ports."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

from trade_app.platform import launcher as module
from trade_app.platform.instance_lock import InstanceLock
from trade_app.platform.lifecycle import atomic_json, load_json
from trade_app.platform.windows_tray import WindowsTray, OPEN_COMMAND, EXIT_COMMAND, CALLBACK_MESSAGE
from test_launcher_cleanup import wait_for
from test_managed_lifecycle import FakeProcess, FakeTree


def test_exit_during_startup_never_opens_browser_and_closes_desktop(tmp_path, monkeypatch):
    launcher = module.ManagedLauncher(tmp_path, port=12345, state_path=tmp_path / 'state.json',
                                     no_browser=False, tree_factory=FakeTree)
    class Desktop:
        closed = False
        def start(self):
            launcher.request_exit()
        def status(self, *args, **kwargs):
            pass
        def close(self):
            self.closed = True
    launcher.desktop = Desktop()
    monkeypatch.setattr(module.webbrowser, 'open', lambda _: pytest.fail('browser opened after exit'))
    assert launcher.run(tmp_path / 'data') == 0
    assert launcher.desktop.closed
    assert not (tmp_path / 'data/trade.sqlite').exists()


def test_exit_timeout_terminates_tree_and_removes_only_own_record(tmp_path):
    launcher = module.ManagedLauncher(tmp_path, port=12345, state_path=tmp_path / 'state.json',
                                     no_browser=True, tree_factory=FakeTree, shutdown_seconds=0.01)
    class Stalled(FakeProcess):
        def wait(self, timeout=None):
            if self.status is None:
                raise subprocess.TimeoutExpired('owned-test-child', timeout)
            return self.status
    class Tree(FakeTree):
        closed = False
        def close(self):
            self.closed = True
    control = tmp_path / 'control'; control.mkdir()
    launcher.process, launcher.process_tree = Stalled(), Tree()
    process, tree = launcher.process, launcher.process_tree
    launcher.active_dir = tmp_path
    launcher.context = {'instance_id': 'a' * 32, 'nonce': 'owned-nonce'}
    atomic_json(tmp_path / '.trade-running.json', {'instance_id': 'a' * 32, 'data_dir': str(tmp_path), 'port': 12345})
    assert launcher._shutdown_owned(control) == 0
    assert load_json(control / 'command.json') == {'kind': 'exit', **launcher.context}
    assert load_json(tmp_path / '.trade-running.json')['exiting'] is True
    launcher._terminate_owned()
    assert process.terminated and tree.closed
    assert not (tmp_path / '.trade-running.json').exists()
    atomic_json(tmp_path / '.trade-running.json', {'instance_id': 'b' * 32})
    launcher._clear_owned_record()
    assert load_json(tmp_path / '.trade-running.json')['instance_id'] == 'b' * 32


def test_stopping_instance_waits_for_lock_then_relaunches(tmp_path, monkeypatch):
    lock = InstanceLock(tmp_path); lock.acquire()
    atomic_json(tmp_path / '.trade-running.json', {'instance_id': 'a' * 32, 'data_dir': str(tmp_path),
                                                 'port': 12345, 'exiting': True})
    monkeypatch.setattr(module, 'health_matches', lambda *_: True)
    monkeypatch.setattr(module, 'legacy_instance_url', lambda *_: pytest.fail('exiting service reused'))
    release = threading.Timer(0.1, lock.release); release.start()
    started = []
    try:
        assert module.launch_or_reopen(tmp_path, no_browser=True, start=lambda _: started.append(True) or 0) == 0
    finally:
        release.join(); lock.release()
    assert started == [True]


def test_locked_unresponsive_directory_does_not_start_another_server(tmp_path, monkeypatch):
    lock = InstanceLock(tmp_path); lock.acquire()
    monkeypatch.setattr(module, 'legacy_instance_url', lambda *_: None)
    try:
        with pytest.raises(RuntimeError, match='占用'):
            module.launch_or_reopen(tmp_path, no_browser=True, start=lambda _: pytest.fail('second server spawned'))
    finally:
        lock.release()


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job shutdown fallback')
def test_stalled_real_child_and_descendant_are_reaped_after_exit_deadline(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    script = ('import json,os,sys,time,subprocess;from pathlib import Path;'
              'from trade_app.platform.instance_lock import InstanceLock;'
              'sys.stdin.buffer.read(1);d=Path(os.environ["TRADE_REBUILD_DATA_DIR"]);'
              'lock=InstanceLock(d);lock.acquire();'
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
              '(d/"descendant.json").write_text(json.dumps({"pid":p.pid}));time.sleep(60)')
    process_handle = None
    from ctypes import wintypes as wt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]; kernel.OpenProcess.restype = wt.HANDLE
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]; kernel.WaitForSingleObject.restype = wt.DWORD
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    def spawn(_args, **kwargs):
        return subprocess.Popen([sys.executable, '-u', '-c', script], **kwargs)
    def ready():
        nonlocal process_handle
        descendant = wait_for(lambda: load_json(tmp_path / 'data/descendant.json'))
        process_handle = kernel.OpenProcess(0x100000, False, descendant['pid'])
        assert process_handle
        launcher.request_exit()
    launcher = module.ManagedLauncher(backend, port=12345, state_path=tmp_path / 'state.json',
        no_browser=True, spawn=spawn, probe=lambda *_: True, shutdown_seconds=.2, on_ready=ready)
    try:
        assert launcher.run(tmp_path / 'data') == 0
        assert launcher.process.poll() is not None
        assert kernel.WaitForSingleObject(process_handle, 10000) == 0
        lock = InstanceLock(tmp_path / 'data'); lock.acquire(); lock.release()
    finally:
        launcher._terminate_owned()
        if process_handle:
            kernel.CloseHandle(process_handle)


def native_api():
    from ctypes import wintypes as wt
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]; user.FindWindowW.restype = wt.HWND
    user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]; user.PostMessageW.restype = wt.BOOL
    user.IsWindow.argtypes = [wt.HWND]; user.IsWindow.restype = wt.BOOL
    return user


def icon_rect(window):
    from ctypes import wintypes as wt
    class Guid(ctypes.Structure):
        _fields_ = [('a', wt.DWORD), ('b', wt.WORD), ('c', wt.WORD), ('d', wt.BYTE * 8)]
    class Identity(ctypes.Structure):
        _fields_ = [('size', wt.DWORD), ('window', wt.HWND), ('id', wt.UINT), ('guid', Guid)]
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(Identity), ctypes.POINTER(wt.RECT)]
    shell.Shell_NotifyIconGetRect.restype = ctypes.c_long
    rect = wt.RECT()
    identity = Identity(size=ctypes.sizeof(Identity), window=window, id=1)
    return shell.Shell_NotifyIconGetRect(ctypes.byref(identity), ctypes.byref(rect)) == 0


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows notification area')
def test_native_tray_open_exit_and_icon_removal():
    user = native_api()
    if not user.FindWindowW('Shell_TrayWnd', None):
        pytest.skip('Interactive Windows shell is unavailable')
    repo = Path(__file__).resolve().parents[2]
    opened, exited = threading.Event(), threading.Event()
    tray = WindowsTray(repo / 'frontend/public/trade-icon.ico', on_open=opened.set, on_exit=exited.set)
    try:
        tray.start()
        window = tray.window
        assert icon_rect(window)
        tray.status('Trade test', can_open=True)
        user.PostMessageW(window, CALLBACK_MESSAGE, 0, 0x400 | (1 << 16))
        assert opened.wait(2)
        user.PostMessageW(window, 0x111, EXIT_COMMAND, 0)
        assert exited.wait(2)
        opened.clear()
        user.PostMessageW(window, 0x111, OPEN_COMMAND, 0)
        assert not opened.wait(.1)
    finally:
        tray.close()
    assert not user.IsWindow(window)
    assert not icon_rect(window)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows launcher and tray')
def test_actual_tray_exit_reopen_persistence_and_legacy_discovery(tmp_path):
    user = native_api()
    if not user.FindWindowW('Shell_TrayWnd', None):
        pytest.skip('Interactive Windows shell is unavailable')
    repo = Path(__file__).resolve().parents[2]
    data, runtime = tmp_path / '独立数据', tmp_path / 'runtime'
    runtime.mkdir()
    frozen = os.environ.get('TRADE_TRAY_TEST_EXE')
    if frozen:
        executable = tmp_path / '独立程序 Trade.exe'
        shutil.copyfile(frozen, executable)
        base_command = [str(executable)]
    else:
        base_command = [sys.executable, str(repo / 'scripts/run_trade_rebuild.py')]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    command = [*base_command, '--no-browser', '--port', str(port), '--data-dir', str(data),
               '--state-file', str(tmp_path / 'state.json')]
    environment = {**os.environ, 'TEMP': str(runtime), 'TMP': str(runtime), 'TMPDIR': str(runtime),
                   'PYINSTALLER_STRICT_UNPACK_MODE': '1'}
    owned = []
    records = []
    with (tmp_path / 'launcher.log').open('wb') as log, httpx.Client(
            base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
        def start():
            child = subprocess.Popen(command, cwd=tmp_path, env=environment, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            owned.append(child)
            return child
        def ready():
            record = load_json(data / '.trade-running.json')
            return record if record and client.get('/health').json().get('instance_id') == record['instance_id'] else None
        def released():
            lock = InstanceLock(data); lock.acquire(); lock.release()
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
            return not list(runtime.glob('_MEI*'))
        try:
            for cycle in range(3):
                child = start()
                record = wait_for(ready, seconds=50); records.append(record)
                window = wait_for(lambda: user.FindWindowW(f'TradeRebuild.Tray.{record["launcher_pid"]}', None))
                assert icon_rect(window)
                assert client.get('/rebuild.html').status_code == 200
                token = client.get('/api/v1/session').json()['data']['csrf_token']
                if cycle == 0:
                    account = client.post('/api/v1/accounts', json={'name': '托盘重启保留账户'},
                        headers={'X-CSRF-Token': token, 'Idempotency-Key': 'tray-fixture'}).json()['data']
                    # Emulate an older release: real lock and API, no discovery file.
                    (data / '.trade-running.json').unlink()
                    assert module.legacy_instance_url(data, port) == f'http://127.0.0.1:{port}/rebuild.html'
                    assert module.legacy_instance_url(tmp_path / 'unrelated', port) is None
                    duplicate = start()
                    assert duplicate.wait(timeout=20) == 0
                    assert child.poll() is None
                    atomic_json(data / '.trade-running.json', record)
                else:
                    assert any(row['id'] == account['id'] for row in client.get('/api/v1/accounts').json()['data'])
                # Native menu command twice, then WM_CLOSE: same production paths.
                assert user.PostMessageW(window, 0x111 if cycle < 2 else 0x10, EXIT_COMMAND if cycle < 2 else 0, 0)
                assert child.wait(timeout=30) == 0
                wait_for(released, seconds=10)
                assert not user.IsWindow(window) and not icon_rect(window)
                assert not (data / '.trade-running.json').exists()
            assert len({r['instance_id'] for r in records}) == 3
            (tmp_path / 'tray-result.json').write_text(json.dumps({'status': 'passed', 'cycles': 3,
                'frozen': bool(frozen), 'checks': ['native_icon_registered_and_removed', 'tray_menu_exit', 'window_close_exit',
                'lock_and_port_released', 'onefile_extraction_cleaned', 'account_persisted', 'legacy_duplicate_reused'],
                'records': records}, indent=2), encoding='utf-8')
        finally:
            for child in owned:
                if child.poll() is None:
                    # These exact Popen handles belong to this test; no global name matching.
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
                    child.wait(timeout=10)
