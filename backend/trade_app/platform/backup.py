"""Consistent SQLite archive and offline restore to a new empty directory."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
import zlib
from contextlib import closing
from pathlib import Path

from trade_app.platform.schema import validate_database


MANIFEST_LIMIT = 8 * 1024 * 1024
DATABASE_LIMIT = 4 * 1024 * 1024 * 1024
MARKET_FILE_LIMIT = 256 * 1024 * 1024
ATTACHMENT_LIMIT = 8 * 1024 * 1024
MEMBER_LIMIT = 100000


def _file_spec(value: object, limit: int, *, database: bool = False) -> dict:
    if not isinstance(value, dict):
        raise ValueError('备份文件描述必须为对象')
    size, digest = value.get('bytes'), value.get('sha256')
    if (type(size) is not int or size < (1 if database else 0) or size > limit
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('备份文件大小或校验值格式无效')
    return value


def _manifest(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError('备份清单必须为对象')
    version = value.get('format')
    if (not isinstance(version, str) or version not in {
            'trade-rebuild-backup-v1', 'trade-rebuild-backup-v2', 'trade-rebuild-backup-v3'}
            or value.get('database') != 'trade.sqlite'):
        raise ValueError('备份版本不受支持')
    _file_spec(value, DATABASE_LIMIT, database=True)
    files = value.get('files', {})
    if not isinstance(files, dict) or (version == 'trade-rebuild-backup-v1' and files):
        raise ValueError('备份文件清单无效')
    allowed = (r'market/[0-9a-f]{64}\.json' if version != 'trade-rebuild-backup-v3'
               else r'(market/[0-9a-f]{64}\.json|attachments/[0-9a-f]{64}\.bin)')
    for relative, spec in files.items():
        if not isinstance(relative, str) or not re.fullmatch(allowed, relative):
            raise ValueError('备份文件路径无效')
        _file_spec(spec, ATTACHMENT_LIMIT if relative.startswith('attachments/') else MARKET_FILE_LIMIT)
        if spec['sha256'] != Path(relative).stem:
            raise ValueError('备份文件标识与校验值不一致')
    return {**value, 'files': files}


def create_backup(data_dir: Path) -> bytes:
    source = (data_dir / 'trade.sqlite').resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    with tempfile.TemporaryDirectory(prefix='trade-backup-') as temp:
        target = Path(temp) / 'trade.sqlite'
        try:
            with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as connection, closing(sqlite3.connect(target)) as snapshot:
                connection.backup(snapshot)
        except sqlite3.DatabaseError as exc:
            raise ValueError('数据库文件损坏，无法创建备份') from exc
        contents = target.read_bytes()
        with closing(sqlite3.connect(target)) as snapshot:
            validate_database(snapshot)
            tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            dataset_ids = [row[0] for row in snapshot.execute('SELECT id FROM market_datasets')] if 'market_datasets' in tables else []
            attachment_hashes = [row[0] for row in snapshot.execute('SELECT DISTINCT sha256 FROM review_attachments')] if 'review_attachments' in tables else []
    files: dict[str, bytes] = {}
    file_manifest: dict[str, dict] = {}
    for dataset_id in dataset_ids:
        if not isinstance(dataset_id, str) or not re.fullmatch(r'[0-9a-f]{64}', dataset_id):
            raise ValueError('行情数据集标识无效')
        relative = f'market/{dataset_id}.json'
        file_bytes = (data_dir / relative).read_bytes()
        digest = hashlib.sha256(file_bytes).hexdigest()
        if digest != dataset_id:
            raise ValueError(f'行情数据集校验失败: {dataset_id}')
        files[relative] = file_bytes
        file_manifest[relative] = {'sha256': digest, 'bytes': len(file_bytes)}
    for attachment_hash in attachment_hashes:
        if not isinstance(attachment_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', attachment_hash):
            raise ValueError('复盘附件标识无效')
        relative = f'attachments/{attachment_hash}.bin'
        file_bytes = (data_dir / relative).read_bytes()
        digest = hashlib.sha256(file_bytes).hexdigest()
        if digest != attachment_hash or len(file_bytes) > ATTACHMENT_LIMIT:
            raise ValueError(f'复盘附件校验失败: {attachment_hash}')
        files[relative] = file_bytes
        file_manifest[relative] = {'sha256': digest, 'bytes': len(file_bytes)}
    digest = hashlib.sha256(contents).hexdigest()
    manifest = {'format': 'trade-rebuild-backup-v3', 'database': 'trade.sqlite',
                'sha256': digest, 'bytes': len(contents), 'files': file_manifest}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        archive.writestr('trade.sqlite', contents)
        for relative, file_bytes in files.items():
            archive.writestr(relative, file_bytes)
    return output.getvalue()


def verify_backup(archive_bytes: bytes) -> dict:
    try:
        return _verify_backup(archive_bytes)
    except (zipfile.BadZipFile, zlib.error, EOFError, KeyError, RuntimeError,
            NotImplementedError, UnicodeError) as exc:
        raise ValueError('备份压缩数据损坏或不受支持') from exc


def _verify_backup(archive_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        if len(archive.infolist()) > MEMBER_LIMIT or archive.getinfo('manifest.json').file_size > MANIFEST_LIMIT:
            raise ValueError('备份文件清单过大')
        manifest = _manifest(json.loads(archive.read('manifest.json')))
        files = manifest['files']
        expected_names = {'manifest.json', 'trade.sqlite', *files}
        if len(archive.namelist()) != len(expected_names) or set(archive.namelist()) != expected_names:
            raise ValueError('备份内容不符合格式')
        info = archive.getinfo('trade.sqlite')
        if info.file_size != manifest['bytes']:
            raise ValueError('备份文件大小与清单不一致')
        contents = archive.read('trade.sqlite')
        for relative, spec in files.items():
            info = archive.getinfo(relative)
            if info.file_size != spec['bytes']:
                raise ValueError('备份内业务文件大小与清单不一致')
            file_bytes = archive.read(relative)
            digest = hashlib.sha256(file_bytes).hexdigest()
            if len(file_bytes) != spec.get('bytes') or digest != spec.get('sha256') or digest != Path(relative).stem:
                raise ValueError(f'业务文件校验失败: {relative}')
    if len(contents) != manifest.get('bytes') or hashlib.sha256(contents).hexdigest() != manifest.get('sha256'):
        raise ValueError('备份校验失败')
    with tempfile.TemporaryDirectory(prefix='trade-verify-') as temp:
        path = Path(temp) / 'trade.sqlite'
        path.write_bytes(contents)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            validate_database(connection)
            found = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            dataset_ids = {row[0] for row in connection.execute('SELECT id FROM market_datasets')} if 'market_datasets' in found else set()
            if {f'market/{item}.json' for item in dataset_ids} != {item for item in files if item.startswith('market/')}:
                raise ValueError('备份行情清单与数据库引用不一致')
            attachment_hashes = {row[0] for row in connection.execute('SELECT DISTINCT sha256 FROM review_attachments')} if 'review_attachments' in found else set()
            if {f'attachments/{item}.bin' for item in attachment_hashes} != {item for item in files if item.startswith('attachments/')}:
                raise ValueError('备份附件清单与数据库引用不一致')
    return manifest


def restore_to_new_directory(archive_bytes: bytes, destination: Path) -> dict:
    manifest = verify_backup(archive_bytes)
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError('恢复目标目录必须为空，避免覆盖原数据')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Publish the database only together with every verified immutable file.
    # A failed extraction leaves no apparently usable half-restored target.
    with tempfile.TemporaryDirectory(prefix='.trade-restore-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'complete'
        staging.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for relative in ['trade.sqlite', *manifest.get('files', {})]:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(archive.read(relative))
                    output.flush()
                    os.fsync(output.fileno())
        if destination.exists():
            # rmdir refuses nonempty directories, including a target populated
            # concurrently after preflight. Never recursively remove a target.
            destination.rmdir()
        os.rename(staging, destination)
    return manifest
