from __future__ import annotations

from reckoning.decision_conversation import (
    ClarifyDecision,
    ConfirmDecision,
    CorrectDecision,
    DecisionTarget,
    NotDecisionRelated,
    interpret_decision_message,
)


TARGET = DecisionTarget(
    reckoning_id="decision-1",
    version=2,
    conflict="Protect the degree baseline or maximize project momentum.",
    record_ids=("record-1",),
)


def test_unambiguous_confirmation_binds_to_the_displayed_target() -> None:
    for message in (
        "Yes, confirm that exact version",
        "Confirm this version.",
        "Looks right — confirm it",
    ):
        assert interpret_decision_message(message, TARGET) == ConfirmDecision(TARGET)


def test_conversational_correction_extracts_the_new_meaning() -> None:
    reply = interpret_decision_message(
        "No, correct it: Mary intends to protect exam preparation.", TARGET
    )

    assert reply == CorrectDecision(
        TARGET, "record-1", "Mary intends to protect exam preparation."
    )


def test_correction_marker_without_text_asks_for_clarification() -> None:
    reply = interpret_decision_message("No, that's wrong.", TARGET)

    assert isinstance(reply, ClarifyDecision)
    assert "revision 2" in reply.prompt


def test_correction_with_several_records_asks_which_one() -> None:
    target = DecisionTarget("decision-1", 1, "conflict", ("record-1", "record-2"))

    reply = interpret_decision_message("Correct it: new meaning", target)

    assert isinstance(reply, ClarifyDecision)


def test_ambiguous_assent_asks_for_clarification() -> None:
    for message in ("Yes", "ok", "sounds good", "Sure."):
        reply = interpret_decision_message(message, TARGET)
        assert isinstance(reply, ClarifyDecision), message
        assert "confirm" in reply.prompt


def test_negated_confirmation_never_confirms() -> None:
    reply = interpret_decision_message("Don't confirm this yet.", TARGET)

    assert isinstance(reply, ClarifyDecision)


def test_unrelated_conversation_is_left_to_the_responder() -> None:
    for message in (
        "Just thinking out loud.",
        "What should I focus on this week?",
        "Tell me more about the trade-offs.",
    ):
        assert isinstance(interpret_decision_message(message, TARGET), NotDecisionRelated)


def test_no_target_means_no_decision_action() -> None:
    assert isinstance(
        interpret_decision_message("Yes, confirm that exact version", None),
        NotDecisionRelated,
    )
