"""Corrupt archives must be rejected before publishing or altering any database."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import zipfile

import pytest

from trade_app.platform.backup import create_backup, restore_to_new_directory, verify_backup
from trade_app.platform.db import open_database
from trade_app.platform.schema import MIGRATION_TABLE_SQL, migration_sources
from test_system_storage import upload
from test_wyckoff_research_api import client_for


def archive_bytes(contents: bytes, *, version='trade-rebuild-backup-v3', manifest=None) -> bytes:
    if manifest is None:
        manifest = {'format': version, 'database': 'trade.sqlite', 'bytes': len(contents),
                    'sha256': hashlib.sha256(contents).hexdigest()}
        if version != 'trade-rebuild-backup-v1':
            manifest['files'] = {}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('trade.sqlite', contents)
    return output.getvalue()


def malformed_manifest(case):
    manifest = {'format': 'trade-rebuild-backup-v3', 'database': 'trade.sqlite', 'bytes': 2,
                'sha256': hashlib.sha256(b'db').hexdigest(), 'files': {}}
    if case == 'array': return []
    if case == 'null': return None
    if case == 'string': return 'invalid'
    if case == 'format_type': manifest['format'] = []
    elif case.startswith('bytes_'):
        manifest['bytes'] = {'boolean': True, 'float': 2.0, 'string': '2', 'negative': -1,
                             'zero': 0, 'too_large': 4 * 1024**3 + 1}[case.removeprefix('bytes_')]
    elif case == 'digest_type': manifest['sha256'] = []
    elif case == 'digest_invalid': manifest['sha256'] = 'not-a-digest'
    elif case == 'files_type': manifest['files'] = []
    elif case == 'file_spec_type': manifest['files'] = {'market/' + '0' * 64 + '.json': 1}
    elif case == 'file_size_type':
        manifest['files'] = {'market/' + '0' * 64 + '.json': {'sha256': '0' * 64, 'bytes': True}}
    elif case == 'v1_hidden_files':
        manifest['format'] = 'trade-rebuild-backup-v1'
        manifest['files'] = {'../../outside.bin': {'sha256': '0' * 64, 'bytes': 0}}
    return manifest


@pytest.mark.parametrize('case', ['array', 'null', 'string', 'format_type', 'bytes_boolean',
    'bytes_float', 'bytes_string', 'bytes_negative', 'bytes_zero', 'bytes_too_large',
    'digest_type', 'digest_invalid', 'files_type', 'file_spec_type', 'file_size_type', 'v1_hidden_files'])
def test_invalid_manifest_schema_is_a_validation_error_and_never_restores(tmp_path, case):
    # Use JSON null explicitly rather than archive_bytes' default manifest sentinel.
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(malformed_manifest(case)))
        archive.writestr('trade.sqlite', b'db')
    contents = output.getvalue()
    destination = tmp_path / 'restored'
    with client_for(tmp_path / 'app') as client:
        for path, fields in [('/backups/preview', None), ('/backups/restore-new',
            {'destination': str(destination), 'expected_fingerprint': '0' * 64})]:
            response = upload(client, path, contents, fields)
            assert response.status_code == 400, response.text
            assert response.json()['error']['code'] == 'INVALID_BACKUP'
    with pytest.raises(ValueError):
        restore_to_new_directory(contents, destination)
    assert not destination.exists()
    assert not (tmp_path / 'outside.bin').exists()


@pytest.mark.parametrize('damage', ['round_notes_table', 'round_notes_column', 'unknown_migration',
                                  'migration_gap', 'missing_history', 'history_column'])
def test_applied_migration_schema_damage_is_rejected_by_export_api_restore_and_startup(tmp_path, damage):
    source = tmp_path / 'damaged'
    engine, _factory = open_database(source)
    engine.dispose()
    statements = {
        'round_notes_table': 'DROP TABLE round_notes',
        'round_notes_column': 'ALTER TABLE round_notes DROP COLUMN summary',
        'unknown_migration': "INSERT INTO schema_migrations(version) VALUES ('9999_unknown')",
        'migration_gap': "DELETE FROM schema_migrations WHERE version = '0016_round_notes'",
        'missing_history': 'DROP TABLE schema_migrations',
        'history_column': 'ALTER TABLE schema_migrations DROP COLUMN applied_at',
    }
    path = source / 'trade.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(statements[damage])
        connection.commit()
    before = path.read_bytes()
    contents = archive_bytes(before)
    with pytest.raises(ValueError): create_backup(source)
    with pytest.raises(ValueError): verify_backup(contents)
    destination = tmp_path / 'refused'
    with pytest.raises(ValueError): restore_to_new_directory(contents, destination)
    with pytest.raises(ValueError): open_database(source)
    with client_for(tmp_path / 'healthy') as client:
        response = upload(client, '/backups/preview', contents)
        assert response.status_code == 400 and response.json()['error']['code'] == 'INVALID_BACKUP'
        response = upload(client, '/backups/restore-new', contents,
            {'destination': str(destination), 'expected_fingerprint': '0' * 64})
        assert response.status_code == 400 and response.json()['error']['code'] == 'INVALID_BACKUP'
    assert path.read_bytes() == before
    assert not destination.exists()


@pytest.mark.parametrize('version,count', [('trade-rebuild-backup-v1', 1),
    ('trade-rebuild-backup-v1', 16), ('trade-rebuild-backup-v2', 19), ('trade-rebuild-backup-v3', 37)])
def test_legitimate_old_migration_prefix_remains_restorable_and_can_upgrade(tmp_path, version, count):
    old = tmp_path / 'old.sqlite'
    with closing(sqlite3.connect(old)) as connection:
        connection.execute(MIGRATION_TABLE_SQL)
        for name, sql in migration_sources()[:count]:
            for statement in sql.split(';'):
                if statement.strip(): connection.execute(statement.strip())
            connection.execute('INSERT INTO schema_migrations(version) VALUES (?)', (name,))
        connection.commit()
    before = old.read_bytes()
    contents = archive_bytes(before, version=version)
    assert verify_backup(contents)['format'] == version
    restored = tmp_path / 'restored'
    restore_to_new_directory(contents, restored)
    assert (restored / 'trade.sqlite').read_bytes() == before
    engine, _factory = open_database(restored)
    engine.dispose()
    with closing(sqlite3.connect(restored / 'trade.sqlite')) as connection:
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == len(migration_sources())
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='round_notes'").fetchone()
    assert old.read_bytes() == before


def test_missing_backup_source_is_never_created(tmp_path):
    with pytest.raises(FileNotFoundError): create_backup(tmp_path / 'missing')
    assert not (tmp_path / 'missing').exists()


def test_offline_cli_rejects_missing_applied_table_without_publishing_target(tmp_path):
    source = tmp_path / 'source'
    engine, _factory = open_database(source)
    engine.dispose()
    with closing(sqlite3.connect(source / 'trade.sqlite')) as connection:
        connection.execute('DROP TABLE round_notes')
        connection.commit()
    archive = tmp_path / 'invalid.zip'
    archive.write_bytes(archive_bytes((source / 'trade.sqlite').read_bytes()))
    destination = tmp_path / 'new-data'
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'trade_rebuild_backup.py'
    done = subprocess.run([sys.executable, str(script), 'restore', str(archive), str(destination)],
                          capture_output=True, timeout=20)
    assert done.returncode != 0
    assert not destination.exists()


@pytest.mark.parametrize('member', ['manifest.json', 'trade.sqlite'])
def test_corrupt_deflate_stream_is_rejected_by_api_and_offline_restore(tmp_path, member):
    source = tmp_path / 'source'
    engine, _factory = open_database(source)
    engine.dispose()
    contents = bytearray(create_backup(source))
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        offset = archive.getinfo(member).header_offset
    name_length, extra_length = struct.unpack_from('<HH', contents, offset + 26)
    # Deflate BTYPE=3 is reserved and provokes zlib.error before the CRC check.
    contents[offset + 30 + name_length + extra_length] = 7
    destination = tmp_path / 'refused'
    with client_for(tmp_path / 'app') as client:
        for path, fields in [('/backups/preview', None), ('/backups/restore-new',
            {'destination': str(destination), 'expected_fingerprint': '0' * 64})]:
            response = upload(client, path, bytes(contents), fields)
            assert response.status_code == 400, response.text
            assert response.json()['error']['code'] == 'INVALID_BACKUP'
    with pytest.raises(ValueError): restore_to_new_directory(bytes(contents), destination)
    assert not destination.exists()


def test_export_rejects_invalid_sqlite_without_modifying_source(tmp_path):
    path = tmp_path / 'trade.sqlite'
    path.write_bytes(b'not a SQLite database')
    before = path.read_bytes()
    with pytest.raises(ValueError): create_backup(tmp_path)
    assert path.read_bytes() == before


def test_storage_uri_handles_hash_and_percent_in_directory_without_creating_parent_file(tmp_path):
    root = tmp_path / 'data#history%25'
    with client_for(root) as client:
        response = client.get('/api/v1/system/storage')
        assert response.status_code == 200, response.text
        assert response.json()['data']['data_dir'] == str(root)
        assert response.json()['data']['usage']['database']['file_count'] >= 1
    assert (root / 'trade.sqlite').is_file()
    assert not (tmp_path / 'data').exists()
