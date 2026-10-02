"""Supervise only the child started here; switch/rollback never replaces a database."""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from trade_app.platform.lifecycle import atomic_json, backup_details, load_json
from trade_app.platform.types import new_id, utc_now
from trade_app.platform.runtime import module_command
from trade_app.platform.instance_lock import InstanceLock
from trade_app.platform.process_lifetime import OwnedProcessTree


def default_state_path() -> Path:
    return Path.home() / '.trade-rebuild-launcher.json'


def selected_directory(explicit: Path | None, state_path: Path) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    state = load_json(state_path) or {}
    saved = state.get('active_data_dir')
    if isinstance(saved, str) and Path(saved).is_absolute() and (Path(saved) / 'trade.sqlite').is_file():
        return Path(saved).resolve()
    return (Path.home() / 'TradeRebuild').resolve()


def health_matches(port: int, instance_id: str) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/health', timeout=0.5) as response:
            data = json.loads(response.read(4096))
            return (response.status == 200 and data.get('product') == 'trade-rebuild'
                    and data.get('status') == 'ok' and data.get('instance_id') == instance_id)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def running_instance_url(directory: Path) -> str | None:
    record = load_json(directory / '.trade-running.json') or {}
    port, identity = record.get('port'), record.get('instance_id')
    if (type(port) is not int or not 1 <= port <= 65535
            or not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity)
            or record.get('data_dir') != str(directory.resolve()) or record.get('exiting') is True):
        return None
    return f'http://127.0.0.1:{port}/rebuild.html' if health_matches(port, identity) else None


def legacy_instance_url(directory: Path, preferred_port: int) -> str | None:
    """Older releases lack discovery files; require both service and directory identity."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    for port in range(preferred_port, min(65535, preferred_port + 19) + 1):
        base = f'http://127.0.0.1:{port}'
        try:
            def read(path):
                with opener.open(base + path, timeout=0.5) as response:
                    return json.loads(response.read(65536))
            health = read('/health')
            if (health.get('product') != 'trade-rebuild' or health.get('status') != 'ok'
                    or not re.fullmatch('[a-f0-9]{32}', str(health.get('instance_id', '')))):
                continue
            read('/api/v1/session')
            status = read('/api/v1/system/lifecycle').get('data', {})
            if (status.get('supported') is True and status.get('instance_id') == health['instance_id']
                    and status.get('state') == 'running' and isinstance(status.get('data_dir'), str)
                    and Path(status['data_dir']).resolve() == directory.resolve()):
                return base + '/rebuild.html'
        except (OSError, ValueError, AttributeError, urllib.error.URLError):
            continue
    return None


def launch_or_reopen(directory: Path, *, no_browser: bool, start, timeout: float = 40,
                     preferred_port: int = 8011) -> int:
    """Serialize concurrent double-clicks; a stale JSON file alone grants no reuse."""
    guard = InstanceLock(directory, '.trade-launcher.lock')
    deadline = time.monotonic() + timeout
    while True:
        url = running_instance_url(directory)
        if url:
            print(f'Trade already running: {url}', flush=True)
            if not no_browser:
                webbrowser.open(url)
            return 0
        try:
            guard.acquire()
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                print(f'Trade is still starting. See {directory / "launch.log"}', file=sys.stderr)
                return 1
            time.sleep(0.1)
    try:
        while True:
            # The first launch could become ready between probe and lock acquire.
            url = running_instance_url(directory)
            if url:
                print(f'Trade already running: {url}', flush=True)
                if not no_browser:
                    webbrowser.open(url)
                return 0
            lock = InstanceLock(directory)
            try:
                lock.acquire()
            except RuntimeError:
                record = load_json(directory / '.trade-running.json') or {}
                if record.get('exiting') is True and record.get('data_dir') == str(directory.resolve()):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('上一实例仍在退出，请稍后重新打开 Trade。')
                    time.sleep(0.1)
                    continue
                url = legacy_instance_url(directory, preferred_port)
                if url:
                    print(f'Trade already running: {url}', flush=True)
                    if not no_browser:
                        webbrowser.open(url)
                    return 0
                raise RuntimeError(f'数据目录正在被另一个 Trade 占用，但服务未响应。\n'
                                   f'请从旧程序的托盘或系统设置退出后重试。\n目录：{directory}') from None
            else:
                lock.release()
            return start(guard.release)
    finally:
        guard.release()


class _ExitRequested(Exception):
    pass


class ManagedLauncher:
    def __init__(self, backend: Path, *, port: int, state_path: Path, no_browser: bool = False,
                 startup_seconds: float = 30, spawn=None, probe=None, tree_factory=OwnedProcessTree, on_ready=None,
                 desktop=None, shutdown_seconds: float = 15):
        self.backend, self.port, self.state_path = backend.resolve(), port, state_path
        self.no_browser, self.startup_seconds = no_browser, startup_seconds
        self.spawn, self.probe = spawn or subprocess.Popen, probe or health_matches
        self.process = None
        self.context = None
        self.active_dir = None
        self.process_tree = None
        self.tree_factory, self.on_ready = tree_factory, on_ready
        self.desktop, self.shutdown_seconds = desktop, shutdown_seconds
        self.exit_requested = threading.Event()

    def request_exit(self) -> None:
        self.exit_requested.set()
        try:
            self._mark_exiting()
        except OSError:
            pass  # Disk failure must never disable the native exit command.

    def _mark_exiting(self) -> None:
        if self.context and self.active_dir:
            path = self.active_dir / '.trade-running.json'
            record = load_json(path) or {}
            if record.get('instance_id') == self.context['instance_id']:
                atomic_json(path, {**record, 'exiting': True})

    def open_ui(self) -> None:
        if self.context and not self.exit_requested.is_set():
            webbrowser.open(self.context['url'])

    def _check_exit(self) -> None:
        if self.exit_requested.is_set():
            raise _ExitRequested()

    def _clear_owned_record(self) -> None:
        if not self.context or not self.active_dir:
            return
        lock = InstanceLock(self.active_dir)
        try:
            lock.acquire()
        except (OSError, RuntimeError):
            return  # Another live instance owns this directory; never touch its record.
        try:
            path = self.active_dir / '.trade-running.json'
            record = load_json(path) or {}
            if record.get('instance_id') == self.context['instance_id']:
                path.unlink(missing_ok=True)
        finally:
            lock.release()

    def _shutdown_owned(self, control: Path) -> int:
        if self.desktop:
            self.desktop.status('Trade · 正在退出并清理后台')
        if self.process is not None and self.process.poll() is None and self.context:
            try:
                self._mark_exiting()
                atomic_json(control / 'command.json', {'kind': 'exit', 'nonce': self.context['nonce'],
                                                       'instance_id': self.context['instance_id']})
                self.process.wait(timeout=self.shutdown_seconds)
            except subprocess.TimeoutExpired:
                print('Trade shutdown timed out; cleaning up the owned process tree.', file=sys.stderr, flush=True)
            except OSError as exc:
                print(f'Trade shutdown command failed; cleaning up the owned process tree: {exc}', file=sys.stderr, flush=True)
        # The caller's finally closes the Job even after a stalled drain or switch.
        return 0

    def _terminate_owned(self) -> None:
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        finally:
            if self.process_tree:
                self.process_tree.close()
                self.process_tree = None
            if self.process is not None and self.process.stdin:
                self.process.stdin.close()
            self._clear_owned_record()

    def _start(self, directory: Path, control: Path, expected: str | None = None) -> bool:
        self._terminate_owned()
        self._check_exit()
        if self.desktop:
            self.desktop.status('Trade · 正在启动')
        directory.mkdir(parents=True, exist_ok=True)
        context = {'control_dir': str(control), 'nonce': secrets.token_urlsafe(32), 'instance_id': new_id(),
                   'url': f'http://127.0.0.1:{self.port}/rebuild.html', 'expected_fingerprint': expected,
                   'stdin_lifeline': True, 'launcher_pid': os.getpid()}
        # Retain the last operation for polling across the child replacement.
        for filename in ('intent.json', 'command.json'):
            (control / filename).unlink(missing_ok=True)
        self.context, self.active_dir = context, directory
        env = dict(os.environ, TRADE_REBUILD_DATA_DIR=str(directory), TRADE_REBUILD_PORT=str(self.port),
                   TRADE_MANAGED_CONTEXT=json.dumps(context))
        self.process_tree = self.tree_factory()
        try:
            with (directory / 'launch.log').open('ab') as log:
                self.process = self.spawn(module_command('trade_app.managed_server'), cwd=self.backend,
                                          env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.process_tree.assign(self.process)
            # The child must not acquire a DB lock or start workers before ownership
            # is attached. Parent death during this short window produces EOF.
            self.process.stdin.write(b'R')
            self.process.stdin.flush()
        except Exception:
            self._terminate_owned()
            raise
        deadline = time.monotonic() + self.startup_seconds
        while time.monotonic() < deadline:
            self._check_exit()
            code = self.process.poll()
            if code is not None:
                # An already-open browser can switch or exit before our first
                # successful health poll. Accept only an authenticated matching intent.
                intent = self._intent(control, directory)
                return bool(intent and ((code == 42 and intent['kind'] == 'switch')
                                        or (code == 0 and intent['kind'] == 'exit')))
            if self.probe(self.port, context['instance_id']):
                return True
            time.sleep(0.1)
        self._terminate_owned()
        return False

    def _commit_state(self, directory: Path, control: Path, transition: dict | None = None) -> None:
        state = {'active_data_dir': str(directory), 'updated_at': utc_now(), 'last_transition': transition}
        atomic_json(self.state_path, state)
        atomic_json(control / 'status.json', state)

    def _intent(self, control: Path, source: Path) -> dict | None:
        intent = load_json(control / 'intent.json')
        if (not intent or not self.context or not secrets.compare_digest(str(intent.get('nonce', '')), self.context['nonce'])
                or intent.get('instance_id') != self.context['instance_id']
                or intent.get('source_data_dir') != str(source) or intent.get('kind') not in ('exit', 'switch')):
            return None
        return intent

    def _switch(self, intent: dict, source: Path, control: Path) -> bool:
        operation = load_json(control / 'operation.json') or {'id': intent['operation_id'], 'kind': 'switch'}
        target = Path(intent['destination']).resolve()
        recovery = Path(intent['recovery_path']).resolve()
        # Only the source recovery directory is accepted; no unrelated file access.
        if recovery.parent != (source / 'recovery').resolve() or not recovery.is_file():
            raise ValueError('Missing controlled source recovery point')
        if hashlib.sha256(recovery.read_bytes()).hexdigest() != intent.get('recovery_sha256'):
            raise ValueError('Source recovery point digest mismatch')
        target_ok = False
        try:
            target_ok = backup_details(target)[1]['fingerprint'] == intent['expected_fingerprint']
        except Exception:
            pass
        if target_ok and self._start(target, control, intent['expected_fingerprint']):
            complete = {**operation, 'state': 'completed', 'active_data_dir': str(target), 'updated_at': utc_now()}
            self._commit_state(target, control, complete)
            return True
        # The source was preserved and never replaced; simply restart it once.
        self._terminate_owned()
        rolled_back = {**operation, 'state': 'rolled_back', 'active_data_dir': str(source),
                       'error': 'TARGET_START_FAILED', 'updated_at': utc_now()}
        atomic_json(control / 'status.json', {'last_transition': rolled_back})
        if self._start(source, control):
            self._commit_state(source, control, rolled_back)
            return True
        rolled_back.update(state='failed', error='ROLLBACK_START_FAILED', updated_at=utc_now())
        self._commit_state(source, control, rolled_back)
        return False

    def run(self, initial_directory: Path) -> int:
        with tempfile.TemporaryDirectory(prefix='trade-managed-') as temporary:
            control = Path(temporary)
            previous = load_json(self.state_path) or {}
            atomic_json(control / 'status.json', previous)
            try:
                if self.desktop:
                    self.desktop.start()
                if not self._start(initial_directory, control):
                    print(f'Service failed to start. See {initial_directory / "launch.log"}', file=sys.stderr)
                    return 1
                self._commit_state(initial_directory, control, previous.get('last_transition'))
                self._check_exit()
                if self.process.poll() == 0 and self._intent(control, initial_directory):
                    return 0  # A completed early exit must not reopen a browser or show an error.
                if self.desktop:
                    self.desktop.status('Trade · 复盘工作台', can_open=True)
                if self.on_ready:
                    self.on_ready()
                print(f'Trade ready: {self.context["url"]}', flush=True)
                print(f'Data directory: {initial_directory}', flush=True)
                if not self.no_browser:
                    webbrowser.open(self.context['url'])
                while True:
                    self._check_exit()
                    try:
                        code = self.process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        continue
                    source = self.active_dir
                    intent = self._intent(control, source)
                    if code != 42 or intent is None or intent['kind'] != 'switch':
                        return code
                    try:
                        if not self._switch(intent, source, control):
                            return 1
                    except _ExitRequested:
                        raise
                    except Exception:
                        # Invalid handoff does not get to choose another directory.
                        # A target may already be ready when persistence fails.
                        # Release our own child and port before restarting source.
                        self._terminate_owned()
                        if not self._start(source, control):
                            return 1
                        self._commit_state(source, control, {'id': intent.get('operation_id'), 'kind': 'switch',
                            'state': 'rolled_back', 'error': 'HANDOFF_VERIFICATION_FAILED',
                            'active_data_dir': str(source), 'updated_at': utc_now()})
                    if self.desktop:
                        self.desktop.status('Trade · 复盘工作台', can_open=True)
            except (KeyboardInterrupt, _ExitRequested):
                return self._shutdown_owned(control)
            finally:
                try:
                    self._terminate_owned()
                finally:
                    if self.desktop:
                        self.desktop.close()
