from __future__ import annotations

from datetime import datetime, timezone

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.continuity import (
    Evidence,
    Inference,
    MaterialQuestion,
    ReckoningDraft,
    SourcedFact,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc)


class UnusedConversationModel:
    def respond(self, request: object) -> str:
        raise AssertionError("Creating a reckoning should use the reckoning provider.")


class ScriptedReckoningProvider:
    def __init__(self, draft: ReckoningDraft) -> None:
        self.draft = draft

    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        assert unstructured_input == MESSY_INPUT
        return self.draft


class FixedIdentifiers:
    def new(self) -> str:
        return "reckoning-1"


MESSY_INPUT = (
    "I have exams, this project, and a job search. The project feels important, "
    "but I cannot let university collapse, and I keep treating all three as urgent."
)


def valid_draft() -> ReckoningDraft:
    return ReckoningDraft(
        conflict=(
            "You are choosing between protecting the degree baseline and giving "
            "one career-building project enough concentration to become evidence."
        ),
        questions=(
            MaterialQuestion(
                text="Which exam is within the next fourteen days?",
                effect_on_recommendation=(
                    "A near exam would move university preparation into primary focus."
                ),
            ),
            MaterialQuestion(
                text="Does the project have a real external deadline?",
                effect_on_recommendation=(
                    "A real deadline would change whether the project can be parked."
                ),
            ),
        ),
        matters_now=("Protect the nearest exam requirement.",),
        maintained=("Keep one small weekly project block.",),
        parked=("Pause broad job applications until the exam date is known.",),
        uncertainties=("The exam dates and current preparation level are unknown.",),
        known=(
            SourcedFact(
                text="University, the project, and job search are competing for time.",
                evidence_ids=("evidence-input",),
            ),
        ),
        inferences=(
            Inference(
                text="Treating every concern as urgent is preventing concentration.",
                evidence_ids=("evidence-input",),
                uncertainty="Likely from the wording, but not yet confirmed.",
            ),
        ),
        evidence=(
            Evidence(
                id="evidence-input",
                source="current user message",
                content=MESSY_INPUT,
            ),
        ),
        next_step="List the exam dates and mark the nearest one.",
    )


def build_application(draft: ReckoningDraft) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=UnusedConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=ScriptedReckoningProvider(draft),
            identifiers=FixedIdentifiers(),
        )
    )


def test_messy_input_becomes_a_bounded_first_reckoning() -> None:
    application = build_application(valid_draft())

    reckoning = application.start_reckoning(MESSY_INPUT)

    assert reckoning.id == "reckoning-1"
    assert reckoning.status == "proposed"
    assert reckoning.version == 1
    assert reckoning.draft.conflict.startswith("You are choosing between")
    assert len(reckoning.draft.questions) == 2
    assert all(
        question.effect_on_recommendation
        for question in reckoning.draft.questions
    )
    assert reckoning.draft.matters_now == (
        "Protect the nearest exam requirement.",
    )
    assert reckoning.draft.maintained == ("Keep one small weekly project block.",)
    assert reckoning.draft.parked == (
        "Pause broad job applications until the exam date is known.",
    )
    assert reckoning.draft.uncertainties == (
        "The exam dates and current preparation level are unknown.",
    )
    assert reckoning.draft.known[0].text.startswith("University")
    assert reckoning.draft.inferences[0].uncertainty.startswith("Likely")
    assert reckoning.draft.next_step == "List the exam dates and mark the nearest one."


def test_first_reckoning_rejects_more_than_three_initial_questions() -> None:
    draft = valid_draft()
    invalid_draft = ReckoningDraft(
        **{
            **draft.__dict__,
            "questions": draft.questions
            + (
                MaterialQuestion("Question three?", "It changes option three."),
                MaterialQuestion("Question four?", "It changes option four."),
            ),
        }
    )
    application = build_application(invalid_draft)

    with pytest.raises(RuntimeError, match="more than three"):
        application.start_reckoning(MESSY_INPUT)
