"""Deterministic interpretation of conversation replies about one decision.

Consent never comes from model confidence: a decision is confirmed or
corrected only when the user's message unambiguously says so, and always
against the proposal actually displayed (its current revision).
"""

from __future__ import annotations

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

_NEGATION_MARKERS = ("n't", "not ", "never")
_CORRECTION_MARKERS = ("correct", "change", "actually")
_ASSENT_PHRASES = (
    "yes",
    "yes.",
    "ok",
    "okay",
    "sure",
    "sounds good",
    "looks good",
    "agreed",
    "fine",
)


def interpret_decision_message(
    text: str, target: DecisionTarget | None
) -> DecisionReply:
    message = text.strip()
    if not message or target is None:
        return NotDecisionRelated()
    normalized = message.casefold()

    has_confirm = "confirm" in normalized
    negated = any(marker in normalized for marker in _NEGATION_MARKERS)
    has_correction = normalized.split()[0].rstrip(",.").startswith("no") or any(
        marker in normalized for marker in _CORRECTION_MARKERS
    )

    if has_correction:
        meaning = _extract_correction(message)
        if meaning is None:
            return ClarifyDecision(_clarification(target))
        if len(target.record_ids) != 1:
            return ClarifyDecision(
                "Which part of the proposal should I correct? "
                "Open the decision and edit the exact record."
            )
        return CorrectDecision(target, target.record_ids[0], meaning)
    if has_confirm and not negated:
        return ConfirmDecision(target)
    if has_confirm or normalized.rstrip(".!") in _ASSENT_PHRASES:
        return ClarifyDecision(_clarification(target))
    return NotDecisionRelated()


def _extract_correction(message: str) -> str | None:
    for separator in (":", "—", "–"):
        if separator in message:
            head, _, tail = message.partition(separator)
            head_normalized = head.casefold()
            if (
                head_normalized.split()[0].rstrip(",.").startswith("no")
                or any(marker in head_normalized for marker in _CORRECTION_MARKERS)
            ) and tail.strip():
                return tail.strip()
    for prefix in ("correct it to ", "correct that to ", "change it to "):
        if message.casefold().startswith(prefix):
            tail = message[len(prefix):].strip()
            if tail:
                return tail
    return None


def _clarification(target: DecisionTarget) -> str:
    return (
        f'Do you want to confirm "{target.conflict}" exactly as shown '
        f"(revision {target.version})? Reply 'confirm' to confirm that exact "
        "version, or tell me what to correct, for example: "
        "'No, correct it: <the corrected meaning>'."
    )
