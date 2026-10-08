"""Versioned SQLite migrations.

The store uses the stdlib sqlite3 connection, so migrations are ordered SQL
steps rather than an external migration framework. A database that already has
rows is copied before the version changes.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

VERSION = 2


def apply(connection: sqlite3.Connection, db_path: Path) -> int:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )"""
    )
    row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    current = int(row[0]) if row else 0
    if current >= VERSION:
        connection.commit()
        return current
    if _needs_backup(connection, current):
        _backup(connection, db_path, current)
    if current < 2:
        _add_column(connection, "runs", "user_id", "TEXT DEFAULT 'local'")
        _add_column(connection, "llm_calls", "user_id", "TEXT DEFAULT 'local'")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_at ON llm_calls(at)")
    connection.execute(
        "INSERT INTO schema_version (id, version) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET version=excluded.version",
        (VERSION,),
    )
    connection.commit()
    return VERSION


def _needs_backup(connection: sqlite3.Connection, current: int) -> bool:
    if current > 0:
        return True
    for table in ("runs", "llm_calls", "chats", "documents"):
        if _table_exists(connection, table) and _has_rows(connection, table):
            return True
    return False


def _backup(connection: sqlite3.Connection, db_path: Path, current: int) -> Path:
    folder = db_path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"pre-migrate-{current}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    out = sqlite3.connect(dest)
    try:
        connection.backup(out)
    finally:
        out.close()
    return dest


def _add_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _table_exists(connection, table):
        return
    names = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in names:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _has_rows(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
