from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from getpass import getpass
from hmac import compare_digest
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server
from wsgiref.types import StartResponse, WSGIEnvironment

from reckoning.application import create_local_application
from reckoning.config import (
    CREDENTIAL_PROVIDER_NAMES,
    DEFAULT_PROVIDER_CREDENTIALS,
    DeepSeekSettings,
    OrcaRouterSettings,
    default_provider_name,
)
from reckoning.interfaces import (
    ReckoningInterfaceApplication,
    create_local_interface_application,
)
from reckoning.json_store import atomic_write_json, read_json
from reckoning.operations import OperationError, load_installation_runtime
from reckoning.web import validate_bind_host

DEFAULT_TELEGRAM_CONFIG = Path.home() / ".config" / "reckoning" / "telegram.json"

TelegramConnectorStatus = Literal[
    "not-configured", "bot-verified", "verified-not-paired", "ready", "error"
]


class TelegramBotApiError(RuntimeError):
    """A safe-to-display Telegram Bot API failure."""


class TelegramBotClient(Protocol):
    def get_me(self) -> dict[str, object]: ...

    def get_webhook_info(self) -> dict[str, object]: ...

    def delete_webhook(self) -> None: ...

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
    ) -> tuple[dict[str, object], ...]: ...

    def send_message(self, chat_id: str, text: str) -> None: ...


@dataclass(frozen=True)
class TelegramPollingSettings:
    bot_token: str = field(repr=False)
    allowed_chat_ids: tuple[str, ...]
    bot_username: str
    next_update_offset: int | None = None
    gateway_name: str = "telegram"
    provider_name: str = "fake"

    @classmethod
    def load(cls, path: Path = DEFAULT_TELEGRAM_CONFIG) -> TelegramPollingSettings:
        data = read_json(path, default={})
        token = str(data.get("bot_token", "")).strip()
        username = str(data.get("bot_username", "")).strip()
        next_update_offset = data.get("next_update_offset")
        gateway_name = str(data.get("gateway_name", "telegram")).strip().casefold()
        provider_name = str(data.get("provider_name", "fake")).strip().casefold()
        if not token or not username:
            raise ValueError(
                "Telegram is not configured. Run reckoning setup first."
            )
        if gateway_name != "telegram":
            raise ValueError("The configured messaging gateway is not supported.")
        if provider_name not in CREDENTIAL_PROVIDER_NAMES + ("fake",):
            raise ValueError("The configured model provider is not supported.")
        if next_update_offset is not None and type(next_update_offset) is not int:
            raise ValueError("Telegram polling offset is invalid.")
        chat_ids: tuple[str, ...]
        if "paired_chat_id" in data:
            # Connector format v2: one paired private chat or none.
            paired = str(data.get("paired_chat_id") or "").strip()
            chat_ids = (paired,) if paired else ()
        else:
            raw_chat_ids = data.get("allowed_chat_ids", [])
            if not isinstance(raw_chat_ids, list):
                raise ValueError(
                    "Telegram is not configured. Run reckoning setup first."
                )
            chat_ids = tuple(
                str(item).strip() for item in raw_chat_ids if str(item).strip()
            )
        if not chat_ids:
            raise ValueError(
                "Telegram is verified but not paired; run reckoning setup or "
                "reckoning channel to pair an owner chat."
            )
        return cls(
            token,
            chat_ids,
            username,
            next_update_offset,
            gateway_name,
            provider_name,
        )

    def save(self, path: Path = DEFAULT_TELEGRAM_CONFIG) -> None:
        atomic_write_json(
            path,
            {
                "bot_token": self.bot_token,
                "allowed_chat_ids": list(self.allowed_chat_ids),
                "bot_username": self.bot_username,
                "next_update_offset": self.next_update_offset,
                "gateway_name": self.gateway_name,
                "provider_name": self.provider_name,
            },
        )
        path.chmod(0o600)


@dataclass(frozen=True)
class TelegramConnectorConfig:
    """Connector state as setup manages it; pairing may still be pending."""

    bot_token: str = field(repr=False)
    bot_username: str = ""
    paired_chat_id: str | None = None
    provider_name: str = "fake"

    @property
    def status(self) -> TelegramConnectorStatus:
        if self.paired_chat_id:
            return "ready"
        return "verified-not-paired"

    @classmethod
    def load(cls, path: Path = DEFAULT_TELEGRAM_CONFIG) -> TelegramConnectorConfig:
        data = read_json(path, default={})
        token = str(data.get("bot_token", "")).strip()
        username = str(data.get("bot_username", "")).strip()
        if not token or not username:
            raise ValueError("Telegram is not configured.")
        provider_name = str(data.get("provider_name", "fake")).strip().casefold()
        if "paired_chat_id" in data:
            paired = str(data.get("paired_chat_id") or "").strip() or None
        else:
            raw_chat_ids = data.get("allowed_chat_ids", [])
            chat_ids = (
                tuple(str(item).strip() for item in raw_chat_ids if str(item).strip())
                if isinstance(raw_chat_ids, list)
                else ()
            )
            paired = chat_ids[0] if chat_ids else None
        return cls(token, username, paired, provider_name)

    def save(self, path: Path = DEFAULT_TELEGRAM_CONFIG) -> None:
        atomic_write_json(
            path,
            {
                "schema_version": 2,
                "bot_token": self.bot_token,
                "bot_username": self.bot_username,
                "paired_chat_id": self.paired_chat_id,
                "gateway_name": "telegram",
                "provider_name": self.provider_name,
            },
        )
        path.chmod(0o600)

    def polling_settings(self) -> TelegramPollingSettings:
        if not self.paired_chat_id:
            raise ValueError(
                "Telegram is verified but not paired; pair an owner chat first."
            )
        return TelegramPollingSettings(
            self.bot_token,
            (self.paired_chat_id,),
            self.bot_username,
            provider_name=self.provider_name,
        )


def telegram_connector_status(
    path: Path = DEFAULT_TELEGRAM_CONFIG,
) -> TelegramConnectorStatus:
    if not path.exists():
        return "not-configured"
    try:
        return TelegramConnectorConfig.load(path).status
    except (ValueError, RuntimeError):
        return "error"


def verify_telegram_bot(
    bot_token: str,
    *,
    api_factory: Callable[[str], TelegramBotClient] | None = None,
) -> str:
    """Validate a bot token with getMe; returns the bot username."""
    token = bot_token.strip()
    if not token:
        raise ValueError("A Telegram bot token is required.")
    client = (api_factory or TelegramBotApi)(token)
    bot = client.get_me()
    username = str(bot.get("username", "")).strip()
    if not username:
        raise TelegramBotApiError("Telegram getMe did not return a bot username.")
    return username


def pair_telegram_owner(
    bot_token: str,
    *,
    pairing_code: str | None = None,
    maximum_polls: int = 12,
    api_factory: Callable[[str], TelegramBotClient] | None = None,
    output: Callable[[str], None] = print,
) -> str:
    """Wait for one matching private /connect message and confirm two-way access."""
    client = (api_factory or TelegramBotApi)(bot_token)
    webhook = client.get_webhook_info()
    if str(webhook.get("url", "")).strip():
        client.delete_webhook()
    code = pairing_code or secrets.token_hex(3)
    output(f"Open the bot in Telegram and send: /connect {code}")
    output("Waiting for that message for up to five minutes...")

    offset: int | None = None
    paired_chat_id = ""
    for _ in range(maximum_polls):
        updates = client.get_updates(offset=offset, timeout=25)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset or 0, update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict) or chat.get("type") != "private":
                continue
            if str(message.get("text", "")).strip() != f"/connect {code}":
                continue
            paired_chat_id = str(chat.get("id", "")).strip()
            if paired_chat_id:
                break
        if paired_chat_id:
            break
    if not paired_chat_id:
        raise OperationError(
            "No matching private /connect message arrived. The bot stays "
            "verified but not paired."
        )
    client.send_message(
        paired_chat_id,
        "Reckoning is connected. Return to the terminal and start the bot.",
    )
    return paired_chat_id


class TelegramBotApi:
    """Small Bot API client for the methods used by local polling."""

    def __init__(
        self,
        bot_token: str,
        *,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        token = bot_token.strip()
        if not token:
            raise ValueError("A Telegram bot token is required.")
        self._endpoint = f"{api_base_url.rstrip('/')}/bot{token}"

    def get_me(self) -> dict[str, object]:
        result = self._call("getMe", {})
        if not isinstance(result, dict):
            raise TelegramBotApiError("Telegram getMe returned an invalid result.")
        return result

    def get_webhook_info(self) -> dict[str, object]:
        result = self._call("getWebhookInfo", {})
        if not isinstance(result, dict):
            raise TelegramBotApiError(
                "Telegram getWebhookInfo returned an invalid result."
            )
        return result

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
    ) -> tuple[dict[str, object], ...]:
        payload: dict[str, object] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 5)
        if not isinstance(result, list) or not all(
            isinstance(item, dict) for item in result
        ):
            raise TelegramBotApiError("Telegram getUpdates returned an invalid result.")
        return tuple(result)

    def send_message(self, chat_id: str, text: str) -> None:
        chunks = _telegram_text_chunks(text)
        for chunk in chunks:
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def _call(
        self,
        method: str,
        payload: dict[str, object],
        *,
        timeout: int = 15,
    ) -> object:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self._endpoint}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                decoded = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise TelegramBotApiError(f"Telegram {method} request failed.") from None
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            description = (
                str(decoded.get("description", "")).strip()
                if isinstance(decoded, dict)
                else ""
            )
            message = f"Telegram {method} failed"
            if description:
                message = f"{message}: {description}"
            raise TelegramBotApiError(f"{message}.")
        return decoded.get("result")


def _telegram_text_chunks(text: str) -> tuple[str, ...]:
    content = text or "Reckoning returned an empty reply."
    return tuple(
        content[index : index + 4096] for index in range(0, len(content), 4096)
    )


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
            for item in required("RECKONING_TELEGRAM_ALLOWED_CHAT_IDS").split(",")
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

    def handle(
        self, authentication_token: str, request: TelegramRequest
    ) -> TelegramReply:
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


class TelegramUpdateAdapter:
    """Translate Telegram message updates into the bounded gateway contract."""

    def __init__(
        self,
        gateway: TelegramGateway,
        *,
        authentication_token: str,
    ) -> None:
        if not authentication_token:
            raise ValueError("A Telegram authentication token is required.")
        self._gateway = gateway
        self._authentication_token = authentication_token

    def handle_update(self, update: dict[str, Any]) -> TelegramReply:
        message = update.get("message")
        if not isinstance(message, dict):
            raise ValueError("Only Telegram message updates are supported.")
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            raise ValueError("A Telegram text message and chat are required.")
        chat_id = str(chat.get("id", "")).strip()
        command, separator, argument = text.strip().partition(" ")
        command = command.partition("@")[0]
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
        elif command in ("/start", "/help"):
            return TelegramReply(
                "message",
                self._authorize_help_chat(chat_id),
                (
                    "Send a message to talk with Reckoning. Use /status to check "
                    "the runtime or /confirm <id> to approve a pending action."
                ),
            )
        else:
            request = TelegramRequest("message", chat_id, text=text)
        return self._gateway.handle(self._authentication_token, request)

    def _authorize_help_chat(self, chat_id: str) -> str:
        status = self._gateway.handle(
            self._authentication_token,
            TelegramRequest("status", chat_id),
        )
        return status.chat_id


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
        self._updates = TelegramUpdateAdapter(
            gateway,
            authentication_token=authentication_token,
        )
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
        reply = self._updates.handle_update(update)
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


class TelegramPollingApplication:
    """Receive Telegram messages without exposing a public HTTP endpoint."""

    def __init__(
        self,
        adapter: TelegramUpdateAdapter,
        client: TelegramBotClient,
        *,
        poll_timeout: int = 30,
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._poll_timeout = poll_timeout

    def run_once(self, offset: int | None = None) -> int | None:
        updates = self._client.get_updates(
            offset=offset,
            timeout=self._poll_timeout,
        )
        next_offset = offset
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            next_offset = max(next_offset or 0, update_id + 1)
            try:
                reply = self._adapter.handle_update(update)
            except (KeyError, PermissionError, ValueError):
                continue
            self._client.send_message(reply.chat_id, reply.text)
        return next_offset

    def run_forever(
        self,
        *,
        bot_username: str,
        initial_offset: int | None = None,
        output: Callable[[str], None] = print,
    ) -> None:
        output(f"Reckoning is listening through @{bot_username}. Press Ctrl+C to stop.")
        offset = initial_offset
        while True:
            try:
                offset = self.run_once(offset)
            except TelegramBotApiError as error:
                output(str(error))
                time.sleep(2)


def setup_telegram_polling(
    *,
    config_path: Path = DEFAULT_TELEGRAM_CONFIG,
    secret_reader: Callable[[str], str] = getpass,
    line_reader: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    api_factory: Callable[[str], TelegramBotClient] = TelegramBotApi,
    pairing_code: str | None = None,
    maximum_polls: int = 12,
    gateway_name: str = "telegram",
    provider_name: str = "fake",
) -> TelegramPollingSettings:
    if config_path.exists():
        replace = line_reader(
            f"Telegram is already configured at {config_path}. Replace it? [y/N] "
        )
        if replace.strip().casefold() not in ("y", "yes"):
            raise OperationError("Telegram setup was not changed.")

    token = secret_reader("Paste the BotFather token (input is hidden): ").strip()
    if not token:
        raise ValueError("A Telegram bot token is required.")
    client = api_factory(token)
    bot = client.get_me()
    username = str(bot.get("username", "")).strip()
    if not username:
        raise TelegramBotApiError("Telegram getMe did not return a bot username.")

    webhook = client.get_webhook_info()
    if str(webhook.get("url", "")).strip():
        switch = line_reader(
            "This bot currently uses a webhook. Switch it to local polling? [y/N] "
        )
        if switch.strip().casefold() not in ("y", "yes"):
            raise OperationError("Telegram setup kept the existing webhook.")
        client.delete_webhook()

    code = pairing_code or secrets.token_hex(3)
    output(f"Bot verified: @{username}")
    output(f"Open the bot in Telegram and send: /connect {code}")
    output("Waiting for that message for up to five minutes...")

    offset: int | None = None
    paired_chat_id = ""
    for _ in range(maximum_polls):
        updates = client.get_updates(offset=offset, timeout=25)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset or 0, update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict) or chat.get("type") != "private":
                continue
            if str(message.get("text", "")).strip() != f"/connect {code}":
                continue
            paired_chat_id = str(chat.get("id", "")).strip()
            if paired_chat_id:
                break
        if paired_chat_id:
            break
    if not paired_chat_id:
        raise OperationError(
            "No matching private /connect message arrived. Telegram setup was not saved."
        )

    settings = TelegramPollingSettings(
        token,
        (paired_chat_id,),
        username,
        offset,
        gateway_name,
        provider_name,
    )
    settings.save(config_path)
    client.send_message(
        paired_chat_id,
        "Reckoning is connected. Return to the terminal and start the bot.",
    )
    output(f"Telegram setup saved to {config_path} with owner-only permissions.")
    return settings


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
            return self._response(
                start_response, "404 Not Found", {"error": "Not found."}
            )
        if method != "POST":
            return self._response(
                start_response,
                "405 Method Not Allowed",
                {"error": "Telegram webhooks require POST."},
            )
        try:
            secret_token = str(environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", ""))
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


def _create_interface(
    arguments: argparse.Namespace,
    *,
    configured_provider: str | None = None,
) -> ReckoningInterfaceApplication:
    runtime = load_installation_runtime(
        arguments.data_dir,
        server_data_dir=arguments.server_data_dir,
    )

    orcarouter_api_key: str | None = None
    deepseek_api_key: str | None = None
    model_name = arguments.model
    base_url = arguments.base_url
    provider_name = arguments.provider or configured_provider
    if provider_name == "orcarouter":
        orcarouter_settings = OrcaRouterSettings.load(
            credential_file=DEFAULT_PROVIDER_CREDENTIALS
        )
        orcarouter_api_key = orcarouter_settings.api_key
        model_name = model_name or orcarouter_settings.model
        base_url = base_url or orcarouter_settings.base_url
    elif provider_name == "deepseek":
        deepseek_settings = DeepSeekSettings.load(
            credential_file=DEFAULT_PROVIDER_CREDENTIALS
        )
        deepseek_api_key = deepseek_settings.api_key
        model_name = model_name or deepseek_settings.model
        base_url = base_url or deepseek_settings.base_url
    elif provider_name is None:
        orcarouter_settings = OrcaRouterSettings.load(
            credential_file=DEFAULT_PROVIDER_CREDENTIALS
        )
        deepseek_settings = DeepSeekSettings.load(
            credential_file=DEFAULT_PROVIDER_CREDENTIALS
        )
        settings_by_provider: dict[str, OrcaRouterSettings | DeepSeekSettings] = {
            "orcarouter": orcarouter_settings,
            "deepseek": deepseek_settings,
        }
        default_name = default_provider_name()
        candidates = (
            ((default_name,) if default_name else ())
            + ("orcarouter", "deepseek")
        )
        for candidate in candidates:
            candidate_settings = settings_by_provider[candidate]
            if not candidate_settings.api_key:
                continue
            provider_name = candidate
            if candidate == "orcarouter":
                orcarouter_api_key = candidate_settings.api_key
            else:
                deepseek_api_key = candidate_settings.api_key
            model_name = model_name or candidate_settings.model
            base_url = base_url or candidate_settings.base_url
            break
        else:
            provider_name = "fake"

    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        provider_name=provider_name or "fake",
        orcarouter_api_key=orcarouter_api_key,
        deepseek_api_key=deepseek_api_key,
        model_name=model_name,
        base_url=base_url,
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    return create_local_interface_application(
        application,
        runtime.state_path("confirmed-state", "interfaces.json"),
        placement=runtime.interface_placement,
        connector_data_dir=runtime.root_for("approved-remote-sources"),
    )


def main(argv: Sequence[str] | None = None, *, prog: str = "reckoning gateway") -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Run every configured channel in one process. Telegram is the "
            "available channel today; future channels attach here."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "webhook"),
        default="run",
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
    parser.add_argument(
        "--telegram-config",
        type=Path,
        default=DEFAULT_TELEGRAM_CONFIG,
        help="Local credential file used by Telegram polling.",
    )
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "webhook":
            host = validate_bind_host(arguments.host)
            webhook_settings = TelegramRuntimeSettings.load()
            interface = _create_interface(arguments)
        else:
            polling_settings = TelegramPollingSettings.load(arguments.telegram_config)
            interface = _create_interface(
                arguments,
                configured_provider=polling_settings.provider_name,
            )
    except (OperationError, RuntimeError, ValueError) as error:
        parser.error(str(error))

    gateway_token = (
        webhook_settings.gateway_token
        if arguments.command == "webhook"
        else secrets.token_urlsafe(32)
    )
    gateway = TelegramGateway(
        interface,
        authentication_token=gateway_token,
        allowed_chat_ids=(
            webhook_settings.allowed_chat_ids
            if arguments.command == "webhook"
            else polling_settings.allowed_chat_ids
        ),
    )
    if arguments.command == "webhook":
        webhook = TelegramWebhookApplication(
            TelegramWebhookAdapter(
                gateway,
                authentication_token=gateway_token,
                webhook_secret=webhook_settings.webhook_secret,
            )
        )
        with make_server(host, arguments.port, webhook) as server:
            print(f"Telegram webhook is listening on http://{host}:{arguments.port}")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nTelegram webhook stopped.")
        return 0

    polling = TelegramPollingApplication(
        TelegramUpdateAdapter(gateway, authentication_token=gateway_token),
        TelegramBotApi(polling_settings.bot_token),
    )
    try:
        polling.run_forever(
            bot_username=polling_settings.bot_username,
            initial_offset=polling_settings.next_update_offset,
        )
    except KeyboardInterrupt:
        print("\nTelegram polling stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
