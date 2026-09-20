from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    ModelRequest,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
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


class CapturingModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def respond(self, request: ModelRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            content="ack",
            provider="fake",
            model="deterministic-fake",
            model_calls=1,
            latency_ms=0,
            retries=0,
            usage=ProviderUsage(),
        )


def _history_scope(tmp_path: Path) -> ProcessingScope:
    repository = JsonFileProcessingGrantRepository(tmp_path / "grants.json")
    destination = ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud")
    scope = ProcessingScope(repository, destination)
    scope.change(
        ("current-request", "recent-channel-history"),
        changed_at=FixedClock().now(),
        expected_version=0,
    )
    return scope


def test_outbound_request_respects_context_window_and_keeps_full_storage(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    model = CapturingModel()
    storage = InMemoryConversationStorage()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=clock,
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=storage,
            processing_scope=_history_scope(tmp_path),
            context_window=2000,
        )
    )

    application.send_message("first message")
    application.send_message("second message")

    second_request = model.requests[-1]
    conversation = second_request.provider_conversation
    # The bounded window should still include the immediately preceding turn.
    assert any("first message" in message.content for message in conversation.messages)
    # But storage keeps everything.
    assert len(storage.list_messages()) == 4


def test_outbound_request_has_exactly_one_current_request() -> None:
    model = CapturingModel()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            context_window=2000,
        )
    )

    application.send_message("hello")

    request = model.requests[0]
    user_messages = [
        message.content for message in request.provider_conversation.messages if message.role == "user"
    ]
    assert user_messages.count("hello") == 1
    assert user_messages[-1] == "hello"


def test_outbound_request_does_not_contain_internal_prompt_labels() -> None:
    model = CapturingModel()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            context_window=2000,
        )
    )

    application.send_message("hello")

    text = "\n".join(
        message.content for message in model.requests[0].provider_conversation.messages
    )
    assert "protected_product_contract" not in text
    assert "retrieved_context" not in text
    assert "confirmed_context" not in text
