from __future__ import annotations

import json
import stat
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from reckoning.config import (
    DeepSeekSettings,
    OrcaRouterSettings,
    ProviderCredentialStore,
)
from reckoning.operations import OperationError
from reckoning.providers import (
    ProviderKeyVerificationError,
    verify_provider_api_key,
)
from reckoning.setup import setup_reckoning
from reckoning.telegram import TelegramPollingSettings


def seed_management_install(
    tmp_path: Path,
    providers: dict[str, str],
    default: str,
) -> None:
    store = ProviderCredentialStore()
    for name, key in providers.items():
        store.set_key(name, key)
    store.default_provider = default
    store.save(tmp_path / "provider.json")
    TelegramPollingSettings(
        "bot-token",
        ("42",),
        "reckoning_test_bot",
        provider_name=default,
    ).save(tmp_path / "telegram.json")


class PairingTelegramClient:
    def __init__(self, updates: tuple[dict[str, object], ...]) -> None:
        self.updates = updates

    def get_me(self) -> dict[str, object]:
        return {"id": 7, "username": "reckoning_test_bot"}

    def get_webhook_info(self) -> dict[str, object]:
        return {"url": ""}

    def delete_webhook(self) -> None:
        raise AssertionError("No webhook is configured.")

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
    ) -> tuple[dict[str, object], ...]:
        return self.updates

    def send_message(self, chat_id: str, text: str) -> None:
        pass


PAIRED_UPDATE = (
    {
        "update_id": 5,
        "message": {
            "chat": {"id": 42, "type": "private"},
            "text": "/connect abc123",
        },
    },
)


class RecordingKeyVerifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, provider_name: str, api_key: str) -> None:
        self.calls.append((provider_name, api_key))
        if self.error is not None:
            raise self.error


class SequenceKeyVerifier:
    """Plays back one verification outcome per call."""

    def __init__(self, outcomes: tuple[Exception | None, ...]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, provider_name: str, api_key: str) -> None:
        self.calls.append((provider_name, api_key))
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


def run_setup(
    tmp_path: Path,
    answers: tuple[str, ...],
    secrets: tuple[str, ...],
    *,
    verifier: Callable[[str, str], None],
    updates: tuple[dict[str, object], ...] = PAIRED_UPDATE,
):
    line_answers = iter(answers)
    secret_answers = iter(secrets)
    secret_prompts: list[str] = []
    messages: list[str] = []

    def read_secret(prompt: str) -> str:
        secret_prompts.append(prompt)
        return next(secret_answers)

    settings = setup_reckoning(
        config_path=tmp_path / "telegram.json",
        credentials_path=tmp_path / "provider.json",
        secret_reader=read_secret,
        line_reader=lambda prompt: next(line_answers),
        output=messages.append,
        api_factory=lambda token: PairingTelegramClient(updates),
        key_verifier=verifier,
        pairing_code="abc123",
        maximum_polls=1,
    )
    return settings, secret_prompts, messages


def test_setup_verifies_and_saves_a_deepseek_key_with_owner_only_permissions(
    tmp_path: Path,
) -> None:
    verifier = RecordingKeyVerifier()

    settings, secret_prompts, messages = run_setup(
        tmp_path,
        ("1", "2", "n"),
        ("sk-deepseek-test", "bot-token"),
        verifier=verifier,
    )

    assert verifier.calls == [("deepseek", "sk-deepseek-test")]
    assert secret_prompts == [
        "Paste the DeepSeek API key (input is hidden): ",
        "Paste the BotFather token (input is hidden): ",
    ]
    assert settings.provider_name == "deepseek"

    credentials_path = tmp_path / "provider.json"
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
    assert json.loads(credentials_path.read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "sk-deepseek-test"},
    }
    displayed = "\n".join(messages)
    assert "DeepSeek verified the API key." in displayed
    assert f"Provider credentials saved to {credentials_path}" in displayed

    loaded = DeepSeekSettings.load(
        tmp_path / "missing.env",
        environ={},
        credential_file=credentials_path,
    )
    assert loaded.api_key == "sk-deepseek-test"


def test_setup_verifies_and_saves_an_orcarouter_key(tmp_path: Path) -> None:
    verifier = RecordingKeyVerifier()

    settings, _, _ = run_setup(
        tmp_path,
        ("1", "3", "n"),
        ("orca-key", "bot-token"),
        verifier=verifier,
    )

    assert verifier.calls == [("orcarouter", "orca-key")]
    assert settings.provider_name == "orcarouter"
    loaded = OrcaRouterSettings.load(
        tmp_path / "missing.env",
        environ={},
        credential_file=tmp_path / "provider.json",
    )
    assert loaded.api_key == "orca-key"


def test_setup_configures_two_providers_in_one_session_with_an_explicit_default(
    tmp_path: Path,
) -> None:
    verifier = RecordingKeyVerifier()

    settings, _, messages = run_setup(
        tmp_path,
        ("1", "2", "y", "3", "n", "2"),
        ("sk-deepseek-test", "orca-key", "bot-token"),
        verifier=verifier,
    )

    assert verifier.calls == [
        ("deepseek", "sk-deepseek-test"),
        ("orcarouter", "orca-key"),
    ]
    assert settings.provider_name == "orcarouter"

    credentials_path = tmp_path / "provider.json"
    assert json.loads(credentials_path.read_text(encoding="utf-8")) == {
        "default_provider": "orcarouter",
        "providers": {
            "deepseek": "sk-deepseek-test",
            "orcarouter": "orca-key",
        },
    }
    displayed = "\n".join(messages)
    assert "Add another provider? [y/N]" in displayed
    assert "DeepSeek (configured)" in displayed
    assert "Which provider is the default?" in displayed

    orcarouter = OrcaRouterSettings.load(
        tmp_path / "missing.env", environ={}, credential_file=credentials_path
    )
    deepseek = DeepSeekSettings.load(
        tmp_path / "missing.env", environ={}, credential_file=credentials_path
    )
    assert orcarouter.api_key == "orca-key"
    assert deepseek.api_key == "sk-deepseek-test"


def test_setup_with_a_single_provider_does_not_ask_for_the_default(
    tmp_path: Path,
) -> None:
    verifier = RecordingKeyVerifier()

    settings, _, messages = run_setup(
        tmp_path,
        ("1", "2", "n"),
        ("sk-deepseek-test", "bot-token"),
        verifier=verifier,
    )

    assert settings.provider_name == "deepseek"
    assert "Which provider is the default?" not in "\n".join(messages)


def test_setup_can_default_to_fake_after_configuring_a_real_provider(
    tmp_path: Path,
) -> None:
    verifier = RecordingKeyVerifier()

    settings, secret_prompts, _ = run_setup(
        tmp_path,
        ("1", "1", "y", "2", "n", "1"),
        ("sk-deepseek-test", "bot-token"),
        verifier=verifier,
    )

    assert verifier.calls == [("deepseek", "sk-deepseek-test")]
    assert secret_prompts == [
        "Paste the DeepSeek API key (input is hidden): ",
        "Paste the BotFather token (input is hidden): ",
    ]
    assert settings.provider_name == "fake"
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "sk-deepseek-test"},
    }


def test_setup_with_only_fake_needs_no_key_and_saves_no_credentials(
    tmp_path: Path,
) -> None:
    verifier = RecordingKeyVerifier()

    settings, secret_prompts, _ = run_setup(
        tmp_path,
        ("1", "1", "n"),
        ("bot-token",),
        verifier=verifier,
    )

    assert verifier.calls == []
    assert secret_prompts == ["Paste the BotFather token (input is hidden): "]
    assert settings.provider_name == "fake"
    assert not (tmp_path / "provider.json").exists()


REJECTED_KEY = ProviderKeyVerificationError(
    "DeepSeek rejected the API key. Check the key and run setup again."
)


def test_setup_retries_a_rejected_key_and_verifies_the_next_one(
    tmp_path: Path,
) -> None:
    verifier = SequenceKeyVerifier((REJECTED_KEY, None))

    settings, secret_prompts, messages = run_setup(
        tmp_path,
        ("1", "2", "1", "n"),
        ("bad-key", "good-key", "bot-token"),
        verifier=verifier,
    )

    assert verifier.calls == [("deepseek", "bad-key"), ("deepseek", "good-key")]
    assert secret_prompts == [
        "Paste the DeepSeek API key (input is hidden): ",
        "Paste the DeepSeek API key (input is hidden): ",
        "Paste the BotFather token (input is hidden): ",
    ]
    displayed = "\n".join(messages)
    assert "DeepSeek rejected the API key." in displayed
    assert "Re-enter the API key" in displayed
    assert "Save the key anyway" in displayed
    assert "Abort setup" in displayed
    assert settings.provider_name == "deepseek"
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "good-key"},
    }


def test_setup_can_save_a_key_the_provider_could_not_verify(
    tmp_path: Path,
) -> None:
    verifier = SequenceKeyVerifier((REJECTED_KEY,))

    settings, _, messages = run_setup(
        tmp_path,
        ("1", "2", "2", "n"),
        ("unverified-key", "bot-token"),
        verifier=verifier,
    )

    assert verifier.calls == [("deepseek", "unverified-key")]
    assert "unverified" in "\n".join(messages)
    assert settings.provider_name == "deepseek"
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "unverified-key"},
    }


def test_setup_abort_after_a_rejected_key_leaves_nothing_written(
    tmp_path: Path,
) -> None:
    verifier = SequenceKeyVerifier((REJECTED_KEY,))

    with pytest.raises(OperationError, match="[Aa]borted"):
        run_setup(
            tmp_path,
            ("1", "2", "3"),
            ("bad-key",),
            verifier=verifier,
        )

    assert not (tmp_path / "provider.json").exists()
    assert not (tmp_path / "telegram.json").exists()


def test_setup_requires_a_non_empty_api_key(tmp_path: Path) -> None:
    verifier = RecordingKeyVerifier()

    with pytest.raises(ValueError, match="A DeepSeek API key is required."):
        run_setup(
            tmp_path,
            ("1", "2", "n"),
            ("   ", "bot-token"),
            verifier=verifier,
        )

    assert verifier.calls == []
    assert not (tmp_path / "provider.json").exists()


def test_an_aborted_pairing_leaves_no_partial_credential(tmp_path: Path) -> None:
    verifier = RecordingKeyVerifier()

    with pytest.raises(OperationError, match="Telegram setup was not saved"):
        run_setup(
            tmp_path,
            ("1", "2", "n"),
            ("sk-deepseek-test", "bot-token"),
            verifier=verifier,
            updates=(),
        )

    assert verifier.calls == [("deepseek", "sk-deepseek-test")]
    assert not (tmp_path / "provider.json").exists()
    assert not (tmp_path / "telegram.json").exists()


def test_verify_provider_api_key_accepts_a_key_the_provider_recognizes() -> None:
    requests: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        requests.append(request)
        return b'{"data": []}'

    verify_provider_api_key("deepseek", "sk-good", transport=transport)

    (request,) = requests
    assert request.full_url == "https://api.deepseek.com/models"
    assert request.headers["Authorization"] == "Bearer sk-good"
    assert request.get_method() == "GET"


def test_verify_provider_api_key_explains_a_rejected_key() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(
        ProviderKeyVerificationError,
        match="OrcaRouter rejected the API key",
    ):
        verify_provider_api_key("orcarouter", "bad-key", transport=transport)


def test_verify_provider_api_key_explains_other_failures() -> None:
    def failing_http(request: Request, timeout: float) -> bytes:
        raise HTTPError(request.full_url, 500, "Server Error", {}, None)

    with pytest.raises(
        ProviderKeyVerificationError,
        match="DeepSeek returned HTTP 500 during API-key verification",
    ):
        verify_provider_api_key("deepseek", "key", transport=failing_http)

    def unreachable(request: Request, timeout: float) -> bytes:
        raise URLError("offline")

    with pytest.raises(
        ProviderKeyVerificationError,
        match="DeepSeek could not be reached: offline",
    ):
        verify_provider_api_key("deepseek", "key", transport=unreachable)


def test_verify_provider_api_key_rejects_an_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported model provider: fake"):
        verify_provider_api_key("fake", "key", transport=lambda r, t: b"")


def test_management_menu_adds_a_provider_and_keeps_the_default_on_enter(
    tmp_path: Path,
) -> None:
    seed_management_install(tmp_path, {"deepseek": "old-key"}, "deepseek")
    verifier = RecordingKeyVerifier()

    settings, _, messages = run_setup(
        tmp_path,
        ("1", "3", "n", "", "5"),
        ("orca-key",),
        verifier=verifier,
    )

    assert verifier.calls == [("orcarouter", "orca-key")]
    displayed = "\n".join(messages)
    assert "What do you want to change?" in displayed
    assert "DeepSeek (configured)" in displayed
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "old-key", "orcarouter": "orca-key"},
    }
    assert settings.provider_name == "deepseek"


def test_management_menu_replaces_a_key_with_live_verification(
    tmp_path: Path,
) -> None:
    seed_management_install(tmp_path, {"deepseek": "old-key"}, "deepseek")
    verifier = RecordingKeyVerifier()

    run_setup(
        tmp_path,
        ("2", "1", "5"),
        ("new-key",),
        verifier=verifier,
    )

    assert verifier.calls == [("deepseek", "new-key")]
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "new-key"},
    }


def test_management_menu_replace_keeps_the_current_key_on_enter(
    tmp_path: Path,
) -> None:
    seed_management_install(tmp_path, {"deepseek": "old-key"}, "deepseek")
    verifier = RecordingKeyVerifier()

    run_setup(
        tmp_path,
        ("2", "1", "5"),
        ("",),
        verifier=verifier,
    )

    assert verifier.calls == []
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "deepseek",
        "providers": {"deepseek": "old-key"},
    }


def test_management_menu_removing_the_default_reassigns_the_default(
    tmp_path: Path,
) -> None:
    seed_management_install(
        tmp_path,
        {"deepseek": "sk-deepseek", "orcarouter": "sk-orca"},
        "deepseek",
    )
    verifier = RecordingKeyVerifier()

    settings, _, _ = run_setup(
        tmp_path,
        ("3", "1", "5"),
        (),
        verifier=verifier,
    )

    assert verifier.calls == []
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "orcarouter",
        "providers": {"orcarouter": "sk-orca"},
    }
    assert settings.provider_name == "orcarouter"
    assert (
        TelegramPollingSettings.load(tmp_path / "telegram.json").provider_name
        == "orcarouter"
    )


def test_management_menu_changes_the_default_provider(tmp_path: Path) -> None:
    seed_management_install(
        tmp_path,
        {"deepseek": "sk-deepseek", "orcarouter": "sk-orca"},
        "deepseek",
    )
    verifier = RecordingKeyVerifier()

    settings, _, _ = run_setup(
        tmp_path,
        ("4", "2", "5"),
        (),
        verifier=verifier,
    )

    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "orcarouter",
        "providers": {"deepseek": "sk-deepseek", "orcarouter": "sk-orca"},
    }
    assert settings.provider_name == "orcarouter"


def test_management_menu_done_keeps_everything_on_enter(tmp_path: Path) -> None:
    seed_management_install(
        tmp_path,
        {"deepseek": "sk-deepseek", "orcarouter": "sk-orca"},
        "orcarouter",
    )
    verifier = RecordingKeyVerifier()

    settings, _, _ = run_setup(
        tmp_path,
        ("",),
        (),
        verifier=verifier,
    )

    assert verifier.calls == []
    assert json.loads((tmp_path / "provider.json").read_text(encoding="utf-8")) == {
        "default_provider": "orcarouter",
        "providers": {"deepseek": "sk-deepseek", "orcarouter": "sk-orca"},
    }
    assert settings.provider_name == "orcarouter"


def test_management_mode_without_telegram_config_runs_pairing(
    tmp_path: Path,
) -> None:
    seed_management_install(tmp_path, {"deepseek": "sk-deepseek"}, "deepseek")
    (tmp_path / "telegram.json").unlink()
    verifier = RecordingKeyVerifier()

    settings, _, _ = run_setup(
        tmp_path,
        ("5",),
        ("bot-token",),
        verifier=verifier,
    )

    assert settings.provider_name == "deepseek"
    assert TelegramPollingSettings.load(tmp_path / "telegram.json") == settings
