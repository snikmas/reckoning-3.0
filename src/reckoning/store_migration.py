from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from reckoning.json_store import atomic_write_json, read_json
from reckoning.root_database import (
    ROOT_DATABASE_FILENAME,
    connect_database,
    metadata,
)

LegacyLoader = Callable[[Path], tuple[dict[str, Any], str]]


def rollback_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.rollback{path.suffix}")


def has_sqlite_authority(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = read_json(path, default={})
    except RuntimeError:
        return False
    return data.get("schema_version") == 2 and data.get("authority") == "sqlite"


def preserve_legacy_rollback(path: Path, data: dict[str, Any]) -> None:
    rollback = rollback_path(path)
    if rollback.exists():
        if read_json(rollback, default={}) != data:
            raise RuntimeError(f"The protected {path.name} rollback source differs.")
        return
    atomic_write_json(rollback, data)
    rollback.chmod(0o400)


def write_legacy_authority(path: Path, *, state: str) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 2,
            "authority": "sqlite",
            "database": ROOT_DATABASE_FILENAME,
            "migration_state": state,
        },
    )
    path.chmod(0o600)


def finalize_legacy_authority(
    database: Path,
    legacy_path: Path,
    *,
    load_legacy: LegacyLoader,
    digest_key: str,
    label: str,
) -> None:
    if not legacy_path.exists():
        write_legacy_authority(legacy_path, state="sqlite-authoritative")
        return
    data = read_json(legacy_path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        if data.get("database") != ROOT_DATABASE_FILENAME:
            raise RuntimeError(f"The legacy {label} authority points elsewhere.")
        if data.get("migration_state") != "sqlite-authoritative":
            write_legacy_authority(legacy_path, state="sqlite-authoritative")
        return
    legacy_data, digest = load_legacy(legacy_path)
    with closing(connect_database(database)) as connection:
        migrated_digest = metadata(connection, digest_key)
    if digest != migrated_digest:
        raise RuntimeError(
            f"Legacy {label} data changed after SQLite became authoritative."
        )
    preserve_legacy_rollback(legacy_path, legacy_data)
    write_legacy_authority(legacy_path, state="sqlite-authoritative")
