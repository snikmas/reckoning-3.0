from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Any, Literal

from reckoning.interfaces import ReckoningInterfaceApplication


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
        if not compare_digest(secret_token, self._webhook_secret):
            raise PermissionError("Telegram webhook authentication failed.")
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
