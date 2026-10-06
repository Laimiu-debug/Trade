"""Real owned-child checks: credentials, hard limits, pipes, cancellation and exit."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from trade_app.platform.compute_process import (ComputeProcessError, DEFAULT_COMPUTE_BUDGET,
                                               run_json_process)


@pytest.fixture
def child_module(tmp_path):
    (tmp_path / 'compute_fixture.py').write_text('''
import json, os, sys, time
from trade_app.platform.compute_process import apply_worker_memory_limit
apply_worker_memory_limit()
body = json.load(sys.stdin)
mode = body.get('mode')
if mode == 'sleep':
    if body.get('pid_file'):
        from pathlib import Path
        Path(body['pid_file']).write_text(str(os.getpid()))
    time.sleep(30)
elif mode == 'stdout':
    sys.stdout.write('x' * 4096)
    sys.stdout.flush()
    time.sleep(30)
elif mode == 'stderr':
    sys.stderr.write('x' * 4096)
    sys.stderr.flush()
    time.sleep(30)
elif mode == 'invalid':
    print('not-json')
    sys.exit(0)
elif mode == 'crash':
    sys.exit(7)
elif mode == 'memory':
    try:
        value = bytearray(128 * 1024 * 1024)
        print(json.dumps({'allocated': True}))
    except MemoryError:
        print(json.dumps({'allocated': False}))
    sys.exit(0)
print(json.dumps({'pid': os.getpid(), 'echo': body, 'environment': dict(os.environ)}))
''', encoding='utf-8')
    return tmp_path


def run_child(child_module, body, **kwargs):
    return run_json_process('compute_fixture', body, module_paths=(child_module,), **kwargs)


def test_frozen_json_only_separate_process_and_sanitized_environment(child_module, monkeypatch):
    monkeypatch.setenv('TRADE_TEST_PRIVATE_SECRET', 'must-not-inherit')
    monkeypatch.setenv('TRADE_REBUILD_DATA_DIR', 'must-not-inherit-path')
    monkeypatch.setenv('_PYI_ARCHIVE_FILE', 'must-not-inherit-while-source')
    monkeypatch.setenv('_PYI_PARENT_PROCESS_LEVEL', '1')
    monkeypatch.setenv('PYINSTALLER_RESET_ENVIRONMENT', '1')
    result = run_child(child_module, {'message': '冻结中文数据', 'bars': [1, 2, 3]})
    assert result.value['pid'] != os.getpid()
    assert result.value['echo'] == {'message': '冻结中文数据', 'bars': [1, 2, 3]}
    assert 'TRADE_TEST_PRIVATE_SECRET' not in result.value['environment']
    assert 'TRADE_REBUILD_DATA_DIR' not in result.value['environment']
    assert '_PYI_ARCHIVE_FILE' not in result.value['environment']
    assert '_PYI_PARENT_PROCESS_LEVEL' not in result.value['environment']
    assert 'PYINSTALLER_RESET_ENVIRONMENT' not in result.value['environment']
    assert result.metrics['memory_enforcement'] == ('windows_job_object' if os.name == 'nt' else 'worker_rlimit_as')
    assert result.metrics['peak_memory_bytes'] > 0
    assert result.metrics['budget']['timeout_seconds'] == 120


def test_frozen_compute_environment_reaches_owned_child_without_application_secrets(child_module, monkeypatch):
    from trade_app.platform import compute_process
    backend = Path(compute_process.__file__).resolve().parents[2]
    executable = sys.executable
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(backend), raising=False)
    # Use a real source child to inspect the exact outgoing environment while
    # simulating the frozen parent's state. The release smoke tests its EXE.
    monkeypatch.setattr(compute_process, 'module_command', lambda module: [executable, '-u', '-m', module])
    fields = {'_PYI_ARCHIVE_FILE': r'C:\移动目录\Trade.exe',
              '_PYI_APPLICATION_HOME_DIR': str(backend), '_PYI_PARENT_PROCESS_LEVEL': '2'}
    for key, value in fields.items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv('_PYI_UNEXPECTED_SECRET', 'private')
    monkeypatch.setenv('MODEL_API_KEY', 'private')
    monkeypatch.setenv('TRADE_MANAGED_CONTEXT', 'private-control-context')
    result = run_child(child_module, {'message': '共享已解包目录'})
    assert {key: result.value['environment'][key] for key in fields} == fields
    assert not {'_PYI_UNEXPECTED_SECRET', 'MODEL_API_KEY', 'TRADE_MANAGED_CONTEXT'} & result.value['environment'].keys()
    assert result.value['pid'] != os.getpid()


@pytest.mark.parametrize('mode,code', [('sleep', 'COMPUTE_TIMEOUT'), ('stdout', 'COMPUTE_OUTPUT_LIMIT'),
                                      ('stderr', 'COMPUTE_OUTPUT_LIMIT'), ('invalid', 'COMPUTE_INVALID_RESULT'),
                                      ('crash', 'COMPUTE_PROCESS_FAILED')])
def test_owned_child_timeout_pipe_caps_and_invalid_results(child_module, mode, code):
    started = time.monotonic()
    budget = replace(DEFAULT_COMPUTE_BUDGET, timeout_seconds=1, output_bytes=1024, stderr_bytes=1024)
    with pytest.raises(ComputeProcessError) as failure:
        run_child(child_module, {'mode': mode}, budget=budget)
    assert failure.value.code == code
    assert time.monotonic() - started < 3
    assert failure.value.metrics['pid'] > 0


@pytest.mark.parametrize('reason,code', [('cancelled', 'COMPUTE_CANCELLED'), ('shutdown', 'COMPUTE_SHUTDOWN')])
def test_cancellation_terminates_owned_child_and_preserves_unrelated_process(child_module, reason, code):
    sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        started = time.monotonic()
        with pytest.raises(ComputeProcessError) as failure:
            run_child(child_module, {'mode': 'sleep'},
                      stop_reason=lambda: reason if time.monotonic() - started > 0.2 else None)
        assert failure.value.code == code
        assert time.monotonic() - started < 2
        assert sibling.poll() is None
        # Opening a terminated PID fails on Windows; on Linux /proc disappears after wait().
        if sys.platform.startswith('linux'):
            assert not Path(f'/proc/{failure.value.metrics["pid"]}').exists()
    finally:
        sibling.terminate()
        sibling.wait(timeout=3)


def test_input_is_rejected_before_spawning(monkeypatch):
    def forbidden_spawn(*_, **__):
        pytest.fail('Input must be bounded before a child is created')
    monkeypatch.setattr(subprocess, 'Popen', forbidden_spawn)
    with pytest.raises(ComputeProcessError, match='冻结计算输入') as failure:
        run_json_process('unused', {'text': 'x' * 100}, budget=replace(DEFAULT_COMPUTE_BUDGET, input_bytes=10))
    assert failure.value.code == 'COMPUTE_INPUT_LIMIT'


def test_hard_memory_ceiling_denies_oversized_allocation(child_module):
    result = run_child(child_module, {'mode': 'memory'},
                       budget=replace(DEFAULT_COMPUTE_BUDGET, memory_bytes=64 * 1024 * 1024))
    assert result.value['allocated'] is False
    assert result.metrics['peak_memory_bytes'] < 64 * 1024 * 1024


@pytest.mark.skipif(os.name != 'nt' or sys.prefix == sys.base_prefix, reason='Windows source venv only')
def test_windows_venv_redirector_computes_and_cancellation_reaps_actual_interpreter(child_module):
    import ctypes
    from ctypes import wintypes
    result = run_child(child_module, {'message': 'venv worker'})
    assert result.value['pid'] != result.metrics['pid']  # The actual interpreter is a child of the redirector.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    pid_file = child_module / 'actual-worker.pid'
    handle = None

    def cancel_running_interpreter():
        nonlocal handle
        if pid_file.exists():
            handle = kernel.OpenProcess(0x100000, False, int(pid_file.read_text()))  # SYNCHRONIZE
            assert handle
            return 'cancelled'
        return None

    try:
        with pytest.raises(ComputeProcessError) as failure:
            run_child(child_module, {'mode': 'sleep', 'pid_file': str(pid_file)},
                      stop_reason=cancel_running_interpreter)
        assert failure.value.code == 'COMPUTE_CANCELLED'
        assert kernel.WaitForSingleObject(handle, 2000) == 0  # The grandchild has exited too.
    finally:
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != 'nt', reason='Windows suspended startup only')
def test_windows_job_setup_failure_never_executes_worker_payload(child_module, monkeypatch):
    from trade_app.platform import compute_process
    pid_file = child_module / 'must-not-start.pid'
    def reject_job(*args, **kwargs):
        raise OSError('Cannot assign compute job')
    monkeypatch.setattr(compute_process, '_WindowsJob', reject_job)
    with pytest.raises(ComputeProcessError) as failure:
        run_child(child_module, {'mode': 'sleep', 'pid_file': str(pid_file)})
    assert failure.value.code == 'COMPUTE_LIMIT_SETUP_FAILED'
    assert not pid_file.exists()
