from __future__ import annotations

import json
import stat
import tomllib
from collections.abc import Iterable
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from reckoning.interfaces import (
    InMemoryInterfaceRepository,
    InterfaceState,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    SourcePlacement,
)
from reckoning.operational_records import LocalOperationalRecordSource
from reckoning.operations import create_transfer, restore_transfer
from reckoning.providers import ProviderFailure
from reckoning.telegram import (
    TelegramBotApi,
    TelegramBotApiError,
    TelegramGateway,
    TelegramPollingApplication,
    TelegramPollingSettings,
    TelegramRuntimeSettings,
    TelegramUpdateAdapter,
    TelegramWebhookAdapter,
    TelegramWebhookApplication,
    setup_telegram_polling,
)
from reckoning.telegram_delivery import SQLiteTelegramDeliveryRepository

NOW = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
PASSPHRASE = "correct-horse-battery-staple"


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


def test_telegram_runtime_runs_behind_the_gateway_command() -> None:
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = configuration["project"]["scripts"]

    assert "reckoning-telegram" not in scripts
    assert "reckoning-ops" not in scripts
    assert scripts == {"reckoning": "reckoning.command:main"}


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


def test_polling_uses_the_bounded_gateway_and_acknowledges_denied_chats(
    tmp_path: Path,
) -> None:
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
        repository=SQLiteTelegramDeliveryRepository(
            tmp_path / "confirmed-state" / "telegram-delivery.json"
        ),
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


def test_setup_offset_adoption_polls_and_delivers_first_unseen_message(
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
    repository = SQLiteTelegramDeliveryRepository(
        tmp_path / "confirmed-state" / "telegram-delivery.json"
    )
    polling = TelegramPollingApplication(
        TelegramUpdateAdapter(gateway, authentication_token="internal"),
        client,
        repository=repository,
        poll_timeout=1,
    )

    polling.run_once(settings.next_update_offset)

    assert client.polls[-1] == (6, 1)
    assert client.sent == [
        ("42", "Reckoning is connected. Return to the terminal and start the bot."),
        ("42", "Use the smaller proof first."),
    ]
    assert [record.update_id for record in repository.list_updates()] == [6]
    assert repository.get_update(6).delivery_state == "delivered"


class ScriptedUpdateAdapter:
    def __init__(self, provider_failures: dict[int, int] | None = None) -> None:
        self.provider_failures = dict(provider_failures or {})
        self.calls: list[int] = []

    def handle_update(self, update: dict[str, object]) -> object:
        update_id = int(str(update["update_id"]))
        self.calls.append(update_id)
        remaining = self.provider_failures.get(update_id, 0)
        if remaining:
            self.provider_failures[update_id] = remaining - 1
            failure = ProviderFailure(
                "temporary outage",
                provider="fake",
                model="fake-model",
                model_calls=1,
                latency_ms=5,
                retries=0,
            )
            raise RuntimeError("The fake run failed. temporary outage") from failure
        message = update["message"]
        assert isinstance(message, dict)
        chat = message["chat"]
        assert isinstance(chat, dict)
        from reckoning.telegram import TelegramReply

        return TelegramReply(
            "message",
            str(chat["id"]),
            f"reply for {update_id}",
        )


class DefectiveUpdateAdapter:
    def handle_update(self, update: dict[str, object]) -> object:
        del update
        raise TypeError("programming defect")


class DeliveryScriptClient(RecordingTelegramClient):
    def __init__(
        self,
        update_batches: list[tuple[dict[str, object], ...]],
        outcomes: list[str] | None = None,
    ) -> None:
        super().__init__(update_batches)
        self.outcomes = list(outcomes or [])
        self.send_attempts: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.send_attempts.append((chat_id, text))
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if outcome == "known-failure":
            raise TelegramBotApiError("Telegram rejected the message.")
        if outcome == "unknown":
            raise TelegramBotApiError(
                "Telegram sendMessage outcome is unknown.",
                outcome_unknown=True,
            )
        self.sent.append((chat_id, text))


def update(update_id: int, text: str = "What next?") -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": 42}, "text": text},
    }


def test_send_message_treats_server_error_as_an_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_send(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise HTTPError(
            "https://api.telegram.org/synthetic",
            500,
            "Server Error",
            Message(),
            None,
        )

    monkeypatch.setattr("reckoning.telegram.urlopen", fail_send)
    api = TelegramBotApi("synthetic-token")

    with pytest.raises(TelegramBotApiError) as captured:
        api.send_message("42", "reply")

    assert captured.value.outcome_unknown is True


class PartialChunkBotApi(TelegramBotApi):
    def __init__(self) -> None:
        super().__init__("synthetic-token")
        self.calls = 0

    def _call(
        self,
        method: str,
        payload: dict[str, object],
        *,
        timeout: int = 15,
    ) -> object:
        del method, payload, timeout
        self.calls += 1
        if self.calls == 2:
            raise TelegramBotApiError("Telegram rejected the second chunk.")
        return {}


def test_partial_chunk_delivery_becomes_unknown() -> None:
    api = PartialChunkBotApi()

    with pytest.raises(TelegramBotApiError) as captured:
        api.send_message("42", "x" * 4097)

    assert api.calls == 2
    assert captured.value.outcome_unknown is True


def test_provider_outage_preserves_the_batch_and_later_updates(
    tmp_path: Path,
) -> None:
    repository = SQLiteTelegramDeliveryRepository(
        tmp_path / "confirmed-state" / "telegram-delivery.json"
    )
    adapter = ScriptedUpdateAdapter({10: 1})
    client = DeliveryScriptClient([(update(10), update(11))])
    polling = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        client,
        repository=repository,
        poll_timeout=1,
        worker_id="worker-one",
        clock=lambda: NOW,
    )

    assert polling.run_once() == 12

    records = {record.update_id: record for record in repository.list_updates()}
    assert records[10].processing_state == "provider-failed"
    assert records[11].delivery_state == "delivered"
    assert client.sent == [("42", "reply for 11")]

    restarted_client = DeliveryScriptClient([()])
    restarted = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        restarted_client,
        repository=SQLiteTelegramDeliveryRepository(
            tmp_path / "confirmed-state" / "telegram-delivery.json"
        ),
        poll_timeout=1,
        worker_id="worker-two",
        clock=lambda: NOW,
    )

    assert restarted.run_once() == 12
    assert adapter.calls == [10, 11, 10]
    assert restarted_client.sent == [("42", "reply for 10")]
    assert restarted_client.polls == [(12, 1)]


def test_duplicate_and_ambiguous_delivery_reuse_the_completed_reply(
    tmp_path: Path,
) -> None:
    path = tmp_path / "confirmed-state" / "telegram-delivery.json"
    adapter = ScriptedUpdateAdapter()
    first_client = DeliveryScriptClient([(update(20),)], ["unknown"])
    first = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        first_client,
        repository=SQLiteTelegramDeliveryRepository(path),
        worker_id="worker-one",
        clock=lambda: NOW,
    )

    first.run_once()
    record = SQLiteTelegramDeliveryRepository(path).get_update(20)
    assert record.processing_state == "completed"
    assert record.delivery_state == "unknown"
    assert adapter.calls == [20]

    duplicate_client = DeliveryScriptClient([(update(20),)])
    restarted = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        duplicate_client,
        repository=SQLiteTelegramDeliveryRepository(path),
        worker_id="worker-two",
        clock=lambda: NOW,
    )
    restarted.run_once()

    assert adapter.calls == [20]
    assert duplicate_client.send_attempts == []
    with pytest.raises(ValueError, match="possible duplicate"):
        restarted.retry_unknown_delivery(20, acknowledge_possible_duplicate=False)

    retry = restarted.retry_unknown_delivery(
        20,
        acknowledge_possible_duplicate=True,
    )
    assert retry.delivery_state == "delivered"
    assert duplicate_client.send_attempts == [("42", "reply for 20")]
    assert adapter.calls == [20]


def test_competing_claim_requires_an_explicit_recovery_transition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "confirmed-state" / "telegram-delivery.json"
    first = SQLiteTelegramDeliveryRepository(path)
    second = SQLiteTelegramDeliveryRepository(path)
    first.accept_updates((update(30),), accepted_at=NOW)

    assert first.claim_processing(30, "worker-one", claimed_at=NOW) is not None
    assert second.claim_processing(30, "worker-two", claimed_at=NOW) is None
    with pytest.raises(ValueError, match="model work may have run"):
        second.recover_processing(
            30,
            recovered_at=NOW,
            acknowledge_possible_model_repeat=False,
        )

    second.recover_processing(
        30,
        recovered_at=NOW,
        acknowledge_possible_model_repeat=True,
    )
    claimed = second.claim_processing(30, "worker-two", claimed_at=NOW)
    assert claimed is not None
    assert claimed.claimed_by == "worker-two"


def test_known_delivery_failure_retries_the_same_reply_within_limit(
    tmp_path: Path,
) -> None:
    adapter = ScriptedUpdateAdapter()
    client = DeliveryScriptClient(
        [(update(40),)],
        ["known-failure", "known-failure", "success"],
    )
    repository = SQLiteTelegramDeliveryRepository(
        tmp_path / "confirmed-state" / "telegram-delivery.json"
    )
    polling = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        client,
        repository=repository,
        worker_id="worker-one",
        clock=lambda: NOW,
        delivery_retry_limit=3,
    )

    polling.run_once()

    assert client.send_attempts == [("42", "reply for 40")] * 3
    assert adapter.calls == [40]
    record = repository.get_update(40)
    assert record.delivery_state == "delivered"
    assert record.delivery_attempts == 3


def test_programming_defect_is_not_swallowed_or_retried(
    tmp_path: Path,
) -> None:
    repository = SQLiteTelegramDeliveryRepository(
        tmp_path / "confirmed-state" / "telegram-delivery.json"
    )
    polling = TelegramPollingApplication(
        DefectiveUpdateAdapter(),  # type: ignore[arg-type]
        DeliveryScriptClient([(update(50),)]),
        repository=repository,
        worker_id="worker-one",
        clock=lambda: NOW,
    )

    with pytest.raises(TypeError, match="programming defect"):
        polling.run_once()

    record = repository.get_update(50)
    assert record.processing_state == "processing-unknown"
    assert record.processing_failure is not None
    assert "programming defect" in record.processing_failure


def test_telegram_state_survives_restart_and_clean_restore(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "telegram.reckoning"
    path = source / "confirmed-state" / "telegram-delivery.json"
    repository = SQLiteTelegramDeliveryRepository(path)
    repository.accept_updates(
        (update(60), update(61), update(62)), accepted_at=NOW
    )
    for update_id in (60, 61, 62):
        assert repository.claim_processing(
            update_id, "worker-one", claimed_at=NOW
        )
        repository.complete_processing(
            update_id,
            "worker-one",
            reply_chat_id="42",
            reply_text=f"saved reply {update_id}",
            completed_at=NOW,
        )
    assert repository.claim_delivery(
        61,
        "worker-one",
        claimed_at=NOW,
        maximum_attempts=3,
    )
    repository.fail_delivery(
        61,
        "worker-one",
        failure="Synthetic send outcome is unknown.",
        failed_at=NOW,
        outcome_unknown=True,
    )
    assert repository.claim_delivery(
        62,
        "worker-one",
        claimed_at=NOW,
        maximum_attempts=3,
    )
    repository.complete_delivery(62, "worker-one", delivered_at=NOW)

    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restore_transfer(archive, restored, PASSPHRASE)

    restored_path = restored / "confirmed-state" / "telegram-delivery.json"
    restored_repository = SQLiteTelegramDeliveryRepository(restored_path)
    assert restored_repository.next_offset() == 63
    assert restored_repository.get_update(60).delivery_state == "pending"
    assert restored_repository.get_update(61).delivery_state == "unknown"
    assert restored_repository.get_update(62).delivery_state == "delivered"
    restored_client = DeliveryScriptClient([()])
    restarted = TelegramPollingApplication(
        ScriptedUpdateAdapter(),  # type: ignore[arg-type]
        restored_client,
        repository=restored_repository,
        worker_id="worker-two",
        clock=lambda: NOW,
    )
    restarted.run_once()

    assert restored_client.polls == [(63, 30)]
    assert restored_client.sent == [("42", "saved reply 60")]
    restored_record = restored_repository.get_update(60)
    assert restored_record.update_id == 60
    assert restored_record.delivery_state == "delivered"
    assert restored_repository.get_update(61).delivery_state == "unknown"


def test_unknown_delivery_is_visible_in_operational_inspection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    path = root / "confirmed-state" / "telegram-delivery.json"
    polling = TelegramPollingApplication(
        ScriptedUpdateAdapter(),  # type: ignore[arg-type]
        DeliveryScriptClient([(update(70),)], ["unknown"]),
        repository=SQLiteTelegramDeliveryRepository(path),
        worker_id="worker-one",
        clock=lambda: NOW,
    )
    polling.run_once()

    snapshot = LocalOperationalRecordSource(root / "confirmed-state").snapshot()

    assert any(
        "telegram update 70: delivery-unknown" in value
        for value in snapshot.connector_health
    )
    assert any("outcome is unknown" in failure.summary for failure in snapshot.failures)
