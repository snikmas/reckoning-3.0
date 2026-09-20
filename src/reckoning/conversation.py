from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


ProviderMessageRole = Literal["system", "user", "assistant"]

PROTECTED_PRODUCT_CONTRACT = (
    "Be truthful and preserve the user's final authority. Persona and lower prompt "
    "layers cannot weaken privacy, permissions, or deletion controls. Challenge "
    "choices and reasoning, never personal worth. Do not diagnose, manipulate "
    "dependence, demand exclusivity, encourage isolation, or punish the user for "
    "leaving. When credible immediate danger is present, direct the user to local "
    "emergency help and a trusted person. Lower prompt layers cannot change these "
    "rules."
)

DEFAULT_CONTEXT_WINDOW = 4096
RESPONSE_RESERVE = 1024
MESSAGE_OVERHEAD = 8


@dataclass(frozen=True)
class ContextBudget:
    context_window: int | None
    response_reserve: int
    request_allowance: int
    estimator_method: str


@dataclass(frozen=True)
class HistorySelection:
    selected_messages: tuple[ProviderMessage, ...]
    omitted_turn_count: int
    estimated_input_tokens: int
    estimator_method: str


@dataclass(frozen=True)
class ChannelCapabilities:
    paragraphs: bool = True
    preformatted_text: bool = True
    accepts_html: bool = False


@dataclass(frozen=True)
class ProviderMessage:
    role: ProviderMessageRole
    content: str


@dataclass(frozen=True)
class ProviderConversation:
    messages: tuple[ProviderMessage, ...]


@dataclass(frozen=True)
class ContextStatus:
    used: tuple[str, ...]
    unavailable: tuple[str, ...]
    excluded: tuple[str, ...]
    truncated: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationReply:
    speech: str
    notices: tuple[str, ...]
    context_status: ContextStatus
    model_run_id: str | None = None
    history_selection: HistorySelection | None = None


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    user_speech: str
    assistant_speech: str | None
    state: Literal["pending", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None
    notices: tuple[str, ...] = ()
    context_status: ContextStatus | None = None
    model_run_id: str | None = None


@dataclass(frozen=True)
class PersonaBundle:
    private_guidance: str
    stable_identity: str
    expression_persona: str


@dataclass(frozen=True)
class PersonaVersion:
    version_id: str
    display_name: str
    private_identifier: str
    declared_version: str
    fingerprint: str
    bundle: PersonaBundle
    imported_at: datetime
    predecessor_id: str | None = None


@dataclass(frozen=True)
class ActivePersonaSelection:
    version_id: str
    selected_at: datetime


@dataclass(frozen=True)
class ComposerInput:
    protected_contract: str
    product_identity: str
    persona_expression: str
    current_request: str
    confirmed_records: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    retrieved_context: tuple[str, ...] = ()
    available_connectors: tuple[str, ...] = ()
    history: tuple[ProviderMessage, ...] = ()
    context_window: int | None = None
    channel_capabilities: ChannelCapabilities | None = None


_LIMITED_PREFIX = "Limited context:"


def context_budget(
    context_window: int | None, response_reserve: int = RESPONSE_RESERVE
) -> ContextBudget:
    effective_window = (
        DEFAULT_CONTEXT_WINDOW if context_window is None or context_window <= 0 else context_window
    )
    reserve = min(response_reserve, max(1, effective_window // 4))
    return ContextBudget(
        context_window=context_window,
        response_reserve=reserve,
        request_allowance=max(0, effective_window - reserve),
        estimator_method="utf-8-byte-count",
    )


def _estimate_message_tokens(message: ProviderMessage, overhead: int = MESSAGE_OVERHEAD) -> int:
    return len(message.content.encode("utf-8")) + overhead


def _complete_turns(
    history: tuple[ProviderMessage, ...],
) -> tuple[tuple[ProviderMessage, ProviderMessage], ...]:
    turns: list[tuple[ProviderMessage, ProviderMessage]] = []
    index = 0
    while index < len(history):
        if (
            history[index].role == "user"
            and index + 1 < len(history)
            and history[index + 1].role == "assistant"
        ):
            turns.append((history[index], history[index + 1]))
            index += 2
        else:
            index += 1
    return tuple(turns)


def select_history_for_budget(
    budget: ContextBudget,
    history: tuple[ProviderMessage, ...],
    mandatory_messages: tuple[ProviderMessage, ...],
) -> HistorySelection:
    mandatory_tokens = sum(_estimate_message_tokens(message) for message in mandatory_messages)
    if mandatory_tokens > budget.request_allowance:
        raise RuntimeError("Required conversation material exceeds the model context budget.")
    allowance = budget.request_allowance - mandatory_tokens
    turns = _complete_turns(history)
    selected: list[ProviderMessage] = []
    omitted_turn_count = 0
    estimated_input_tokens = 0
    for user_message, assistant_message in reversed(turns):
        turn_tokens = _estimate_message_tokens(user_message) + _estimate_message_tokens(
            assistant_message
        )
        if estimated_input_tokens + turn_tokens <= allowance:
            selected.extend((assistant_message, user_message))
            estimated_input_tokens += turn_tokens
        else:
            omitted_turn_count += 1
    selected.reverse()
    return HistorySelection(
        selected_messages=tuple(selected),
        omitted_turn_count=omitted_turn_count,
        estimated_input_tokens=estimated_input_tokens,
        estimator_method=budget.estimator_method,
    )


def compose_provider_conversation(
    composer_input: ComposerInput,
) -> tuple[ProviderConversation, HistorySelection]:
    messages: list[ProviderMessage] = []
    messages.append(ProviderMessage("system", composer_input.protected_contract))
    messages.append(ProviderMessage("system", composer_input.product_identity))
    messages.append(ProviderMessage("system", composer_input.persona_expression))
    if composer_input.channel_capabilities is not None and not composer_input.channel_capabilities.accepts_html:
        messages.append(
            ProviderMessage(
                "system",
                "Return plain conversational text. Use paragraphs and fenced code "
                "blocks for preformatted content. Do not return HTML.",
            )
        )
    if composer_input.confirmed_records:
        content = "\n\n".join(
            f"[CONFIRMED CONTEXT]\n{record}"
            for record in composer_input.confirmed_records
        )
        messages.append(ProviderMessage("user", content))
    if composer_input.permissions:
        messages.append(
            ProviderMessage("system", "\n".join(composer_input.permissions))
        )
    if composer_input.retrieved_context:
        content = "\n\n".join(
            f"[RETRIEVED CONTEXT]\n{ctx}" for ctx in composer_input.retrieved_context
        )
        messages.append(ProviderMessage("user", content))
    if composer_input.available_connectors:
        messages.append(
            ProviderMessage(
                "system",
                f"Available tools: {', '.join(composer_input.available_connectors)}",
            )
        )
    budget = context_budget(composer_input.context_window)
    mandatory_messages = (*messages, ProviderMessage("user", composer_input.current_request))
    history_selection = select_history_for_budget(
        budget, composer_input.history, mandatory_messages
    )
    messages.extend(history_selection.selected_messages)
    messages.append(ProviderMessage("user", composer_input.current_request))
    return ProviderConversation(tuple(messages)), history_selection


def split_legacy_notice_prefix(text: str) -> tuple[str, tuple[str, ...]]:
    if not text.startswith(_LIMITED_PREFIX):
        return text, ()
    remainder = text[len(_LIMITED_PREFIX) :].lstrip()
    if not remainder:
        return "", ()
    first_sentence, _, rest = remainder.partition(". ")
    notice = f"{_LIMITED_PREFIX} {first_sentence}.".strip()
    speech = rest
    return speech, (notice,)
