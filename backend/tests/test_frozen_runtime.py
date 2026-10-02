import io
import json
import os
import runpy
import sys

import pytest

from trade_app.platform.runtime import child_environment, dispatch_worker, module_command


def test_frozen_worker_forces_utf8_despite_host_console_encoding(monkeypatch):
    output, errors, incoming = io.BytesIO(), io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, 'stdout', io.TextIOWrapper(output, encoding='gbk'))
    monkeypatch.setattr(sys, 'stderr', io.TextIOWrapper(errors, encoding='gbk'))
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(incoming, encoding='gbk'))
    def execute(module, **kwargs):
        assert module == 'trade_app.research.backtest_worker'
        assert kwargs == {'run_name': '__main__', 'alter_sys': True}
        sys.stdout.write(json.dumps({'message': '已完成组合计算'}, ensure_ascii=False))
        sys.stdout.flush()
    monkeypatch.setattr(runpy, 'run_module', execute)
    # PyInstaller ignores PYTHONIOENCODING, so dispatch itself owns this protocol.
    assert dispatch_worker(['--trade-module', 'trade_app.research.backtest_worker'])
    assert json.loads(output.getvalue()) == {'message': '已完成组合计算'}
    assert sys.stdin.encoding == sys.stderr.encoding == 'utf-8'


def test_frozen_dispatch_only_allows_registered_application_modules(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    command = module_command('trade_app.research.event_store_worker')
    assert command == [sys.executable, '--trade-module', 'trade_app.research.event_store_worker']
    with pytest.raises(ValueError):
        module_command('arbitrary_code')
    with pytest.raises(SystemExit):
        dispatch_worker(['--trade-module', 'arbitrary_code'])
    assert dispatch_worker(['--no-browser']) is False


@pytest.mark.parametrize('frozen', [False, True])
def test_child_environment_preserves_only_frozen_bootloader_fields_verbatim(monkeypatch, frozen):
    fields = {
        '_PYI_ARCHIVE_FILE': r'C:\移动后的软件\Trade.exe',
        '_PYI_PARENT_PROCESS_LEVEL': '2',
        '_PYI_APPLICATION_HOME_DIR': r'C:\临时文件\_MEI123456',
        '_PYI_LINUX_PROCESS_NAME': 'Trade', '_PYI_SPLASH_IPC': '0',
        'LD_LIBRARY_PATH': '/tmp/_MEI123456:/usr/local/lib',
        'LD_LIBRARY_PATH_ORIG': '/usr/local/lib',
        'LIBPATH': '/tmp/_MEI123456:/usr/lib', 'LIBPATH_ORIG': '/usr/lib',
    }
    private = {'_PYI_UNRECOGNIZED_SECRET': 'private', 'PYINSTALLER_RESET_ENVIRONMENT': '1',
               'MODEL_API_KEY': 'private', 'TRADE_REBUILD_DATA_DIR': 'private-data',
               'PYTHONPATH': 'untrusted-modules'}
    for key, value in (fields | private | {'DISPLAY': ':7', 'XAUTHORITY': '/tmp/display-cookie'}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    before = dict(os.environ)
    environment = child_environment(extra_allowed=('DISPLAY', 'XAUTHORITY'))
    for key, value in fields.items():
        assert environment.get(key) == (value if frozen else None)
    assert not set(private) & set(environment)
    assert environment['DISPLAY'] == ':7' and environment['XAUTHORITY'] == '/tmp/display-cookie'
    assert dict(os.environ) == before
