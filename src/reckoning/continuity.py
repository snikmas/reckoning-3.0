from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4

ReckoningStatus = Literal["proposed", "confirmed"]
PersonalRecordStatus = Literal["proposed", "confirmed"]
PersonalRecordType = Literal[
    "profile_fact",
    "current_state",
    "direction",
    "goal",
    "decision",
    "preference",
    "boundary",
]


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    content: str


@dataclass(frozen=True)
class SourcedFact:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Inference:
    text: str
    evidence_ids: tuple[str, ...]
    uncertainty: str


@dataclass(frozen=True)
class MaterialQuestion:
    text: str
    effect_on_recommendation: str


@dataclass(frozen=True)
class PersonalRecordProposal:
    record_type: PersonalRecordType
    meaning: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class PersonalRecordVersion:
    record_id: str
    version: int
    status: PersonalRecordStatus
    record_type: PersonalRecordType
    meaning: str
    evidence_ids: tuple[str, ...]
    created_at: datetime
    supersedes_version: int | None = None


@dataclass(frozen=True)
class ReckoningDraft:
    conflict: str
    questions: tuple[MaterialQuestion, ...]
    matters_now: tuple[str, ...]
    maintained: tuple[str, ...]
    parked: tuple[str, ...]
    uncertainties: tuple[str, ...]
    known: tuple[SourcedFact, ...]
    inferences: tuple[Inference, ...]
    evidence: tuple[Evidence, ...]
    next_step: str
    proposed_records: tuple[PersonalRecordProposal, ...] = ()

    def validate(self) -> None:
        if len(self.questions) > 3:
            raise RuntimeError("A first reckoning cannot ask more than three questions.")
        if any(
            not question.text.strip()
            or not question.effect_on_recommendation.strip()
            for question in self.questions
        ):
            raise RuntimeError(
                "Every question must state how it could change the recommendation."
            )
        if not self.conflict.strip():
            raise RuntimeError("A first reckoning must identify the conflict.")
        if not self.next_step.strip():
            raise RuntimeError("A first reckoning must end with a next step.")
        evidence_ids = {evidence.id for evidence in self.evidence}
        if any(
            not proposal.meaning.strip()
            or not proposal.evidence_ids
            or not set(proposal.evidence_ids).issubset(evidence_ids)
            for proposal in self.proposed_records
        ):
            raise RuntimeError(
                "Every proposed personal record must name its supporting evidence."
            )


@dataclass(frozen=True)
class ReckoningProviderResult:
    draft: ReckoningDraft
    provider: str
    model: str
    model_calls: int
    latency_ms: int
    retries: int
    input_tokens: int
    output_tokens: int
    billable_units: int


@dataclass(frozen=True)
class Reckoning:
    id: str
    version: int
    status: ReckoningStatus
    created_at: datetime
    source_input: str
    draft: ReckoningDraft
    record_versions: tuple[PersonalRecordVersion, ...] = ()

    @property
    def current_records(self) -> tuple[PersonalRecordVersion, ...]:
        current_by_id: dict[str, PersonalRecordVersion] = {}
        for record in self.record_versions:
            current_by_id[record.record_id] = record
        return tuple(current_by_id.values())


@dataclass(frozen=True)
class WhyView:
    reckoning_id: str
    evidence: tuple[Evidence, ...]
    record_versions: tuple[PersonalRecordVersion, ...]


@dataclass(frozen=True)
class CheckIn:
    id: str
    decision_id: str
    occurred_at: datetime
    outcome: str
    supporting_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionResume:
    decision: Reckoning
    reasons: tuple[str, ...]
    stored_facts: tuple[str, ...]
    missing_information: tuple[str, ...]
    check_ins: tuple[CheckIn, ...]


class ReckoningRevisionConflict(RuntimeError):
    def __init__(self, reckoning_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Reckoning {reckoning_id} changed from revision "
            f"{expected} to {actual}."
        )
        self.reckoning_id = reckoning_id
        self.expected = expected
        self.actual = actual


class ReckoningProvider(Protocol):
    def reckon(
        self, unstructured_input: str
    ) -> ReckoningDraft | ReckoningProviderResult: ...


class ReckoningRepository(Protocol):
    def save(self, reckoning: Reckoning, *, expected_version: int) -> None: ...

    def get(self, reckoning_id: str) -> Reckoning: ...

    def save_check_in(self, check_in: CheckIn) -> None: ...

    def list_check_ins(self, decision_id: str) -> tuple[CheckIn, ...]: ...


class IdentifierFactory(Protocol):
    def new(self) -> str: ...


class UuidIdentifierFactory:
    def new(self) -> str:
        return str(uuid4())


class InMemoryReckoningRepository:
    def __init__(self) -> None:
        self._reckonings: dict[str, Reckoning] = {}
        self._check_ins: dict[str, CheckIn] = {}

    def save(self, reckoning: Reckoning, *, expected_version: int) -> None:
        actual = self._reckonings.get(reckoning.id)
        actual_version = actual.version if actual is not None else 0
        if actual_version != expected_version:
            raise ReckoningRevisionConflict(
                reckoning.id, expected_version, actual_version
            )
        if reckoning.version != expected_version + 1:
            raise ValueError("Reckoning versions must increase by one.")
        self._reckonings[reckoning.id] = reckoning

    def get(self, reckoning_id: str) -> Reckoning:
        try:
            return self._reckonings[reckoning_id]
        except KeyError as error:
            raise KeyError(f"Unknown reckoning: {reckoning_id}") from error

    def save_check_in(self, check_in: CheckIn) -> None:
        self._check_ins[check_in.id] = check_in

    def list_check_ins(self, decision_id: str) -> tuple[CheckIn, ...]:
        return tuple(
            check_in
            for check_in in self._check_ins.values()
            if check_in.decision_id == decision_id
        )


class DeterministicFakeReckoningProvider:
    """A stable provider for local development before a cloud model is configured."""

    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        evidence = Evidence(
            id="current-message",
            source="current user message",
            content=unstructured_input,
        )
        return ReckoningDraft(
            conflict=(
                "You need to choose one primary concern without abandoning the "
                "minimum maintenance that protects the others."
            ),
            questions=(),
            matters_now=("Choose the concern with the nearest real consequence.",),
            maintained=("Keep the smallest necessary maintenance for other concerns.",),
            parked=("Park work that has no current consequence or commitment.",),
            uncertainties=(
                "The deadlines, consequences, and available time are not yet known.",
            ),
            known=(
                SourcedFact(
                    text="The user described competing concerns.",
                    evidence_ids=(evidence.id,),
                ),
            ),
            inferences=(
                Inference(
                    text="The concerns may be competing for the same limited capacity.",
                    evidence_ids=(evidence.id,),
                    uncertainty="This is an inference until the user confirms it.",
                ),
            ),
            evidence=(evidence,),
            next_step="Name the nearest irreversible consequence among the concerns.",
        )
