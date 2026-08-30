from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib.error import URLError

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ProtectedResponsePolicy,
    ReckoningApplication,
)
from reckoning.providers import DeepSeekModelProvider, ProviderResponse, ProviderUsage


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc)


class SequenceIdentifiers:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new(self) -> str:
        return next(self._values)


class SequenceTimer:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def build_application(provider: DeepSeekModelProvider) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=provider,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            identifiers=SequenceIdentifiers("run-1"),
        )
    )


def test_deepseek_uses_same_boundary_and_records_billable_units() -> None:
    seen_authorization = ""

    def transport(request: object, timeout: float) -> bytes:
        nonlocal seen_authorization
        seen_authorization = request.headers["Authorization"]
        assert timeout == 10.0
        payload = json.loads(request.data)
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["messages"][-1] == {
            "role": "user",
            "content": "Compare these options.",
        }
        return json.dumps(
            {
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "Choose the smaller proof."}}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 9,
                    "total_tokens": 129,
                },
            }
        ).encode()

    application = build_application(
        DeepSeekModelProvider(
            "secret-value",
            timeout_seconds=10.0,
            transport=transport,
            timer=SequenceTimer(1.0, 1.245),
        )
    )

    response = application.send_message("Compare these options.")

    assert response.content == "Choose the smaller proof."
    assert seen_authorization == "Bearer secret-value"
    assert application.inspect_model_runs()[0].__dict__ == {
        "id": "run-1",
        "requested_at": datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc),
        "status": "succeeded",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "model_calls": 1,
        "latency_ms": 245,
        "retries": 0,
        "input_tokens": 120,
        "output_tokens": 9,
        "billable_units": 129,
        "failure": None,
    }


def test_provider_failure_is_recorded_and_never_becomes_a_completed_answer() -> None:
    attempts = 0

    def failing_transport(request: object, timeout: float) -> bytes:
        nonlocal attempts
        attempts += 1
        raise URLError("offline")

    application = build_application(
        DeepSeekModelProvider(
            "secret-value",
            max_retries=1,
            transport=failing_transport,
            timer=SequenceTimer(1.0, 1.2),
        )
    )

    with pytest.raises(RuntimeError, match="run failed.*could not be reached"):
        application.send_message("Do the work.")

    run = application.inspect_model_runs()[0]
    assert attempts == 2
    assert run.status == "failed"
    assert run.model_calls == 2
    assert run.retries == 1
    assert run.failure == "DeepSeek could not be reached: offline"
    assert [message.role for message in application.open_session()] == ["user"]


def test_protected_boundary_records_a_limited_run() -> None:
    class BoundaryCrossingProvider:
        def respond(self, request: object) -> ProviderResponse:
            return ProviderResponse(
                "You are worthless.",
                "deepseek",
                "deepseek-v4-flash",
                1,
                10,
                0,
                ProviderUsage(10, 3, 13),
            )

    application = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=BoundaryCrossingProvider(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            identifiers=SequenceIdentifiers("run-1"),
            response_policy=ProtectedResponsePolicy(),
        )
    )

    response = application.send_message("Challenge this choice.")

    assert response.content.startswith("That request crosses a protected boundary")
    assert application.inspect_model_runs()[0].status == "limited"
