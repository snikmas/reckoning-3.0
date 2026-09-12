from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from reckoning.automation import (
    RoutineProposal,
    RoutineReceipt,
    RoutineRun,
    RoutineRunClaimConflict,
    StepResult,
    _proposal_from_data,
    _proposal_to_data,
    _receipt_from_data,
    _receipt_to_data,
    _run_from_data,
    _run_to_data,
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


AUTOMATION_SCHEMA_VERSION = "1"


class SQLiteAutomationRepository:
    """Transactional per-placement-root storage for routine authority and runs."""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path
        self._root = logical_root_for(legacy_path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def create_proposal(
        self, routine_id: str, build: Callable[[int], RoutineProposal]
    ) -> RoutineProposal:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = _next_version(connection, routine_id)
                proposal = build(version)
                _insert_proposal(connection, proposal)
                connection.commit()
                return proposal
            except BaseException:
                connection.rollback()
                raise

    def revise_proposal(
        self,
        current_id: str,
        build: Callable[[RoutineProposal, int], RoutineProposal],
    ) -> RoutineProposal:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _select_proposal(connection, current_id)
                if current is None:
                    raise KeyError(f"Unknown routine proposal: {current_id}")
                if current.status != "confirmed":
                    raise ValueError(
                        "Only a confirmed routine contract can be revised."
                    )
                version = _max_version(connection, current.routine_id) + 1
                candidate = build(current, version)
                _insert_proposal(connection, candidate)
                connection.commit()
                return candidate
            except BaseException:
                connection.rollback()
                raise

    def confirm_proposal(self, proposal_id: str) -> RoutineProposal:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                proposal = _select_proposal(connection, proposal_id)
                if proposal is None:
                    raise KeyError(f"Unknown routine proposal: {proposal_id}")
                if proposal.status != "proposed":
                    raise ValueError(
                        f"Cannot confirm a {proposal.status} routine proposal."
                    )
                if proposal.supersedes_proposal_id is not None:
                    previous = _select_proposal(
                        connection, proposal.supersedes_proposal_id
                    )
                    if previous is not None and previous.status == "confirmed":
                        _set_proposal_status(
                            connection, previous.id, "superseded"
                        )
                for other in _proposals_for(connection, proposal.routine_id):
                    if other.id != proposal.id and other.status == "confirmed":
                        _set_proposal_status(connection, other.id, "superseded")
                _set_proposal_status(connection, proposal.id, "confirmed")
                confirmed = _select_proposal(connection, proposal.id)
                connection.commit()
                assert confirmed is not None
                return confirmed
            except BaseException:
                connection.rollback()
                raise

    def get_proposal(self, proposal_id: str) -> RoutineProposal:
        with closing(connect_database(self._path)) as connection:
            proposal = _select_proposal(connection, proposal_id)
            if proposal is None:
                raise KeyError(f"Unknown routine proposal: {proposal_id}")
            return proposal

    def proposals_for(self, routine_id: str) -> tuple[RoutineProposal, ...]:
        with closing(connect_database(self._path)) as connection:
            return _proposals_for(connection, routine_id)

    def list_proposals(self) -> tuple[RoutineProposal, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT proposal_id FROM routine_proposals ORDER BY rowid"
            ).fetchall()
            return tuple(
                _select_proposal(connection, str(row[0])) for row in rows
            )

    def save_proposal(self, proposal: RoutineProposal) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = _select_proposal(connection, proposal.id)
                if existing is None:
                    _insert_proposal(connection, proposal)
                else:
                    connection.execute(
                        """
                        UPDATE routine_proposals
                        SET routine_id = ?, version = ?, status = ?,
                            supersedes_proposal_id = ?, payload = ?,
                            created_at = ?, revision = revision + 1
                        WHERE proposal_id = ?
                        """,
                        (
                            proposal.routine_id,
                            proposal.version,
                            proposal.status,
                            proposal.supersedes_proposal_id,
                            json.dumps(_proposal_to_data(proposal), sort_keys=True),
                            proposal.created_at.isoformat(),
                            proposal.id,
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def start_run(self, run: RoutineRun) -> RoutineRun:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = _select_run_by_key(connection, run.idempotency_key)
                if existing is not None:
                    connection.commit()
                    return existing
                if _select_run(connection, run.id) is not None:
                    raise ValueError(
                        f"Routine run {run.id} already exists with another key."
                    )
                _insert_run(connection, run)
                connection.commit()
                return run
            except BaseException:
                connection.rollback()
                raise

    def claim_run(
        self, run_id: str, *, owner: str, claimed_at: datetime
    ) -> int:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT claim_owner, claim_state, revision "
                    "FROM routine_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown routine run: {run_id}")
                if row[1] == "finalized":
                    raise RoutineRunClaimConflict(
                        f"Routine run {run_id} is already finalized."
                    )
                if row[1] == "active" and str(row[0]) != owner:
                    raise RoutineRunClaimConflict(
                        f"Routine run {run_id} is claimed by another worker."
                    )
                revision = int(row[2]) + 1
                connection.execute(
                    """
                    UPDATE routine_runs
                    SET claim_owner = ?, claim_state = 'active',
                        claimed_at = ?, revision = ?
                    WHERE run_id = ?
                    """,
                    (owner, claimed_at.isoformat(), revision, run_id),
                )
                connection.commit()
                return revision
            except BaseException:
                connection.rollback()
                raise

    def release_run(self, run_id: str, owner: str) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE routine_runs
                    SET claim_state = 'unclaimed', revision = revision + 1
                    WHERE run_id = ? AND claim_owner = ?
                      AND claim_state = 'active'
                    """,
                    (run_id, owner),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def recover_run(
        self,
        run_id: str,
        *,
        expected_revision: int,
        new_owner: str,
        reason: str,
        recovered_at: datetime,
    ) -> int:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT claim_owner, claim_state, revision "
                    "FROM routine_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown routine run: {run_id}")
                if int(row[2]) != expected_revision:
                    raise RoutineRunClaimConflict(
                        f"Routine run {run_id} changed from revision "
                        f"{expected_revision} to {row[2]}."
                    )
                if row[1] == "finalized":
                    raise RoutineRunClaimConflict(
                        f"Routine run {run_id} is already finalized."
                    )
                revision = int(row[2]) + 1
                connection.execute(
                    """
                    UPDATE routine_runs
                    SET claim_owner = ?, claim_state = 'active',
                        revision = ?, recovery_owner = ?,
                        recovery_reason = ?, recovered_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        new_owner,
                        revision,
                        str(row[0]),
                        reason,
                        recovered_at.isoformat(),
                        run_id,
                    ),
                )
                connection.commit()
                return revision
            except BaseException:
                connection.rollback()
                raise

    def run_revision(self, run_id: str) -> int:
        with closing(connect_database(self._path)) as connection:
            row = connection.execute(
                "SELECT revision FROM routine_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown routine run: {run_id}")
            return int(row[0])

    def save_run_progress(
        self, run: RoutineRun, *, owner: str, expected_revision: int
    ) -> int:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                revision = _write_run_progress(
                    connection, run, owner=owner, expected_revision=expected_revision
                )
                connection.commit()
                return revision
            except BaseException:
                connection.rollback()
                raise

    def finalize_run(
        self,
        run: RoutineRun,
        receipt: RoutineReceipt,
        *,
        owner: str,
        expected_revision: int,
    ) -> int:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                revision = _write_run_progress(
                    connection, run, owner=owner, expected_revision=expected_revision
                )
                connection.execute(
                    """
                    UPDATE routine_runs
                    SET claim_state = 'finalized', revision = revision + 1
                    WHERE run_id = ? AND claim_owner = ?
                    """,
                    (run.id, owner),
                )
                _insert_receipt(connection, receipt)
                connection.commit()
                return revision + 1
            except BaseException:
                connection.rollback()
                raise

    def get_run(self, run_id: str) -> RoutineRun:
        with closing(connect_database(self._path)) as connection:
            run = _select_run(connection, run_id)
            if run is None:
                raise KeyError(f"Unknown routine run: {run_id}")
            return run

    def find_run_by_key(self, idempotency_key: str) -> RoutineRun | None:
        with closing(connect_database(self._path)) as connection:
            return _select_run_by_key(connection, idempotency_key)

    def list_runs(self) -> tuple[RoutineRun, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT run_id FROM routine_runs ORDER BY rowid"
            ).fetchall()
            return tuple(_select_run(connection, str(row[0])) for row in rows)

    def save_run(self, run: RoutineRun) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if _select_run(connection, run.id) is None:
                    _insert_run(connection, run)
                else:
                    _update_run_columns(connection, run)
                    _replace_step_results(connection, run)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def get_receipt(self, run_id: str) -> RoutineReceipt | None:
        with closing(connect_database(self._path)) as connection:
            return _select_receipt(connection, run_id)

    def list_receipts(self) -> tuple[RoutineReceipt, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT run_id FROM routine_receipts ORDER BY rowid"
            ).fetchall()
            return tuple(
                _select_receipt(connection, str(row[0])) for row in rows
            )

    def save_receipt(self, receipt: RoutineReceipt) -> None:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _insert_receipt(connection, receipt)
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
                digest_key="automation_legacy_sha256",
                label="automation",
            )
            return

        legacy_data: dict[str, Any] | None = None
        legacy: _LegacyAutomation | None = None
        legacy_digest: str | None = None
        if self._legacy_path.exists():
            try:
                legacy_data, legacy, legacy_digest = _load_legacy(self._legacy_path)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if _store_ready(self._path):
                    return
                raise RuntimeError("Invalid legacy automation storage.") from None
            preserve_legacy_rollback(self._legacy_path, legacy_data)
            write_legacy_authority(self._legacy_path, state="migration-pending")

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = metadata(connection, "automation_schema_version")
                if current_version is not None and not _tables_exist(connection):
                    raise RuntimeError(
                        "Automation schema authority has incomplete tables."
                    )
                _create_schema(connection)
                current_version = metadata(connection, "automation_schema_version")
                if current_version not in (None, AUTOMATION_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported automation storage schema.")
                if current_version is None:
                    if _record_count(connection):
                        raise RuntimeError(
                            "Automation storage has records without schema authority."
                        )
                    if legacy is not None:
                        _import_legacy(connection, legacy)
                    set_metadata(
                        connection,
                        "automation_schema_version",
                        AUTOMATION_SCHEMA_VERSION,
                    )
                    set_metadata(
                        connection,
                        "automation_legacy_sha256",
                        legacy_digest or "not-required",
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        write_legacy_authority(self._legacy_path, state="sqlite-authoritative")


def validate_automation_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, "automation_schema_version")
    has_tables = _tables_exist(connection)
    if version not in (None, AUTOMATION_SCHEMA_VERSION):
        raise RuntimeError("Unsupported automation storage schema.")
    if version is None and _any_table_exists(connection):
        raise RuntimeError("Incomplete automation storage schema.")
    if version is not None and not has_tables:
        raise RuntimeError("Incomplete automation storage schema.")


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS routine_proposals (
            proposal_id TEXT PRIMARY KEY,
            routine_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'confirmed', 'superseded')
            ),
            supersedes_proposal_id TEXT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revision INTEGER NOT NULL,
            UNIQUE (routine_id, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS routine_runs (
            run_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            scheduled_for TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'success', 'partial', 'blocked', 'failed')
            ),
            current_step INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            current_step_attempts INTEGER NOT NULL,
            steps TEXT NOT NULL,
            payload TEXT NOT NULL,
            claim_owner TEXT,
            claim_state TEXT NOT NULL CHECK (
                claim_state IN ('unclaimed', 'active', 'finalized')
            ),
            claimed_at TEXT,
            recovery_owner TEXT,
            recovery_reason TEXT,
            recovered_at TEXT,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS routine_step_results (
            run_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            cost_units INTEGER NOT NULL,
            model_calls INTEGER NOT NULL,
            PRIMARY KEY (run_id, position)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS routine_receipts (
            run_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            status TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            executed INTEGER NOT NULL,
            results TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
        """
    )


def _tables_exist(connection: sqlite3.Connection) -> bool:
    return all(
        table_exists(connection, table)
        for table in (
            "routine_proposals",
            "routine_runs",
            "routine_step_results",
            "routine_receipts",
        )
    )


def _any_table_exists(connection: sqlite3.Connection) -> bool:
    return any(
        table_exists(connection, table)
        for table in (
            "routine_proposals",
            "routine_runs",
            "routine_step_results",
            "routine_receipts",
        )
    )


def _record_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM routine_proposals) +
                (SELECT COUNT(*) FROM routine_runs) +
                (SELECT COUNT(*) FROM routine_step_results) +
                (SELECT COUNT(*) FROM routine_receipts)
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
                metadata(connection, "automation_schema_version")
                == AUTOMATION_SCHEMA_VERSION
                and _tables_exist(connection)
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _next_version(connection: sqlite3.Connection, routine_id: str) -> int:
    return _max_version(connection, routine_id) + 1


def _max_version(connection: sqlite3.Connection, routine_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM routine_proposals "
        "WHERE routine_id = ?",
        (routine_id,),
    ).fetchone()
    return int(row[0])


def _select_proposal(
    connection: sqlite3.Connection, proposal_id: str
) -> RoutineProposal | None:
    row = connection.execute(
        "SELECT payload FROM routine_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        return None
    return _proposal_from_data(json.loads(str(row[0])))


def _proposals_for(
    connection: sqlite3.Connection, routine_id: str
) -> tuple[RoutineProposal, ...]:
    rows = connection.execute(
        "SELECT payload FROM routine_proposals WHERE routine_id = ? "
        "ORDER BY version",
        (routine_id,),
    ).fetchall()
    return tuple(_proposal_from_data(json.loads(str(row[0]))) for row in rows)


def _insert_proposal(
    connection: sqlite3.Connection, proposal: RoutineProposal
) -> None:
    connection.execute(
        """
        INSERT INTO routine_proposals (
            proposal_id, routine_id, version, status,
            supersedes_proposal_id, payload, created_at, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            proposal.id,
            proposal.routine_id,
            proposal.version,
            proposal.status,
            proposal.supersedes_proposal_id,
            json.dumps(_proposal_to_data(proposal), sort_keys=True),
            proposal.created_at.isoformat(),
        ),
    )


def _set_proposal_status(
    connection: sqlite3.Connection, proposal_id: str, status: str
) -> None:
    proposal = _select_proposal(connection, proposal_id)
    if proposal is None:
        return
    updated = _proposal_to_data(proposal)
    updated["status"] = status
    connection.execute(
        """
        UPDATE routine_proposals
        SET status = ?, payload = ?, revision = revision + 1
        WHERE proposal_id = ?
        """,
        (status, json.dumps(updated, sort_keys=True), proposal_id),
    )


def _select_run(
    connection: sqlite3.Connection, run_id: str
) -> RoutineRun | None:
    row = connection.execute(
        "SELECT payload FROM routine_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return _run_from_data(json.loads(str(row[0])))


def _select_run_by_key(
    connection: sqlite3.Connection, idempotency_key: str
) -> RoutineRun | None:
    row = connection.execute(
        "SELECT payload FROM routine_runs WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    return _run_from_data(json.loads(str(row[0])))


def _run_payload(run: RoutineRun) -> str:
    return json.dumps(_run_to_data(run), sort_keys=True)


def _insert_run(connection: sqlite3.Connection, run: RoutineRun) -> None:
    connection.execute(
        """
        INSERT INTO routine_runs (
            run_id, proposal_id, idempotency_key, scheduled_for, status,
            current_step, attempts, current_step_attempts, steps, payload,
            claim_owner, claim_state, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'unclaimed', 1)
        """,
        (
            run.id,
            run.proposal_id,
            run.idempotency_key,
            run.scheduled_for.isoformat(),
            run.status,
            run.current_step,
            run.attempts,
            run.current_step_attempts,
            json.dumps(
                [
                    {"id": step.id, "kind": step.kind, "action": step.action}
                    for step in run.steps
                ]
            ),
            _run_payload(run),
        ),
    )
    _replace_step_results(connection, run)


def _update_run_columns(
    connection: sqlite3.Connection, run: RoutineRun
) -> None:
    connection.execute(
        """
        UPDATE routine_runs
        SET proposal_id = ?, idempotency_key = ?, scheduled_for = ?,
            status = ?, current_step = ?, attempts = ?,
            current_step_attempts = ?, steps = ?, payload = ?,
            revision = revision + 1
        WHERE run_id = ?
        """,
        (
            run.proposal_id,
            run.idempotency_key,
            run.scheduled_for.isoformat(),
            run.status,
            run.current_step,
            run.attempts,
            run.current_step_attempts,
            json.dumps(
                [
                    {"id": step.id, "kind": step.kind, "action": step.action}
                    for step in run.steps
                ]
            ),
            _run_payload(run),
            run.id,
        ),
    )


def _replace_step_results(
    connection: sqlite3.Connection, run: RoutineRun
) -> None:
    connection.execute(
        "DELETE FROM routine_step_results WHERE run_id = ?", (run.id,)
    )
    for position, result in enumerate(run.results):
        connection.execute(
            """
            INSERT INTO routine_step_results (
                run_id, position, status, detail, cost_units, model_calls
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                position,
                result.status,
                result.detail,
                result.cost_units,
                result.model_calls,
            ),
        )


def _write_run_progress(
    connection: sqlite3.Connection,
    run: RoutineRun,
    *,
    owner: str,
    expected_revision: int,
) -> int:
    row = connection.execute(
        "SELECT claim_owner, claim_state, revision "
        "FROM routine_runs WHERE run_id = ?",
        (run.id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown routine run: {run.id}")
    if str(row[0]) != owner:
        raise RoutineRunClaimConflict(
            f"Routine run {run.id} is owned by another worker."
        )
    if int(row[2]) != expected_revision:
        raise RoutineRunClaimConflict(
            f"Routine run {run.id} changed from revision "
            f"{expected_revision} to {row[2]}."
        )
    revision = expected_revision + 1
    connection.execute(
        """
        UPDATE routine_runs
        SET proposal_id = ?, idempotency_key = ?, scheduled_for = ?,
            status = ?, current_step = ?, attempts = ?,
            current_step_attempts = ?, steps = ?, payload = ?,
            revision = ?
        WHERE run_id = ?
        """,
        (
            run.proposal_id,
            run.idempotency_key,
            run.scheduled_for.isoformat(),
            run.status,
            run.current_step,
            run.attempts,
            run.current_step_attempts,
            json.dumps(
                [
                    {"id": step.id, "kind": step.kind, "action": step.action}
                    for step in run.steps
                ]
            ),
            _run_payload(run),
            revision,
            run.id,
        ),
    )
    _replace_step_results(connection, run)
    return revision


def _select_receipt(
    connection: sqlite3.Connection, run_id: str
) -> RoutineReceipt | None:
    row = connection.execute(
        """
        SELECT run_id, proposal_id, status, completed_at, attempts, executed,
               results
        FROM routine_receipts WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    results = tuple(
        StepResult(
            item["status"],
            str(item["detail"]),
            int(item["cost_units"]),
            int(item["model_calls"]),
        )
        for item in json.loads(str(row[6]))
    )
    return RoutineReceipt(
        run_id=str(row[0]),
        proposal_id=str(row[1]),
        status=row[2],
        completed_at=datetime.fromisoformat(str(row[3])),
        attempts=int(row[4]),
        results=results,
        executed=bool(row[5]),
    )


def _insert_receipt(
    connection: sqlite3.Connection, receipt: RoutineReceipt
) -> None:
    connection.execute(
        """
        INSERT INTO routine_receipts (
            run_id, proposal_id, status, completed_at, attempts, executed,
            results, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT (run_id) DO UPDATE SET
            proposal_id = excluded.proposal_id,
            status = excluded.status,
            completed_at = excluded.completed_at,
            attempts = excluded.attempts,
            executed = excluded.executed,
            results = excluded.results,
            revision = routine_receipts.revision + 1
        """,
        (
            receipt.run_id,
            receipt.proposal_id,
            receipt.status,
            receipt.completed_at.isoformat(),
            receipt.attempts,
            int(receipt.executed),
            json.dumps(
                [
                    {
                        "status": result.status,
                        "detail": result.detail,
                        "cost_units": result.cost_units,
                        "model_calls": result.model_calls,
                    }
                    for result in receipt.results
                ]
            ),
        ),
    )


class _LegacyAutomation:
    def __init__(
        self,
        proposals: tuple[RoutineProposal, ...],
        runs: tuple[RoutineRun, ...],
        receipts: tuple[RoutineReceipt, ...],
    ) -> None:
        self.proposals = proposals
        self.runs = runs
        self.receipts = receipts


def _load_legacy_authority(path: Path) -> tuple[dict[str, Any], str]:
    data, _legacy, digest = _load_legacy(path)
    return data, digest


def _load_legacy(
    path: Path,
) -> tuple[dict[str, Any], _LegacyAutomation, str]:
    from reckoning.json_store import read_json

    data = read_json(
        path,
        default={"schema_version": 1, "proposals": [], "runs": [], "receipts": []},
    )
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        from reckoning.store_migration import rollback_path

        data = read_json(
            rollback_path(path),
            default={"schema_version": 1, "proposals": [], "runs": [], "receipts": []},
        )
    raw_proposals = data.get("proposals")
    raw_runs = data.get("runs")
    raw_receipts = data.get("receipts")
    if (
        data.get("schema_version") != 1
        or not isinstance(raw_proposals, list)
        or any(not isinstance(item, dict) for item in raw_proposals)
        or not isinstance(raw_runs, list)
        or any(not isinstance(item, dict) for item in raw_runs)
        or not isinstance(raw_receipts, list)
        or any(not isinstance(item, dict) for item in raw_receipts)
    ):
        raise RuntimeError("Unsupported automation storage schema.")
    proposals = tuple(_proposal_from_data(item) for item in raw_proposals)
    runs = tuple(_run_from_data(item) for item in raw_runs)
    receipts = tuple(_receipt_from_data(item) for item in raw_receipts)
    legacy = _LegacyAutomation(proposals, runs, receipts)
    _validate_legacy(legacy)
    canonical = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return data, legacy, sha256(canonical).hexdigest()


def _validate_legacy(legacy: _LegacyAutomation) -> None:
    proposal_ids = [item.id for item in legacy.proposals]
    if any(not item for item in proposal_ids) or len(proposal_ids) != len(
        set(proposal_ids)
    ):
        raise ValueError("Routine proposal identifiers must be unique.")
    versions = [(item.routine_id, item.version) for item in legacy.proposals]
    if len(versions) != len(set(versions)):
        raise ValueError("Routine proposal versions must be unique per routine.")
    known = set(proposal_ids)
    for proposal in legacy.proposals:
        if proposal.status not in ("proposed", "confirmed", "superseded"):
            raise ValueError("Legacy routine proposal status is invalid.")
        if (
            proposal.supersedes_proposal_id is not None
            and proposal.supersedes_proposal_id not in known
        ):
            raise ValueError("Routine supersession links are invalid.")
    run_ids = [item.id for item in legacy.runs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("Routine run identifiers must be unique.")
    keys = [item.idempotency_key for item in legacy.runs]
    if len(keys) != len(set(keys)):
        raise ValueError("Routine run idempotency keys must be unique.")
    if any(item.proposal_id not in known for item in legacy.runs):
        raise ValueError("Routine run proposal links are invalid.")
    receipt_ids = [item.run_id for item in legacy.receipts]
    if len(receipt_ids) != len(set(receipt_ids)) or any(
        item not in set(run_ids) for item in receipt_ids
    ):
        raise ValueError("Routine receipt links are invalid.")


def _import_legacy(
    connection: sqlite3.Connection, legacy: _LegacyAutomation
) -> None:
    for proposal in legacy.proposals:
        _insert_proposal(connection, proposal)
    for run in legacy.runs:
        _insert_run(connection, run)
    for receipt in legacy.receipts:
        _insert_receipt(connection, receipt)
