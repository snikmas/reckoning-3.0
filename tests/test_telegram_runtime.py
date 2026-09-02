from __future__ import annotations

import json
import stat
import tomllib
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

import pytest

from reckoning.interfaces import (
    InMemoryInterfaceRepository,
    InterfaceState,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    SourcePlacement,
)
from reckoning.setup import setup_reckoning
from reckoning.telegram import (
    TelegramGateway,
    TelegramPollingApplication,
    TelegramPollingSettings,
    TelegramRuntimeSettings,
    TelegramUpdateAdapter,
    TelegramWebhookAdapter,
    TelegramWebhookApplication,
    setup_telegram_polling,
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
    body = (
        payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    )
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


class RecordingTelegramClient:
    def __init__(
        self,
        update_batches: list[tuple[dict[str, object], ...]],
        *,
        webhook_url: str = "",
    ) -> None:
        self.update_batches = list(update_batches)
        self.webhook_url = webhook_url
        self.deleted_webhook = False
        self.sent: list[tuple[str, str]] = []
        self.polls: list[tuple[int | None, int]] = []

    def get_me(self) -> dict[str, object]:
        return {"id": 7, "username": "reckoning_test_bot"}

    def get_webhook_info(self) -> dict[str, object]:
        return {"url": self.webhook_url}

    def delete_webhook(self) -> None:
        self.deleted_webhook = True

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
    ) -> tuple[dict[str, object], ...]:
        self.polls.append((offset, timeout))
        return self.update_batches.pop(0) if self.update_batches else ()

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def test_terminal_setup_lists_future_choices_and_configures_the_available_path(
    tmp_path: Path,
) -> None:
    client = RecordingTelegramClient(
        [
            (
                {
                    "update_id": 5,
                    "message": {
                        "chat": {"id": 42, "type": "private"},
                        "text": "/connect abc123",
                    },
                },
            )
        ]
    )
    answers = iter(("2", "1", "2", "1"))
    prompts: list[str] = []
    messages: list[str] = []

    def read_line(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    secret_prompts: list[str] = []

    def read_secret(prompt: str) -> str:
        secret_prompts.append(prompt)
        return "bot-token"

    config_path = tmp_path / "telegram.json"
    settings = setup_reckoning(
        config_path=config_path,
        secret_reader=read_secret,
        line_reader=read_line,
        output=messages.append,
        api_factory=lambda token: client,
        pairing_code="abc123",
        maximum_polls=1,
    )

    displayed = "\n".join(messages)
    assert "1. Telegram" in displayed
    assert "2. Discord (coming later)" in displayed
    assert "3. WhatsApp (coming later)" in displayed
    assert "4. Slack (coming later)" in displayed
    assert "1. Fake (no API key)" in displayed
    assert "2. DeepSeek (API-key setup coming later)" in displayed
    assert "3. OrcaRouter (API-key setup coming later)" in displayed
    assert "Discord is not available yet." in displayed
    assert "DeepSeek setup is not available yet." in displayed
    assert prompts == [
        "Gateway [1]: ",
        "Gateway [1]: ",
        "Provider [1]: ",
        "Provider [1]: ",
    ]
    assert secret_prompts == ["Paste the BotFather token (input is hidden): "]
    assert settings.gateway_name == "telegram"
    assert settings.provider_name == "fake"
    assert TelegramPollingSettings.load(config_path) == settings


def test_polling_uses_the_bounded_gateway_and_acknowledges_denied_chats() -> None:
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(InterfaceState()),
        responder=FixedResponder(),  # type: ignore[arg-type]
        placement=PlacementPolicy(
            profile="local",
            categories=(
                SourcePlacement("personal-context", "private", "local", "local"),
            ),
            local_node_available=True,
            server_node_available=False,
        ),
    )
    gateway = TelegramGateway(
        interface,
        authentication_token="internal",
        allowed_chat_ids=("42",),
    )
    client = RecordingTelegramClient(
        [
            (
                {"update_id": 10, "message": {"chat": {"id": 99}, "text": "No"}},
                {
                    "update_id": 11,
                    "message": {"chat": {"id": 42}, "text": "What should I do?"},
                },
            )
        ]
    )
    polling = TelegramPollingApplication(
        TelegramUpdateAdapter(gateway, authentication_token="internal"),
        client,
        poll_timeout=1,
    )

    offset = polling.run_once()

    assert offset == 12
    assert client.polls == [(None, 1)]
    assert client.sent == [("42", "Use the smaller proof first.")]


def test_polling_settings_are_stored_outside_the_repo_with_private_permissions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config" / "telegram.json"
    settings = TelegramPollingSettings(
        "bot-token",
        ("42",),
        "reckoning_test_bot",
    )

    settings.save(path)

    assert TelegramPollingSettings.load(path) == settings
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "bot-token" not in repr(settings)


def test_polling_settings_direct_unconfigured_users_to_product_setup(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Run reckoning setup first"):
        TelegramPollingSettings.load(tmp_path / "missing.json")


def test_terminal_setup_pairs_only_the_matching_private_chat(tmp_path: Path) -> None:
    path = tmp_path / "telegram.json"
    client = RecordingTelegramClient(
        [
            (
                {
                    "update_id": 3,
                    "message": {
                        "chat": {"id": -100, "type": "group"},
                        "text": "/connect abc123",
                    },
                },
                {
                    "update_id": 4,
                    "message": {
                        "chat": {"id": 42, "type": "private"},
                        "text": "/connect wrong",
                    },
                },
                {
                    "update_id": 5,
                    "message": {
                        "chat": {"id": 84, "type": "private"},
                        "text": "/connect abc123",
                    },
                },
            ),
            (),
        ]
    )
    messages: list[str] = []

    settings = setup_telegram_polling(
        config_path=path,
        secret_reader=lambda prompt: "bot-token",
        line_reader=lambda prompt: "yes",
        output=messages.append,
        api_factory=lambda token: client,
        pairing_code="abc123",
        maximum_polls=1,
    )

    assert settings.allowed_chat_ids == ("84",)
    assert TelegramPollingSettings.load(path) == settings
    assert client.sent == [
        ("84", "Reckoning is connected. Return to the terminal and start the bot.")
    ]
    assert settings.next_update_offset == 6
    assert client.polls == [(None, 25)]
    assert all("bot-token" not in message for message in messages)


def test_message_queued_after_pairing_is_delivered_when_polling_starts(
    tmp_path: Path,
) -> None:
    client = RecordingTelegramClient(
        [
            (
                {
                    "update_id": 5,
                    "message": {
                        "chat": {"id": 42, "type": "private"},
                        "text": "/connect abc123",
                    },
                },
            ),
            (
                {
                    "update_id": 6,
                    "message": {
                        "chat": {"id": 42, "type": "private"},
                        "text": "What should I do next?",
                    },
                },
            ),
        ]
    )
    settings = setup_telegram_polling(
        config_path=tmp_path / "telegram.json",
        secret_reader=lambda prompt: "bot-token",
        output=lambda message: None,
        api_factory=lambda token: client,
        pairing_code="abc123",
        maximum_polls=1,
    )
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(InterfaceState()),
        responder=FixedResponder(),  # type: ignore[arg-type]
        placement=PlacementPolicy(
            profile="local",
            categories=(
                SourcePlacement("personal-context", "private", "local", "local"),
            ),
            local_node_available=True,
            server_node_available=False,
        ),
    )
    gateway = TelegramGateway(
        interface,
        authentication_token="internal",
        allowed_chat_ids=settings.allowed_chat_ids,
    )
    polling = TelegramPollingApplication(
        TelegramUpdateAdapter(gateway, authentication_token="internal"),
        client,
        poll_timeout=1,
    )

    polling.run_once(settings.next_update_offset)

    assert client.sent == [
        ("42", "Reckoning is connected. Return to the terminal and start the bot."),
        ("42", "Use the smaller proof first."),
    ]
