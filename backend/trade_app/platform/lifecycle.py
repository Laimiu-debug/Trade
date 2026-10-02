"""Owned application lifecycle: quiesce, recoverable handoff, and graceful exit.

The controller has no business-domain imports. The app supplies background
operations and the managed Uvicorn entry point supplies its own exit callback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from typing import Callable

from trade_app.platform.backup import create_backup, verify_backup
from trade_app.platform.instance_lock import InstanceLock
from trade_app.platform.types import TradeError, new_id, utc_now

MAX_BACKUP_BYTES = 512 * 1024 * 1024
PREVIEW_SECONDS = 600
DRAIN_SECONDS = 60


def atomic_json(path: Path, value: dict) -> None:
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8'))


def atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.trade-publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path: Path) -> dict | None:
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def backup_details(data_dir: Path) -> tuple[bytes, dict]:
    """Verify complete business content; cap this interactive lifecycle operation."""
    sizes = sum(path.stat().st_size for pattern in ('trade.sqlite*', 'market/*.json', 'attachments/*.bin')
                for path in data_dir.glob(pattern) if path.is_file())
    if sizes > MAX_BACKUP_BYTES:
        raise TradeError('LIFECYCLE_BACKUP_LIMIT', '数据超过 512 MiB 自动切换预算，请使用离线备份与启动工具', 409)
    try:
        contents = create_backup(data_dir)
        manifest = verify_backup(contents)
    except (OSError, ValueError, KeyError) as exc:
        raise TradeError('LIFECYCLE_DATA_INVALID', '数据目录完整性校验失败', 409) from exc
    fingerprint = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()
    files = manifest.get('files', {})
    return contents, {'fingerprint': fingerprint, 'database_bytes': manifest['bytes'],
                       'total_bytes': manifest['bytes'] + sum(row['bytes'] for row in files.values()),
                       'dataset_count': sum(name.startswith('market/') for name in files),
                       'attachment_count': sum(name.startswith('attachments/') for name in files)}


class LifecycleController:
    def __init__(self, data_dir: Path, *, managed: dict | None = None,
                 request_exit: Callable[[], None] | None = None, drain_seconds: float = DRAIN_SECONDS):
        self.data_dir = data_dir.resolve()
        self.instance_id = (managed or {}).get('instance_id') or new_id()
        self.managed = managed
        self._exit = request_exit
        self._condition = threading.Condition()
        self._quiescing = False
        self._writes = 0
        self._background: dict[str, int] = {}
        self._previews: dict[str, dict] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._operations: dict[str, dict] = {}
        self._current: str | None = None
        self._drain_seconds = drain_seconds

    @property
    def supported(self) -> bool:
        return self.managed is not None and self._exit is not None

    @property
    def quiescing(self) -> bool:
        with self._condition:
            return self._quiescing

    def set_exit_callback(self, callback: Callable[[], None]) -> None:
        self._exit = callback

    def validate_startup(self) -> None:
        """Called by lifespan under the directory lock, before any DB migrations."""
        expected = (self.managed or {}).get('expected_fingerprint')
        if expected and backup_details(self.data_dir)[1]['fingerprint'] != expected:
            raise TradeError('LIFECYCLE_TARGET_CHANGED', '待切换目录在重启前发生变化', 409)

    def _last_transition(self) -> dict | None:
        if not self.managed:
            return None
        record = load_json(Path(self.managed['control_dir']) / 'status.json') or {}
        return record.get('last_transition') if isinstance(record.get('last_transition'), dict) else None

    def status(self) -> dict:
        with self._condition:
            operation = dict(self._operations[self._current]) if self._current else None
            return {'supported': self.supported, 'instance_id': self.instance_id, 'data_dir': str(self.data_dir),
                    'state': operation['state'] if operation and self._quiescing else 'running',
                    'operation': operation, 'last_transition': self._last_transition(),
                    'capabilities': {'exit': self.supported, 'switch': self.supported},
                    'active_writes': self._writes, 'active_background': dict(self._background)}

    def operation(self, operation_id: str) -> dict:
        with self._condition:
            row = self._operations.get(operation_id)
            if row:
                return dict(row)
        previous = self._last_transition()
        if previous and previous.get('id') == operation_id:
            return previous
        raise TradeError('LIFECYCLE_OPERATION_NOT_FOUND', '维护操作不存在', 404)

    def _require_managed(self) -> None:
        if not self.supported:
            raise TradeError('MANAGED_LAUNCHER_REQUIRED', '当前由外部服务启动，请使用 Trade 启动器以控制退出或切换目录', 409)

    def enter_write(self, *, maintenance: bool = False) -> bool:
        with self._condition:
            if self._quiescing and not maintenance:
                return False
            self._writes += 1
            return True

    def leave_write(self) -> None:
        with self._condition:
            self._writes -= 1
            self._condition.notify_all()

    def reconfigure_local_source(self, operation):
        """Only the current HTTP write may be active; block new writes/dispatch during selection."""
        with self._condition:
            if self._quiescing or self._writes > 1 or self._background.get('tdx-universe', 0):
                raise TradeError('TDX_SOURCE_BUSY', '正在保存或扫描通达信数据，请结束当前操作后再切换目录', 409)
            return operation()

    def run_background(self, name: str, operation: Callable[[], bool]) -> bool:
        with self._condition:
            if self._quiescing:
                return False
            self._background[name] = self._background.get(name, 0) + 1
        try:
            return operation()
        finally:
            with self._condition:
                self._background[name] -= 1
                if self._background[name] == 0:
                    del self._background[name]
                self._condition.notify_all()

    def preview_switch(self, destination: str) -> dict:
        self._require_managed()
        raw = Path(destination).expanduser()
        if not raw.is_absolute():
            raise TradeError('INVALID_STORAGE_PATH', '目标目录必须是绝对路径')
        target = raw.resolve()
        if (target == self.data_dir or target in self.data_dir.parents or self.data_dir in target.parents
                or target == Path(target.anchor) or not (target / 'trade.sqlite').is_file()):
            raise TradeError('INVALID_STORAGE_PATH', '请选择当前目录之外、已恢复或复制并包含数据库的独立目录')
        lock = InstanceLock(target)
        try:
            lock.acquire()
        except RuntimeError as exc:
            raise TradeError('LIFECYCLE_TARGET_IN_USE', '目标数据目录正在被另一个程序使用', 409) from exc
        try:
            _, summary = backup_details(target)
        finally:
            lock.release()
        token = secrets.token_urlsafe(24)
        row = {**summary, 'verification_id': token, 'destination': str(target),
               'source_data_dir': str(self.data_dir),
               'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=PREVIEW_SECONDS)).isoformat()}
        with self._condition:
            now = datetime.now(timezone.utc).isoformat()
            self._previews = {key: value for key, value in self._previews.items() if value['expires_at'] > now}
            if len(self._previews) >= 20:
                raise TradeError('LIFECYCLE_PREVIEW_LIMIT', '切换预览过多，请稍后重试', 429)
            self._previews[token] = row
        return dict(row)

    def request(self, kind: str, body: dict, idempotency_key: str) -> dict:
        self._require_managed()
        if kind not in ('exit', 'switch'):
            raise TradeError('INVALID_LIFECYCLE_ACTION', '维护动作无效')
        if not idempotency_key or len(idempotency_key) > 128:
            raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key')
        signature = hashlib.sha256(json.dumps({'kind': kind, 'body': body}, sort_keys=True).encode()).hexdigest()
        with self._condition:
            prior = self._idempotency.get(idempotency_key)
            if prior:
                if prior[0] != signature:
                    raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同维护操作', 409)
                return dict(self._operations[prior[1]])
            if self._quiescing:
                raise TradeError('LIFECYCLE_BUSY', '已有退出或目录切换正在进行', 409)
            preview = None
            if kind == 'switch':
                preview = self._previews.get(body.get('verification_id'))
                if (not preview or preview['expires_at'] <= datetime.now(timezone.utc).isoformat()
                        or preview['fingerprint'] != body.get('expected_fingerprint')):
                    raise TradeError('LIFECYCLE_PREVIEW_STALE', '切换预览已失效，请重新验证目标目录', 409)
            now, operation_id = utc_now(), new_id()
            row = {'id': operation_id, 'kind': kind, 'state': 'draining',
                   'source_data_dir': str(self.data_dir), 'destination': preview['destination'] if preview else None,
                   'recovery_path': None, 'error': None, 'created_at': now, 'updated_at': now,
                   'reconnect_url': self.managed.get('url') if self.managed else None}
            self._operations[operation_id] = row
            self._idempotency[idempotency_key] = (signature, operation_id)
            try:
                atomic_json(Path(self.managed['control_dir']) / 'operation.json', row)
            except OSError as exc:
                del self._operations[operation_id]
                del self._idempotency[idempotency_key]
                raise TradeError('LIFECYCLE_JOURNAL_FAILED', '无法保存维护操作记录') from exc
            self._current, self._quiescing = operation_id, True
            thread = threading.Thread(target=self._execute, args=(operation_id, dict(preview) if preview else None),
                                      name='trade-lifecycle', daemon=True)
            thread.start()
            return dict(row)

    def _update(self, operation_id: str, **fields) -> None:
        with self._condition:
            self._operations[operation_id].update(fields, updated_at=utc_now())
            row = dict(self._operations[operation_id])
        if self.managed:
            atomic_json(Path(self.managed['control_dir']) / 'operation.json', row)

    def _execute(self, operation_id: str, preview: dict | None) -> None:
        target_lock = None
        try:
            if preview:
                target_lock = InstanceLock(Path(preview['destination']))
                try:
                    target_lock.acquire()
                except RuntimeError as exc:
                    raise TradeError('LIFECYCLE_TARGET_IN_USE', '目标目录正在使用', 409) from exc
            deadline = time.monotonic() + self._drain_seconds
            with self._condition:
                while self._writes or self._background:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TradeError('LIFECYCLE_DRAIN_TIMEOUT', '仍有保存或后台请求未结束，已恢复正常使用', 409)
                    self._condition.wait(min(remaining, 0.1))
            intent = {'nonce': self.managed['nonce'], 'instance_id': self.instance_id,
                      'source_data_dir': str(self.data_dir), 'operation_id': operation_id}
            if preview:
                self._update(operation_id, state='snapshotting')
                _, target_summary = backup_details(Path(preview['destination']))
                if target_summary['fingerprint'] != preview['fingerprint']:
                    raise TradeError('LIFECYCLE_TARGET_CHANGED', '目标目录内容已在预览后变化', 409)
                contents, _ = backup_details(self.data_dir)
                recovery = self.data_dir / 'recovery' / f'lifecycle-{operation_id}.zip'
                if recovery.exists():
                    raise TradeError('LIFECYCLE_RECOVERY_EXISTS', '恢复点已存在，拒绝覆盖', 409)
                atomic_bytes(recovery, contents)
                recovery_sha = hashlib.sha256(contents).hexdigest()
                self._update(operation_id, state='restarting', recovery_path=str(recovery))
                intent.update(kind='switch', destination=preview['destination'],
                              expected_fingerprint=preview['fingerprint'], recovery_path=str(recovery),
                              recovery_sha256=recovery_sha)
            else:
                self._update(operation_id, state='exiting')
                intent.update(kind='exit')
            atomic_json(Path(self.managed['control_dir']) / 'intent.json', intent)
            self._exit()
        except Exception as exc:
            intent_path = Path(self.managed['control_dir']) / 'intent.json'
            intent = load_json(intent_path)
            try:
                if intent and intent.get('operation_id') == operation_id:
                    intent_path.unlink(missing_ok=True)
            except OSError:
                pass
            finally:
                with self._condition:
                    self._operations[operation_id].update(state='failed', error=getattr(exc, 'code', 'LIFECYCLE_FAILED'),
                                                         updated_at=utc_now())
                    self._quiescing = False
                    self._condition.notify_all()
                    failed = dict(self._operations[operation_id])
                try:
                    atomic_json(Path(self.managed['control_dir']) / 'operation.json', failed)
                except OSError:
                    pass  # A full disk must still reopen the current source for the user.
        finally:
            if target_lock:
                target_lock.release()


class LifecycleWriteGate:
    """Track through ASGI final body, including incremental AI stream responses."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        controller = getattr(getattr(scope.get('app'), 'state', None), 'lifecycle', None)
        unsafe = (scope['type'] == 'http' and scope.get('path', '').startswith('/api/v1')
                  and scope.get('method') not in ('GET', 'HEAD', 'OPTIONS'))
        if not unsafe or controller is None:
            return await self.app(scope, receive, send)
        maintenance = scope.get('path') in ('/api/v1/system/lifecycle/exit', '/api/v1/system/lifecycle/switch')
        if not controller.enter_write(maintenance=maintenance):
            from starlette.responses import JSONResponse
            response = JSONResponse({'error': {'code': 'LIFECYCLE_QUIESCING',
                                    'message': '应用正在保存恢复点或退出，暂时停止新写入'}}, status_code=503)
            return await response(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            controller.leave_write()
