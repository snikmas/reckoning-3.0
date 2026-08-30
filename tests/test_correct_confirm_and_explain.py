from __future__ import annotations

from datetime import datetime, timezone

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.continuity import (
    Evidence,
    InMemoryReckoningRepository,
    Inference,
    PersonalRecordProposal,
    ReckoningDraft,
    SourcedFact,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


class UnusedConversationModel:
    def respond(self, request: object) -> str:
        raise AssertionError("This journey does not send a conversation message.")


class ScriptedReckoningProvider:
    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        return ReckoningDraft(
            conflict="Protect the degree baseline or maximize project momentum.",
            questions=(),
            matters_now=("Protect the nearest exam.",),
            maintained=("Keep one project block.",),
            parked=("Park broad job applications.",),
            uncertainties=("The exact exam date is missing.",),
            known=(
                SourcedFact(
                    "Three commitments compete for time.", ("message-evidence",)
                ),
            ),
            inferences=(
                Inference(
                    "University is probably the primary commitment.",
                    ("message-evidence",),
                    "The user has not confirmed that meaning.",
                ),
            ),
            evidence=(
                Evidence(
                    "message-evidence", "current user message", unstructured_input
                ),
            ),
            next_step="Confirm what protecting university means this week.",
            proposed_records=(
                PersonalRecordProposal(
                    record_type="current_state",
                    meaning="Mary has chosen university over the project.",
                    evidence_ids=("message-evidence",),
                ),
            ),
        )


class SequenceIdentifiers:
    def __init__(self) -> None:
        self._values = iter(("reckoning-1", "record-1", "correction-evidence-1"))

    def new(self) -> str:
        return next(self._values)


def build_application() -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=UnusedConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=ScriptedReckoningProvider(),
            reckoning_repository=InMemoryReckoningRepository(),
            identifiers=SequenceIdentifiers(),
        )
    )


def test_user_correction_stays_proposed_until_explicit_confirmation() -> None:
    application = build_application()
    first = application.start_reckoning("Exams, project, and job search all compete.")
    proposed = first.current_records[0]

    assert proposed.status == "proposed"
    assert proposed.version == 1

    corrected = application.correct_personal_record(
        first.id,
        proposed.record_id,
        "Mary intends to protect exam preparation while maintaining one project block.",
    )

    current = corrected.current_records[0]
    assert current.status == "proposed"
    assert current.version == 2
    assert current.meaning.startswith("Mary intends to protect exam preparation")
    assert corrected.draft.conflict == first.draft.conflict
    assert corrected.draft.next_step == first.draft.next_step
    assert [version.version for version in corrected.record_versions] == [1, 2]

    confirmed = application.confirm_reckoning(first.id)

    assert confirmed.status == "confirmed"
    assert confirmed.current_records[0].status == "confirmed"
    assert confirmed.current_records[0].version == 3
    assert confirmed.current_records[0].meaning == current.meaning


def test_why_view_names_exact_evidence_and_active_record_versions() -> None:
    application = build_application()
    first = application.start_reckoning("Exams, project, and job search all compete.")
    application.correct_personal_record(
        first.id,
        first.current_records[0].record_id,
        "Mary intends to protect exam preparation while maintaining one project block.",
    )
    application.confirm_reckoning(first.id)

    why = application.explain_reckoning(first.id)

    assert [(record.record_id, record.version) for record in why.record_versions] == [
        ("record-1", 3)
    ]
    assert {evidence.id for evidence in why.evidence} == {
        "message-evidence",
        "correction-evidence-1",
    }
    correction = next(
        evidence
        for evidence in why.evidence
        if evidence.id == "correction-evidence-1"
    )
    assert correction.source == "direct user correction"
    assert correction.content.startswith("Mary intends to protect exam preparation")
