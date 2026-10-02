import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from trade_app.platform import directory_picker as picker
from trade_app.platform.types import TradeError
from test_wyckoff_research_api import client_for, data, write


def test_external_server_cannot_open_desktop_dialog(tmp_path):
    with client_for(tmp_path) as client:
        assert data(client.get('/api/v1/system/directory-picker')) == {'supported': False}
        response = write(client, '/system/directory-picker', {})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'DIRECTORY_PICKER_UNAVAILABLE'


@pytest.mark.parametrize('cancelled', [True, False])
@pytest.mark.parametrize('frozen', [False, True])
def test_choose_only_returns_directory_without_writing_it(tmp_path, monkeypatch, cancelled, frozen):
    chosen = tmp_path / '中文所选目录'
    chosen.mkdir()
    monkeypatch.setattr(picker, 'supported', lambda managed: managed)
    monkeypatch.setenv('MODEL_API_KEY', 'must-not-leave-parent')
    monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR', str(tmp_path))
    monkeypatch.setenv('_PYI_ARCHIVE_FILE', r'C:\Moved\Trade.exe')
    monkeypatch.setenv('_PYI_PARENT_PROCESS_LEVEL', '2')
    monkeypatch.setenv('PYINSTALLER_RESET_ENVIRONMENT', '1')
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    def run(command, **kwargs):
        assert command[-1] == 'trade_app.platform.directory_picker'
        assert 'MODEL_API_KEY' not in kwargs['env']
        assert 'PYINSTALLER_RESET_ENVIRONMENT' not in kwargs['env']
        assert kwargs['env'].get('_PYI_APPLICATION_HOME_DIR') == (str(tmp_path) if frozen else None)
        assert kwargs['env'].get('_PYI_ARCHIVE_FILE') == (r'C:\Moved\Trade.exe' if frozen else None)
        assert kwargs['env'].get('_PYI_PARENT_PROCESS_LEVEL') == ('2' if frozen else None)
        if frozen: assert kwargs['cwd'] == tmp_path
        assert json.loads(kwargs['input']) == {'initial': str(tmp_path)}
        assert kwargs['timeout'] == 180
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'cancelled': cancelled, 'path': None if cancelled else str(chosen)}, ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(picker.subprocess, 'run', run)
    result = picker.choose_directory(managed=True, initial=tmp_path)
    assert result == {'cancelled': cancelled, 'path': None if cancelled else str(chosen)}
    assert list(chosen.iterdir()) == []


def test_timeout_releases_dialog_lock_and_parallel_selection_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(picker, 'supported', lambda managed: True)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 180)
    monkeypatch.setattr(picker.subprocess, 'run', timeout)
    with pytest.raises(TradeError) as failed:
        picker.choose_directory(managed=True, initial=tmp_path)
    assert failed.value.code == 'DIRECTORY_PICKER_TIMEOUT'
    assert picker._dialog_lock.acquire(blocking=False)
    try:
        with pytest.raises(TradeError) as blocked:
            picker.choose_directory(managed=True, initial=tmp_path)
        assert blocked.value.code == 'DIRECTORY_PICKER_BUSY'
    finally:
        picker._dialog_lock.release()
