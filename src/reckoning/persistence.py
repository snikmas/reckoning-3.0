from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from reckoning.continuity import (
    CheckIn,
    Evidence,
    Inference,
    MaterialQuestion,
    PersonalRecordProposal,
    PersonalRecordVersion,
    Reckoning,
    ReckoningDraft,
    ReckoningRevisionConflict,
    SourcedFact,
)
from reckoning.json_store import atomic_write_json, read_json
from reckoning.root_database import (
    ROOT_DATABASE_FILENAME,
    ROOT_DATABASE_SCHEMA_VERSION,
    connect_database,
    ensure_root_schema,
    logical_root_for,
    metadata,
    set_metadata,
    table_exists,
)


CONTINUITY_SCHEMA_VERSION = "1"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}.")


def _reckoning_from_data(data: dict[str, Any]) -> Reckoning:
    draft_data = data["draft"]
    if not isinstance(draft_data, dict):
        raise RuntimeError("The stored reckoning draft is invalid.")
    draft = ReckoningDraft(
        conflict=str(draft_data["conflict"]),
        questions=tuple(
            MaterialQuestion(
                text=str(item["text"]),
                effect_on_recommendation=str(item["effect_on_recommendation"]),
            )
            for item in draft_data["questions"]
        ),
        matters_now=tuple(str(item) for item in draft_data["matters_now"]),
        maintained=tuple(str(item) for item in draft_data["maintained"]),
        parked=tuple(str(item) for item in draft_data["parked"]),
        uncertainties=tuple(str(item) for item in draft_data["uncertainties"]),
        known=tuple(
            SourcedFact(
                text=str(item["text"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
            )
            for item in draft_data["known"]
        ),
        inferences=tuple(
            Inference(
                text=str(item["text"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
                uncertainty=str(item["uncertainty"]),
            )
            for item in draft_data["inferences"]
        ),
        evidence=tuple(
            Evidence(
                id=str(item["id"]),
                source=str(item["source"]),
                content=str(item["content"]),
            )
            for item in draft_data["evidence"]
        ),
        next_step=str(draft_data["next_step"]),
        proposed_records=tuple(
            PersonalRecordProposal(
                record_type=item["record_type"],
                meaning=str(item["meaning"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
            )
            for item in draft_data.get("proposed_records", ())
        ),
    )
    return Reckoning(
        id=str(data["id"]),
        version=int(data["version"]),
        status=data["status"],
        created_at=datetime.fromisoformat(str(data["created_at"])),
        source_input=str(data["source_input"]),
        draft=draft,
        record_versions=tuple(
            PersonalRecordVersion(
                record_id=str(item["record_id"]),
                version=int(item["version"]),
                status=item["status"],
                record_type=item["record_type"],
                meaning=str(item["meaning"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
                created_at=datetime.fromisoformat(str(item["created_at"])),
                supersedes_version=(
                    int(item["supersedes_version"])
                    if item.get("supersedes_version") is not None
                    else None
                ),
            )
            for item in data.get("record_versions", ())
        ),
    )


def _check_in_from_data(data: dict[str, Any]) -> CheckIn:
    return CheckIn(
        id=str(data["id"]),
        decision_id=str(data["decision_id"]),
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
        outcome=str(data["outcome"]),
        supporting_evidence_ids=tuple(
            str(value) for value in data["supporting_evidence_ids"]
        ),
    )


class JsonFileReckoningRepository:
    """Compatibility entry point backed by per-root SQLite transactions."""

    def __init__(self, path: Path) -> None:
        self._legacy_path = path
        self._root = logical_root_for(path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def save(self, reckoning: Reckoning, *, expected_version: int) -> None:
        if reckoning.version != expected_version + 1:
            raise ValueError("Reckoning versions must increase by one.")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                actual = _current_reckoning_version(connection, reckoning.id)
                if actual != expected_version:
                    raise ReckoningRevisionConflict(
                        reckoning.id, expected_version, actual
                    )
                if actual == 0:
                    _insert_reckoning(connection, reckoning)
                else:
                    cursor = connection.execute(
                        """
                        UPDATE continuity_reckonings
                        SET version = ?, status = ?, payload = ?
                        WHERE id = ? AND version = ?
                        """,
                        (
                            reckoning.version,
                            reckoning.status,
                            _reckoning_payload(reckoning),
                            reckoning.id,
                            expected_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        current = _current_reckoning_version(connection, reckoning.id)
                        raise ReckoningRevisionConflict(
                            reckoning.id, expected_version, current
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def get(self, reckoning_id: str) -> Reckoning:
        with closing(connect_database(self._path)) as connection:
            row = connection.execute(
                "SELECT payload FROM continuity_reckonings WHERE id = ?",
                (reckoning_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown reckoning: {reckoning_id}")
        try:
            data = json.loads(str(row[0]))
            if not isinstance(data, dict):
                raise TypeError("payload is not an object")
            return _reckoning_from_data(data)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Continuity storage is invalid.") from error

    def save_check_in(self, check_in: CheckIn) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM continuity_reckonings WHERE id = ?",
                    (check_in.decision_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown reckoning: {check_in.decision_id}")
                if row[0] != "confirmed":
                    raise ValueError("A check-in requires a confirmed decision.")
                _insert_check_in(connection, check_in)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def list_check_ins(self, decision_id: str) -> tuple[CheckIn, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT id, decision_id, occurred_at, outcome,
                       supporting_evidence_ids
                FROM continuity_check_ins
                WHERE decision_id = ?
                ORDER BY sequence
                """,
                (decision_id,),
            ).fetchall()
        try:
            return tuple(
                CheckIn(
                    id=str(row[0]),
                    decision_id=str(row[1]),
                    occurred_at=datetime.fromisoformat(str(row[2])),
                    outcome=str(row[3]),
                    supporting_evidence_ids=tuple(
                        str(value) for value in json.loads(str(row[4]))
                    ),
                )
                for row in rows
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Continuity storage is invalid.") from error

    def _initialize_or_migrate(self) -> None:
        if _continuity_store_ready(self._path):
            _finalize_legacy_authority(self._path, self._legacy_path)
            return

        legacy_data: dict[str, Any] | None = None
        legacy_reckonings: tuple[Reckoning, ...] = ()
        legacy_check_ins: tuple[CheckIn, ...] = ()
        legacy_digest: str | None = None
        if self._legacy_path.exists():
            try:
                (
                    legacy_data,
                    legacy_reckonings,
                    legacy_check_ins,
                    legacy_digest,
                ) = _load_legacy_continuity(self._legacy_path)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if _continuity_store_ready(self._path):
                    return
                raise RuntimeError("Invalid legacy continuity storage.") from None
            _preserve_legacy_rollback(self._legacy_path, legacy_data)
            _write_legacy_authority(self._legacy_path, state="migration-pending")

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = metadata(connection, "continuity_schema_version")
                if current_version is not None and not _continuity_tables_exist(
                    connection
                ):
                    raise RuntimeError(
                        "Continuity schema authority has incomplete tables."
                    )
                _create_schema(connection)
                current_version = metadata(connection, "continuity_schema_version")
                if current_version not in (None, CONTINUITY_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported continuity storage schema.")
                if current_version is None:
                    counts = connection.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM continuity_reckonings),
                            (SELECT COUNT(*) FROM continuity_check_ins)
                        """
                    ).fetchone()
                    if counts != (0, 0):
                        raise RuntimeError(
                            "Continuity storage has records without schema authority."
                        )
                    for reckoning in legacy_reckonings:
                        _insert_reckoning(connection, reckoning)
                    for check_in in legacy_check_ins:
                        _insert_check_in(connection, check_in)
                    set_metadata(
                        connection,
                        "continuity_schema_version",
                        CONTINUITY_SCHEMA_VERSION,
                    )
                    set_metadata(
                        connection,
                        "continuity_legacy_sha256",
                        legacy_digest or "not-required",
                    )
                    _validate_migration(
                        connection, legacy_reckonings, legacy_check_ins
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        _write_legacy_authority(self._legacy_path, state="sqlite-authoritative")


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS continuity_reckonings (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL CHECK (version > 0),
            status TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed')),
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS continuity_check_ins (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            decision_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            outcome TEXT NOT NULL,
            supporting_evidence_ids TEXT NOT NULL,
            FOREIGN KEY (decision_id) REFERENCES continuity_reckonings(id)
        )
        """
    )


def _continuity_tables_exist(connection: sqlite3.Connection) -> bool:
    return table_exists(connection, "continuity_reckonings") and table_exists(
        connection, "continuity_check_ins"
    )


def _any_continuity_table_exists(connection: sqlite3.Connection) -> bool:
    return table_exists(connection, "continuity_reckonings") or table_exists(
        connection, "continuity_check_ins"
    )


def validate_continuity_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, "continuity_schema_version")
    has_tables = _continuity_tables_exist(connection)
    if version not in (None, CONTINUITY_SCHEMA_VERSION):
        raise RuntimeError("Unsupported continuity storage schema.")
    if version is None and _any_continuity_table_exists(connection):
        raise RuntimeError("Incomplete continuity storage schema.")
    if version is not None and not has_tables:
        raise RuntimeError("Incomplete continuity storage schema.")


def _continuity_store_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            return (
                metadata(connection, "root_schema_version")
                == ROOT_DATABASE_SCHEMA_VERSION
                and metadata(connection, "continuity_schema_version")
                == CONTINUITY_SCHEMA_VERSION
                and _continuity_tables_exist(connection)
            )
    except sqlite3.Error:
        return False


def _current_reckoning_version(
    connection: sqlite3.Connection, reckoning_id: str
) -> int:
    row = connection.execute(
        "SELECT version FROM continuity_reckonings WHERE id = ?", (reckoning_id,)
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _reckoning_payload(reckoning: Reckoning) -> str:
    return json.dumps(
        asdict(reckoning),
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _insert_reckoning(
    connection: sqlite3.Connection, reckoning: Reckoning
) -> None:
    connection.execute(
        """
        INSERT INTO continuity_reckonings (id, version, status, payload)
        VALUES (?, ?, ?, ?)
        """,
        (
            reckoning.id,
            reckoning.version,
            reckoning.status,
            _reckoning_payload(reckoning),
        ),
    )


def _insert_check_in(connection: sqlite3.Connection, check_in: CheckIn) -> None:
    connection.execute(
        """
        INSERT INTO continuity_check_ins (
            id, decision_id, occurred_at, outcome, supporting_evidence_ids
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            check_in.id,
            check_in.decision_id,
            check_in.occurred_at.isoformat(),
            check_in.outcome,
            json.dumps(check_in.supporting_evidence_ids),
        ),
    )


def _load_legacy_continuity(
    path: Path,
) -> tuple[dict[str, Any], tuple[Reckoning, ...], tuple[CheckIn, ...], str]:
    data = read_json(path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        data = read_json(_rollback_path(path), default={})
    raw_reckonings = data.get("reckonings")
    raw_check_ins = data.get("check_ins")
    if (
        data.get("schema_version") != 1
        or not isinstance(raw_reckonings, list)
        or any(not isinstance(item, dict) for item in raw_reckonings)
        or not isinstance(raw_check_ins, list)
        or any(not isinstance(item, dict) for item in raw_check_ins)
    ):
        raise RuntimeError("Unsupported continuity storage schema.")
    reckonings = tuple(_reckoning_from_data(item) for item in raw_reckonings)
    check_ins = tuple(_check_in_from_data(item) for item in raw_check_ins)
    _validate_legacy(reckonings, check_ins)
    canonical = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return data, reckonings, check_ins, sha256(canonical).hexdigest()


def _validate_legacy(
    reckonings: tuple[Reckoning, ...], check_ins: tuple[CheckIn, ...]
) -> None:
    ids = [item.id for item in reckonings]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("Continuity identifiers must be unique and nonempty.")
    if any(
        item.version < 1 or item.status not in {"proposed", "confirmed"}
        for item in reckonings
    ):
        raise ValueError("Legacy continuity fields are invalid.")
    known = set(ids)
    check_in_ids = [item.id for item in check_ins]
    if (
        any(not item for item in check_in_ids)
        or len(check_in_ids) != len(set(check_in_ids))
        or any(item.decision_id not in known for item in check_ins)
    ):
        raise ValueError("Legacy check-in links are invalid.")


def _validate_migration(
    connection: sqlite3.Connection,
    reckonings: tuple[Reckoning, ...],
    check_ins: tuple[CheckIn, ...],
) -> None:
    migrated_reckonings = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM continuity_reckonings ORDER BY sequence"
        ).fetchall()
    )
    migrated_check_ins = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM continuity_check_ins ORDER BY sequence"
        ).fetchall()
    )
    if migrated_reckonings != tuple(item.id for item in reckonings) or (
        migrated_check_ins != tuple(item.id for item in check_ins)
    ):
        raise RuntimeError("Continuity migration validation failed.")


def _rollback_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.rollback{path.suffix}")


def _preserve_legacy_rollback(path: Path, data: dict[str, Any]) -> None:
    rollback = _rollback_path(path)
    if rollback.exists():
        if read_json(rollback, default={}) != data:
            raise RuntimeError("The protected continuity rollback source differs.")
        return
    atomic_write_json(rollback, data)
    rollback.chmod(0o400)


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
        _write_legacy_authority(legacy_path, state="sqlite-authoritative")
        return
    data = read_json(legacy_path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        if data.get("database") != ROOT_DATABASE_FILENAME:
            raise RuntimeError("The legacy continuity authority points elsewhere.")
        if data.get("migration_state") != "sqlite-authoritative":
            _write_legacy_authority(legacy_path, state="sqlite-authoritative")
        return
    legacy_data, _, _, digest = _load_legacy_continuity(legacy_path)
    with closing(connect_database(database)) as connection:
        migrated_digest = metadata(connection, "continuity_legacy_sha256")
    if digest != migrated_digest:
        raise RuntimeError(
            "Legacy continuity data changed after SQLite became authoritative."
        )
    _preserve_legacy_rollback(legacy_path, legacy_data)
    _write_legacy_authority(legacy_path, state="sqlite-authoritative")
