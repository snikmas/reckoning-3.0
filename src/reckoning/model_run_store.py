from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from reckoning.json_store import atomic_write_json, read_json

if TYPE_CHECKING:
    from reckoning.application import ModelRunRecord


ROOT_DATABASE_FILENAME = "reckoning.sqlite3"
ROOT_DATABASE_SCHEMA_VERSION = "1"
MODEL_RUN_SCHEMA_VERSION = "1"


class SQLiteModelRunRepository:
    """Persist model-run receipts transactionally within one placement root."""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path
        self._root = _logical_root_for(legacy_path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def save_run(self, run: ModelRunRecord) -> None:
        with closing(_connect(self._path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO model_runs (
                    id,
                    requested_at,
                    status,
                    provider,
                    model,
                    model_calls,
                    latency_ms,
                    retries,
                    input_tokens,
                    output_tokens,
                    billable_units,
                    failure
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.requested_at.isoformat(),
                    run.status,
                    run.provider,
                    run.model,
                    run.model_calls,
                    run.latency_ms,
                    run.retries,
                    run.input_tokens,
                    run.output_tokens,
                    run.billable_units,
                    run.failure,
                ),
            )

    def list_runs(self) -> tuple[ModelRunRecord, ...]:
        from reckoning.application import ModelRunRecord

        with closing(_connect(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    requested_at,
                    status,
                    provider,
                    model,
                    model_calls,
                    latency_ms,
                    retries,
                    input_tokens,
                    output_tokens,
                    billable_units,
                    failure
                FROM model_runs
                ORDER BY sequence
                """
            ).fetchall()
        return tuple(
            ModelRunRecord(
                id=str(row[0]),
                requested_at=datetime.fromisoformat(str(row[1])),
                status=row[2],
                provider=str(row[3]),
                model=str(row[4]),
                model_calls=int(row[5]),
                latency_ms=int(row[6]),
                retries=int(row[7]),
                input_tokens=int(row[8]),
                output_tokens=int(row[9]),
                billable_units=int(row[10]),
                failure=str(row[11]) if row[11] is not None else None,
            )
            for row in rows
        )

    def _initialize_or_migrate(self) -> None:
        if _model_run_store_ready(self._path):
            _finalize_legacy_authority(self._path, self._legacy_path)
            return

        legacy_data: dict[str, Any] | None = None
        legacy_runs: tuple[ModelRunRecord, ...] = ()
        legacy_digest: str | None = None
        if self._legacy_path.exists():
            try:
                legacy_data, legacy_runs, legacy_digest = _load_legacy_runs(
                    self._legacy_path
                )
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if _model_run_store_ready(self._path):
                    return
                raise RuntimeError("Invalid legacy model-run storage.") from None
            _preserve_legacy_rollback(self._legacy_path, legacy_data)
            _write_legacy_authority(self._legacy_path, state="migration-pending")

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(_connect(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = _metadata(connection, "model_runs_schema_version")
                if current_version is not None and not _table_exists(
                    connection, "model_runs"
                ):
                    raise RuntimeError(
                        "Model-run schema authority has no record table."
                    )
                _create_schema(connection)
                current_version = _metadata(connection, "model_runs_schema_version")
                if current_version not in (None, MODEL_RUN_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported model-run storage schema.")
                if current_version is None:
                    existing = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM model_runs"
                        ).fetchone()[0]
                    )
                    if existing:
                        raise RuntimeError(
                            "Model-run storage has records without schema authority."
                        )
                    for run in legacy_runs:
                        _insert_run(connection, run)
                    _set_metadata(
                        connection,
                        "model_runs_schema_version",
                        MODEL_RUN_SCHEMA_VERSION,
                    )
                    _set_metadata(
                        connection,
                        "model_runs_legacy_sha256",
                        legacy_digest or "not-required",
                    )
                    _validate_migration(connection, legacy_runs)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        if legacy_data is not None:
            _write_legacy_authority(self._legacy_path, state="sqlite-authoritative")


def snapshot_database(path: Path) -> bytes:
    """Return a transactionally consistent SQLite snapshot without journal files."""
    with TemporaryDirectory(prefix="reckoning-sqlite-snapshot-") as temporary:
        snapshot_path = Path(temporary) / ROOT_DATABASE_FILENAME
        try:
            source = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=30.0,
            )
            target = sqlite3.connect(snapshot_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        except sqlite3.Error as error:
            raise RuntimeError(
                "Could not snapshot the placement-root database."
            ) from error
        validate_database(snapshot_path)
        return snapshot_path.read_bytes()


def validate_database(path: Path) -> None:
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError("SQLite integrity check failed.")
            root_version = _metadata(connection, "root_schema_version")
            model_run_version = _metadata(connection, "model_runs_schema_version")
            if root_version != ROOT_DATABASE_SCHEMA_VERSION:
                raise RuntimeError("Unsupported placement-root database schema.")
            if model_run_version not in (None, MODEL_RUN_SCHEMA_VERSION):
                raise RuntimeError("Unsupported model-run storage schema.")
            has_model_runs = _table_exists(connection, "model_runs")
            if (model_run_version is None) != (not has_model_runs):
                raise RuntimeError("Incomplete model-run storage schema.")
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RuntimeError("Invalid placement-root database.") from error


def validate_database_bytes(content: bytes) -> None:
    with TemporaryDirectory(prefix="reckoning-sqlite-validation-") as temporary:
        path = Path(temporary) / ROOT_DATABASE_FILENAME
        path.write_bytes(content)
        validate_database(path)


def _logical_root_for(legacy_path: Path) -> Path:
    state_root = legacy_path.expanduser().resolve().parent
    if state_root.name == "confirmed-state":
        return state_root.parent
    return state_root


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    path.chmod(0o600)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS root_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    root_version = _metadata(connection, "root_schema_version")
    if root_version not in (None, ROOT_DATABASE_SCHEMA_VERSION):
        raise RuntimeError("Unsupported placement-root database schema.")
    if root_version is None:
        _set_metadata(connection, "root_schema_version", ROOT_DATABASE_SCHEMA_VERSION)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS model_runs (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            requested_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'limited')),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            model_calls INTEGER NOT NULL CHECK (model_calls >= 0),
            latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
            retries INTEGER NOT NULL CHECK (retries >= 0),
            input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
            output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
            billable_units INTEGER NOT NULL CHECK (billable_units >= 0),
            failure TEXT
        )
        """
    )


def _model_run_store_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return (
                _metadata(connection, "root_schema_version")
                == ROOT_DATABASE_SCHEMA_VERSION
                and _metadata(connection, "model_runs_schema_version")
                == MODEL_RUN_SCHEMA_VERSION
                and _table_exists(connection, "model_runs")
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _metadata(connection: sqlite3.Connection, key: str) -> str | None:
    try:
        row = connection.execute(
            "SELECT value FROM root_metadata WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]) if row is not None else None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO root_metadata (key, value) VALUES (?, ?)",
        (key, value),
    )


def _load_legacy_runs(
    path: Path,
) -> tuple[dict[str, Any], tuple[ModelRunRecord, ...], str]:
    from reckoning.application import ModelRunRecord

    data = read_json(path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        data = read_json(_rollback_path(path), default={})
    if data.get("schema_version") != 1 or not isinstance(data.get("runs"), list):
        raise RuntimeError("Unsupported model-run storage schema.")
    runs = tuple(
        ModelRunRecord(
            id=str(item["id"]),
            requested_at=datetime.fromisoformat(str(item["requested_at"])),
            status=item["status"],
            provider=str(item["provider"]),
            model=str(item["model"]),
            model_calls=int(item["model_calls"]),
            latency_ms=int(item["latency_ms"]),
            retries=int(item["retries"]),
            input_tokens=int(item["input_tokens"]),
            output_tokens=int(item["output_tokens"]),
            billable_units=int(item["billable_units"]),
            failure=str(item["failure"]) if item.get("failure") else None,
        )
        for item in data["runs"]
    )
    _validate_records(runs)
    canonical = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return data, runs, sha256(canonical).hexdigest()


def _validate_records(runs: tuple[ModelRunRecord, ...]) -> None:
    identifiers = [run.id for run in runs]
    if len(set(identifiers)) != len(identifiers) or any(
        not item for item in identifiers
    ):
        raise ValueError("Model-run identifiers must be unique and nonempty.")
    if any(
        run.status not in {"succeeded", "failed", "limited"}
        or not run.provider
        or not run.model
        or min(
            run.model_calls,
            run.latency_ms,
            run.retries,
            run.input_tokens,
            run.output_tokens,
            run.billable_units,
        )
        < 0
        for run in runs
    ):
        raise ValueError("Legacy model-run fields are invalid.")


def _preserve_legacy_rollback(path: Path, data: dict[str, Any]) -> None:
    rollback = _rollback_path(path)
    if rollback.exists():
        if read_json(rollback, default={}) != data:
            raise RuntimeError("The protected model-run rollback source differs.")
        return
    atomic_write_json(rollback, data)
    rollback.chmod(0o400)


def _rollback_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.rollback{path.suffix}")


def _write_legacy_authority(path: Path, *, state: str) -> None:
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


def _finalize_legacy_authority(database: Path, legacy_path: Path) -> None:
    if not legacy_path.exists():
        return
    data = read_json(legacy_path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        if data.get("database") != ROOT_DATABASE_FILENAME:
            raise RuntimeError("The legacy receipt authority points elsewhere.")
        if data.get("migration_state") != "sqlite-authoritative":
            _write_legacy_authority(legacy_path, state="sqlite-authoritative")
        return
    legacy_data, _, digest = _load_legacy_runs(legacy_path)
    with closing(_connect(database)) as connection:
        migrated_digest = _metadata(connection, "model_runs_legacy_sha256")
    if digest != migrated_digest:
        raise RuntimeError(
            "Legacy model-run data changed after SQLite became authoritative."
        )
    _preserve_legacy_rollback(legacy_path, legacy_data)
    _write_legacy_authority(legacy_path, state="sqlite-authoritative")


def _insert_run(connection: sqlite3.Connection, run: ModelRunRecord) -> None:
    values = asdict(run)
    connection.execute(
        """
        INSERT INTO model_runs (
            id, requested_at, status, provider, model, model_calls, latency_ms,
            retries, input_tokens, output_tokens, billable_units, failure
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values["id"],
            run.requested_at.isoformat(),
            values["status"],
            values["provider"],
            values["model"],
            values["model_calls"],
            values["latency_ms"],
            values["retries"],
            values["input_tokens"],
            values["output_tokens"],
            values["billable_units"],
            values["failure"],
        ),
    )


def _validate_migration(
    connection: sqlite3.Connection, expected: tuple[ModelRunRecord, ...]
) -> None:
    rows = connection.execute("SELECT id FROM model_runs ORDER BY sequence").fetchall()
    migrated_ids = tuple(str(row[0]) for row in rows)
    expected_ids = tuple(run.id for run in expected)
    if len(migrated_ids) != len(expected_ids) or migrated_ids != expected_ids:
        raise RuntimeError("Model-run migration validation failed.")
