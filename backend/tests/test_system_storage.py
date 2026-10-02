import io
import zipfile
from pathlib import Path

import pytest

from trade_app.platform import backup as backups
from test_wyckoff_research_api import client_for, data, dataset, write


def upload(client, path, contents, fields=None):
    return client.post('/api/v1/system' + path, files={'file': ('backup.zip', contents, 'application/zip')},
                       data=fields or {})


def test_copy_preview_freezes_revision_refuses_stale_and_preserves_original(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'copy'
    with client_for(source) as client:
        account = data(write(client, '/accounts', {'name': '保留的原账户'}))
        ds = dataset(client)
        status = data(client.get('/api/v1/system/storage'))
        assert status['data_dir'] == str(source.resolve())
        assert status['usage']['market']['file_count'] == 1
        assert status['migration_versions']
        preview = data(write(client, '/system/storage/copy-preview', {'destination': str(target)}))
        assert not target.exists()
        data(write(client, '/accounts', {'name': '预览后新增'}))
        stale = write(client, '/system/storage/copy', {'destination': str(target), 'expected_fingerprint': preview['fingerprint']})
        assert stale.status_code == 409 and stale.json()['error']['code'] == 'STORAGE_PREVIEW_STALE'
        assert not target.exists()
        preview = data(write(client, '/system/storage/copy-preview', {'destination': str(target)}))
        copied = data(write(client, '/system/storage/copy', {'destination': str(target), 'expected_fingerprint': preview['fingerprint']}))
        assert copied['active_data_dir'] == str(source)
        assert copied['switch_requires_restart'] is True
        assert len(data(client.get('/api/v1/accounts'))) == 2
        assert write(client, '/system/storage/copy-preview', {'destination': str(target)}).status_code == 409
    with client_for(target) as client:
        assert account['id'] in {item['id'] for item in data(client.get('/api/v1/accounts'))}
        assert data(client.get('/api/v1/market/datasets/' + ds['id']))['bars']


def test_backup_upload_preflight_rejects_mismatch_and_restores_only_empty_target(tmp_path):
    source, restored = tmp_path / 'source', tmp_path / 'restored'
    with client_for(source) as client:
        account = data(write(client, '/accounts', {'name': '恢复验收'}))
        archive = client.get('/api/v1/backups/export').content
        preview = data(upload(client, '/backups/preview', archive))
        rejected = upload(client, '/backups/restore-new', archive, {'destination': str(restored), 'expected_fingerprint': '0' * 64})
        assert rejected.status_code == 409 and not restored.exists()
        data(upload(client, '/backups/restore-new', archive, {'destination': str(restored), 'expected_fingerprint': preview['fingerprint']}))
        assert upload(client, '/backups/restore-new', archive, {'destination': str(source), 'expected_fingerprint': preview['fingerprint']}).status_code == 400
        assert data(client.get('/api/v1/accounts'))[0]['id'] == account['id']
    with client_for(restored) as client:
        assert data(client.get('/api/v1/accounts'))[0]['id'] == account['id']


@pytest.mark.parametrize('target_kind', ['same', 'parent', 'child', 'relative', 'root'])
def test_copy_never_targets_current_tree_or_relative_path(tmp_path, target_kind):
    source = tmp_path / 'source'
    targets = {'same': source, 'parent': tmp_path, 'child': source / 'nested', 'relative': Path('relative'), 'root': Path(source.anchor)}
    with client_for(source) as client:
        assert write(client, '/system/storage/copy-preview', {'destination': str(targets[target_kind])}).status_code == 400


def test_restore_extraction_failure_never_publishes_half_database(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'failed-target'
    with client_for(source) as client:
        dataset(client)
        archive = client.get('/api/v1/backups/export').content
    real_open = Path.open

    def broken_open(self, mode='r', *args, **kwargs):
        if mode == 'xb' and self.suffix == '.json':
            raise OSError('simulated disk full')
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', broken_open)
    with pytest.raises(OSError, match='disk full'):
        backups.restore_to_new_directory(archive, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.trade-restore-*'))
    assert (source / 'trade.sqlite').is_file()


def test_upload_rejects_malformed_and_oversized_archive_metadata(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        assert upload(client, '/backups/preview', b'not a zip').status_code == 400
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', '{}')
            archive.writestr('large', b'x' * 100)
        monkeypatch.setattr('trade_app.api.system_routes.EXPANDED_LIMIT', 50)
        assert upload(client, '/backups/preview', blob.getvalue()).status_code == 400


@pytest.mark.parametrize('failure,code,status', [
    (PermissionError('private system detail'), 'STORAGE_ACCESS_DENIED', 403),
    (OSError(28, 'disk full'), 'STORAGE_SPACE_LOW', 507),
    (FileExistsError('race'), 'STORAGE_NOT_EMPTY', 409),
])
def test_restore_filesystem_failure_is_actionable_and_leaves_original_active(tmp_path, monkeypatch, failure, code, status):
    source, target = tmp_path / 'source', tmp_path / 'target'
    with client_for(source) as client:
        preview = data(write(client, '/system/storage/copy-preview', {'destination': str(target)}))
        def fail(*_):
            raise failure
        monkeypatch.setattr('trade_app.api.system_routes.restore_to_new_directory', fail)
        response = write(client, '/system/storage/copy', {'destination': str(target), 'expected_fingerprint': preview['fingerprint']})
        assert response.status_code == status and response.json()['error']['code'] == code
        assert 'private system detail' not in response.text
        assert not target.exists()
        assert data(client.get('/api/v1/system/storage'))['data_dir'] == str(source)
