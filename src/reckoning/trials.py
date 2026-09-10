from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
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

EvidenceKind = Literal["automated_check", "real_use"]
CheckOutcome = Literal["passed", "failed"]
RealUseOutcome = Literal["positive", "negative"]
TrialDecisionStatus = Literal["pending", "accepted", "rejected"]
TrialMetric = Literal[
    "useful_outcomes",
    "correction_burden",
    "interruption_burden",
    "spending",
    "reliability",
    "repeated_use",
    "mary_judgment",
]

REQUIRED_REAL_USE_METRICS: tuple[TrialMetric, ...] = (
    "useful_outcomes",
    "correction_burden",
    "interruption_burden",
    "spending",
    "reliability",
    "repeated_use",
    "mary_judgment",
)
TRIAL_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class TrialEvidence:
    id: str
    kind: EvidenceKind
    metric: TrialMetric | None
    value: str
    observed_at: datetime
    notes: str = ""
    check_outcome: CheckOutcome | None = None
    real_use_outcome: RealUseOutcome | None = None
    required: bool = True


@dataclass(frozen=True)
class TrialDecision:
    status: TrialDecisionStatus
    decided_at: datetime
    reason: str
    assessed_revision: str


@dataclass(frozen=True)
class SliceTrial:
    id: str
    version: int
    slice_id: str
    started_at: datetime
    assessed_revision: str
    evidence_start_index: int = 0
    evidence: tuple[TrialEvidence, ...] = ()
    decisions: tuple[TrialDecision, ...] = ()

    @property
    def missing_evidence(self) -> tuple[str, ...]:
        recorded_real_use = {
            item.metric
            for item in self.current_evidence
            if item.kind == "real_use" and item.metric is not None
        }
        missing: list[str] = [
            metric
            for metric in REQUIRED_REAL_USE_METRICS
            if metric not in recorded_real_use
        ]
        if not any(
            item.kind == "automated_check" and item.required
            for item in self.current_evidence
        ):
            missing.append("required_automated_check")
        return tuple(missing)

    @property
    def evidence_complete(self) -> bool:
        return not self.missing_evidence

    @property
    def required_checks_pass(self) -> bool:
        checks = tuple(
            item
            for item in self.current_evidence
            if item.kind == "automated_check" and item.required
        )
        return bool(checks) and all(item.check_outcome == "passed" for item in checks)

    @property
    def real_use_is_positive(self) -> bool:
        observations = tuple(
            item for item in self.current_evidence if item.kind == "real_use"
        )
        return bool(observations) and all(
            item.real_use_outcome == "positive" for item in observations
        )

    @property
    def current_evidence(self) -> tuple[TrialEvidence, ...]:
        return self.evidence[self.evidence_start_index :]

    @property
    def decision(self) -> TrialDecision:
        if self.decisions:
            return self.decisions[-1]
        return TrialDecision(
            "pending",
            self.started_at,
            "Awaiting an explicit decision.",
            self.assessed_revision,
        )

    @property
    def accepted(self) -> bool:
        decision = self.decision
        return (
            self.evidence_complete
            and self.required_checks_pass
            and self.real_use_is_positive
            and decision.status == "accepted"
            and decision.assessed_revision == self.assessed_revision
        )


@dataclass(frozen=True)
class TrialReport:
    trial_id: str
    slice_id: str
    assessed_revision: str
    record_version: int
    decision: TrialDecision
    total_evidence_count: int
    current_evidence_count: int
    missing_evidence: tuple[str, ...]
    required_checks_pass: bool
    real_use_is_positive: bool
    release_accepted: bool


class TrialRevisionConflict(RuntimeError):
    def __init__(self, trial_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Trial {trial_id} changed from revision {expected} to {actual}."
        )
        self.trial_id = trial_id
        self.expected = expected
        self.actual = actual


class TrialRepository(Protocol):
    def save(self, trial: SliceTrial, *, expected_version: int) -> None: ...

    def get(self, trial_id: str) -> SliceTrial: ...


class InMemoryTrialRepository:
    def __init__(self) -> None:
        self._trials: dict[str, SliceTrial] = {}

    def save(self, trial: SliceTrial, *, expected_version: int) -> None:
        current = self._trials.get(trial.id)
        actual = current.version if current is not None else 0
        if actual != expected_version:
            raise TrialRevisionConflict(trial.id, expected_version, actual)
        if trial.version != expected_version + 1:
            raise ValueError("Trial versions must increase by one.")
        self._trials[trial.id] = trial

    def get(self, trial_id: str) -> SliceTrial:
        try:
            return self._trials[trial_id]
        except KeyError as error:
            raise KeyError(f"Unknown slice trial: {trial_id}") from error


class JsonFileTrialRepository:
    """Compatibility entry point backed by per-root SQLite transactions."""

    def __init__(self, path: Path) -> None:
        self._legacy_path = path
        self._root = logical_root_for(path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def save(self, trial: SliceTrial, *, expected_version: int) -> None:
        if trial.version != expected_version + 1:
            raise ValueError("Trial versions must increase by one.")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                actual = _current_version(connection, trial.id)
                if actual != expected_version:
                    raise TrialRevisionConflict(trial.id, expected_version, actual)
                if actual == 0:
                    connection.execute(
                        "INSERT INTO slice_trials (id, version, payload) "
                        "VALUES (?, ?, ?)",
                        (trial.id, trial.version, _trial_payload(trial)),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE slice_trials SET version = ?, payload = ?
                        WHERE id = ? AND version = ?
                        """,
                        (
                            trial.version,
                            _trial_payload(trial),
                            trial.id,
                            expected_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise TrialRevisionConflict(
                            trial.id,
                            expected_version,
                            _current_version(connection, trial.id),
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def get(self, trial_id: str) -> SliceTrial:
        with closing(connect_database(self._path)) as connection:
            row = connection.execute(
                "SELECT payload FROM slice_trials WHERE id = ?", (trial_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown slice trial: {trial_id}")
        try:
            return _trial_from_data(json.loads(str(row[0])))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Stored trial is invalid.") from error

    def _initialize_or_migrate(self) -> None:
        if _trial_store_ready(self._path):
            _finalize_authority(self._legacy_path)
            return

        legacy_trials: tuple[SliceTrial, ...] = ()
        if self._legacy_path.exists():
            data = read_json(self._legacy_path, default={})
            if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
                data = read_json(_rollback_path(self._legacy_path), default={})
            if data.get("schema_version") != 1 or not isinstance(
                data.get("trials"), list
            ):
                raise RuntimeError("Unsupported trial storage schema.")
            legacy_trials = tuple(
                _legacy_trial_from_data(item) for item in data["trials"]
            )
            _preserve_rollback(self._legacy_path, data)

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _create_schema(connection)
                version = metadata(connection, "trials_schema_version")
                if version not in (None, TRIAL_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported trial storage schema.")
                if version is None:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM slice_trials"
                        ).fetchone()[0]
                    )
                    if count:
                        raise RuntimeError(
                            "Trial records exist without schema authority."
                        )
                    for trial in legacy_trials:
                        connection.execute(
                            "INSERT INTO slice_trials (id, version, payload) "
                            "VALUES (?, ?, ?)",
                            (trial.id, trial.version, _trial_payload(trial)),
                        )
                    set_metadata(
                        connection, "trials_schema_version", TRIAL_SCHEMA_VERSION
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        _finalize_authority(self._legacy_path)


class TrialRecorder:
    def __init__(self, repository: TrialRepository) -> None:
        self._repository = repository

    def start(
        self,
        trial_id: str,
        slice_id: str,
        started_at: datetime,
        *,
        assessed_revision: str = "unversioned",
    ) -> SliceTrial:
        if not trial_id.strip() or not slice_id.strip():
            raise ValueError("A trial must name its id and slice under evaluation.")
        if not assessed_revision.strip():
            raise ValueError("A trial must name the assessed product revision.")
        trial = SliceTrial(
            trial_id.strip(),
            1,
            slice_id.strip(),
            started_at,
            assessed_revision.strip(),
            decisions=(
                TrialDecision(
                    "pending",
                    started_at,
                    "Awaiting complete evidence and an explicit decision.",
                    assessed_revision.strip(),
                ),
            ),
        )
        self._repository.save(trial, expected_version=0)
        return trial

    def inspect(self, trial_id: str) -> TrialReport:
        trial = self._repository.get(trial_id)
        return TrialReport(
            trial_id=trial.id,
            slice_id=trial.slice_id,
            assessed_revision=trial.assessed_revision,
            record_version=trial.version,
            decision=trial.decision,
            total_evidence_count=len(trial.evidence),
            current_evidence_count=len(trial.current_evidence),
            missing_evidence=trial.missing_evidence,
            required_checks_pass=trial.required_checks_pass,
            real_use_is_positive=trial.real_use_is_positive,
            release_accepted=trial.accepted,
        )

    def record(
        self,
        trial_id: str,
        evidence: TrialEvidence,
        *,
        expected_revision: int | None = None,
    ) -> SliceTrial:
        _validate_evidence(evidence)
        trial = self._repository.get(trial_id)
        _check_expected(trial, expected_revision)
        if any(item.id == evidence.id for item in trial.evidence):
            raise ValueError(f"Trial evidence id already exists: {evidence.id}")
        updated = replace(
            trial,
            version=trial.version + 1,
            evidence=trial.evidence + (evidence,),
        )
        self._repository.save(updated, expected_version=trial.version)
        return updated

    def decide(
        self,
        trial_id: str,
        status: Literal["accepted", "rejected"],
        *,
        reason: str,
        decided_at: datetime,
        assessed_revision: str,
        expected_revision: int,
    ) -> SliceTrial:
        if status not in {"accepted", "rejected"}:
            raise ValueError("A trial decision must be accepted or rejected.")
        if not reason.strip():
            raise ValueError("A trial decision requires a reason.")
        trial = self._repository.get(trial_id)
        _check_expected(trial, expected_revision)
        if assessed_revision != trial.assessed_revision:
            raise ValueError("The trial decision does not assess the current revision.")
        if status == "accepted":
            if not trial.evidence_complete:
                raise ValueError("Trial evidence is incomplete.")
            if not trial.required_checks_pass:
                raise ValueError("Required trial checks have not passed.")
            if not trial.real_use_is_positive:
                raise ValueError("Required real-use evidence is not positive.")
        decision = TrialDecision(status, decided_at, reason.strip(), assessed_revision)
        updated = replace(
            trial,
            version=trial.version + 1,
            decisions=trial.decisions + (decision,),
        )
        self._repository.save(updated, expected_version=trial.version)
        return updated

    def reassess_revision(
        self,
        trial_id: str,
        revision: str,
        *,
        reason: str,
        changed_at: datetime,
        expected_revision: int,
    ) -> SliceTrial:
        normalized_revision = revision.strip()
        if not normalized_revision or not reason.strip():
            raise ValueError("A material reassessment requires a revision and reason.")
        trial = self._repository.get(trial_id)
        _check_expected(trial, expected_revision)
        if normalized_revision == trial.assessed_revision:
            return trial
        pending = TrialDecision(
            "pending", changed_at, reason.strip(), normalized_revision
        )
        updated = replace(
            trial,
            version=trial.version + 1,
            assessed_revision=normalized_revision,
            evidence_start_index=len(trial.evidence),
            decisions=trial.decisions + (pending,),
        )
        self._repository.save(updated, expected_version=trial.version)
        return updated


def _check_expected(trial: SliceTrial, expected: int | None) -> None:
    if expected is not None and trial.version != expected:
        raise TrialRevisionConflict(trial.id, expected, trial.version)


def _validate_evidence(evidence: TrialEvidence) -> None:
    if not evidence.id.strip() or not evidence.value.strip():
        raise ValueError("Trial evidence requires an id and value.")
    if evidence.kind not in {"automated_check", "real_use"}:
        raise ValueError("Trial evidence kind is invalid.")
    if evidence.kind == "real_use":
        if evidence.metric is None:
            raise ValueError("Real-use evidence must identify a trial metric.")
        if evidence.check_outcome is not None:
            raise ValueError("Real-use evidence cannot report an automated outcome.")
        if evidence.real_use_outcome not in {"positive", "negative"}:
            raise ValueError("Real-use evidence requires a structured outcome.")
    elif (
        evidence.metric is not None
        or evidence.real_use_outcome is not None
        or evidence.check_outcome not in {
        "passed",
        "failed",
        }
    ):
        raise ValueError("Automated evidence requires a structured check outcome.")


def validate_trial_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, "trials_schema_version")
    has_table = table_exists(connection, "slice_trials")
    if version not in (None, TRIAL_SCHEMA_VERSION):
        raise RuntimeError("Unsupported trial storage schema.")
    if (version is None) != (not has_table):
        raise RuntimeError("Incomplete trial storage schema.")


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS slice_trials (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL CHECK (version > 0),
            payload TEXT NOT NULL
        )
        """
    )


def _trial_store_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            return (
                metadata(connection, "root_schema_version")
                == ROOT_DATABASE_SCHEMA_VERSION
                and metadata(connection, "trials_schema_version")
                == TRIAL_SCHEMA_VERSION
                and table_exists(connection, "slice_trials")
            )
    except sqlite3.Error:
        return False


def inspect_authoritative_trial(path: Path, trial_id: str) -> SliceTrial:
    """Read one accepted-format trial without migrating or changing storage."""
    authority = read_json(path, default={})
    if (
        authority.get("schema_version") != 2
        or authority.get("authority") != "sqlite"
        or authority.get("database") != ROOT_DATABASE_FILENAME
    ):
        raise RuntimeError("Trial storage is not SQLite-authoritative.")
    database = logical_root_for(path) / ROOT_DATABASE_FILENAME
    if not _trial_store_ready(database):
        raise RuntimeError("Trial storage is unavailable or incomplete.")
    with closing(
        sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT payload FROM slice_trials WHERE id = ?", (trial_id,)
        ).fetchone()
    if row is None:
        raise KeyError(f"Unknown slice trial: {trial_id}")
    try:
        return _trial_from_data(json.loads(str(row[0])))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("Stored trial is invalid.") from error


def _current_version(connection: sqlite3.Connection, trial_id: str) -> int:
    row = connection.execute(
        "SELECT version FROM slice_trials WHERE id = ?", (trial_id,)
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _trial_payload(trial: SliceTrial) -> str:
    return json.dumps(
        _trial_to_data(trial), ensure_ascii=False, separators=(",", ":")
    )


def _trial_to_data(trial: SliceTrial) -> dict[str, object]:
    return {
        **asdict(trial),
        "started_at": trial.started_at.isoformat(),
        "evidence": [
            {**asdict(item), "observed_at": item.observed_at.isoformat()}
            for item in trial.evidence
        ],
        "decisions": [
            {**asdict(item), "decided_at": item.decided_at.isoformat()}
            for item in trial.decisions
        ],
    }


def _trial_from_data(data: dict[str, Any]) -> SliceTrial:
    trial = SliceTrial(
        id=str(data["id"]),
        version=int(data["version"]),
        slice_id=str(data["slice_id"]),
        started_at=datetime.fromisoformat(str(data["started_at"])),
        assessed_revision=str(data["assessed_revision"]),
        evidence_start_index=int(data.get("evidence_start_index", 0)),
        evidence=tuple(
            _evidence_from_data(item) for item in data.get("evidence", ())
        ),
        decisions=tuple(
            TrialDecision(
                status=item["status"],
                decided_at=datetime.fromisoformat(str(item["decided_at"])),
                reason=str(item["reason"]),
                assessed_revision=str(item["assessed_revision"]),
            )
            for item in data.get("decisions", ())
        ),
    )
    if not 0 <= trial.evidence_start_index <= len(trial.evidence):
        raise ValueError("Trial evidence boundary is invalid.")
    if (
        not trial.id.strip()
        or trial.version < 1
        or not trial.slice_id.strip()
        or not trial.assessed_revision.strip()
    ):
        raise ValueError("Trial identity or revision is invalid.")
    evidence_ids = [item.id for item in trial.evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("Trial evidence identifiers are not unique.")
    for evidence in trial.evidence:
        _validate_evidence(evidence)
    if any(
        decision.status not in {"pending", "accepted", "rejected"}
        or not decision.reason.strip()
        or not decision.assessed_revision.strip()
        for decision in trial.decisions
    ):
        raise ValueError("Trial decision status is invalid.")
    return trial


def _legacy_trial_from_data(data: dict[str, Any]) -> SliceTrial:
    started_at = datetime.fromisoformat(str(data["started_at"]))
    return SliceTrial(
        id=str(data["id"]),
        version=1,
        slice_id=str(data["slice_id"]),
        started_at=started_at,
        assessed_revision="legacy-unclassified",
        evidence=tuple(
            _evidence_from_data(item, legacy=True)
            for item in data.get("evidence", ())
        ),
        decisions=(
            TrialDecision(
                "pending",
                started_at,
                "Migrated legacy evidence; no historical acceptance was inferred.",
                "legacy-unclassified",
            ),
        ),
    )


def _evidence_from_data(
    data: dict[str, Any], *, legacy: bool = False
) -> TrialEvidence:
    return TrialEvidence(
        id=str(data["id"]),
        kind=data["kind"],
        metric=data.get("metric"),
        value=str(data["value"]),
        observed_at=datetime.fromisoformat(str(data["observed_at"])),
        notes=str(data.get("notes", "")),
        check_outcome=(
            "failed"
            if legacy and data["kind"] == "automated_check"
            else data.get("check_outcome")
        ),
        real_use_outcome=(
            "negative"
            if legacy and data["kind"] == "real_use"
            else data.get("real_use_outcome")
        ),
        required=bool(data.get("required", True)),
    )


def _rollback_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.rollback{path.suffix}")


def _preserve_rollback(path: Path, data: dict[str, Any]) -> None:
    rollback = _rollback_path(path)
    if rollback.exists():
        if read_json(rollback, default={}) != data:
            raise RuntimeError("The protected trial rollback source differs.")
        return
    atomic_write_json(rollback, data)
    rollback.chmod(0o400)


def _write_authority(path: Path) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 2,
            "authority": "sqlite",
            "database": ROOT_DATABASE_FILENAME,
        },
    )
    path.chmod(0o600)


def _finalize_authority(path: Path) -> None:
    if not path.exists():
        _write_authority(path)
        return
    data = read_json(path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        if data.get("database") != ROOT_DATABASE_FILENAME:
            raise RuntimeError("The legacy trial authority points elsewhere.")
        return
    if data.get("schema_version") != 1 or not isinstance(data.get("trials"), list):
        raise RuntimeError("Unsupported trial storage schema.")
    rollback = _rollback_path(path)
    if not rollback.exists() or read_json(rollback, default={}) != data:
        raise RuntimeError("The protected trial rollback source differs.")
    _write_authority(path)
