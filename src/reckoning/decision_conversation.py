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

_CORRECTION_MARKERS = ("correct", "change", "actually")


def _normalize_confirmation(text: str) -> str:
    """Strip surrounding whitespace, casefold, and trailing sentence punctuation."""
    normalized = text.strip().casefold()
    while normalized and normalized[-1] in ".!?":
        normalized = normalized[:-1]
    return normalized.strip()


def interpret_decision_message(
    text: str, target: DecisionTarget | None
) -> DecisionReply:
    message = text.strip()
    if not message or target is None:
        return NotDecisionRelated()

    # Questions are never mutations.
    if message.rstrip().endswith("?"):
        return NotDecisionRelated()

    normalized = _normalize_confirmation(message)
    has_correction = (
        normalized.split()[0].rstrip(",.").startswith("no")
        if normalized
        else False
    ) or any(marker in normalized for marker in _CORRECTION_MARKERS)

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

    confirmed = normalized in _CONFIRM_PHRASES
    if confirmed:
        return ConfirmDecision(target)

    # Any remaining mention of confirmation, or bare assent, asks for the
    # explicit documented phrase instead of mutating.
    if "confirm" in normalized or normalized in _ASSENT_PHRASES:
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
