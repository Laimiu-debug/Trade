"""Copy a live legacy directory using a SQLite snapshot, without moving its source."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from ..database import Base


def copy_data_directory(source: Path, target: Path) -> None:
    source, target = source.resolve(), target.resolve()
    if (target == source or target in source.parents or source in target.parents
            or target == Path(target.anchor)):
        raise ValueError('请选择原目录之外的独立空目录')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError('目标目录必须为空')
    database = source / 'laimiutrade.db'
    if not database.is_file():
        raise ValueError('原目录缺少数据库')
    for folder, directories, filenames in os.walk(source, followlinks=False):
        for name in directories + filenames:
            path = Path(folder) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('数据目录含链接，请先使用独立备份工具处理')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.journal-copy-', dir=target.parent) as temporary:
        staging = Path(temporary) / 'complete'
        staging.mkdir()
        snapshot = staging / database.name
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(snapshot)) as copied:
                original.backup(copied)
                if copied.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('原数据库完整性校验失败')
                tables = {row[0] for row in copied.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in Base.metadata.sorted_tables:
                    if table.name not in tables:
                        raise ValueError('原数据库缺少业务数据表')
                    columns = {row[1] for row in copied.execute(f'PRAGMA table_info("{table.name}")')}
                    if not {column.name for column in table.columns} <= columns:
                        raise ValueError('原数据库缺少业务数据列')
        excluded = {database.name, database.name + '-wal', database.name + '-shm', database.name + '-journal'}
        for item in source.iterdir():
            if item.name in excluded:
                continue
            if item.is_dir(): shutil.copytree(item, staging / item.name)
            else: shutil.copy2(item, staging / item.name)
        if target.exists():
            target.rmdir()  # Refuses a concurrently populated target; never deletes its files.
        os.rename(staging, target)


def publish_location(path: Path, target: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix='.journal-location-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(str(target))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
