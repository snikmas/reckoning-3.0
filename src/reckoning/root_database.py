from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT_DATABASE_FILENAME = "reckoning.sqlite3"
ROOT_DATABASE_SCHEMA_VERSION = "1"
_PLACEMENT_CATEGORY_DIRECTORIES = {
    "approved-remote-sources",
    "confirmed-state",
    "personal-context",
}


def logical_root_for(state_path: Path) -> Path:
    state_root = state_path.expanduser().resolve().parent
    if state_root.name in _PLACEMENT_CATEGORY_DIRECTORIES:
        return state_root.parent
    return state_root


def connect_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    path.chmod(0o600)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_root_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS root_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    root_version = metadata(connection, "root_schema_version")
    if root_version not in (None, ROOT_DATABASE_SCHEMA_VERSION):
        raise RuntimeError("Unsupported placement-root database schema.")
    if root_version is None:
        set_metadata(connection, "root_schema_version", ROOT_DATABASE_SCHEMA_VERSION)


def metadata(connection: sqlite3.Connection, key: str) -> str | None:
    try:
        row = connection.execute(
            "SELECT value FROM root_metadata WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]) if row is not None else None


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO root_metadata (key, value) VALUES (?, ?)",
        (key, value),
    )
