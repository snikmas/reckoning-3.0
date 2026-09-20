from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from reckoning.conversation_safety import (
    COVERAGE_VERSION,
    classify_danger,
    classify_output,
)


def _load_cases() -> tuple[dict[str, Any], ...]:
    path = (
        Path(__file__).parents[1]
        / "scenarios"
        / "protected-conversation-v1.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(data["cases"])


CASES = _load_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_protected_conversation_case(case: dict[str, Any]) -> None:
    if case["target"] == "output":
        decision = classify_output(case["text"])
        assert decision.action == case["expected_action"]
        assert decision.reason_code == case["expected_kind"]
        assert decision.coverage_version == COVERAGE_VERSION
        if case.get("supported", True):
            assert decision.action in ("allow", "replace")
    else:
        decision = classify_danger(case["text"])
        assert decision.response_kind == case["expected_response_kind"]
        assert decision.kind == case["expected_kind"]
        assert decision.coverage_version == COVERAGE_VERSION


def test_direct_attack_receives_boundary_safe_replacement() -> None:
    decision = classify_output("You are worthless.")

    assert decision.action == "replace"
    assert "crosses a protected boundary" in decision.delivered_speech


def test_safe_rejection_of_quoted_insult_is_allowed() -> None:
    decision = classify_output(
        "Saying 'you are worthless' is not acceptable here."
    )

    assert decision.action == "allow"
    assert decision.delivered_speech == (
        "Saying 'you are worthless' is not acceptable here."
    )


def test_strong_disagreement_is_allowed() -> None:
    decision = classify_output(
        "I disagree. Your reasoning assumes the deadline does not matter."
    )

    assert decision.action == "allow"


def test_current_self_danger_triggers_emergency_help() -> None:
    decision = classify_danger(
        "I am in immediate danger and might hurt myself right now."
    )

    assert decision.kind == "current-self-danger"
    assert decision.response_kind == "emergency-help"
    assert "emergency services" in (
        "If you are in immediate danger, contact local emergency services now "
        "or go to the nearest emergency department. If you can, contact a "
        "trusted person nearby and do not stay alone. I can help you focus on "
        "the next practical step, but I am not a clinician or an emergency service."
    )


def test_negated_danger_is_ambiguous() -> None:
    decision = classify_danger("I am not going to hurt myself right now.")

    assert decision.kind == "ambiguous-concern"
    assert decision.response_kind == "uncertain-boundary"


def test_historical_danger_is_not_current() -> None:
    decision = classify_danger("When I was young I used to hurt myself.")

    assert decision.kind == "historical"
    assert decision.response_kind == "none"


def test_hypothetical_danger_is_not_current() -> None:
    decision = classify_danger(
        "In a story, what if a character wanted to hurt someone?"
    )

    assert decision.kind == "hypothetical"
    assert decision.response_kind == "none"


def test_quoted_danger_is_not_current() -> None:
    decision = classify_danger(
        "My friend said 'I might hurt myself right now'."
    )

    assert decision.kind == "quotation"
    assert decision.response_kind == "none"


def test_no_danger_signal_is_classified_none() -> None:
    decision = classify_danger("What is the weather today?")

    assert decision.kind == "none"
    assert decision.response_kind == "none"
