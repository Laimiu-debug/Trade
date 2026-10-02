"""Local storage inspection and explicit restores into a separate empty directory."""
from __future__ import annotations

import hashlib
import errno
import io
import json
import shutil
import sqlite3
import zipfile
import zlib
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from trade_app.platform.backup import create_backup, restore_to_new_directory, verify_backup
from trade_app.platform.types import TradeError
from trade_app.platform.directory_picker import choose_directory, supported as picker_supported


router = APIRouter(prefix='/api/v1/system', tags=['system-storage'])
UPLOAD_LIMIT = 256 * 1024 * 1024
EXPANDED_LIMIT = 512 * 1024 * 1024


def _storage_io(operation):
    try:
        return operation()
    except PermissionError as exc:
        raise TradeError('STORAGE_ACCESS_DENIED', '无法访问所选目录，请检查读写权限或文件占用', 403) from exc
    except FileExistsError as exc:
        raise TradeError('STORAGE_NOT_EMPTY', '目标目录在操作过程中变为非空，请重新选择独立空目录', 409) from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise TradeError('STORAGE_SPACE_LOW', '磁盘空间不足，未完成的数据不会作为恢复结果发布', 507) from exc
        raise TradeError('STORAGE_IO_FAILED', '目录操作失败，请检查磁盘、路径与文件占用；原目录仍保留', 503) from exc


def _fingerprint(manifest):
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _check_archive(contents):
    try:
        if len(contents) > UPLOAD_LIMIT:
            raise ValueError('备份包超过256 MiB页面上传限制，可使用离线恢复工具')
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            rows = archive.infolist()
            if len(rows) > 100000 or sum(row.file_size for row in rows) > EXPANDED_LIMIT:
                raise ValueError('解压后超过512 MiB页面恢复限制，可使用离线恢复工具')
            if archive.getinfo('manifest.json').file_size > 8 * 1024 * 1024:
                raise ValueError('备份清单超过大小限制')
        return verify_backup(contents)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, NotImplementedError,
            sqlite3.DatabaseError, zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise TradeError('INVALID_BACKUP', '备份校验失败：' + str(exc)[:300]) from exc


def _destination(text: str, active: Path):
    if '\x00' in text:
        raise TradeError('INVALID_STORAGE_PATH', '目录路径包含无效字符')
    return _storage_io(lambda: _resolve_destination(text, active))


def _resolve_destination(text: str, active: Path):
    raw = Path(text).expanduser()
    if not raw.is_absolute():
        raise TradeError('INVALID_STORAGE_PATH', '目标目录必须是绝对路径')
    target = raw.resolve()
    current = active.resolve()
    if target == current or target in current.parents or current in target.parents or target == Path(target.anchor):
        raise TradeError('INVALID_STORAGE_PATH', '请选原目录之外的独立空目录，不能使用根目录、父目录或子目录')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise TradeError('STORAGE_NOT_EMPTY', '目标目录必须为空，不会覆盖已有文件', 409)
    parent = target.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise TradeError('INVALID_STORAGE_PATH', '目标父路径不是目录')
    return target, shutil.disk_usage(parent).free


def _summary(manifest):
    files = manifest.get('files', {})
    return {'format': manifest['format'], 'fingerprint': _fingerprint(manifest),
            'database_bytes': manifest['bytes'], 'total_bytes': manifest['bytes'] + sum(row['bytes'] for row in files.values()),
            'dataset_count': sum(name.startswith('market/') for name in files),
            'attachment_count': sum(name.startswith('attachments/') for name in files)}


class CopyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    destination: str = Field(min_length=1, max_length=2000)


class CopyCommit(CopyRequest):
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class DirectoryPick(BaseModel):
    model_config = ConfigDict(extra='forbid')


@router.get('/directory-picker')
def directory_picker_status(request: Request):
    return {'data': {'supported': picker_supported(request.app.state.lifecycle.supported)}}


@router.post('/directory-picker')
def directory_picker(request: Request, payload: DirectoryPick):
    return {'data': choose_directory(managed=request.app.state.lifecycle.supported,
                                     initial=request.app.state.data_dir.parent)}


@router.get('/storage')
def storage(request: Request):
    root = request.app.state.data_dir
    totals = {}
    for category, entries in (
        ('database', list(root.glob('trade.sqlite*'))),
        ('market', (root / 'market').glob('*.json')),
        ('attachments', (root / 'attachments').glob('*.bin')),
    ):
        sizes = [path.stat().st_size for path in entries if path.is_file()]
        totals[category] = {'file_count': len(sizes), 'bytes': sum(sizes)}
    with closing(sqlite3.connect((root / 'trade.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        versions = [row[0] for row in connection.execute('SELECT version FROM schema_migrations ORDER BY version')] if connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() else []
    return {'data': {'data_dir': str(root), 'usage': totals, 'free_bytes': shutil.disk_usage(root).free,
                     'migration_versions': versions, 'backup_url': '/api/v1/backups/export',
                     'switch_requires_restart': True, 'upload_limit_bytes': UPLOAD_LIMIT,
                     'restore_expanded_limit_bytes': EXPANDED_LIMIT,
                     'background_errors': dict(request.app.state.background_errors)}}


@router.post('/storage/copy-preview')
def preview_copy(request: Request, payload: CopyRequest):
    target, free = _destination(payload.destination, request.app.state.data_dir)
    manifest = _check_archive(_storage_io(lambda: create_backup(request.app.state.data_dir)))
    summary = _summary(manifest)
    if free < summary['total_bytes'] * 2:
        raise TradeError('STORAGE_SPACE_LOW', '目标磁盘空间不足，需要保留复制与验证空间', 409)
    return {'data': {**summary, 'destination': str(target), 'free_bytes': free}}


@router.post('/storage/copy')
def copy_storage(request: Request, payload: CopyCommit):
    target, free = _destination(payload.destination, request.app.state.data_dir)
    contents = _storage_io(lambda: create_backup(request.app.state.data_dir))
    manifest = _check_archive(contents)
    summary = _summary(manifest)
    if _fingerprint(manifest) != payload.expected_fingerprint:
        raise TradeError('STORAGE_PREVIEW_STALE', '数据已在预览后更新，请重新预览再复制', 409)
    if free < summary['total_bytes'] * 2:
        raise TradeError('STORAGE_SPACE_LOW', '目标磁盘可用空间不足', 409)
    _storage_io(lambda: restore_to_new_directory(contents, target))
    return {'data': {**summary, 'destination': str(target), 'status': 'copied',
                     'active_data_dir': str(request.app.state.data_dir), 'switch_requires_restart': True}}


async def _upload(file):
    try:
        return await file.read(UPLOAD_LIMIT + 1)
    finally:
        await file.close()


@router.post('/backups/preview')
async def preview_backup(file: UploadFile = File(...)):
    contents = await _upload(file)
    manifest = await run_in_threadpool(_check_archive, contents)
    return {'data': _summary(manifest)}


@router.post('/backups/restore-new')
async def restore_backup(request: Request, file: UploadFile = File(...), destination: str = Form(...),
                         expected_fingerprint: str = Form(...)):
    target, free = _destination(destination, request.app.state.data_dir)
    contents = await _upload(file)

    def restore():
        manifest = _check_archive(contents)
        summary = _summary(manifest)
        if _fingerprint(manifest) != expected_fingerprint:
            raise TradeError('BACKUP_PREVIEW_STALE', '备份文件与预检结果不一致，请重新选择并预检', 409)
        if free < summary['total_bytes'] * 2:
            raise TradeError('STORAGE_SPACE_LOW', '目标磁盘可用空间不足', 409)
        _storage_io(lambda: restore_to_new_directory(contents, target))
        return {**summary, 'destination': str(target), 'status': 'restored', 'switch_requires_restart': True}

    return {'data': await run_in_threadpool(restore)}
