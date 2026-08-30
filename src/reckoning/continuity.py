from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4

ReckoningStatus = Literal["proposed", "confirmed"]


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


@dataclass(frozen=True)
class Reckoning:
    id: str
    version: int
    status: ReckoningStatus
    created_at: datetime
    source_input: str
    draft: ReckoningDraft


class ReckoningProvider(Protocol):
    def reckon(self, unstructured_input: str) -> ReckoningDraft: ...


class ReckoningRepository(Protocol):
    def save(self, reckoning: Reckoning) -> None: ...

    def get(self, reckoning_id: str) -> Reckoning: ...


class IdentifierFactory(Protocol):
    def new(self) -> str: ...


class UuidIdentifierFactory:
    def new(self) -> str:
        return str(uuid4())


class InMemoryReckoningRepository:
    def __init__(self) -> None:
        self._reckonings: dict[str, Reckoning] = {}

    def save(self, reckoning: Reckoning) -> None:
        self._reckonings[reckoning.id] = reckoning

    def get(self, reckoning_id: str) -> Reckoning:
        try:
            return self._reckonings[reckoning_id]
        except KeyError as error:
            raise KeyError(f"Unknown reckoning: {reckoning_id}") from error


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
