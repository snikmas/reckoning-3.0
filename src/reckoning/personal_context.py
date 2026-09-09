from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol

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

ContextStatus = Literal["proposed", "active", "archived", "forgotten"]
Sensitivity = Literal["low", "private", "restricted"]
MAX_PROFILE_BYTES = 65_536
PERSONAL_CONTEXT_SCHEMA_VERSION = "1"
_RETRIEVAL_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
    "what",
    "when",
    "with",
}


@dataclass(frozen=True)
class UserProfileEntry:
    record_id: str
    section: str
    text: str
    source: str


@dataclass(frozen=True)
class PersonalContextVersion:
    record_id: str
    version: int
    status: ContextStatus
    original_text: str
    language: str
    canonical_meaning: str
    source: str
    sensitivity: Sensitivity
    retrieval_permitted: bool
    processing_location: str
    created_at: datetime
    fresh_until: datetime | None = None
    retention_until: datetime | None = None
    supersedes_version: int | None = None


@dataclass(frozen=True)
class SuppressionMarker:
    deleted_record_id: str
    deleted_at: datetime


@dataclass(frozen=True)
class ContextWhyView:
    current: PersonalContextVersion
    superseded: tuple[PersonalContextVersion, ...]


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    now: datetime
    allowed_sources: tuple[str, ...]
    allowed_sensitivities: tuple[Sensitivity, ...]
    processing_location: str


class ContextRevisionConflict(RuntimeError):
    def __init__(self, record_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Personal context {record_id} changed from revision "
            f"{expected} to {actual}."
        )
        self.record_id = record_id
        self.expected = expected
        self.actual = actual


class PersonalContextRepository(Protocol):
    def save(
        self,
        version: PersonalContextVersion,
        *,
        expected_version: int,
    ) -> None: ...

    def versions(self, record_id: str) -> tuple[PersonalContextVersion, ...]: ...

    def all_versions(self) -> tuple[PersonalContextVersion, ...]: ...

    def delete(
        self,
        record_id: str,
        marker: SuppressionMarker,
        *,
        expected_version: int,
    ) -> None: ...

    def suppression_markers(self) -> tuple[SuppressionMarker, ...]: ...


class ContextLinkRemover(Protocol):
    def remove_context_links(self, record_id: str) -> None: ...


class InMemoryPersonalContextRepository:
    def __init__(self) -> None:
        self._versions: list[PersonalContextVersion] = []
        self._markers: dict[str, SuppressionMarker] = {}

    def save(
        self,
        version: PersonalContextVersion,
        *,
        expected_version: int,
    ) -> None:
        actual = self._current_version(version.record_id)
        if actual != expected_version:
            raise ContextRevisionConflict(version.record_id, expected_version, actual)
        if version.record_id in self._markers:
            raise KeyError(f"Permanently deleted personal context: {version.record_id}")
        self._versions.append(version)

    def versions(self, record_id: str) -> tuple[PersonalContextVersion, ...]:
        return tuple(item for item in self._versions if item.record_id == record_id)

    def all_versions(self) -> tuple[PersonalContextVersion, ...]:
        return tuple(self._versions)

    def delete(
        self,
        record_id: str,
        marker: SuppressionMarker,
        *,
        expected_version: int,
    ) -> None:
        _validate_suppression_marker(record_id, marker)
        actual = self._current_version(record_id)
        if actual != expected_version:
            raise ContextRevisionConflict(record_id, expected_version, actual)
        self._versions = [
            item for item in self._versions if item.record_id != record_id
        ]
        self._markers[record_id] = marker

    def suppression_markers(self) -> tuple[SuppressionMarker, ...]:
        return tuple(self._markers.values())

    def _current_version(self, record_id: str) -> int:
        versions = self.versions(record_id)
        return versions[-1].version if versions else 0


class JsonFilePersonalContextRepository:
    """Compatibility entry point backed by per-root transactional storage."""

    def __init__(self, path: Path) -> None:
        self._legacy_path = path
        self._root = logical_root_for(path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def save(
        self,
        version: PersonalContextVersion,
        *,
        expected_version: int,
    ) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                actual = _current_database_version(connection, version.record_id)
                if actual != expected_version:
                    raise ContextRevisionConflict(
                        version.record_id, expected_version, actual
                    )
                if connection.execute(
                    "SELECT 1 FROM personal_context_suppressions "
                    "WHERE deleted_record_id = ?",
                    (version.record_id,),
                ).fetchone():
                    raise KeyError(
                        f"Permanently deleted personal context: {version.record_id}"
                    )
                _validate_next_version(version, expected_version)
                _insert_version(connection, version)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def versions(self, record_id: str) -> tuple[PersonalContextVersion, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    record_id, version, status, original_text, language,
                    canonical_meaning, source, sensitivity, retrieval_permitted,
                    processing_location, created_at, fresh_until, retention_until,
                    supersedes_version
                FROM personal_context_versions
                WHERE record_id = ?
                ORDER BY version
                """,
                (record_id,),
            ).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    def all_versions(self) -> tuple[PersonalContextVersion, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    record_id, version, status, original_text, language,
                    canonical_meaning, source, sensitivity, retrieval_permitted,
                    processing_location, created_at, fresh_until, retention_until,
                    supersedes_version
                FROM personal_context_versions
                ORDER BY sequence
                """
            ).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    def delete(
        self,
        record_id: str,
        marker: SuppressionMarker,
        *,
        expected_version: int,
    ) -> None:
        _validate_suppression_marker(record_id, marker)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                actual = _current_database_version(connection, record_id)
                if actual != expected_version:
                    raise ContextRevisionConflict(record_id, expected_version, actual)
                if actual == 0:
                    raise KeyError(f"Unknown personal context: {record_id}")
                _suppress_context_rollback(self._legacy_path, marker)
                connection.execute(
                    "DELETE FROM personal_context_versions WHERE record_id = ?",
                    (record_id,),
                )
                connection.execute(
                    """
                    INSERT INTO personal_context_suppressions (
                        deleted_record_id, deleted_at
                    ) VALUES (?, ?)
                    ON CONFLICT(deleted_record_id) DO UPDATE SET
                        deleted_at = excluded.deleted_at
                    """,
                    (marker.deleted_record_id, marker.deleted_at.isoformat()),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def suppression_markers(self) -> tuple[SuppressionMarker, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT deleted_record_id, deleted_at
                FROM personal_context_suppressions
                ORDER BY sequence
                """
            ).fetchall()
        return tuple(
            SuppressionMarker(str(row[0]), datetime.fromisoformat(str(row[1])))
            for row in rows
        )

    def initialize(self) -> None:
        self._initialize_or_migrate()

    def _initialize_or_migrate(self) -> None:
        if _personal_context_store_ready(self._path):
            _finalize_legacy_context_authority(self._path, self._legacy_path)
            return

        legacy_data: dict[str, Any] | None = None
        legacy_versions: tuple[PersonalContextVersion, ...] = ()
        legacy_markers: tuple[SuppressionMarker, ...] = ()
        legacy_digest: str | None = None
        if self._legacy_path.exists():
            try:
                (
                    legacy_data,
                    legacy_versions,
                    legacy_markers,
                    legacy_digest,
                ) = _load_legacy_context(self._legacy_path)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if _personal_context_store_ready(self._path):
                    return
                raise RuntimeError("Invalid legacy personal-context storage.") from None
            _preserve_context_rollback(self._legacy_path, legacy_data)

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = metadata(
                    connection, "personal_context_schema_version"
                )
                if current_version is not None and not _personal_context_tables_exist(
                    connection
                ):
                    raise RuntimeError(
                        "Personal-context schema authority has incomplete tables."
                    )
                _create_personal_context_schema(connection)
                current_version = metadata(
                    connection, "personal_context_schema_version"
                )
                if current_version not in (None, PERSONAL_CONTEXT_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported personal-context storage schema.")
                if current_version is None:
                    counts = connection.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM personal_context_versions),
                            (SELECT COUNT(*) FROM personal_context_suppressions)
                        """
                    ).fetchone()
                    if counts != (0, 0):
                        raise RuntimeError(
                            "Personal-context storage has records without schema authority."
                        )
                    for version in legacy_versions:
                        _insert_version(connection, version)
                    for marker in legacy_markers:
                        connection.execute(
                            """
                            INSERT INTO personal_context_suppressions (
                                deleted_record_id, deleted_at
                            ) VALUES (?, ?)
                            """,
                            (marker.deleted_record_id, marker.deleted_at.isoformat()),
                        )
                    set_metadata(
                        connection,
                        "personal_context_schema_version",
                        PERSONAL_CONTEXT_SCHEMA_VERSION,
                    )
                    set_metadata(
                        connection,
                        "personal_context_legacy_sha256",
                        legacy_digest or "not-required",
                    )
                    _validate_context_migration(
                        connection, legacy_versions, legacy_markers
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        _write_context_authority(self._legacy_path)


def _create_personal_context_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_context_versions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version > 0),
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'active', 'archived', 'forgotten')
            ),
            original_text TEXT NOT NULL,
            language TEXT NOT NULL,
            canonical_meaning TEXT NOT NULL,
            source TEXT NOT NULL,
            sensitivity TEXT NOT NULL CHECK (
                sensitivity IN ('low', 'private', 'restricted')
            ),
            retrieval_permitted INTEGER NOT NULL CHECK (
                retrieval_permitted IN (0, 1)
            ),
            processing_location TEXT NOT NULL,
            created_at TEXT NOT NULL,
            fresh_until TEXT,
            retention_until TEXT,
            supersedes_version INTEGER,
            UNIQUE (record_id, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_context_suppressions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            deleted_record_id TEXT NOT NULL UNIQUE,
            deleted_at TEXT NOT NULL
        )
        """
    )


def _personal_context_tables_exist(connection: sqlite3.Connection) -> bool:
    return table_exists(connection, "personal_context_versions") and table_exists(
        connection, "personal_context_suppressions"
    )


def _any_personal_context_table_exists(connection: sqlite3.Connection) -> bool:
    return table_exists(connection, "personal_context_versions") or table_exists(
        connection, "personal_context_suppressions"
    )


def _personal_context_store_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            return (
                metadata(connection, "root_schema_version")
                == ROOT_DATABASE_SCHEMA_VERSION
                and metadata(connection, "personal_context_schema_version")
                == PERSONAL_CONTEXT_SCHEMA_VERSION
                and _personal_context_tables_exist(connection)
            )
    except sqlite3.Error:
        return False


def validate_personal_context_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, "personal_context_schema_version")
    has_tables = _personal_context_tables_exist(connection)
    if version not in (None, PERSONAL_CONTEXT_SCHEMA_VERSION):
        raise RuntimeError("Unsupported personal-context storage schema.")
    if version is None and _any_personal_context_table_exists(connection):
        raise RuntimeError("Incomplete personal-context storage schema.")
    if version is not None and not has_tables:
        raise RuntimeError("Incomplete personal-context storage schema.")


def _current_database_version(connection: sqlite3.Connection, record_id: str) -> int:
    row = connection.execute(
        "SELECT MAX(version) FROM personal_context_versions WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def _validate_next_version(
    version: PersonalContextVersion, expected_version: int
) -> None:
    if version.version != expected_version + 1:
        raise ValueError("Personal-context versions must increase by one.")
    expected_superseded = expected_version or None
    if version.supersedes_version != expected_superseded:
        raise ValueError("Personal-context supersession does not match its revision.")


def _validate_suppression_marker(record_id: str, marker: SuppressionMarker) -> None:
    if marker.deleted_record_id != record_id:
        raise ValueError("The suppression marker must identify the deleted context.")


def _insert_version(
    connection: sqlite3.Connection, version: PersonalContextVersion
) -> None:
    connection.execute(
        """
        INSERT INTO personal_context_versions (
            record_id, version, status, original_text, language,
            canonical_meaning, source, sensitivity, retrieval_permitted,
            processing_location, created_at, fresh_until, retention_until,
            supersedes_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version.record_id,
            version.version,
            version.status,
            version.original_text,
            version.language,
            version.canonical_meaning,
            version.source,
            version.sensitivity,
            int(version.retrieval_permitted),
            version.processing_location,
            version.created_at.isoformat(),
            version.fresh_until.isoformat() if version.fresh_until else None,
            version.retention_until.isoformat() if version.retention_until else None,
            version.supersedes_version,
        ),
    )


def _version_from_row(row: tuple[Any, ...]) -> PersonalContextVersion:
    return PersonalContextVersion(
        record_id=str(row[0]),
        version=int(row[1]),
        status=row[2],
        original_text=str(row[3]),
        language=str(row[4]),
        canonical_meaning=str(row[5]),
        source=str(row[6]),
        sensitivity=row[7],
        retrieval_permitted=bool(row[8]),
        processing_location=str(row[9]),
        created_at=datetime.fromisoformat(str(row[10])),
        fresh_until=datetime.fromisoformat(str(row[11])) if row[11] else None,
        retention_until=datetime.fromisoformat(str(row[12])) if row[12] else None,
        supersedes_version=int(row[13]) if row[13] is not None else None,
    )


def _load_legacy_context(
    path: Path,
) -> tuple[
    dict[str, Any],
    tuple[PersonalContextVersion, ...],
    tuple[SuppressionMarker, ...],
    str,
]:
    data = read_json(path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        data = read_json(_context_rollback_path(path), default={})
    raw_versions = data.get("versions")
    raw_suppressions = data.get("suppressions")
    if (
        data.get("schema_version") != 1
        or not isinstance(raw_versions, list)
        or any(not isinstance(item, dict) for item in raw_versions)
        or not isinstance(raw_suppressions, list)
        or any(not isinstance(item, dict) for item in raw_suppressions)
    ):
        raise RuntimeError("Unsupported personal-context storage schema.")
    versions = tuple(_version_from_data(item) for item in raw_versions)
    markers = tuple(
        SuppressionMarker(
            deleted_record_id=str(item["deleted_record_id"]),
            deleted_at=datetime.fromisoformat(str(item["deleted_at"])),
        )
        for item in raw_suppressions
    )
    _validate_legacy_context(versions, markers)
    canonical = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return data, versions, markers, sha256(canonical).hexdigest()


def _validate_legacy_context(
    versions: tuple[PersonalContextVersion, ...],
    markers: tuple[SuppressionMarker, ...],
) -> None:
    by_record: dict[str, list[PersonalContextVersion]] = {}
    for version in versions:
        if (
            not version.record_id
            or not version.original_text
            or not version.language
            or not version.canonical_meaning
            or not version.source
            or not version.processing_location
            or version.status not in {"proposed", "active", "archived", "forgotten"}
            or version.sensitivity not in {"low", "private", "restricted"}
        ):
            raise ValueError("Legacy personal-context fields are invalid.")
        by_record.setdefault(version.record_id, []).append(version)
    for record_id, record_versions in by_record.items():
        for expected, version in enumerate(record_versions, start=1):
            if version.version != expected or version.supersedes_version != (
                expected - 1 or None
            ):
                raise ValueError(
                    f"Legacy personal-context history is invalid: {record_id}"
                )
    marker_ids = [marker.deleted_record_id for marker in markers]
    if (
        any(not record_id for record_id in marker_ids)
        or len(set(marker_ids)) != len(marker_ids)
        or set(marker_ids) & set(by_record)
    ):
        raise ValueError("Legacy personal-context suppressions are invalid.")


def _validate_context_migration(
    connection: sqlite3.Connection,
    expected_versions: tuple[PersonalContextVersion, ...],
    expected_markers: tuple[SuppressionMarker, ...],
) -> None:
    version_rows = connection.execute(
        """
        SELECT
            record_id, version, status, original_text, language,
            canonical_meaning, source, sensitivity, retrieval_permitted,
            processing_location, created_at, fresh_until, retention_until,
            supersedes_version
        FROM personal_context_versions
        ORDER BY sequence
        """
    ).fetchall()
    marker_rows = connection.execute(
        """
        SELECT deleted_record_id, deleted_at
        FROM personal_context_suppressions
        ORDER BY sequence
        """
    ).fetchall()
    migrated_versions = tuple(_version_from_row(row) for row in version_rows)
    migrated_markers = tuple(
        SuppressionMarker(str(row[0]), datetime.fromisoformat(str(row[1])))
        for row in marker_rows
    )
    if migrated_versions != expected_versions or migrated_markers != expected_markers:
        raise RuntimeError("Personal-context migration validation failed.")


def _context_rollback_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.rollback{path.suffix}")


def _preserve_context_rollback(path: Path, data: dict[str, Any]) -> None:
    rollback = _context_rollback_path(path)
    if rollback.exists():
        if read_json(rollback, default={}) != data:
            raise RuntimeError(
                "The protected personal-context rollback source differs."
            )
        return
    atomic_write_json(rollback, data)
    rollback.chmod(0o400)


def _suppress_context_rollback(path: Path, marker: SuppressionMarker) -> None:
    rollback = _context_rollback_path(path)
    if not rollback.exists():
        return
    _load_legacy_context(rollback)
    data = read_json(rollback, default={})
    raw_versions = data.get("versions")
    raw_suppressions = data.get("suppressions")
    if (
        data.get("schema_version") != 1
        or not isinstance(raw_versions, list)
        or not isinstance(raw_suppressions, list)
    ):
        raise RuntimeError("Invalid personal-context rollback source.")
    versions = [
        item
        for item in raw_versions
        if isinstance(item, dict) and item.get("record_id") != marker.deleted_record_id
    ]
    suppressions = [
        item
        for item in raw_suppressions
        if isinstance(item, dict)
        and item.get("deleted_record_id") != marker.deleted_record_id
    ]
    suppressions.append(
        {
            "deleted_record_id": marker.deleted_record_id,
            "deleted_at": marker.deleted_at.isoformat(),
        }
    )
    sanitized = {**data, "versions": versions, "suppressions": suppressions}
    _validate_legacy_context(
        tuple(_version_from_data(item) for item in versions),
        tuple(
            SuppressionMarker(
                deleted_record_id=str(item["deleted_record_id"]),
                deleted_at=datetime.fromisoformat(str(item["deleted_at"])),
            )
            for item in suppressions
        ),
    )
    atomic_write_json(rollback, sanitized)
    rollback.chmod(0o400)


def _write_context_authority(path: Path) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 2,
            "authority": "sqlite",
            "database": ROOT_DATABASE_FILENAME,
            "migration_state": "sqlite-authoritative",
        },
    )
    path.chmod(0o600)


def _finalize_legacy_context_authority(database: Path, legacy_path: Path) -> None:
    if not legacy_path.exists():
        _write_context_authority(legacy_path)
        return
    data = read_json(legacy_path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        if data.get("database") != ROOT_DATABASE_FILENAME:
            raise RuntimeError(
                "The legacy personal-context authority points elsewhere."
            )
        if data.get("migration_state") != "sqlite-authoritative":
            _write_context_authority(legacy_path)
        return
    legacy_data, _, _, digest = _load_legacy_context(legacy_path)
    with closing(connect_database(database)) as connection:
        migrated_digest = metadata(connection, "personal_context_legacy_sha256")
    if digest != migrated_digest:
        raise RuntimeError(
            "Legacy personal-context data changed after SQLite became authoritative."
        )
    _preserve_context_rollback(legacy_path, legacy_data)
    _write_context_authority(legacy_path)


class PersonalContextService:
    def __init__(
        self,
        repository: PersonalContextRepository,
        link_removers: tuple[ContextLinkRemover, ...] = (),
    ) -> None:
        self._repository = repository
        self._link_removers = link_removers

    def remember(
        self,
        *,
        record_id: str,
        original_text: str,
        language: str,
        canonical_meaning: str,
        source: str,
        created_at: datetime,
        sensitivity: Sensitivity = "low",
        retrieval_permitted: bool = True,
        processing_location: str = "local",
        fresh_until: datetime | None = None,
        retention_until: datetime | None = None,
    ) -> PersonalContextVersion:
        if self._repository.versions(record_id):
            raise ValueError(f"Personal context already exists: {record_id}")
        version = PersonalContextVersion(
            record_id=record_id,
            version=1,
            status="active",
            original_text=_required(original_text, "Original wording"),
            language=_required(language, "Language"),
            canonical_meaning=_required(canonical_meaning, "Canonical meaning"),
            source=_required(source, "Source"),
            sensitivity=sensitivity,
            retrieval_permitted=retrieval_permitted,
            processing_location=processing_location,
            created_at=created_at,
            fresh_until=fresh_until,
            retention_until=retention_until,
        )
        self._repository.save(version, expected_version=0)
        return version

    def propose(
        self,
        *,
        record_id: str,
        original_text: str,
        language: str,
        canonical_meaning: str,
        source: str,
        created_at: datetime,
        sensitivity: Sensitivity = "private",
        processing_location: str = "local",
    ) -> PersonalContextVersion:
        if self._repository.versions(record_id):
            raise ValueError(f"Personal context already exists: {record_id}")
        version = PersonalContextVersion(
            record_id=record_id,
            version=1,
            status="proposed",
            original_text=_required(original_text, "Original wording"),
            language=_required(language, "Language"),
            canonical_meaning=_required(canonical_meaning, "Canonical meaning"),
            source=_required(source, "Source"),
            sensitivity=sensitivity,
            retrieval_permitted=False,
            processing_location=processing_location,
            created_at=created_at,
        )
        self._repository.save(version, expected_version=0)
        return version

    def correct(
        self,
        record_id: str,
        *,
        original_text: str,
        language: str,
        canonical_meaning: str,
        corrected_at: datetime,
        expected_revision: int | None = None,
    ) -> PersonalContextVersion:
        current = self._current(record_id)
        if expected_revision is not None and current.version != expected_revision:
            raise ContextRevisionConflict(record_id, expected_revision, current.version)
        corrected = replace(
            current,
            version=current.version + 1,
            status="proposed" if current.status == "proposed" else "active",
            original_text=_required(original_text, "Correction wording"),
            language=_required(language, "Correction language"),
            canonical_meaning=_required(canonical_meaning, "Canonical meaning"),
            source="direct user correction",
            created_at=corrected_at,
            supersedes_version=current.version,
        )
        self._repository.save(corrected, expected_version=current.version)
        return corrected

    def confirm(self, record_id: str, confirmed_at: datetime) -> PersonalContextVersion:
        current = self._current(record_id)
        if current.status != "proposed":
            raise ValueError("Only proposed personal context can be confirmed.")
        confirmed = replace(
            current,
            version=current.version + 1,
            status="active",
            retrieval_permitted=True,
            created_at=confirmed_at,
            supersedes_version=current.version,
        )
        self._repository.save(confirmed, expected_version=current.version)
        return confirmed

    def retrieve(self, query: RetrievalQuery) -> tuple[PersonalContextVersion, ...]:
        query_terms = _lexical_terms(query.text)
        return tuple(
            item
            for item in self._current_versions()
            if item.status == "active"
            and item.retrieval_permitted
            and item.source in query.allowed_sources
            and item.sensitivity in query.allowed_sensitivities
            and item.processing_location == query.processing_location
            and (item.fresh_until is None or item.fresh_until >= query.now)
            and _is_relevant(query.text, query_terms, item)
        )

    def retrieve_proposed(
        self, query: RetrievalQuery
    ) -> tuple[PersonalContextVersion, ...]:
        query_terms = _lexical_terms(query.text)
        return tuple(
            item
            for item in self._current_versions()
            if item.status == "proposed"
            and item.source in query.allowed_sources
            and item.sensitivity in query.allowed_sensitivities
            and item.processing_location == query.processing_location
            and _is_relevant(query.text, query_terms, item)
        )

    def list_active(self) -> tuple[PersonalContextVersion, ...]:
        return tuple(
            item for item in self._current_versions() if item.status == "active"
        )

    def list_proposed(self) -> tuple[PersonalContextVersion, ...]:
        return tuple(
            item for item in self._current_versions() if item.status == "proposed"
        )

    def inspect(self, record_id: str) -> ContextWhyView:
        versions = self._repository.versions(record_id)
        if not versions:
            raise KeyError(f"Unknown personal context: {record_id}")
        return ContextWhyView(current=versions[-1], superseded=versions[:-1])

    def archive(self, record_id: str, changed_at: datetime) -> PersonalContextVersion:
        return self._transition(record_id, "archived", changed_at)

    def forget(self, record_id: str, changed_at: datetime) -> PersonalContextVersion:
        return self._transition(record_id, "forgotten", changed_at)

    def recover(self, record_id: str, changed_at: datetime) -> PersonalContextVersion:
        return self._transition(record_id, "active", changed_at)

    def enforce_retention(self, now: datetime) -> tuple[SuppressionMarker, ...]:
        expired_ids = {
            item.record_id
            for item in self._current_versions()
            if item.retention_until is not None and item.retention_until <= now
        }
        return tuple(self.delete(record_id, now) for record_id in sorted(expired_ids))

    def delete(self, record_id: str, deleted_at: datetime) -> SuppressionMarker:
        current = self._current(record_id)
        marker = SuppressionMarker(record_id, deleted_at)
        for remover in self._link_removers:
            remover.remove_context_links(record_id)
        self._repository.delete(record_id, marker, expected_version=current.version)
        return marker

    def _transition(
        self, record_id: str, status: ContextStatus, changed_at: datetime
    ) -> PersonalContextVersion:
        current = self._current(record_id)
        if current.status == status:
            return current
        transitioned = replace(
            current,
            version=current.version + 1,
            status=status,
            created_at=changed_at,
            supersedes_version=current.version,
        )
        self._repository.save(transitioned, expected_version=current.version)
        return transitioned

    def _current(self, record_id: str) -> PersonalContextVersion:
        versions = self._repository.versions(record_id)
        if not versions:
            raise KeyError(f"Unknown personal context: {record_id}")
        return versions[-1]

    def _current_versions(self) -> tuple[PersonalContextVersion, ...]:
        current: dict[str, PersonalContextVersion] = {}
        for item in self._repository.all_versions():
            current[item.record_id] = item
        return tuple(current.values())


def _required(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} cannot be empty.")
    return cleaned


def _lexical_terms(text: str) -> set[str]:
    normalized = text.casefold()
    words = {
        word
        for word in re.findall(r"\w+", normalized, flags=re.UNICODE)
        if word not in _RETRIEVAL_STOP_WORDS and len(word) > 1
    }
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    words.update(cjk[index : index + 2] for index in range(len(cjk) - 1))
    return words


def _is_relevant(
    query_text: str,
    query_terms: set[str],
    item: PersonalContextVersion,
) -> bool:
    searchable = f"{item.original_text} {item.canonical_meaning}".casefold()
    normalized_query = query_text.casefold().strip()
    return normalized_query in searchable or bool(
        query_terms & _lexical_terms(searchable)
    )


def _version_to_data(version: PersonalContextVersion) -> dict[str, object]:
    data = asdict(version)
    data["created_at"] = version.created_at.isoformat()
    data["fresh_until"] = (
        version.fresh_until.isoformat() if version.fresh_until else None
    )
    data["retention_until"] = (
        version.retention_until.isoformat() if version.retention_until else None
    )
    return data


def _version_from_data(data: dict[str, Any]) -> PersonalContextVersion:
    return PersonalContextVersion(
        record_id=str(data["record_id"]),
        version=int(data["version"]),
        status=data["status"],
        original_text=str(data["original_text"]),
        language=str(data["language"]),
        canonical_meaning=str(data["canonical_meaning"]),
        source=str(data["source"]),
        sensitivity=data["sensitivity"],
        retrieval_permitted=bool(data["retrieval_permitted"]),
        processing_location=str(data["processing_location"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
        fresh_until=(
            datetime.fromisoformat(str(data["fresh_until"]))
            if data.get("fresh_until")
            else None
        ),
        retention_until=(
            datetime.fromisoformat(str(data["retention_until"]))
            if data.get("retention_until")
            else None
        ),
        supersedes_version=(
            int(data["supersedes_version"])
            if data.get("supersedes_version") is not None
            else None
        ),
    )


def read_user_profile(path: Path) -> tuple[UserProfileEntry, ...]:
    source_path = path.expanduser()
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("The user profile must be a regular file.")
    if source_path.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError("The user profile cannot exceed 65,536 bytes.")
    try:
        text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("The user profile must be UTF-8 text.") from error
    entries = _profile_entries(text, source_path.name)
    if not entries:
        raise ValueError("The user profile contains no reviewable statements.")
    return entries


def _profile_entries(text: str, source_name: str) -> tuple[UserProfileEntry, ...]:
    section = "Profile"
    paragraphs: list[tuple[str, str]] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        statement = " ".join(current).strip()
        current.clear()
        if statement:
            paragraphs.append((section, statement))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if heading:
            flush()
            section = heading.group(1).strip()
            continue
        if re.match(r"^#\s+", line) or re.fullmatch(r"\|?\s*:?-{3,}.*", line):
            continue
        if not line:
            flush()
            continue
        list_item = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if list_item:
            flush()
            paragraphs.append((section, list_item.group(1).strip()))
            continue
        current.append(line)
    flush()

    entries: list[UserProfileEntry] = []
    for index, (entry_section, statement) in enumerate(paragraphs, start=1):
        digest = sha256(f"{entry_section}\0{statement}".encode("utf-8")).hexdigest()[
            :16
        ]
        entries.append(
            UserProfileEntry(
                record_id=f"profile-{index:03d}-{digest}",
                section=entry_section,
                text=statement,
                source=f"user profile import: {source_name}#{entry_section}",
            )
        )
    return tuple(entries)
