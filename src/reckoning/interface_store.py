from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from reckoning.interfaces import (
    ChannelMessage,
    ChannelName,
    ChannelSession,
    InterfaceProjectionConflict,
    InterfaceSessionConflict,
    InterfaceState,
    OperationalFailure,
    RunReceipt,
    _state_from_data,
)
from reckoning.root_database import (
    ROOT_DATABASE_FILENAME,
    connect_database,
    ensure_root_schema,
    logical_root_for,
    metadata,
    set_metadata,
    table_exists,
)
from reckoning.store_migration import (
    finalize_legacy_authority,
    preserve_legacy_rollback,
    write_legacy_authority,
)


INTERFACE_SCHEMA_VERSION = "1"

_PROJECTIONS = (
    "returning_user",
    "matters_now",
    "changes",
    "decisions",
    "confirmed_records",
    "permissions",
)
_ACTIVITY_RANK = {"idle": 0, "listening": 1, "reasoning": 2, "executing": 3}
_DEFAULT_ACTIVITY = "idle"


class SQLiteInterfaceRepository:
    """Transactional per-placement-root storage for interface sessions and projections."""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path
        self._root = logical_root_for(legacy_path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def load(self) -> InterfaceState:
        with closing(connect_database(self._path)) as connection:
            return _load_state(connection)

    def save(self, state: InterfaceState) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _merge_state(connection, state)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def session_revision(self, channel: ChannelName, session_id: str) -> int:
        with closing(connect_database(self._path)) as connection:
            return _session_revision(connection, channel, session_id)

    def projection_revision(self, projection: str) -> int:
        with closing(connect_database(self._path)) as connection:
            return _projection_revision(connection, projection)

    def append_completed_turn(
        self,
        channel: ChannelName,
        session_id: str,
        user_text: str,
        assistant_text: str,
        *,
        expected_revision: int,
    ) -> ChannelSession:
        return self.append_messages(
            channel,
            session_id,
            (
                ChannelMessage("user", user_text),
                ChannelMessage("assistant", assistant_text),
            ),
            expected_revision=expected_revision,
            returning_user=True,
        )

    def append_messages(
        self,
        channel: ChannelName,
        session_id: str,
        messages: tuple[ChannelMessage, ...],
        *,
        expected_revision: int,
        returning_user: bool = False,
    ) -> ChannelSession:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                actual = _session_revision(connection, channel, session_id)
                if actual != expected_revision:
                    raise InterfaceSessionConflict(
                        channel, session_id, expected_revision, actual
                    )
                if not messages:
                    raise ValueError("At least one interface message is required.")
                if actual == 0:
                    connection.execute(
                        """
                        INSERT INTO interface_sessions (channel, session_id, revision)
                        VALUES (?, ?, 1)
                        """,
                        (channel, session_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE interface_sessions SET revision = revision + 1
                        WHERE channel = ? AND session_id = ?
                        """,
                        (channel, session_id),
                    )
                start = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(position), -1) + 1
                        FROM interface_session_messages
                        WHERE channel = ? AND session_id = ?
                        """,
                        (channel, session_id),
                    ).fetchone()[0]
                )
                for offset, message in enumerate(messages):
                    connection.execute(
                        """
                        INSERT INTO interface_session_messages (
                            channel, session_id, position, role, content
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            channel,
                            session_id,
                            start + offset,
                            message.role,
                            message.content,
                        ),
                    )
                if returning_user:
                    _set_returning_user(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return ChannelSession(channel, session_id, messages)

    def set_activity(
        self,
        activity: str,
        *,
        channel: ChannelName | str = "",
        session_id: str = "",
    ) -> None:
        if activity not in _ACTIVITY_RANK:
            raise ValueError(f"Unknown interface activity: {activity}")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO interface_activity (
                        channel, session_id, activity, revision
                    ) VALUES (?, ?, ?, 1)
                    ON CONFLICT (channel, session_id) DO UPDATE SET
                        activity = excluded.activity,
                        revision = interface_activity.revision + 1
                    """,
                    (channel, session_id, activity),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def set_returning_user(self) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _set_returning_user(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def add_pending_approval(self, approval_id: str) -> None:
        if not approval_id.strip():
            raise ValueError("A pending approval identifier is required.")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state FROM interface_pending_approvals "
                    "WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                if row is not None:
                    if row[0] == "pending":
                        connection.commit()
                        return
                    raise InterfaceProjectionConflict(approval_id, 0, 1)
                connection.execute(
                    """
                    INSERT INTO interface_pending_approvals (
                        approval_id, state, created_at, revision
                    ) VALUES (?, 'pending', ?, 1)
                    """,
                    (approval_id, datetime.now().astimezone().isoformat()),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def resolve_approval(
        self, approval_id: str, *, expected_revision: int | None = None
    ) -> bool:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state, revision FROM interface_pending_approvals "
                    "WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                if row is None or row[0] != "pending":
                    connection.commit()
                    return False
                if expected_revision is not None and int(row[1]) != expected_revision:
                    raise InterfaceProjectionConflict(
                        approval_id, expected_revision, int(row[1])
                    )
                connection.execute(
                    """
                    UPDATE interface_pending_approvals
                    SET state = 'resolved', resolved_at = ?, revision = revision + 1
                    WHERE approval_id = ? AND state = 'pending'
                    """,
                    (datetime.now().astimezone().isoformat(), approval_id),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def replace_projection(
        self, projection: str, values: tuple[str, ...], *, expected_revision: int
    ) -> None:
        if projection not in _PROJECTIONS or projection == "returning_user":
            raise ValueError(f"Unknown replaceable interface projection: {projection}")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                actual = _projection_revision(connection, projection)
                if actual != expected_revision:
                    raise InterfaceProjectionConflict(
                        projection, expected_revision, actual
                    )
                _write_projection(connection, projection, values)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def record_operational_run(
        self,
        receipt: RunReceipt,
        *,
        failure: OperationalFailure | None = None,
        expected_revision: int | None = None,
    ) -> None:
        if failure is not None and failure.run_id != receipt.id:
            raise ValueError("An operational failure must identify the same run.")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if expected_revision is not None:
                    actual = _receipt_revision(connection, receipt.id)
                    if actual != expected_revision:
                        raise InterfaceProjectionConflict(
                            f"receipt:{receipt.id}", expected_revision, actual
                        )
                _upsert_receipt(connection, receipt)
                if failure is not None:
                    _upsert_failure(connection, failure)
                connection.execute(
                    """
                    INSERT INTO interface_activity (
                        channel, session_id, activity, revision
                    ) VALUES ('', '', 'idle', 1)
                    ON CONFLICT (channel, session_id) DO UPDATE SET
                        activity = 'idle',
                        revision = interface_activity.revision + 1
                    """
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _initialize_or_migrate(self) -> None:
        if _store_ready(self._path):
            finalize_legacy_authority(
                self._path,
                self._legacy_path,
                load_legacy=_load_legacy_authority,
                digest_key="interfaces_legacy_sha256",
                label="interface",
            )
            return

        legacy_data: dict[str, Any] | None = None
        legacy_state: InterfaceState | None = None
        legacy_digest: str | None = None
        if self._legacy_path.exists():
            try:
                legacy_data, legacy_state, legacy_digest = _load_legacy(
                    self._legacy_path
                )
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if _store_ready(self._path):
                    return
                raise RuntimeError("Invalid legacy interface storage.") from None
            preserve_legacy_rollback(self._legacy_path, legacy_data)
            write_legacy_authority(self._legacy_path, state="migration-pending")

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = metadata(connection, "interfaces_schema_version")
                if current_version is not None and not _tables_exist(connection):
                    raise RuntimeError(
                        "Interface schema authority has incomplete tables."
                    )
                _create_schema(connection)
                current_version = metadata(connection, "interfaces_schema_version")
                if current_version not in (None, INTERFACE_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported interface storage schema.")
                if current_version is None:
                    if _record_count(connection):
                        raise RuntimeError(
                            "Interface storage has records without schema authority."
                        )
                    if legacy_state is not None:
                        _merge_state(connection, legacy_state)
                    else:
                        _merge_state(connection, InterfaceState())
                    set_metadata(
                        connection,
                        "interfaces_schema_version",
                        INTERFACE_SCHEMA_VERSION,
                    )
                    set_metadata(
                        connection,
                        "interfaces_legacy_sha256",
                        legacy_digest or "not-required",
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        write_legacy_authority(self._legacy_path, state="sqlite-authoritative")


def validate_interface_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, "interfaces_schema_version")
    has_tables = _tables_exist(connection)
    if version not in (None, INTERFACE_SCHEMA_VERSION):
        raise RuntimeError("Unsupported interface storage schema.")
    if version is None and _any_table_exists(connection):
        raise RuntimeError("Incomplete interface storage schema.")
    if version is not None and not has_tables:
        raise RuntimeError("Incomplete interface storage schema.")


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_projections (
            projection TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_sessions (
            channel TEXT NOT NULL,
            session_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            PRIMARY KEY (channel, session_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_session_messages (
            channel TEXT NOT NULL,
            session_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            PRIMARY KEY (channel, session_id, position),
            FOREIGN KEY (channel, session_id)
                REFERENCES interface_sessions(channel, session_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_activity (
            channel TEXT NOT NULL,
            session_id TEXT NOT NULL,
            activity TEXT NOT NULL CHECK (
                activity IN ('idle', 'listening', 'reasoning', 'executing')
            ),
            revision INTEGER NOT NULL,
            PRIMARY KEY (channel, session_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_pending_approvals (
            approval_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('pending', 'resolved')),
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_receipts (
            run_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interface_failures (
            run_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
        """
    )


def _tables_exist(connection: sqlite3.Connection) -> bool:
    return all(
        table_exists(connection, table)
        for table in (
            "interface_projections",
            "interface_sessions",
            "interface_session_messages",
            "interface_activity",
            "interface_pending_approvals",
            "interface_receipts",
            "interface_failures",
        )
    )


def _any_table_exists(connection: sqlite3.Connection) -> bool:
    return any(
        table_exists(connection, table)
        for table in (
            "interface_projections",
            "interface_sessions",
            "interface_session_messages",
            "interface_activity",
            "interface_pending_approvals",
            "interface_receipts",
            "interface_failures",
        )
    )


def _record_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM interface_projections) +
                (SELECT COUNT(*) FROM interface_sessions) +
                (SELECT COUNT(*) FROM interface_session_messages) +
                (SELECT COUNT(*) FROM interface_activity) +
                (SELECT COUNT(*) FROM interface_pending_approvals) +
                (SELECT COUNT(*) FROM interface_receipts) +
                (SELECT COUNT(*) FROM interface_failures)
            """
        ).fetchone()[0]
    )


def _store_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return (
                metadata(connection, "interfaces_schema_version")
                == INTERFACE_SCHEMA_VERSION
                and _tables_exist(connection)
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _merge_state(connection: sqlite3.Connection, state: InterfaceState) -> None:
    if state.returning_user:
        _set_returning_user(connection)
    _write_projection(connection, "matters_now", state.matters_now)
    _write_projection(connection, "changes", state.changes)
    _write_projection(connection, "decisions", state.decisions)
    _write_projection(connection, "confirmed_records", state.confirmed_records)
    _write_projection(connection, "permissions", state.permissions)
    for session in state.sessions:
        _replace_session(connection, session)
    if state.activity in _ACTIVITY_RANK:
        connection.execute(
            """
            INSERT INTO interface_activity (channel, session_id, activity, revision)
            VALUES ('', '', ?, 1)
            ON CONFLICT (channel, session_id) DO UPDATE SET
                activity = excluded.activity,
                revision = interface_activity.revision + 1
            """,
            (state.activity,),
        )
    for approval_id in state.pending_approvals:
        connection.execute(
            """
            INSERT INTO interface_pending_approvals (
                approval_id, state, created_at, revision
            ) VALUES (?, 'pending', ?, 1)
            ON CONFLICT (approval_id) DO NOTHING
            """,
            (approval_id, datetime.now().astimezone().isoformat()),
        )
    for receipt in state.receipts:
        _upsert_receipt(connection, receipt)
    for failure in state.failures:
        _upsert_failure(connection, failure)


def _replace_session(
    connection: sqlite3.Connection, session: ChannelSession
) -> None:
    row = connection.execute(
        "SELECT revision FROM interface_sessions "
        "WHERE channel = ? AND session_id = ?",
        (session.channel, session.session_id),
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO interface_sessions (channel, session_id, revision)
            VALUES (?, ?, 1)
            """,
            (session.channel, session.session_id),
        )
    else:
        connection.execute(
            """
            UPDATE interface_sessions SET revision = revision + 1
            WHERE channel = ? AND session_id = ?
            """,
            (session.channel, session.session_id),
        )
        connection.execute(
            """
            DELETE FROM interface_session_messages
            WHERE channel = ? AND session_id = ?
            """,
            (session.channel, session.session_id),
        )
    for position, message in enumerate(session.messages):
        connection.execute(
            """
            INSERT INTO interface_session_messages (
                channel, session_id, position, role, content
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                session.channel,
                session.session_id,
                position,
                message.role,
                message.content,
            ),
        )


def _write_projection(
    connection: sqlite3.Connection,
    projection: str,
    values: tuple[str, ...],
    *,
    returning_user: bool = False,
) -> None:
    payload = (
        json.dumps(returning_user)
        if projection == "returning_user"
        else json.dumps(list(values))
    )
    connection.execute(
        """
        INSERT INTO interface_projections (projection, payload, revision)
        VALUES (?, ?, 1)
        ON CONFLICT (projection) DO UPDATE SET
            payload = excluded.payload,
            revision = interface_projections.revision + 1
        """,
        (projection, payload),
    )


def _set_returning_user(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT payload FROM interface_projections WHERE projection = 'returning_user'"
    ).fetchone()
    if row is not None and bool(json.loads(str(row[0]))):
        return
    _write_projection(connection, "returning_user", (), returning_user=True)


def _projection_revision(connection: sqlite3.Connection, projection: str) -> int:
    row = connection.execute(
        "SELECT revision FROM interface_projections WHERE projection = ?",
        (projection,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _session_revision(
    connection: sqlite3.Connection, channel: str, session_id: str
) -> int:
    row = connection.execute(
        "SELECT revision FROM interface_sessions "
        "WHERE channel = ? AND session_id = ?",
        (channel, session_id),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _receipt_revision(connection: sqlite3.Connection, run_id: str) -> int:
    row = connection.execute(
        "SELECT revision FROM interface_receipts WHERE run_id = ?", (run_id,)
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _upsert_receipt(connection: sqlite3.Connection, receipt: RunReceipt) -> None:
    payload = json.dumps(
        {**asdict(receipt), "occurred_at": receipt.occurred_at.isoformat()},
        sort_keys=True,
    )
    connection.execute(
        """
        INSERT INTO interface_receipts (run_id, payload, revision)
        VALUES (?, ?, 1)
        ON CONFLICT (run_id) DO UPDATE SET
            payload = excluded.payload,
            revision = interface_receipts.revision + 1
        """,
        (receipt.id, payload),
    )


def _upsert_failure(
    connection: sqlite3.Connection, failure: OperationalFailure
) -> None:
    payload = json.dumps(
        {**asdict(failure), "occurred_at": failure.occurred_at.isoformat()},
        sort_keys=True,
    )
    connection.execute(
        """
        INSERT INTO interface_failures (run_id, payload, revision)
        VALUES (?, ?, 1)
        ON CONFLICT (run_id) DO UPDATE SET
            payload = excluded.payload,
            revision = interface_failures.revision + 1
        """,
        (failure.run_id, payload),
    )


def _load_state(connection: sqlite3.Connection) -> InterfaceState:
    projections = {
        str(row[0]): json.loads(str(row[1]))
        for row in connection.execute(
            "SELECT projection, payload FROM interface_projections"
        ).fetchall()
    }
    sessions = tuple(
        ChannelSession(
            channel=session_row[0],
            session_id=str(session_row[1]),
            messages=tuple(
                ChannelMessage(str(message_row[0]), str(message_row[1]))
                for message_row in connection.execute(
                    """
                    SELECT role, content FROM interface_session_messages
                    WHERE channel = ? AND session_id = ?
                    ORDER BY position
                    """,
                    (session_row[0], session_row[1]),
                ).fetchall()
            ),
        )
        for session_row in connection.execute(
            "SELECT channel, session_id FROM interface_sessions "
            "ORDER BY rowid"
        ).fetchall()
    )
    activity_rows = connection.execute(
        "SELECT activity FROM interface_activity"
    ).fetchall()
    activity = _DEFAULT_ACTIVITY
    for row in activity_rows:
        candidate = str(row[0])
        if _ACTIVITY_RANK.get(candidate, 0) >= _ACTIVITY_RANK[activity]:
            activity = candidate
    pending = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT approval_id FROM interface_pending_approvals "
            "WHERE state = 'pending' ORDER BY rowid"
        ).fetchall()
    )
    receipts = tuple(
        _receipt_from_payload(str(row[0]))
        for row in connection.execute(
            "SELECT payload FROM interface_receipts ORDER BY rowid"
        ).fetchall()
    )
    failures = tuple(
        _failure_from_payload(str(row[0]))
        for row in connection.execute(
            "SELECT payload FROM interface_failures ORDER BY rowid"
        ).fetchall()
    )
    return InterfaceState(
        returning_user=bool(projections.get("returning_user", False)),
        matters_now=tuple(projections.get("matters_now", [])),
        changes=tuple(projections.get("changes", [])),
        decisions=tuple(projections.get("decisions", [])),
        activity=activity,  # type: ignore[arg-type]
        confirmed_records=tuple(projections.get("confirmed_records", [])),
        permissions=tuple(projections.get("permissions", [])),
        pending_approvals=pending,
        receipts=receipts,
        failures=failures,
        sessions=sessions,
    )


def _receipt_from_payload(payload: str) -> RunReceipt:
    data = json.loads(payload)
    return RunReceipt(
        id=str(data["id"]),
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
        status=str(data["status"]),
        summary=str(data["summary"]),
        evidence=tuple(str(value) for value in data.get("evidence", [])),
        memory=tuple(str(value) for value in data.get("memory", [])),
        permissions=tuple(str(value) for value in data.get("permissions", [])),
        approvals=tuple(str(value) for value in data.get("approvals", [])),
        cost_units=int(data.get("cost_units", 0)),
        routine_health=tuple(str(value) for value in data.get("routine_health", [])),
        connector_health=tuple(
            str(value) for value in data.get("connector_health", [])
        ),
        suppressed_findings=tuple(
            str(value) for value in data.get("suppressed_findings", [])
        ),
        tools=tuple(str(value) for value in data.get("tools", [])),
        actions=tuple(str(value) for value in data.get("actions", [])),
    )


def _failure_from_payload(payload: str) -> OperationalFailure:
    data = json.loads(payload)
    return OperationalFailure(
        id=str(data["id"]),
        run_id=str(data["run_id"]),
        summary=str(data["summary"]),
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
    )


def _load_legacy_authority(path: Path) -> tuple[dict[str, Any], str]:
    data, _state, digest = _load_legacy(path)
    return data, digest


def _load_legacy(
    path: Path,
) -> tuple[dict[str, Any], InterfaceState, str]:
    from reckoning.json_store import read_json

    data = read_json(
        path, default={"schema_version": 1, "state": {}}
    )
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        from reckoning.store_migration import rollback_path

        data = read_json(rollback_path(path), default={"schema_version": 1, "state": {}})
    if data.get("schema_version") != 1 or not isinstance(data.get("state"), dict):
        raise RuntimeError("Unsupported interface storage schema.")
    state = _state_from_data(data["state"])
    _validate_legacy(state)
    canonical = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return data, state, sha256(canonical).hexdigest()


def _validate_legacy(state: InterfaceState) -> None:
    keys = [(session.channel, session.session_id) for session in state.sessions]
    if len(keys) != len(set(keys)):
        raise ValueError("Interface sessions must be unique by channel and identifier.")
    for session in state.sessions:
        if session.channel not in ("terminal", "web", "telegram"):
            raise ValueError("Interface session channel is invalid.")
        if any(
            message.role not in ("user", "assistant")
            for message in session.messages
        ):
            raise ValueError("Interface session message roles are invalid.")
    receipt_ids = [receipt.id for receipt in state.receipts]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("Interface receipt identifiers must be unique.")
    failure_ids = [failure.run_id for failure in state.failures]
    if len(failure_ids) != len(set(failure_ids)):
        raise ValueError("Interface failure identifiers must be unique.")
    if len(state.pending_approvals) != len(set(state.pending_approvals)):
        raise ValueError("Interface approval identifiers must be unique.")



