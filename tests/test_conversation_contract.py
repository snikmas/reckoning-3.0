"""Contract tests for typed conversation composition.

These tests pin the authority order, delimiter conventions, and omission rules
that prevent internal prompt labels from reaching the provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.conversation import (
    ComposerInput,
    ProviderConversation,
    ProviderMessage,
    compose_provider_conversation,
)
from reckoning.processing import (
    JsonFileProcessingGrantRepository,
    ProcessingDestination,
    ProcessingScope,
)
from reckoning.providers import ProviderResponse, ProviderUsage


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)


class EchoModel:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def respond(self, request: object) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            content="echo",
            provider="fake",
            model="deterministic-fake",
            model_calls=1,
            latency_ms=0,
            retries=0,
            usage=ProviderUsage(),
        )


def _conversation_text(conversation: ProviderConversation) -> str:
    return "\n".join(message.content for message in conversation.messages)


def test_current_request_is_last_user_message() -> None:
    conversation, _history_selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            confirmed_records=("record",),
        )
    )

    assert conversation.messages[-1] == ProviderMessage("user", "current")
    assert conversation.messages[-1].role == "user"
    assert sum(1 for m in conversation.messages if m.content == "current") == 1


def test_empty_optional_sections_disappear() -> None:
    conversation, _history_selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
        )
    )

    roles = [message.role for message in conversation.messages]
    contents = _conversation_text(conversation)
    assert roles == ["system", "system", "system", "user"]
    assert "[CONFIRMED CONTEXT]" not in contents
    assert "[RETRIEVED CONTEXT]" not in contents
    assert "Available tools" not in contents


def test_internal_layer_names_never_reach_provider() -> None:
    conversation, _history_selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="The protected product contract content.",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            confirmed_records=("A confirmed record.",),
            retrieved_context=("some context",),
            available_connectors=("connector",),
        )
    )

    text = _conversation_text(conversation)
    assert "protected_product_contract" not in text
    assert "retrieved_context" not in text
    assert "confirmed_context" not in text


def test_confirmed_context_appears_as_delimited_data() -> None:
    conversation, _history_selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            confirmed_records=("record one", "record two"),
        )
    )

    data_message = next(
        message
        for message in conversation.messages
        if message.role == "user" and message.content != "current"
    )
    assert "[CONFIRMED CONTEXT]" in data_message.content
    assert "record one" in data_message.content
    assert "record two" in data_message.content


def test_history_includes_prior_user_and_assistant_speech() -> None:
    conversation, _history_selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            history=(
                ProviderMessage("user", "prior user"),
                ProviderMessage("assistant", "prior assistant"),
            ),
        )
    )

    assert conversation.messages[-3] == ProviderMessage("user", "prior user")
    assert conversation.messages[-2] == ProviderMessage("assistant", "prior assistant")
    assert conversation.messages[-1] == ProviderMessage("user", "current")


def test_notices_are_not_part_of_provider_conversation() -> None:
    conversation, _history_selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
        )
    )

    text = _conversation_text(conversation)
    assert "Limited context" not in text


def test_missing_profile_does_not_change_simon_speech() -> None:
    clock = FixedClock()
    model = EchoModel()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=clock,
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
        )
    )

    reply = application.send_message("hello")

    assert reply.speech == "echo"
    assert "profile" not in reply.speech.casefold()
    assert reply.context_status.unavailable == ()


def _current_request_and_history_scope(tmp_path: Path) -> ProcessingScope:
    repository = JsonFileProcessingGrantRepository(tmp_path / "grants.json")
    destination = ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud")
    scope = ProcessingScope(repository, destination)
    scope.change(
        ("current-request", "recent-channel-history"),
        changed_at=FixedClock().now(),
        expected_version=0,
    )
    return scope


def test_processing_limitations_are_notices_not_speech(tmp_path: Path) -> None:
    clock = FixedClock()
    model = EchoModel()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=clock,
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            processing_scope=_current_request_and_history_scope(tmp_path),
        )
    )

    reply = application.respond_with_channel_context(
        "hello",
        (),
        ("confirmed record",),
        (),
        required_processing_categories=("current-request",),
    )

    assert reply.speech == "echo"
    assert any("unavailable" in notice for notice in reply.notices)
    assert "Limited context" not in reply.speech


def test_second_provider_request_includes_prior_speech_excludes_notices(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    model = EchoModel()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=clock,
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            processing_scope=_current_request_and_history_scope(tmp_path),
        )
    )

    first = application.respond_with_channel_context(
        "first",
        (),
        ("confirmed record",),
        (),
        required_processing_categories=("current-request",),
    )
    second = application.respond_with_channel_context(
        "second",
        (("user", "first"), ("assistant", first.speech)),
        ("confirmed record",),
        (),
        required_processing_categories=("current-request",),
    )

    assert first.speech == "echo"
    assert first.notices
    assert second.speech == "echo"
    second_request = model.requests[-1]
    conversation = getattr(second_request, "provider_conversation", None)
    assert conversation is not None
    history_text = "\n".join(
        message.content
        for message in conversation.messages
        if message.role == "assistant"
    )
    assert "echo" in history_text
    assert "unavailable" not in history_text
    assert "Limited context" not in history_text
