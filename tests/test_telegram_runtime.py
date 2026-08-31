from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tomllib
from typing import Iterable

import pytest

from reckoning.interfaces import (
    InMemoryInterfaceRepository,
    InterfaceState,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    SourcePlacement,
)
from reckoning.telegram import (
    TelegramGateway,
    TelegramRuntimeSettings,
    TelegramWebhookAdapter,
    TelegramWebhookApplication,
)


class FixedResponder:
    def respond(self, request: object) -> str:
        del request
        return "Use the smaller proof first."


def telegram_application() -> TelegramWebhookApplication:
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(
            InterfaceState(confirmed_records=("decision d-1",))
        ),
        responder=FixedResponder(),  # type: ignore[arg-type]
        placement=PlacementPolicy(
            profile="local",
            categories=(
                SourcePlacement(
                    "personal-context",
                    "private",
                    "local",
                    "local",
                ),
            ),
            local_node_available=True,
            server_node_available=False,
        ),
    )
    gateway = TelegramGateway(
        interface,
        authentication_token="gateway-secret",
        allowed_chat_ids=("42",),
    )
    return TelegramWebhookApplication(
        TelegramWebhookAdapter(
            gateway,
            authentication_token="gateway-secret",
            webhook_secret="webhook-secret",
        )
    )


def request(
    application: TelegramWebhookApplication,
    payload: dict[str, object] | bytes,
    *,
    secret: str,
) -> tuple[str, dict[str, str], dict[str, object]]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    captured_status = ""
    captured_headers: dict[str, str] = {}

    def start_response(
        status: str,
        headers: list[tuple[str, str]],
        exc_info: object | None = None,
    ) -> None:
        del exc_info
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = dict(headers)

    response: Iterable[bytes] = application(
        {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/telegram/webhook",
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/json",
            "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": secret,
            "wsgi.input": BytesIO(body),
        },  # type: ignore[arg-type]
        start_response,  # type: ignore[arg-type]
    )
    return (
        captured_status,
        captured_headers,
        json.loads(b"".join(response)),
    )


def test_installed_telegram_webhook_authenticates_real_http_ingress() -> None:
    application = telegram_application()

    status, headers, response = request(
        application,
        {"message": {"chat": {"id": 42}, "text": "What should I do?"}},
        secret="webhook-secret",
    )
    denied, _, denied_response = request(
        application,
        {"message": {"chat": {"id": 42}, "text": "/status"}},
        secret="wrong",
    )
    malformed_denied, _, _ = request(application, b"{", secret="wrong")

    assert status == "200 OK"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert response == {
        "method": "sendMessage",
        "chat_id": "42",
        "text": "Use the smaller proof first.",
    }
    assert denied == "401 Unauthorized"
    assert denied_response == {"error": "Telegram webhook authentication failed."}
    assert malformed_denied == "401 Unauthorized"


def test_telegram_runtime_reads_only_explicit_process_configuration() -> None:
    settings = TelegramRuntimeSettings.load(
        environ={
            "RECKONING_TELEGRAM_GATEWAY_TOKEN": "gateway-secret",
            "RECKONING_TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
            "RECKONING_TELEGRAM_ALLOWED_CHAT_IDS": "42, 84",
            "UNRELATED_SECRET": "must-not-load",
        }
    )

    assert settings.gateway_token == "gateway-secret"
    assert settings.webhook_secret == "webhook-secret"
    assert settings.allowed_chat_ids == ("42", "84")
    assert "UNRELATED_SECRET" not in settings.__dict__
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        TelegramRuntimeSettings.load(
            environ={
                "RECKONING_TELEGRAM_GATEWAY_TOKEN": "gateway-secret",
                "RECKONING_TELEGRAM_ALLOWED_CHAT_IDS": "42",
            }
        )


def test_package_installs_a_separate_bounded_telegram_command() -> None:
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["project"]["scripts"]["reckoning-telegram"] == (
        "reckoning.telegram:main"
    )
