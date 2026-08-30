from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from reckoning.json_store import atomic_write_json, read_json

EvidenceKind = Literal["automated_check", "real_use"]
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


@dataclass(frozen=True)
class TrialEvidence:
    id: str
    kind: EvidenceKind
    metric: TrialMetric | None
    value: str
    observed_at: datetime
    notes: str = ""


@dataclass(frozen=True)
class SliceTrial:
    id: str
    slice_id: str
    started_at: datetime
    evidence: tuple[TrialEvidence, ...] = ()

    @property
    def missing_evidence(self) -> tuple[str, ...]:
        recorded_real_use = {
            item.metric
            for item in self.evidence
            if item.kind == "real_use" and item.metric is not None
        }
        missing = [
            metric for metric in REQUIRED_REAL_USE_METRICS
            if metric not in recorded_real_use
        ]
        if not any(item.kind == "automated_check" for item in self.evidence):
            missing.append("automated_check")
        return tuple(missing)

    @property
    def accepted(self) -> bool:
        return not self.missing_evidence


class TrialRepository(Protocol):
    def save(self, trial: SliceTrial) -> None: ...

    def get(self, trial_id: str) -> SliceTrial: ...


class InMemoryTrialRepository:
    def __init__(self) -> None:
        self._trials: dict[str, SliceTrial] = {}

    def save(self, trial: SliceTrial) -> None:
        self._trials[trial.id] = trial

    def get(self, trial_id: str) -> SliceTrial:
        try:
            return self._trials[trial_id]
        except KeyError as error:
            raise KeyError(f"Unknown slice trial: {trial_id}") from error


class JsonFileTrialRepository(InMemoryTrialRepository):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        data = read_json(path, default={"schema_version": 1, "trials": []})
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported trial storage schema.")
        self._trials = {
            str(item["id"]): _trial_from_data(item) for item in data["trials"]
        }

    def save(self, trial: SliceTrial) -> None:
        super().save(trial)
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "trials": [
                    _trial_to_data(item) for item in self._trials.values()
                ],
            },
        )


class TrialRecorder:
    def __init__(self, repository: TrialRepository) -> None:
        self._repository = repository

    def start(self, trial_id: str, slice_id: str, started_at: datetime) -> SliceTrial:
        if not slice_id.strip():
            raise ValueError("A trial must name the slice under evaluation.")
        trial = SliceTrial(trial_id, slice_id.strip(), started_at)
        self._repository.save(trial)
        return trial

    def record(self, trial_id: str, evidence: TrialEvidence) -> SliceTrial:
        if not evidence.value.strip():
            raise ValueError("Trial evidence cannot be empty.")
        if evidence.kind == "real_use" and evidence.metric is None:
            raise ValueError("Real-use evidence must identify a trial metric.")
        trial = self._repository.get(trial_id)
        updated = replace(trial, evidence=trial.evidence + (evidence,))
        self._repository.save(updated)
        return updated


def _trial_to_data(trial: SliceTrial) -> dict[str, object]:
    data = asdict(trial)
    data["started_at"] = trial.started_at.isoformat()
    data["evidence"] = [
        {
            **asdict(item),
            "observed_at": item.observed_at.isoformat(),
        }
        for item in trial.evidence
    ]
    return data


def _trial_from_data(data: dict[str, object]) -> SliceTrial:
    evidence_data = data.get("evidence", [])
    if not isinstance(evidence_data, list):
        raise RuntimeError("Stored trial evidence is invalid.")
    return SliceTrial(
        id=str(data["id"]),
        slice_id=str(data["slice_id"]),
        started_at=datetime.fromisoformat(str(data["started_at"])),
        evidence=tuple(
            TrialEvidence(
                id=str(item["id"]),
                kind=item["kind"],
                metric=item.get("metric"),
                value=str(item["value"]),
                observed_at=datetime.fromisoformat(str(item["observed_at"])),
                notes=str(item.get("notes", "")),
            )
            for item in evidence_data
        ),
    )
