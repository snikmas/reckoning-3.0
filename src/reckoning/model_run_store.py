from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal, cast

from reckoning.conversation import (
    HistorySelection,
    ProviderMessage,
    ProviderMessageRole,
)
from reckoning.conversation_safety import (
    COVERAGE_VERSION,
    DangerDecision,
    OutputPolicyDecision,
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

if TYPE_CHECKING:
    from reckoning.application import ModelRunRecord, ModelRunUsageStatus


MODEL_RUN_SCHEMA_VERSION = "4"


def _load_output_policy_decision(
    action: object, reason_code: object, delivered_speech: object
) -> OutputPolicyDecision | None:
    if action is None or reason_code is None or delivered_speech is None:
        return None
    return OutputPolicyDecision(
        action=cast(Literal["allow", "replace", "uncertain"], str(action)),
        reason_code=str(reason_code),
        delivered_speech=str(delivered_speech),
        coverage_version=COVERAGE_VERSION,
    )


def _load_danger_decision(
    kind: object, response_kind: object, reason_code: object
) -> DangerDecision | None:
    if kind is None or response_kind is None or reason_code is None:
        return None
    return DangerDecision(
        kind=cast(
            Literal[
                "none",
                "current-self-danger",
                "current-other-danger",
                "ambiguous-concern",
                "quotation",
                "historical",
                "hypothetical",
                "uncertain",
            ],
            str(kind),
        ),
        response_kind=cast(
            Literal["none", "emergency-help", "safe-replacement", "uncertain-boundary"],
            str(response_kind),
        ),
        reason_code=str(reason_code),
        coverage_version=COVERAGE_VERSION,
    )


class SQLiteModelRunRepository:
    """Persist model-run receipts transactionally within one placement root."""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path
        self._root = logical_root_for(legacy_path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def save_run(self, run: ModelRunRecord) -> None:
        output_policy = run.output_policy_decision
        danger = run.danger_decision
        with closing(connect_database(self._path)) as connection, connection:
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
                    usage_status,
                    failure,
                    output_policy_action,
                    output_policy_reason_code,
                    output_policy_delivered_speech,
                    danger_kind,
                    danger_response_kind,
                    danger_reason_code,
                    budgeting_json,
                    private_persona_identifier,
                    private_persona_version_id,
                    private_persona_declared_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    run.usage_status,
                    run.failure,
                    output_policy.action if output_policy is not None else None,
                    output_policy.reason_code if output_policy is not None else None,
                    output_policy.delivered_speech if output_policy is not None else None,
                    danger.kind if danger is not None else None,
                    danger.response_kind if danger is not None else None,
                    danger.reason_code if danger is not None else None,
                    _budgeting_to_json(run.history_selection),
                    run.private_persona_identifier,
                    run.private_persona_version_id,
                    run.private_persona_declared_version,
                ),
            )

    def list_runs(self) -> tuple[ModelRunRecord, ...]:
        from reckoning.application import ModelRunRecord

        with closing(connect_database(self._path)) as connection:
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
                    usage_status,
                    failure,
                    output_policy_action,
                    output_policy_reason_code,
                    output_policy_delivered_speech,
                    danger_kind,
                    danger_response_kind,
                    danger_reason_code,
                    budgeting_json,
                    private_persona_identifier,
                    private_persona_version_id,
                    private_persona_declared_version
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
                usage_status=row[11],
                failure=str(row[12]) if row[12] is not None else None,
                output_policy_decision=_load_output_policy_decision(
                    row[13], row[14], row[15]
                ),
                danger_decision=_load_danger_decision(
                    row[16], row[17], row[18]
                ),
                history_selection=_budgeting_from_json(row[19]),
                private_persona_identifier=(
                    str(row[20]) if row[20] is not None else None
                ),
                private_persona_version_id=(
                    str(row[21]) if row[21] is not None else None
                ),
                private_persona_declared_version=(
                    str(row[22]) if row[22] is not None else None
                ),
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
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = metadata(connection, "model_runs_schema_version")
                if current_version is not None and not table_exists(
                    connection, "model_runs"
                ):
                    raise RuntimeError(
                        "Model-run schema authority has no record table."
                    )
                _create_schema(connection)
                current_version = metadata(connection, "model_runs_schema_version")
                if current_version not in (
                    None,
                    "1",
                    "2",
                    "3",
                    MODEL_RUN_SCHEMA_VERSION,
                ):
                    raise RuntimeError("Unsupported model-run storage schema.")
                if current_version == "1":
                    _migrate_v1_to_v2(connection)
                    current_version = "2"
                if current_version == "2":
                    _migrate_v2_to_v3(connection)
                    current_version = "3"
                if current_version == "3":
                    _migrate_v3_to_v4(connection)
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
                    set_metadata(
                        connection,
                        "model_runs_schema_version",
                        MODEL_RUN_SCHEMA_VERSION,
                    )
                    set_metadata(
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
    from reckoning.automation_store import validate_automation_schema
    from reckoning.external_write_store import validate_external_write_schema
    from reckoning.interface_store import validate_interface_schema
    from reckoning.persistence import validate_continuity_schema
    from reckoning.personal_context import validate_personal_context_schema
    from reckoning.telegram_delivery import validate_telegram_delivery_schema
    from reckoning.trials import validate_trial_schema

    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError("SQLite integrity check failed.")
            root_version = metadata(connection, "root_schema_version")
            model_run_version = metadata(connection, "model_runs_schema_version")
            if root_version != ROOT_DATABASE_SCHEMA_VERSION:
                raise RuntimeError("Unsupported placement-root database schema.")
            if model_run_version not in (None, MODEL_RUN_SCHEMA_VERSION):
                raise RuntimeError("Unsupported model-run storage schema.")
            has_model_runs = table_exists(connection, "model_runs")
            if (model_run_version is None) != (not has_model_runs):
                raise RuntimeError("Incomplete model-run storage schema.")
            validate_continuity_schema(connection)
            validate_personal_context_schema(connection)
            validate_telegram_delivery_schema(connection)
            validate_external_write_schema(connection)
            validate_interface_schema(connection)
            validate_automation_schema(connection)
            validate_trial_schema(connection)
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RuntimeError("Invalid placement-root database.") from error


def validate_database_bytes(content: bytes) -> None:
    with TemporaryDirectory(prefix="reckoning-sqlite-validation-") as temporary:
        path = Path(temporary) / ROOT_DATABASE_FILENAME
        path.write_bytes(content)
        validate_database(path)


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    for column in (
        "output_policy_action",
        "output_policy_reason_code",
        "output_policy_delivered_speech",
        "danger_kind",
        "danger_response_kind",
        "danger_reason_code",
    ):
        connection.execute(
            f"ALTER TABLE model_runs ADD COLUMN {column} TEXT"
        )
    set_metadata(connection, "model_runs_schema_version", "2")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE model_runs ADD COLUMN budgeting_json TEXT"
    )
    set_metadata(connection, "model_runs_schema_version", "3")


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    for column in (
        "private_persona_identifier",
        "private_persona_version_id",
        "private_persona_declared_version",
    ):
        connection.execute(f"ALTER TABLE model_runs ADD COLUMN {column} TEXT")
    set_metadata(connection, "model_runs_schema_version", MODEL_RUN_SCHEMA_VERSION)


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
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
            usage_status TEXT NOT NULL CHECK (
                usage_status IN ('reported', 'not-billable', 'unknown')
            ),
            failure TEXT,
            output_policy_action TEXT,
            output_policy_reason_code TEXT,
            output_policy_delivered_speech TEXT,
            danger_kind TEXT,
            danger_response_kind TEXT,
            danger_reason_code TEXT,
            budgeting_json TEXT,
            private_persona_identifier TEXT,
            private_persona_version_id TEXT,
            private_persona_declared_version TEXT
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
                metadata(connection, "root_schema_version")
                == ROOT_DATABASE_SCHEMA_VERSION
                and metadata(connection, "model_runs_schema_version")
                == MODEL_RUN_SCHEMA_VERSION
                and table_exists(connection, "model_runs")
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _load_legacy_runs(
    path: Path,
) -> tuple[dict[str, Any], tuple[ModelRunRecord, ...], str]:
    from reckoning.application import ModelRunRecord

    data = read_json(path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        data = read_json(_rollback_path(path), default={})
    raw_runs = data.get("runs")
    if (
        data.get("schema_version") != 1
        or not isinstance(raw_runs, list)
        or any(not isinstance(item, dict) for item in raw_runs)
    ):
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
            usage_status=_legacy_usage_status(item),
            failure=str(item["failure"]) if item.get("failure") else None,
        )
        for item in raw_runs
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
        or run.usage_status not in {"reported", "not-billable", "unknown"}
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
    for run in runs:
        evidence = (
            run.private_persona_identifier,
            run.private_persona_version_id,
            run.private_persona_declared_version,
        )
        if any(value is not None for value in evidence) != all(
            isinstance(value, str) and bool(value) for value in evidence
        ):
            raise ValueError("Model-run private persona evidence is incomplete.")


def _preserve_legacy_rollback(path: Path, data: dict[str, Any]) -> None:
    rollback = _rollback_path(path)
    if rollback.exists():
        if read_json(rollback, default={}) != data:
            raise RuntimeError("The protected model-run rollback source differs.")
        return
    atomic_write_json(rollback, data)
    rollback.chmod(0o400)


def _legacy_usage_status(item: dict[str, Any]) -> ModelRunUsageStatus:
    stored = item.get("usage_status")
    if stored in {"reported", "not-billable", "unknown"}:
        return cast("ModelRunUsageStatus", stored)
    usage = (
        int(item["input_tokens"]),
        int(item["output_tokens"]),
        int(item["billable_units"]),
    )
    if any(value > 0 for value in usage):
        return "reported"
    if str(item["provider"]) == "fake":
        return "not-billable"
    return "unknown"


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
    with closing(connect_database(database)) as connection:
        migrated_digest = metadata(connection, "model_runs_legacy_sha256")
    if digest != migrated_digest:
        raise RuntimeError(
            "Legacy model-run data changed after SQLite became authoritative."
        )
    _preserve_legacy_rollback(legacy_path, legacy_data)
    _write_legacy_authority(legacy_path, state="sqlite-authoritative")


def _insert_run(connection: sqlite3.Connection, run: ModelRunRecord) -> None:
    output_policy = run.output_policy_decision
    danger = run.danger_decision
    connection.execute(
        """
        INSERT INTO model_runs (
            id, requested_at, status, provider, model, model_calls, latency_ms,
            retries, input_tokens, output_tokens, billable_units, usage_status, failure,
            output_policy_action, output_policy_reason_code, output_policy_delivered_speech,
            danger_kind, danger_response_kind, danger_reason_code, budgeting_json,
            private_persona_identifier, private_persona_version_id,
            private_persona_declared_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            run.usage_status,
            run.failure,
            output_policy.action if output_policy is not None else None,
            output_policy.reason_code if output_policy is not None else None,
            output_policy.delivered_speech if output_policy is not None else None,
            danger.kind if danger is not None else None,
            danger.response_kind if danger is not None else None,
            danger.reason_code if danger is not None else None,
            _budgeting_to_json(run.history_selection),
            run.private_persona_identifier,
            run.private_persona_version_id,
            run.private_persona_declared_version,
        ),
    )


def _budgeting_to_json(selection: HistorySelection | None) -> str | None:
    if selection is None:
        return None
    return json.dumps(
        {
            "selected_messages": [
                {"role": message.role, "content": message.content}
                for message in selection.selected_messages
            ],
            "omitted_turn_count": selection.omitted_turn_count,
            "estimated_input_tokens": selection.estimated_input_tokens,
            "estimator_method": selection.estimator_method,
            "configured_context_window": selection.configured_context_window,
            "effective_context_window": selection.effective_context_window,
            "response_reserve": selection.response_reserve,
            "required_input_tokens": selection.required_input_tokens,
            "selected_turn_count": selection.selected_turn_count,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _budgeting_from_json(data: object) -> HistorySelection | None:
    if data is None:
        return None
    parsed = json.loads(str(data))
    if not isinstance(parsed, dict):
        return None
    return HistorySelection(
        selected_messages=tuple(
            ProviderMessage(
                cast(ProviderMessageRole, str(item["role"])),
                str(item["content"]),
            )
            for item in parsed.get("selected_messages", [])
        ),
        omitted_turn_count=int(parsed.get("omitted_turn_count", 0)),
        estimated_input_tokens=int(parsed.get("estimated_input_tokens", 0)),
        estimator_method=str(parsed.get("estimator_method", "")),
        configured_context_window=(
            int(parsed["configured_context_window"])
            if parsed.get("configured_context_window") is not None
            else None
        ),
        effective_context_window=int(parsed.get("effective_context_window", 0)),
        response_reserve=int(parsed.get("response_reserve", 0)),
        required_input_tokens=int(parsed.get("required_input_tokens", 0)),
        selected_turn_count=int(parsed.get("selected_turn_count", 0)),
    )


def _validate_migration(
    connection: sqlite3.Connection, expected: tuple[ModelRunRecord, ...]
) -> None:
    rows = connection.execute("SELECT id FROM model_runs ORDER BY sequence").fetchall()
    migrated_ids = tuple(str(row[0]) for row in rows)
    expected_ids = tuple(run.id for run in expected)
    if len(migrated_ids) != len(expected_ids) or migrated_ids != expected_ids:
        raise RuntimeError("Model-run migration validation failed.")
