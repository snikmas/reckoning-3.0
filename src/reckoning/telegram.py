from __future__ import annotations

import argparse
from dataclasses import dataclass
from hmac import compare_digest
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping
from wsgiref.simple_server import make_server
from wsgiref.types import StartResponse, WSGIEnvironment

from reckoning.application import create_local_application
from reckoning.config import DeepSeekSettings, OrcaRouterSettings
from reckoning.interfaces import (
    ReckoningInterfaceApplication,
    create_local_interface_application,
)
from reckoning.operations import OperationError, load_installation_runtime
from reckoning.web import validate_bind_host


@dataclass(frozen=True)
class TelegramRuntimeSettings:
    gateway_token: str
    webhook_secret: str
    allowed_chat_ids: tuple[str, ...]

    @classmethod
    def load(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> TelegramRuntimeSettings:
        values = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required.")
            return value

        allowed_chat_ids = tuple(
            item.strip()
            for item in required(
                "RECKONING_TELEGRAM_ALLOWED_CHAT_IDS"
            ).split(",")
            if item.strip()
        )
        if not allowed_chat_ids:
            raise ValueError(
                "RECKONING_TELEGRAM_ALLOWED_CHAT_IDS must name at least one chat."
            )
        return cls(
            gateway_token=required("RECKONING_TELEGRAM_GATEWAY_TOKEN"),
            webhook_secret=required("RECKONING_TELEGRAM_WEBHOOK_SECRET"),
            allowed_chat_ids=allowed_chat_ids,
        )


@dataclass(frozen=True)
class TelegramRequest:
    kind: str
    chat_id: str
    text: str = ""
    confirmation_id: str = ""


@dataclass(frozen=True)
class TelegramReply:
    kind: Literal["message", "status", "confirmation", "routine"]
    chat_id: str
    text: str


class TelegramGateway:
    """Authenticated translation into Telegram's bounded channel capabilities."""

    def __init__(
        self,
        application: ReckoningInterfaceApplication,
        *,
        authentication_token: str,
        allowed_chat_ids: tuple[str, ...],
    ) -> None:
        if not authentication_token:
            raise ValueError("A Telegram authentication token is required.")
        normalized_chat_ids = frozenset(
            chat_id.strip() for chat_id in allowed_chat_ids if chat_id.strip()
        )
        if not normalized_chat_ids:
            raise ValueError("At least one authorized Telegram chat is required.")
        self._application = application
        self._authentication_token = authentication_token
        self._allowed_chat_ids = normalized_chat_ids

    def handle(self, authentication_token: str, request: TelegramRequest) -> TelegramReply:
        self._authenticate(authentication_token)
        chat_id = self._authorize_chat(request.chat_id)

        if request.kind == "message":
            reply = self._application.send_channel_message(
                "telegram",
                request.text,
                session_id=chat_id,
            )
            return TelegramReply("message", chat_id, reply.text)
        if request.kind == "status":
            status = self._application.status("telegram")
            approvals = ", ".join(status.pending_approvals) or "none"
            return TelegramReply(
                "status",
                chat_id,
                (
                    f"Reckoning is {status.health}. {status.placement.notice} "
                    f"Pending approvals: {approvals}."
                ),
            )
        if request.kind == "confirm":
            confirmation_id = request.confirmation_id.strip()
            if not confirmation_id:
                raise ValueError("A confirmation identifier is required.")
            if not self._application.confirm(confirmation_id):
                raise KeyError(f"Unknown confirmation: {confirmation_id}")
            return TelegramReply(
                "confirmation",
                chat_id,
                f"Confirmed {confirmation_id}.",
            )
        raise ValueError(
            "The authenticated gateway exposes only bounded message, status, "
            "confirmation, and routine-delivery capabilities."
        )

    def deliver_routine(
        self,
        authentication_token: str,
        chat_id: str,
        text: str,
        *,
        receipt_id: str,
    ) -> TelegramReply:
        self._authenticate(authentication_token)
        authorized_chat_id = self._authorize_chat(chat_id)
        if not receipt_id.strip():
            raise ValueError("A routine delivery requires a receipt identifier.")
        self._application.explain_run(receipt_id)
        delivered = self._application.deliver_routine(
            "telegram",
            text,
            session_id=authorized_chat_id,
        )
        return TelegramReply("routine", authorized_chat_id, delivered.content)

    def _authenticate(self, provided_token: str) -> None:
        if not compare_digest(provided_token, self._authentication_token):
            raise PermissionError("Telegram gateway authentication failed.")

    def _authorize_chat(self, chat_id: str) -> str:
        normalized = chat_id.strip()
        if not normalized:
            raise ValueError("A Telegram chat identifier is required.")
        if normalized not in self._allowed_chat_ids:
            raise PermissionError("Telegram chat is not authorized.")
        return normalized


class TelegramWebhookAdapter:
    """Translate Telegram update payloads without expanding gateway authority."""

    def __init__(
        self,
        gateway: TelegramGateway,
        *,
        authentication_token: str,
        webhook_secret: str,
    ) -> None:
        if not authentication_token:
            raise ValueError("A Telegram authentication token is required.")
        if not webhook_secret:
            raise ValueError("A Telegram webhook secret is required.")
        self._gateway = gateway
        self._authentication_token = authentication_token
        self._webhook_secret = webhook_secret

    def handle_update(
        self,
        update: dict[str, Any],
        *,
        secret_token: str,
    ) -> dict[str, object]:
        self.authenticate(secret_token)
        message = update.get("message")
        if not isinstance(message, dict):
            raise ValueError("Only Telegram message updates are supported.")
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            raise ValueError("A Telegram text message and chat are required.")
        chat_id = str(chat.get("id", "")).strip()
        command, separator, argument = text.strip().partition(" ")
        if command == "/status":
            request = TelegramRequest("status", chat_id)
        elif command == "/confirm":
            if not separator or not argument.strip():
                raise ValueError("Use /confirm with a confirmation identifier.")
            request = TelegramRequest(
                "confirm",
                chat_id,
                confirmation_id=argument.strip(),
            )
        else:
            request = TelegramRequest("message", chat_id, text=text)
        reply = self._gateway.handle(self._authentication_token, request)
        return {
            "method": "sendMessage",
            "chat_id": reply.chat_id,
            "text": reply.text,
        }

    def authenticate(self, secret_token: str) -> None:
        if not compare_digest(secret_token, self._webhook_secret):
            raise PermissionError("Telegram webhook authentication failed.")

    def deliver_routine(
        self,
        *,
        chat_id: str,
        text: str,
        receipt_id: str,
    ) -> dict[str, object]:
        """Build one bounded outbound Telegram request for a recorded run."""
        reply = self._gateway.deliver_routine(
            self._authentication_token,
            chat_id,
            text,
            receipt_id=receipt_id,
        )
        return {
            "method": "sendMessage",
            "chat_id": reply.chat_id,
            "text": reply.text,
        }


class TelegramWebhookApplication:
    """WSGI endpoint for Telegram's authenticated webhook delivery."""

    _maximum_body_bytes = 1_048_576

    def __init__(self, adapter: TelegramWebhookAdapter) -> None:
        self._adapter = adapter

    def __call__(
        self,
        environ: WSGIEnvironment,
        start_response: StartResponse,
    ) -> list[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        if path != "/telegram/webhook":
            return self._response(start_response, "404 Not Found", {"error": "Not found."})
        if method != "POST":
            return self._response(
                start_response,
                "405 Method Not Allowed",
                {"error": "Telegram webhooks require POST."},
            )
        try:
            secret_token = str(
                environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
            )
            self._adapter.authenticate(secret_token)
            content_length = int(str(environ.get("CONTENT_LENGTH") or "0"))
            if content_length <= 0 or content_length > self._maximum_body_bytes:
                raise ValueError("Telegram webhook body size is invalid.")
            input_stream = environ.get("wsgi.input")
            if input_stream is None or not hasattr(input_stream, "read"):
                raise ValueError("Telegram webhook body is missing.")
            decoded = json.loads(input_stream.read(content_length))
            if not isinstance(decoded, dict):
                raise ValueError("Telegram webhook body must be a JSON object.")
            result = self._adapter.handle_update(
                decoded,
                secret_token=secret_token,
            )
        except PermissionError as error:
            return self._response(
                start_response,
                "401 Unauthorized",
                {"error": str(error)},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return self._response(
                start_response,
                "400 Bad Request",
                {"error": str(error)},
            )
        return self._response(start_response, "200 OK", result)

    @staticmethod
    def _response(
        start_response: StartResponse,
        status: str,
        payload: dict[str, object],
    ) -> list[bytes]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        start_response(
            status,
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Reckoning's bounded Telegram webhook."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8081, type=int)
    parser.add_argument("--provider", choices=("fake", "deepseek", "orcarouter"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
    )
    parser.add_argument(
        "--server-data-dir",
        type=Path,
        help="Configured personal-server storage root for server or hybrid placement.",
    )
    arguments = parser.parse_args()
    try:
        host = validate_bind_host(arguments.host)
        telegram_settings = TelegramRuntimeSettings.load()
        runtime = load_installation_runtime(
            arguments.data_dir,
            server_data_dir=arguments.server_data_dir,
        )
    except (OperationError, ValueError) as error:
        parser.error(str(error))

    orcarouter_settings = OrcaRouterSettings.load()
    deepseek_settings = DeepSeekSettings.load()
    provider_name = arguments.provider or (
        "orcarouter"
        if orcarouter_settings.api_key
        else "deepseek"
        if deepseek_settings.api_key
        else "fake"
    )
    provider_settings = (
        deepseek_settings if provider_name == "deepseek" else orcarouter_settings
    )
    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        provider_name=provider_name,
        orcarouter_api_key=orcarouter_settings.api_key,
        deepseek_api_key=deepseek_settings.api_key,
        model_name=arguments.model or provider_settings.model,
        base_url=arguments.base_url or provider_settings.base_url,
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    interface = create_local_interface_application(
        application,
        runtime.state_path("confirmed-state", "interfaces.json"),
        placement=runtime.interface_placement,
        connector_data_dir=runtime.root_for("approved-remote-sources"),
    )
    gateway = TelegramGateway(
        interface,
        authentication_token=telegram_settings.gateway_token,
        allowed_chat_ids=telegram_settings.allowed_chat_ids,
    )
    webhook = TelegramWebhookApplication(
        TelegramWebhookAdapter(
            gateway,
            authentication_token=telegram_settings.gateway_token,
            webhook_secret=telegram_settings.webhook_secret,
        )
    )
    with make_server(host, arguments.port, webhook) as server:
        print(f"Telegram webhook is listening on http://{host}:{arguments.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nTelegram webhook stopped.")


if __name__ == "__main__":
    main()
