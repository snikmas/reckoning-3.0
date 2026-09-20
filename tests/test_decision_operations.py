from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
    PersonalRecordProposal,
    ReckoningDraft,
    ReckoningOperationConflict,
    SourcedFact,
)
from reckoning.persistence import JsonFileReckoningRepository
from reckoning.providers import ProviderFailure


NOW = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class UnusedConversationModel:
    def respond(self, request: object) -> str:
        raise AssertionError("Decision operations do not use the conversation model.")


class DecisionProvider:
    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        evidence = Evidence(
            f"evidence:{unstructured_input}", "current user message", unstructured_input
        )
        return ReckoningDraft(
            conflict=f"Conflict for {unstructured_input}",
            questions=(),
            matters_now=("Protect the fixed commitment.",),
            maintained=(),
            parked=(),
            uncertainties=("Outcome not known yet.",),
            known=(SourcedFact(unstructured_input, (evidence.id,)),),
            inferences=(),
            evidence=(evidence,),
            next_step="Choose the next block.",
            proposed_records=(
                PersonalRecordProposal(
                    "decision", f"Decision for {unstructured_input}", (evidence.id,)
                ),
            ),
        )


class FailingProvider:
    def reckon(self, unstructured_input: str) -> object:
        raise ProviderFailure(
            provider="fake",
            model="deterministic-fake",
            model_calls=1,
            latency_ms=0,
            retries=0,
            message="Simulated provider failure.",
        )


class SequenceIdentifiers:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 0

    def new(self) -> str:
        self.next_value += 1
        return f"{self.prefix}-{self.next_value}"


def build_application(
    state_path: Path, prefix: str, *, reckoning_provider: object | None = None
) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=UnusedConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=reckoning_provider or DecisionProvider(),  # type: ignore[arg-type]
            reckoning_repository=JsonFileReckoningRepository(state_path),
            identifiers=SequenceIdentifiers(prefix),
        )
    )


def test_replay_after_restart_returns_the_same_decision(tmp_path: Path) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    decision = first.start_reckoning("Competing commitments.", operation_id="op-1")

    restarted = build_application(state_path, "second")
    replayed = restarted.start_reckoning("Competing commitments.", operation_id="op-1")

    assert replayed.id == decision.id
    assert [item.id for item in restarted.list_reckonings()] == [decision.id]


def test_replay_with_a_different_payload_conflicts_even_after_restart(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    first.start_reckoning("Competing commitments.", operation_id="op-1")

    restarted = build_application(state_path, "second")
    with pytest.raises(ReckoningOperationConflict):
        restarted.start_reckoning("A different situation.", operation_id="op-1")


def test_failed_proposal_input_survives_restart_and_retry_completes_once(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    failing = build_application(
        state_path, "first", reckoning_provider=FailingProvider()
    )
    with pytest.raises(RuntimeError, match="reckoning run failed"):
        failing.start_reckoning("Competing commitments.", operation_id="op-2")

    restarted = build_application(state_path, "second")
    pending = restarted.pending_decision_inputs()
    assert [operation.pending_input for operation in pending] == [
        "Competing commitments."
    ]
    assert [operation.status for operation in pending] == ["failed"]

    decision = restarted.start_reckoning("Competing commitments.", operation_id="op-2")
    assert restarted.pending_decision_inputs() == ()

    replayed = restarted.start_reckoning("Competing commitments.", operation_id="op-2")
    assert replayed.id == decision.id
    assert [item.id for item in restarted.list_reckonings()] == [decision.id]


def test_confirmation_replay_after_restart_does_not_duplicate(tmp_path: Path) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    decision = first.start_reckoning("Competing commitments.", operation_id="op-3")
    confirmed = first.confirm_reckoning(
        decision.id, expected_revision=decision.version, operation_id="op-4"
    )

    restarted = build_application(state_path, "second")
    replayed = restarted.confirm_reckoning(
        decision.id, expected_revision=decision.version, operation_id="op-4"
    )

    assert replayed.version == confirmed.version
    assert restarted.inspect_reckoning(decision.id).version == confirmed.version


def test_correction_replay_after_restart_does_not_duplicate(tmp_path: Path) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    decision = first.start_reckoning("Competing commitments.")
    record_id = decision.current_records[0].record_id
    corrected = first.correct_personal_record(
        decision.id,
        record_id,
        "Protect exam preparation.",
        expected_revision=decision.version,
        operation_id="op-5",
    )

    restarted = build_application(state_path, "second")
    replayed = restarted.correct_personal_record(
        decision.id,
        record_id,
        "Protect exam preparation.",
        expected_revision=decision.version,
        operation_id="op-5",
    )

    assert replayed.version == corrected.version
    assert restarted.inspect_reckoning(decision.id).version == corrected.version


def test_confirmation_replay_with_changed_expected_revision_conflicts(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    decision = first.start_reckoning("Competing commitments.", operation_id="op-7")
    first.confirm_reckoning(
        decision.id, expected_revision=decision.version, operation_id="op-8"
    )

    restarted = build_application(state_path, "second")
    with pytest.raises(ReckoningOperationConflict):
        restarted.confirm_reckoning(
            decision.id,
            expected_revision=decision.version + 1,
            operation_id="op-8",
        )


def test_correction_replay_with_changed_expected_revision_conflicts(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    decision = first.start_reckoning("Competing commitments.")
    record_id = decision.current_records[0].record_id
    first.correct_personal_record(
        decision.id,
        record_id,
        "Protect exam preparation.",
        expected_revision=decision.version,
        operation_id="op-9",
    )

    restarted = build_application(state_path, "second")
    with pytest.raises(ReckoningOperationConflict):
        restarted.correct_personal_record(
            decision.id,
            record_id,
            "Protect exam preparation.",
            expected_revision=decision.version + 1,
            operation_id="op-9",
        )


def test_failed_operation_rejects_a_different_payload_on_retry(tmp_path: Path) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    failing = build_application(
        state_path, "first", reckoning_provider=FailingProvider()
    )
    with pytest.raises(RuntimeError, match="reckoning run failed"):
        failing.start_reckoning("Competing commitments.", operation_id="op-6")

    restarted = build_application(state_path, "second")
    with pytest.raises(ReckoningOperationConflict):
        restarted.start_reckoning("Edited situation text.", operation_id="op-6")


def test_concurrent_identical_confirmations_produce_one_domain_effect(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    decision = first.start_reckoning("Competing commitments.")

    second = build_application(state_path, "second")

    confirmed_first = first.confirm_reckoning(
        decision.id, expected_revision=decision.version, operation_id="op-concurrent"
    )
    confirmed_second = second.confirm_reckoning(
        decision.id, expected_revision=decision.version, operation_id="op-concurrent"
    )

    assert confirmed_first.version == confirmed_second.version
    assert first.inspect_reckoning(decision.id).version == confirmed_first.version
    assert second.inspect_reckoning(decision.id).version == confirmed_first.version
