from __future__ import annotations

import json
from pathlib import Path

import pytest

from reckoning.operations import OperationError
from reckoning.telegram import (
    TelegramConnectorConfig,
    pair_telegram_owner,
    telegram_connector_status,
    verify_telegram_bot,
)


class StubBotClient:
    def __init__(self, updates: tuple[dict[str, object], ...] = ()) -> None:
        self.updates = updates
        self.sent: list[tuple[str, str]] = []
        self.webhook_deleted = False

    def get_me(self) -> dict[str, object]:
        return {"id": 7, "username": "reckoning_test_bot"}

    def get_webhook_info(self) -> dict[str, object]:
        return {"url": ""}

    def delete_webhook(self) -> None:
        self.webhook_deleted = True

    def get_updates(
        self, *, offset: int | None, timeout: int
    ) -> tuple[dict[str, object], ...]:
        return self.updates

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


PAIRING_UPDATE = (
    {
        "update_id": 5,
        "message": {
            "chat": {"id": 42, "type": "private"},
            "text": "/connect abc123",
        },
    },
)


def test_status_is_not_configured_without_a_file(tmp_path: Path) -> None:
    assert telegram_connector_status(tmp_path / "telegram.json") == "not-configured"


def test_status_is_error_for_a_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "telegram.json"
    path.write_text("{not json", encoding="utf-8")
    assert telegram_connector_status(path) == "error"


def test_a_verified_unpaired_bot_saves_and_reports_its_state(tmp_path: Path) -> None:
    path = tmp_path / "telegram.json"
    config = TelegramConnectorConfig("token", "reckoning_test_bot", None, "fake")
    config.save(path)

    assert telegram_connector_status(path) == "verified-not-paired"
    loaded = TelegramConnectorConfig.load(path)
    assert loaded.bot_username == "reckoning_test_bot"
    assert loaded.paired_chat_id is None
    with pytest.raises(ValueError, match="not paired"):
        loaded.polling_settings()


def test_pairing_records_one_private_chat_and_confirms_two_way_access(
    tmp_path: Path,
) -> None:
    client = StubBotClient(PAIRING_UPDATE)

    chat_id = pair_telegram_owner(
        "token",
        pairing_code="abc123",
        maximum_polls=1,
        api_factory=lambda token: client,
        output=lambda line: None,
    )

    assert chat_id == "42"
    assert client.sent and client.sent[0][0] == "42"

    config = TelegramConnectorConfig("token", "reckoning_test_bot", chat_id, "fake")
    config.save(tmp_path / "telegram.json")
    assert telegram_connector_status(tmp_path / "telegram.json") == "ready"
    assert config.polling_settings().allowed_chat_ids == ("42",)


def test_pairing_ignores_group_chats_and_wrong_codes(tmp_path: Path) -> None:
    updates = (
        {
            "update_id": 1,
            "message": {
                "chat": {"id": 42, "type": "group"},
                "text": "/connect abc123",
            },
        },
        {
            "update_id": 2,
            "message": {
                "chat": {"id": 43, "type": "private"},
                "text": "/connect wrong",
            },
        },
    )
    client = StubBotClient(updates)

    with pytest.raises(OperationError, match="verified but not paired"):
        pair_telegram_owner(
            "token",
            pairing_code="abc123",
            maximum_polls=1,
            api_factory=lambda token: client,
            output=lambda line: None,
        )
    assert client.sent == []


def test_verify_telegram_bot_returns_the_username() -> None:
    username = verify_telegram_bot("token", api_factory=lambda token: StubBotClient())
    assert username == "reckoning_test_bot"


def test_legacy_polling_files_still_load_with_their_chat(tmp_path: Path) -> None:
    path = tmp_path / "telegram.json"
    path.write_text(
        json.dumps(
            {
                "bot_token": "token",
                "bot_username": "reckoning_test_bot",
                "allowed_chat_ids": ["42"],
                "gateway_name": "telegram",
                "provider_name": "fake",
            }
        ),
        encoding="utf-8",
    )
    assert telegram_connector_status(path) == "ready"
    assert TelegramConnectorConfig.load(path).paired_chat_id == "42"
