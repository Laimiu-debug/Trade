from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from trade_app.platform.schema import MIGRATION_TABLE_SQL, validate_schema


class Base(DeclarativeBase):
    pass


def open_database(data_dir: Path) -> tuple[Engine, sessionmaker]:
    """Called only from lifespan after the single-instance lock is held."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "trade.sqlite"
    if db_path.exists():
        # Inspect the recorded schema before changing WAL mode or migrating it.
        # Missing tables with already-applied versions cannot be repaired by replay.
        try:
            with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as existing:
                validate_schema(existing)
        except sqlite3.DatabaseError as exc:
            raise ValueError('数据库结构校验失败，原数据未修改') from exc
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar()
        if mode != "wal":
            engine.dispose()
            raise RuntimeError(f"Trade requires SQLite WAL on the selected data directory (got {mode})")

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    with engine.begin() as connection:
        # sqlite3's legacy transaction mode does not automatically wrap DDL.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.execute(text(MIGRATION_TABLE_SQL.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ', 1)))
        applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
        for migration in sorted(migrations_dir.glob("*.sql")):
            if migration.stem in applied:
                continue
            statements = [chunk.strip() for chunk in migration.read_text(encoding="utf-8").split(";") if chunk.strip()]
            for statement in statements:
                connection.exec_driver_sql(statement)
            connection.execute(text("INSERT INTO schema_migrations(version) VALUES (:version)"), {"version": migration.stem})
    return engine, sessionmaker(engine, autoflush=False, expire_on_commit=False)
