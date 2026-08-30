from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
    PersonalRecordProposal,
    ReckoningDraft,
    ReckoningProvider,
    SourcedFact,
)
from reckoning.persistence import JsonFileReckoningRepository


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


class UnusedConversationModel:
    def respond(self, request: object) -> str:
        raise AssertionError("This journey does not send a conversation message.")


class FirstDecisionProvider:
    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        return ReckoningDraft(
            conflict="Protect the exam baseline or maximize project momentum.",
            questions=(),
            matters_now=("Protect the exam baseline.",),
            maintained=("Keep one project block.",),
            parked=("Park job applications for one week.",),
            uncertainties=("The result of the next practice exam is still missing.",),
            known=(
                SourcedFact(
                    "The exam is the nearest fixed commitment.",
                    ("message-evidence",),
                ),
            ),
            inferences=(
                Inference(
                    "One project block should preserve momentum.",
                    ("message-evidence",),
                    "The block size may need correction after the exam.",
                ),
            ),
            evidence=(
                Evidence(
                    "message-evidence", "current user message", unstructured_input
                ),
            ),
            next_step="Schedule the first exam preparation block.",
            proposed_records=(
                PersonalRecordProposal(
                    "decision",
                    "Protect the exam baseline and maintain one project block.",
                    ("message-evidence",),
                ),
            ),
        )


class ProviderMustNotRun:
    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        raise AssertionError("Resume must use durable state, not invent a new reckoning.")


class SequenceIdentifiers:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new(self) -> str:
        return next(self._values)


def build_application(
    path: Path,
    instant: datetime,
    provider: ReckoningProvider,
    identifiers: SequenceIdentifiers,
) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(instant),
            model=UnusedConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=provider,
            reckoning_repository=JsonFileReckoningRepository(path),
            identifiers=identifiers,
        )
    )


def test_confirmed_decision_resumes_after_restart_and_keeps_outcome(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "continuity.json"
    decided_at = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
    first_application = build_application(
        state_path,
        decided_at,
        FirstDecisionProvider(),
        SequenceIdentifiers("reckoning-1", "record-1"),
    )
    reckoning = first_application.start_reckoning(
        "The exam is fixed, but I do not want the project to disappear."
    )
    first_application.confirm_reckoning(reckoning.id)

    restarted = build_application(
        state_path,
        datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc),
        ProviderMustNotRun(),
        SequenceIdentifiers("check-in-1"),
    )
    resumed = restarted.resume_decision(reckoning.id)

    assert resumed.decision.status == "confirmed"
    assert resumed.decision.id == "reckoning-1"
    assert resumed.reasons == (
        "Protect the exam baseline or maximize project momentum.",
        "Protect the exam baseline.",
        "Keep one project block.",
        "Park job applications for one week.",
        "Schedule the first exam preparation block.",
    )
    assert resumed.stored_facts == (
        "The exam is the nearest fixed commitment.",
        "Protect the exam baseline and maintain one project block.",
    )
    assert resumed.missing_information == (
        "The result of the next practice exam is still missing.",
    )
    assert resumed.check_ins == ()

    check_in = restarted.record_check_in(
        reckoning.id,
        "I completed four exam blocks and one project block; the exam score rose.",
    )

    assert check_in.id == "check-in-1"
    assert check_in.decision_id == "reckoning-1"
    assert check_in.occurred_at == datetime(
        2026, 9, 6, 15, 0, tzinfo=timezone.utc
    )
    assert check_in.supporting_evidence_ids == ("message-evidence",)

    restarted_again = build_application(
        state_path,
        datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc),
        ProviderMustNotRun(),
        SequenceIdentifiers(),
    )
    resumed_again = restarted_again.resume_decision(reckoning.id)

    assert resumed_again.check_ins == (check_in,)
