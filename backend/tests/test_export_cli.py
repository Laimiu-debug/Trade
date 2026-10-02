import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from trade_app.platform.local_api_client import LocalAPI
from trade_app.platform.types import TradeError
from trade_app.reviews.export_cli import export_pdf
from trade_app.api import settings_service
from test_sync_cli import server


def write(client, path, value, method='POST'):
    return client.request('/api/v1' + path, method, value, request_id=uuid4().hex)


def test_actual_cli_exports_signed_review_to_configured_directory_without_overwriting(server, tmp_path):
    _, url = server
    client = LocalAPI(url)
    client.connect()
    account = write(client, '/accounts', {'name': '独立打印账户'})['id']
    write(client, '/accounts/' + account + '/daily-reviews/2025-01-06',
          {'expected_revision': 0, 'title': '中文复盘', 'decision_review': '保留原始记录'}, 'PUT')
    directory = tmp_path / 'exports'
    write(client, '/settings/groups/print', {'expected_revision': 0,
          'value': {'author': '中文署名 <示例>', 'export_directory': str(directory)}}, 'PUT')
    before = client.request('/api/v1/accounts')
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(root / 'scripts/run_trade_rebuild.py'), 'export', '--url', url,
               'review', '--account', account, '--kind', 'daily', '--key', '2025-01-06']
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    done = subprocess.run(command, capture_output=True, timeout=45, env=env)
    assert done.returncode == 0, done.stderr.decode('utf-8')
    result = json.loads(done.stdout)
    output = Path(result['path'])
    raw = output.read_bytes()
    assert output.parent == directory and raw.startswith(b'%PDF-') and b'/FontFile2' in raw
    assert result['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['bytes'] == len(raw)
    repeated = subprocess.run(command, capture_output=True, timeout=45, env=env)
    assert repeated.returncode == 1 and json.loads(repeated.stderr)['error'] == 'EXPORT_FILE_EXISTS'
    assert output.read_bytes() == raw
    assert client.request('/api/v1/accounts') == before
    # Metadata and cover use the same saved author, without changing saved review text.
    path = f'/api/v1/accounts/{account}/exports/review/daily/2025-01-06.md'
    with client.opener.open(client.base + path) as response:
        assert '署名：中文署名 <示例>' in response.read().decode('utf-8')
    assert client.request('/api/v1/accounts/' + account + '/daily-reviews/2025-01-06')['decision_review'] == '保留原始记录'


def test_statistics_export_and_failures_never_create_a_misleading_pdf(server, tmp_path):
    _, url = server
    client = LocalAPI(url)
    account = write(client, '/accounts', {'name': '统计'})['id']
    with pytest.raises(TradeError, match='导出目录'):
        export_pdf(client, account, 'statistics')
    target = tmp_path / 'statistics.pdf'
    result = export_pdf(client, account, 'statistics', output=target)
    assert result['state'] == 'exported' and target.read_bytes().startswith(b'%PDF-')
    missing = tmp_path / 'missing.pdf'
    with pytest.raises(TradeError) as error:
        export_pdf(client, 'f' * 32, 'statistics', output=missing)
    assert error.value.code == 'EXPORT_API_ERROR' and not missing.exists()
    with pytest.raises(TradeError):
        export_pdf(client, account, 'review', key='2025-02-30', output=missing)
    with pytest.raises(TradeError):
        export_pdf(client, account, 'review', kind='weekly', key=None, output=missing)
    assert not missing.exists()


def test_print_settings_revision_validation_and_reset_are_isolated(server, tmp_path):
    app, _ = server
    with app.state.db_factory.begin() as session:
        before = settings_service.get_group(session, 'market_sources')
        initial = settings_service.get_group(session, 'print')
        assert initial['revision'] == 0 and initial['value'] == {'author': '', 'export_directory': ''}
        custom = {'author': '  打印人  ', 'export_directory': str(tmp_path / 'output')}
        saved = settings_service.save_group(session, 'print', None, 0, custom)
        assert saved['revision'] == 1 and saved['value']['author'] == '打印人'
        with pytest.raises(TradeError) as conflict:
            settings_service.save_group(session, 'print', None, 0, custom)
        assert conflict.value.code == 'SETTINGS_REVISION_CONFLICT'
        for bad in ({'author': 'A', 'export_directory': 'relative'}, {'author': 'a\nb', 'export_directory': ''},
                    {'author': 'a' * 81, 'export_directory': ''}, {**custom, 'unknown': True}):
            with pytest.raises(TradeError):
                settings_service.save_group(session, 'print', None, 1, bad)
        preview = settings_service.preview(session, 'print', None, 1)
        assert settings_service.get_group(session, 'print')['revision'] == 1
        reset = settings_service.apply_preview(session, 'print', None, 1, preview['preview_sha256'])
        assert reset['revision'] == 2 and reset['value'] == initial['value']
        assert settings_service.get_group(session, 'market_sources') == before
        assert not (tmp_path / 'output').exists()
