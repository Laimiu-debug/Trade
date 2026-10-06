"""Resolve application resources and trusted child entry points in source or bundles."""
from pathlib import Path
import os
import sys

WORKER_MODULES = frozenset({
    'trade_app.managed_server', 'trade_app.market.baostock_worker',
    'trade_app.research.backtest_worker', 'trade_app.research.scan_job_worker',
    'trade_app.research.portfolio_worker',
    'trade_app.research.event_store_worker',
    'trade_app.research.lab_worker',
    'trade_app.research.portfolio_analysis_worker',
    'trade_app.platform.directory_picker',
})

_SYSTEM_CHILD_ENV = frozenset({
    'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PATH', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL',
})
_FROZEN_CHILD_ENV = frozenset({
    '_PYI_ARCHIVE_FILE', '_PYI_PARENT_PROCESS_LEVEL', '_PYI_APPLICATION_HOME_DIR',
    '_PYI_LINUX_PROCESS_NAME', '_PYI_SPLASH_IPC',
    'LD_LIBRARY_PATH', 'LD_LIBRARY_PATH_ORIG', 'LIBPATH', 'LIBPATH_ORIG',
})


def child_environment(*, extra_allowed: tuple[str, ...] = ()) -> dict[str, str]:
    """Sanitize owned-worker environments while retaining the active bundle.

    PyInstaller 6.19's documented private fields are copied verbatim, only from
    a frozen parent. Dropping them makes onefile workers unpack again and spawn
    another bootloader, incompatible with a frozen compute Job's one-process limit.
    Do not forward arbitrary _PYI_* values or PYINSTALLER_RESET_ENVIRONMENT:
    these children share the supervising launcher's lifetime and extraction.
    """
    allowed = _SYSTEM_CHILD_ENV | {name.upper() for name in extra_allowed}
    if getattr(sys, 'frozen', False):
        allowed |= _FROZEN_CHILD_ENV
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def resource_root() -> Path:
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[3]


def backend_root() -> Path:
    return resource_root() if getattr(sys, 'frozen', False) else resource_root() / 'backend'


def module_command(module: str, *arguments: str) -> list[str]:
    if getattr(sys, 'frozen', False):
        if module not in WORKER_MODULES:
            raise ValueError('Module is not an application worker')
        return [sys.executable, '--trade-module', module, *arguments]
    return [sys.executable, '-u', '-m', module, *arguments]


def configure_protocol_stdio() -> None:
    # PyInstaller's isolated interpreter ignores PYTHONIOENCODING. Worker JSON
    # must use the same UTF-8 protocol on Chinese Windows and source installs.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')


def dispatch_worker(arguments: list[str]) -> bool:
    if not arguments or arguments[0] != '--trade-module':
        return False
    if len(arguments) < 2 or arguments[1] not in WORKER_MODULES:
        raise SystemExit('Unrecognized application worker')
    configure_protocol_stdio()
    import runpy
    sys.argv = [arguments[1], *arguments[2:]]
    runpy.run_module(arguments[1], run_name='__main__', alter_sys=True)
    return True
