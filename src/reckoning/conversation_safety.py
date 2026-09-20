"""Protected conversation boundary classification.

Coverage is deliberately explicit. Detection currently supports English
wording only:

* English: direct worth attacks, dependency and exclusivity demands,
  clinical-role claims, quoted discussion, negation, historical statements,
  hypothetical statements, and explicit or ambiguous current self/other
  danger.
* Russian: not detected. Russian fixtures are recorded as explicit
  limitations rather than claims of coverage.
* Chinese: not detected. Chinese fixtures are recorded as explicit
  limitations rather than claims of coverage.
* Mixed language: only the supported English phrases are detected. Any
  non-English wording in the same message is not classified.

The application owns every safety response. Explicit current danger and
ambiguous concern short-circuit before any provider call with fixed,
non-diagnostic text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


COVERAGE_VERSION = "2026-09-21"

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


BOUNDARY_REPLACEMENT = (
    "That request crosses a protected boundary. I can challenge your choice "
    "and reasoning, but not your worth or your relationships. You remain "
    "the authority over what you do."
)

EMERGENCY_RESPONSE = (
    "If you are in immediate danger, contact local emergency services now "
    "or go to the nearest emergency department. If you can, contact a "
    "trusted person nearby and do not stay alone. I can help you focus on "
    "the next practical step, but I am not a clinician or an emergency service."
)

UNCERTAIN_BOUNDARY_RESPONSE = (
    "Before we continue, I want to make sure I understand. Are you in danger "
    "right now? If you are, contact local emergency services or a trusted "
    "person nearby. If this is about the past, a story, or someone else, tell "
    "me and we will go from there. I am not a clinician and I will not guess "
    "about your diagnosis."
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

_SAFE_QUOTATION_CONTEXT = (
    "say",
    "saying",
    "said",
    "quote",
    "quoting",
    "quoted",
    "repeat",
    "repeating",
    "show",
    "showing",
    "example",
    "phrase",
    "not acceptable",
    "unacceptable",
    "discuss",
    "discussion",
    "mention",
    "report",
    "reported",
    "wrote",
    "writes",
    "read",
    "told",
    "call",
    "called",
    "word",
    "refuse",
    "refused",
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
    "not",
    "no",
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
    "if",
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

_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "\u201c": "\u201d",
    "\u2018": "\u2019",
    "\u00ab": "\u00bb",
    "\u300c": "\u300d",
}
_QUOTE_OPENERS = frozenset(_QUOTE_PAIRS)
_QUOTE_CLOSERS = frozenset(_QUOTE_PAIRS.values())

_CLAUSE_BOUNDARY = re.compile(r"[.!?;:\u3002\uff01\uff1f\uff1b\uff1a\n]+|,\s*|\uff0c")


def _boundary_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9a-z]){re.escape(marker)}(?![0-9a-z])")


_MARKER_PATTERNS: dict[tuple[str, ...], tuple[re.Pattern[str], ...]] = {}


def _patterns(markers: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    cached = _MARKER_PATTERNS.get(markers)
    if cached is None:
        cached = tuple(_boundary_pattern(marker) for marker in markers)
        _MARKER_PATTERNS[markers] = cached
    return cached


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(pattern.search(text) is not None for pattern in _patterns(markers))


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


def _has_safe_quotation_context(text: str) -> bool:
    return _has_marker(text, _SAFE_QUOTATION_CONTEXT)


def _quote_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return balanced quotation spans as (open_index, close_index) pairs.

    A straight apostrophe between two word characters is a contraction and is
    never treated as a quotation delimiter. Unbalanced openers are ignored, so
    text that merely starts a quote cannot hide a later clause.
    """
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, str]] = []
    for index, char in enumerate(text):
        if char == "'" and 0 < index < len(text) - 1:
            if text[index - 1].isalnum() and text[index + 1].isalnum():
                continue
        if stack and _QUOTE_PAIRS[stack[-1][1]] == char:
            open_index, _ = stack.pop()
            spans.append((open_index, index))
        elif char in _QUOTE_OPENERS:
            stack.append((index, char))
    return tuple(spans)


def _is_unquoted(
    start: int, length: int, spans: tuple[tuple[int, int], ...]
) -> bool:
    end = start + length
    return not any(
        open_index < start and end <= close_index
        for open_index, close_index in spans
    )


def _without_quoted_spans(text: str) -> str:
    characters = list(text)
    for open_index, close_index in _quote_spans(text):
        for index in range(open_index, close_index + 1):
            characters[index] = " "
    return "".join(characters)


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


def _classify_danger_clause(clause: str) -> DangerDecision:
    normalized = clause.casefold()
    if not _contains_danger_signal(normalized):
        return _danger_decision("none", "none", "no-danger-signal")
    if not _contains_danger_signal(_without_quoted_spans(normalized)):
        return _danger_decision("quotation", "none", "danger-in-quotation")
    if _is_negated(normalized):
        return _danger_decision(
            "ambiguous-concern", "uncertain-boundary", "negated-danger"
        )
    if _is_current(normalized):
        if _danger_target(normalized) == "self":
            return _danger_decision(
                "current-self-danger", "emergency-help", "current-self-danger"
            )
        return _danger_decision(
            "current-other-danger", "emergency-help", "current-other-danger"
        )
    if _is_hypothetical(normalized):
        return _danger_decision(
            "hypothetical", "none", "danger-is-hypothetical"
        )
    if _is_historical(normalized):
        return _danger_decision("historical", "none", "danger-in-past")
    return _danger_decision(
        "ambiguous-concern", "uncertain-boundary", "danger-without-current-marker"
    )


def classify_danger(user_message: str) -> DangerDecision:
    """Classify a user message for credible immediate danger.

    Each clause is evaluated independently so a quoted or historical clause
    cannot hide a current danger clause. Explicit current danger outranks every
    lower-risk classification.
    """
    normalized = user_message.casefold()
    if not _contains_danger_signal(normalized):
        return _danger_decision("none", "none", "no-danger-signal")
    clauses = tuple(
        clause for clause in _CLAUSE_BOUNDARY.split(user_message) if clause.strip()
    )
    decisions = tuple(_classify_danger_clause(clause) for clause in clauses)
    for kind in ("current-self-danger", "current-other-danger"):
        for decision in decisions:
            if decision.kind == kind:
                return decision
    for decision in decisions:
        if decision.kind == "ambiguous-concern":
            return decision
    for decision in decisions:
        if decision.kind in ("quotation", "historical", "hypothetical"):
            return decision
    return _danger_decision("none", "none", "no-danger-signal")


def _find_fragment(text: str, fragment: str, start: int = 0) -> int | None:
    index = text.find(fragment, start)
    return index if index != -1 else None


def classify_output(proposed_response: str) -> OutputPolicyDecision:
    """Classify assistant output against protected conversation boundaries.

    Quotation is parsed as balanced spans rather than character presence, so a
    contraction apostrophe cannot open a quotation. Each prohibited fragment is
    checked against its own span: an unquoted fragment is always replaced, while
    a quoted fragment is allowed only when the surrounding text frames it as
    discussion.
    """
    normalized = proposed_response.casefold()
    spans = _quote_spans(normalized)
    quoted_fragment = False
    for fragment in _PROHIBITED_RESPONSE_FRAGMENTS:
        occurrence = _find_fragment(normalized, fragment)
        while occurrence is not None:
            if _is_unquoted(occurrence, len(fragment), spans):
                return OutputPolicyDecision(
                    action="replace",
                    reason_code="prohibited-boundary-fragment",
                    delivered_speech=BOUNDARY_REPLACEMENT,
                    coverage_version=COVERAGE_VERSION,
                )
            quoted_fragment = True
            occurrence = _find_fragment(normalized, fragment, occurrence + 1)
    if quoted_fragment and not _has_safe_quotation_context(normalized):
        return OutputPolicyDecision(
            action="uncertain",
            reason_code="prohibited-boundary-in-quotation",
            delivered_speech=BOUNDARY_REPLACEMENT,
            coverage_version=COVERAGE_VERSION,
        )
    return OutputPolicyDecision(
        action="allow",
        reason_code="no-prohibited-boundary-signal",
        delivered_speech=proposed_response,
        coverage_version=COVERAGE_VERSION,
    )


def danger_response_for(decision: DangerDecision) -> str | None:
    """Return the canonical application-owned response for a danger decision."""
    if decision.response_kind == "emergency-help":
        return EMERGENCY_RESPONSE
    if decision.response_kind == "uncertain-boundary":
        return UNCERTAIN_BOUNDARY_RESPONSE
    return None
