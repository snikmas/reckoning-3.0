from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime


@dataclass(frozen=True)
class PlacementState:
    processing_location: str
    storage_location: str
    local_node_available: bool


@dataclass(frozen=True)
class ModelRequest:
    user_message: str
    history: tuple[Message, ...]
    requested_at: datetime
    placement: PlacementState
    available_connectors: tuple[str, ...]


class Clock(Protocol):
    def now(self) -> datetime: ...


class ModelProvider(Protocol):
    def respond(self, request: ModelRequest) -> str: ...


class ConnectorRegistry(Protocol):
    def available_names(self) -> tuple[str, ...]: ...


class ConversationStorage(Protocol):
    def append(
        self, role: MessageRole, content: str, created_at: datetime
    ) -> Message: ...

    def list_messages(self) -> tuple[Message, ...]: ...


@dataclass(frozen=True)
class ApplicationDependencies:
    clock: Clock
    model: ModelProvider
    placement: PlacementState
    connectors: ConnectorRegistry
    storage: ConversationStorage


class ReckoningApplication:
    """The channel-independent boundary for Reckoning operations."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    def send_message(self, text: str) -> Message:
        user_message = text.strip()
        if not user_message:
            raise ValueError("A message cannot be empty.")

        requested_at = self._dependencies.clock.now()
        response = self._dependencies.model.respond(
            ModelRequest(
                user_message=user_message,
                history=self._dependencies.storage.list_messages(),
                requested_at=requested_at,
                placement=self._dependencies.placement,
                available_connectors=self._dependencies.connectors.available_names(),
            )
        ).strip()
        if not response:
            raise RuntimeError("The model provider returned an empty response.")

        self._dependencies.storage.append("user", user_message, requested_at)
        return self._dependencies.storage.append("assistant", response, requested_at)

    def open_session(self) -> tuple[Message, ...]:
        return self._dependencies.storage.list_messages()


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DeterministicFakeModel:
    def respond(self, request: ModelRequest) -> str:
        return f'Simon received your message: "{request.user_message}"'


class NoConnectors:
    def available_names(self) -> tuple[str, ...]:
        return ()


class InMemoryConversationStorage:
    def __init__(self) -> None:
        self._messages: list[Message] = []

    def append(
        self, role: MessageRole, content: str, created_at: datetime
    ) -> Message:
        message = Message(
            sequence=len(self._messages) + 1,
            role=role,
            content=content,
            created_at=created_at,
        )
        self._messages.append(message)
        return message

    def list_messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)


def create_local_application() -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=SystemClock(),
            model=DeterministicFakeModel(),
            placement=PlacementState(
                processing_location="local",
                storage_location="local",
                local_node_available=True,
            ),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
        )
    )
