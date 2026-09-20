"""Deterministic interpretation of conversation replies about one decision.

Consent never comes from model confidence: a decision is confirmed or
corrected only when the user's message unambiguously says so, and always
against the proposal actually displayed (its current revision).

Intent is parsed structurally rather than by scanning for marker substrings:

* Confirmation is the whole message, after case and trailing punctuation
  normalization, matching one documented phrase.
* Correction requires an imperative correction verb as the leading word
  (optionally after a leading interjection such as "no" or "actually"). A
  negated, conditional, quoted, or reported verb therefore never starts the
  message, so it never mutates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionTarget:
    """The single proposed decision a conversation reply can refer to."""

    reckoning_id: str
    version: int
    conflict: str
    record_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConfirmDecision:
    target: DecisionTarget


@dataclass(frozen=True)
class CorrectDecision:
    target: DecisionTarget
    record_id: str
    meaning: str


@dataclass(frozen=True)
class ClarifyDecision:
    prompt: str


@dataclass(frozen=True)
class NotDecisionRelated:
    pass


DecisionReply = ConfirmDecision | CorrectDecision | ClarifyDecision | NotDecisionRelated

# Exact normalized phrases that confer consent. They must be the whole message
# (after controlled punctuation/case normalization), not a substring.
_CONFIRM_PHRASES = frozenset(
    {
        "confirm",
        "i confirm this version",
        "confirm this version",
        "подтверждаю эту версию",
        "确认这个版本",
    }
)

# Phrases that show assent but are not explicit consent. They ask for the
# documented confirmation phrase instead of mutating.
_ASSENT_PHRASES = frozenset(
    {
        "yes",
        "ok",
        "okay",
        "sure",
        "sounds good",
        "looks good",
        "agreed",
        "fine",
        "да",
        "好",
    }
)

# An imperative correction verb must lead the directive. Anything that puts a
# word before it (a subject, negation, modal, or reporting clause) is not a
# direct instruction and never mutates.
_CORRECTION_VERBS = frozenset(
    {"correct", "change", "update", "revise", "fix", "adjust", "edit"}
)

# A leading interjection can introduce a correction but cannot carry one on
# its own; on its own it asks for the explicit review control.
_LEADING_INTERJECTIONS = ("no", "actually", "wait", "hmm", "well")

_CONFIRMATION_ROOTS = ("confirm", "подтвержд", "确认")

_INTERJECTION_PREFIX = re.compile(
    r"^(?:(?:no|actually|wait|hmm|well)\b[\s,;:]*)+", re.IGNORECASE
)
_TO_MEANING = re.compile(
    r"^(?:correct|change|update|revise|fix|adjust|edit)\b.*?\bto\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_confirmation(text: str) -> str:
    """Strip surrounding whitespace, casefold, and trailing sentence punctuation."""
    normalized = text.strip().casefold()
    normalized = normalized.rstrip(".!?\u3002\uff01\uff1f")
    return normalized.strip()


def _is_question(message: str) -> bool:
    return message.rstrip().endswith(("?", "\uff1f"))


def _references_confirmation(normalized: str) -> bool:
    return any(root in normalized for root in _CONFIRMATION_ROOTS)


def _strip_leading_interjections(message: str) -> tuple[str, bool]:
    """Return (directive, had_interjection) after removing leading interjections."""
    stripped = message
    had_interjection = False
    while True:
        match = _INTERJECTION_PREFIX.match(stripped)
        if match is None:
            break
        had_interjection = True
        stripped = stripped[match.end() :]
    return stripped.strip(), had_interjection


def _leading_verb(directive: str) -> str:
    if not directive:
        return ""
    first_word = directive.split(maxsplit=1)[0]
    return first_word.rstrip(",;:").casefold()


def _extract_meaning(directive: str) -> str | None:
    for separator in (":", "\u2014", "\u2013"):
        if separator in directive:
            _, _, tail = directive.partition(separator)
            if tail.strip():
                return tail.strip()
    match = _TO_MEANING.match(directive)
    if match is not None and match.group(1).strip():
        return match.group(1).strip()
    return None


def _correction_reply(
    message: str, target: DecisionTarget
) -> DecisionReply | None:
    directive, had_interjection = _strip_leading_interjections(message)
    if _leading_verb(directive) in _CORRECTION_VERBS:
        meaning = _extract_meaning(directive)
        if meaning is None:
            return ClarifyDecision(_clarification(target))
        if len(target.record_ids) != 1:
            return ClarifyDecision(
                "Which part of the proposal should I correct? "
                "Open the decision and edit the exact record."
            )
        return CorrectDecision(target, target.record_ids[0], meaning)
    if had_interjection:
        return ClarifyDecision(_clarification(target))
    return None


def interpret_decision_message(
    text: str, target: DecisionTarget | None
) -> DecisionReply:
    message = text.strip()
    if not message or target is None:
        return NotDecisionRelated()

    # Questions are never mutations.
    if _is_question(message):
        return NotDecisionRelated()

    correction = _correction_reply(message, target)
    if correction is not None:
        return correction

    normalized = _normalize_confirmation(message)
    if normalized in _CONFIRM_PHRASES:
        return ConfirmDecision(target)

    # Any remaining mention of confirmation, or bare assent, asks for the
    # explicit documented phrase instead of mutating.
    if _references_confirmation(normalized) or normalized in _ASSENT_PHRASES:
        return ClarifyDecision(_clarification(target))
    return NotDecisionRelated()


def _clarification(target: DecisionTarget) -> str:
    return (
        f'Do you want to confirm "{target.conflict}" exactly as shown '
        f"(revision {target.version})? Reply 'confirm' to confirm that exact "
        "version, or tell me what to correct, for example: "
        "'No, correct it: <the corrected meaning>'."
    )
