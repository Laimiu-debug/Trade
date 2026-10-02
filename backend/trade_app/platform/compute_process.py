"""Run one owned Python compute child with bounded JSON pipes and resources.

The worker receives no database handle or ambient application credentials. On
Windows an owned Job Object enforces a private-memory ceiling and kills its
process on close; on Linux the worker applies RLIMIT_AS and the parent samples
RSS. Only processes created here are terminated by timeout/cancellation.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
from trade_app.platform.runtime import backend_root, child_environment, module_command
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ComputeBudget:
    version: str = 'single-cpu-json-v1'
    timeout_seconds: float = 120.0
    memory_bytes: int = 512 * 1024 * 1024
    input_bytes: int = 4 * 1024 * 1024
    output_bytes: int = 16 * 1024 * 1024
    stderr_bytes: int = 64 * 1024
    poll_seconds: float = 0.1
    cancel_grace_seconds: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_COMPUTE_BUDGET = ComputeBudget()


class ComputeProcessError(Exception):
    def __init__(self, code: str, message: str, metrics: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.metrics = code, message, metrics or {}


@dataclass(frozen=True)
class ComputeResult:
    value: dict
    metrics: dict


class _WindowsJob:
    """A handle-scoped Job Object, attached before sending any compute payload."""
    def __init__(self, pid: int, memory_bytes: int):
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
                        'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        self.info_type = Extended
        process_handle = None
        try:
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = Extended()
            limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x8  # kill on close, memory, one process
            limits.BasicLimitInformation.ActiveProcessLimit = 1
            limits.ProcessMemoryLimit = memory_bytes
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            process_handle = self.kernel.OpenProcess(0x100 | 0x1, False, pid)  # set quota + terminate
            if not process_handle or not self.kernel.AssignProcessToJobObject(self.handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        finally:
            if process_handle:
                self.kernel.CloseHandle(process_handle)

    def peak_memory(self) -> int:
        info = self.info_type()
        if self.handle and self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            return int(info.PeakProcessMemoryUsed)
        return 0

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def apply_worker_memory_limit() -> None:
    """Call before importing calculators in a trusted worker entry point."""
    raw = os.environ.pop('TRADE_COMPUTE_MEMORY_BYTES', '')
    if raw and os.name != 'nt':
        import resource
        limit = int(raw)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _rss(pid: int) -> int:
    if sys.platform.startswith('linux'):
        try:
            for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
    return 0


def run_json_process(module: str, payload: dict, *, budget: ComputeBudget = DEFAULT_COMPUTE_BUDGET,
                     stop_reason: Callable[[], str | None] | None = None,
                     module_paths: tuple[Path, ...] = ()) -> ComputeResult:
    """Execute a trusted module; ``stop_reason`` may return cancelled or shutdown."""
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)*', module):
        raise ValueError('Invalid compute module')
    if not (0 < budget.timeout_seconds <= 3600 and budget.memory_bytes >= 16 * 1024 * 1024
            and 0 < budget.input_bytes <= 64 * 1024 * 1024 and 0 < budget.output_bytes <= 64 * 1024 * 1024
            and 0 < budget.stderr_bytes <= 1024 * 1024 and 0 < budget.poll_seconds <= 1
            and 0 < budget.cancel_grace_seconds <= 5):
        raise ValueError('Invalid compute budget')
    contents = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(contents) > budget.input_bytes:
        raise ComputeProcessError('COMPUTE_INPUT_LIMIT', '冻结计算输入超过资源预算')
    backend = backend_root()
    environment = child_environment()
    environment.update({'PYTHONPATH': os.pathsep.join(str(path.resolve()) for path in (backend, *module_paths)),
                        'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                        'TRADE_COMPUTE_MEMORY_BYTES': str(budget.memory_bytes)})
    started = time.monotonic()
    process = subprocess.Popen(module_command(module), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=backend, env=environment,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
                               start_new_session=os.name != 'nt')
    job = None
    peak_memory = 0
    readers: list[threading.Thread] = []
    chunks: dict[str, list[bytes]] = {'stdout': [], 'stderr': []}
    sizes = {'stdout': 0, 'stderr': 0}
    overflow = threading.Event()
    errors = []

    def metrics() -> dict:
        return {'pid': process.pid, 'elapsed_seconds': round(time.monotonic() - started, 4),
                'peak_memory_bytes': peak_memory, 'stdout_bytes': sizes['stdout'],
                'stderr_bytes': sizes['stderr'], 'budget': budget.to_dict(),
                'memory_enforcement': 'windows_job_object' if os.name == 'nt' else 'worker_rlimit_as'}

    def read_pipe(name: str, maximum: int):
        try:
            stream = getattr(process, name)
            while part := stream.read1(16 * 1024):
                remaining = maximum - sizes[name]
                if remaining > 0:
                    chunks[name].append(part[:remaining])
                sizes[name] += len(part)
                if sizes[name] > maximum:
                    overflow.set()
        except (OSError, ValueError) as exc:
            errors.append(type(exc).__name__)

    def feed_input():
        try:
            process.stdin.write(contents)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    try:
        if os.name == 'nt':
            try:
                job = _WindowsJob(process.pid, budget.memory_bytes)
            except OSError as exc:
                raise ComputeProcessError('COMPUTE_LIMIT_SETUP_FAILED', '无法设置计算进程资源限制') from exc
        for name, cap in (('stdout', budget.output_bytes), ('stderr', budget.stderr_bytes)):
            thread = threading.Thread(target=read_pipe, args=(name, cap), daemon=True,
                                      name=f'trade-compute-{name}-{process.pid}')
            readers.append(thread)
            thread.start()
        writer = threading.Thread(target=feed_input, daemon=True, name=f'trade-compute-input-{process.pid}')
        readers.append(writer)
        writer.start()
        while True:
            reason = stop_reason() if stop_reason else None
            if reason:
                code = 'COMPUTE_SHUTDOWN' if reason == 'shutdown' else 'COMPUTE_CANCELLED'
                raise ComputeProcessError(code, '计算已因服务停止中断' if reason == 'shutdown' else '计算已取消', metrics())
            peak_memory = max(peak_memory, job.peak_memory() if job else _rss(process.pid))
            if peak_memory > budget.memory_bytes:
                raise ComputeProcessError('COMPUTE_MEMORY_LIMIT', '计算内存超过资源预算', metrics())
            if overflow.is_set():
                raise ComputeProcessError('COMPUTE_OUTPUT_LIMIT', '计算产物超过资源预算', metrics())
            if time.monotonic() - started > budget.timeout_seconds:
                raise ComputeProcessError('COMPUTE_TIMEOUT', '计算超过时间预算', metrics())
            if process.poll() is not None:
                break
            time.sleep(budget.poll_seconds)
        for reader in readers:
            reader.join(timeout=budget.cancel_grace_seconds)
        if overflow.is_set():
            raise ComputeProcessError('COMPUTE_OUTPUT_LIMIT', '计算产物超过资源预算', metrics())
        if errors or any(reader.is_alive() for reader in readers):
            raise ComputeProcessError('COMPUTE_PIPE_ERROR', '计算进程通信未正常完成', metrics())
        output = b''.join(chunks['stdout'])
        if process.returncode:
            raise ComputeProcessError('COMPUTE_PROCESS_FAILED', f'计算进程异常退出（{process.returncode}）', metrics())
        try:
            value = json.loads(output)
            if not isinstance(value, dict):
                raise ValueError('Object required')
        except (ValueError, UnicodeError) as exc:
            raise ComputeProcessError('COMPUTE_INVALID_RESULT', '计算进程返回了无效结果', metrics()) from exc
        return ComputeResult(value, metrics())
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=budget.cancel_grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=budget.cancel_grace_seconds)
        if job:
            job.close()
        for reader in readers:
            reader.join(timeout=budget.cancel_grace_seconds)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
