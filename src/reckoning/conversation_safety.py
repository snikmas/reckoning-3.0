from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


COVERAGE_VERSION = "2026-09-20"

_OutputAction = Literal["allow", "replace", "uncertain"]
_DangerKind = Literal[
    "none",
    "current-self-danger",
    "current-other-danger",
    "ambiguous-concern",
    "quotation",
    "historical",
    "hypothetical",
    "uncertain",
]
_ResponseKind = Literal["none", "emergency-help", "safe-replacement", "uncertain-boundary"]


@dataclass(frozen=True)
class OutputPolicyDecision:
    action: _OutputAction
    reason_code: str
    delivered_speech: str
    coverage_version: str


@dataclass(frozen=True)
class DangerDecision:
    kind: _DangerKind
    response_kind: _ResponseKind
    reason_code: str
    coverage_version: str


_BOUNDARY_REPLACEMENT = (
    "That request crosses a protected boundary. I can challenge your choice "
    "and reasoning, but not your worth or your relationships. You remain "
    "the authority over what you do."
)

_EMERGENCY_RESPONSE = (
    "If you are in immediate danger, contact local emergency services now "
    "or go to the nearest emergency department. If you can, contact a "
    "trusted person nearby and do not stay alone. I can help you focus on "
    "the next practical step, but I am not a clinician or an emergency service."
)

_PROHIBITED_RESPONSE_FRAGMENTS = (
    "you are worthless",
    "you're worthless",
    "you are a failure as a person",
    "do not speak to anyone else",
    "don't speak to anyone else",
    "do not need anyone else",
    "don't need anyone else",
    "you only need me",
    "never leave me",
    "as your therapist",
    "your diagnosis is",
)

_DANGER_SELF_PHRASES = (
    "hurt myself",
    "kill myself",
)

_DANGER_OTHER_PHRASES = (
    "hurt someone",
    "someone might hurt me",
    "someone is threatening me",
)

_CURRENT_MARKERS = (
    "right now",
    "immediate danger",
    "now",
    "tonight",
    "today",
    "currently",
    "immediately",
)

_NEGATION_MARKERS = (
    "not ",
    "no ",
    "never",
    "don't",
    "doesn't",
    "didn't",
    "wasn't",
    "weren't",
    "haven't",
    "hasn't",
    "cannot",
    "can't",
    "wouldn't",
    "shouldn't",
    "couldn't",
)

_HYPOTHETICAL_MARKERS = (
    "if ",
    "suppose",
    "hypothetical",
    "in a story",
    "as a character",
    "imagine",
    "what if",
)

_HISTORICAL_MARKERS = (
    "used to",
    "when i was",
    "in the past",
    "last year",
    "yesterday",
    "ago",
    "last night",
    "last week",
)

_QUOTE_CHARS = frozenset({'"', "'", "«", "»", "「", "」"})


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _contains_danger_signal(text: str) -> bool:
    return _has_marker(text, _DANGER_SELF_PHRASES + _DANGER_OTHER_PHRASES)


def _is_current(text: str) -> bool:
    return _has_marker(text, _CURRENT_MARKERS)


def _is_negated(text: str) -> bool:
    return _has_marker(text, _NEGATION_MARKERS)


def _is_hypothetical(text: str) -> bool:
    return _has_marker(text, _HYPOTHETICAL_MARKERS)


def _is_historical(text: str) -> bool:
    return _has_marker(text, _HISTORICAL_MARKERS)


def _is_quotation(text: str) -> bool:
    return any(char in text for char in _QUOTE_CHARS)


def _danger_target(text: str) -> Literal["self", "other"]:
    if _has_marker(text, _DANGER_SELF_PHRASES):
        return "self"
    return "other"


def _danger_decision(
    kind: _DangerKind, response_kind: _ResponseKind, reason_code: str
) -> DangerDecision:
    return DangerDecision(
        kind=kind,
        response_kind=response_kind,
        reason_code=reason_code,
        coverage_version=COVERAGE_VERSION,
    )


def classify_danger(user_message: str) -> DangerDecision:
    """Classify a user message for credible immediate danger."""
    normalized = user_message.casefold()
    if not _contains_danger_signal(normalized):
        return _danger_decision("none", "none", "no-danger-signal")
    if _is_quotation(normalized):
        return _danger_decision("quotation", "none", "danger-in-quotation")
    if _is_historical(normalized):
        return _danger_decision("historical", "none", "danger-in-past")
    if _is_hypothetical(normalized):
        return _danger_decision("hypothetical", "none", "danger-is-hypothetical")
    if _is_negated(normalized):
        return _danger_decision(
            "ambiguous-concern", "uncertain-boundary", "negated-danger"
        )
    target = _danger_target(normalized)
    if _is_current(normalized):
        if target == "self":
            return _danger_decision(
                "current-self-danger", "emergency-help", "current-self-danger"
            )
        return _danger_decision(
            "current-other-danger", "emergency-help", "current-other-danger"
        )
    return _danger_decision(
        "ambiguous-concern", "uncertain-boundary", "danger-without-current-marker"
    )


def _has_unquoted_fragment(text: str, fragment: str) -> bool:
    in_quote = False
    for index, char in enumerate(text):
        if char in _QUOTE_CHARS:
            in_quote = not in_quote
        if text.startswith(fragment, index) and not in_quote:
            return True
    return False


def classify_output(proposed_response: str) -> OutputPolicyDecision:
    """Classify assistant output against protected conversation boundaries."""
    normalized = proposed_response.casefold()
    for fragment in _PROHIBITED_RESPONSE_FRAGMENTS:
        if _has_unquoted_fragment(normalized, fragment):
            return OutputPolicyDecision(
                action="replace",
                reason_code="prohibited-boundary-fragment",
                delivered_speech=_BOUNDARY_REPLACEMENT,
                coverage_version=COVERAGE_VERSION,
            )
    return OutputPolicyDecision(
        action="allow",
        reason_code="no-prohibited-boundary-signal",
        delivered_speech=proposed_response,
        coverage_version=COVERAGE_VERSION,
    )
