"""Read-only database validation against its recorded, supported migrations."""
from __future__ import annotations

from contextlib import closing
from functools import lru_cache
from pathlib import Path
import sqlite3


MIGRATION_TABLE_SQL = (
    'CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, '
    'applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'
)


def migration_sources() -> tuple[tuple[str, str], ...]:
    directory = Path(__file__).resolve().parents[1] / 'migrations'
    return tuple((path.stem, path.read_text(encoding='utf-8'))
                 for path in sorted(directory.glob('*.sql')))


def _columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    quoted = table.replace('"', '""')
    return frozenset(row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")'))


@lru_cache(maxsize=128)
def _expected_tables(sources: tuple[tuple[str, str], ...]) -> tuple[tuple[str, frozenset[str]], ...]:
    # Replay only trusted shipped migrations in memory, never in the inspected DB.
    with closing(sqlite3.connect(':memory:')) as reference:
        reference.execute(MIGRATION_TABLE_SQL)
        for _version, sql in sources:
            for chunk in sql.split(';'):
                if chunk.strip():
                    reference.execute(chunk.strip())
        names = [row[0] for row in reference.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return tuple((name, _columns(reference, name)) for name in sorted(names))


def validate_schema(connection: sqlite3.Connection) -> None:
    """Accept a legitimate old schema; reject missing objects or dishonest history."""
    found = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if 'schema_migrations' not in found or not {'version', 'applied_at'} <= _columns(connection, 'schema_migrations'):
        raise ValueError('数据库缺少有效迁移记录')
    applied = tuple(row[0] for row in connection.execute(
        'SELECT version FROM schema_migrations ORDER BY version'))
    sources = migration_sources()
    known = tuple(version for version, _sql in sources)
    if not applied or applied != known[:len(applied)]:
        raise ValueError('数据库迁移记录未知或不连续')
    for table, columns in _expected_tables(sources[:len(applied)]):
        if table not in found:
            raise ValueError(f'数据库缺少已应用迁移要求的数据表: {table}')
        if not columns <= _columns(connection, table):
            raise ValueError(f'数据库缺少已应用迁移要求的数据列: {table}')


def validate_database(connection: sqlite3.Connection) -> None:
    try:
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('数据库完整性校验失败')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('数据库引用完整性校验失败')
        validate_schema(connection)
    except sqlite3.DatabaseError as exc:
        raise ValueError('数据库结构或完整性校验失败') from exc
