from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from trade_app.market import baostock_online as provider
from trade_app.platform.types import TradeError


@pytest.mark.parametrize('frozen', [False, True])
def test_provider_worker_uses_bundle_context_but_not_business_credentials(tmp_path, monkeypatch, frozen):
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    fields = {'_PYI_APPLICATION_HOME_DIR': str(tmp_path), '_PYI_ARCHIVE_FILE': r'C:\移动后的文件\Trade.exe',
              '_PYI_PARENT_PROCESS_LEVEL': '2'}
    for key, value in fields.items(): monkeypatch.setenv(key, value)
    for key in ('MODEL_API_KEY', 'TRADE_MANAGED_CONTEXT', '_PYI_UNKNOWN_SECRET', 'PYINSTALLER_RESET_ENVIRONMENT'):
        monkeypatch.setenv(key, 'private')
    expected = [{'code': 'sh.600000', 'date': '2025-01-02', 'close': '10.02'}]
    def run(command, **kwargs):
        assert command[-4:-1] == ['sh.600000', '2025-01-01', '2025-01-03']
        assert kwargs['timeout'] == 45 and kwargs['capture_output'] is True
        for key, value in fields.items(): assert kwargs['env'].get(key) == (value if frozen else None)
        assert not {'MODEL_API_KEY', 'TRADE_MANAGED_CONTEXT', '_PYI_UNKNOWN_SECRET', 'PYINSTALLER_RESET_ENVIRONMENT'} & kwargs['env'].keys()
        if frozen: assert kwargs['cwd'] == tmp_path
        Path(command[-1]).write_text(json.dumps(expected), encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(provider.subprocess, 'run', run)
    assert provider.fetch_baostock_rows('sh600000', date(2025, 1, 1), date(2025, 1, 3), tmp_path) == expected
    assert list(tmp_path.iterdir()) == []


def test_provider_timeout_removes_only_its_temporary_output(tmp_path, monkeypatch):
    existing = tmp_path / 'preserved.txt'
    existing.write_text('untouched', encoding='utf-8')
    def timed_out(command, **kwargs):
        Path(command[-1]).write_text('partial', encoding='utf-8')
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(provider.subprocess, 'run', timed_out)
    with pytest.raises(TradeError) as failure:
        provider.fetch_baostock_rows('sh600000', date(2025, 1, 1), date(2025, 1, 3), tmp_path)
    assert failure.value.code == 'MARKET_PROVIDER_TIMEOUT'
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text(encoding='utf-8') == 'untouched'
