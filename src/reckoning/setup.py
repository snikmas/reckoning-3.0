from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from reckoning.config import (
    DEFAULT_PROVIDER_CREDENTIALS,
    ProviderCredentialStore,
)
from reckoning.providers import verify_provider_api_key
from reckoning.telegram import (
    DEFAULT_TELEGRAM_CONFIG,
    TelegramBotApi,
    TelegramBotClient,
    TelegramPollingSettings,
    setup_telegram_polling,
)


@dataclass(frozen=True)
class SetupMenuOption:
    name: str
    label: str
    available: bool
    unavailable_message: str = ""


GATEWAY_SETUP_OPTIONS = (
    SetupMenuOption("telegram", "Telegram", True),
    SetupMenuOption(
        "discord",
        "Discord (coming later)",
        False,
        "Discord is not available yet.",
    ),
    SetupMenuOption(
        "whatsapp",
        "WhatsApp (coming later)",
        False,
        "WhatsApp is not available yet.",
    ),
    SetupMenuOption(
        "slack",
        "Slack (coming later)",
        False,
        "Slack is not available yet.",
    ),
)

PROVIDER_SETUP_OPTIONS = (
    SetupMenuOption("fake", "Fake (no API key)", True),
    SetupMenuOption("deepseek", "DeepSeek", True),
    SetupMenuOption("orcarouter", "OrcaRouter", True),
)

_PROVIDER_DISPLAY_NAMES = {
    "deepseek": "DeepSeek",
    "orcarouter": "OrcaRouter",
}


def _choose_setup_option(
    heading: str,
    prompt: str,
    options: tuple[SetupMenuOption, ...],
    *,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    output(heading)
    for index, option in enumerate(options, start=1):
        output(f"  {index}. {option.label}")

    while True:
        raw_choice = line_reader(prompt).strip() or "1"
        try:
            choice = int(raw_choice)
        except ValueError:
            output(f"Enter a number from 1 to {len(options)}.")
            continue
        if choice < 1 or choice > len(options):
            output(f"Enter a number from 1 to {len(options)}.")
            continue
        option = options[choice - 1]
        if not option.available:
            output(option.unavailable_message)
            continue
        return option.name


def setup_reckoning(
    *,
    config_path: Path = DEFAULT_TELEGRAM_CONFIG,
    credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    secret_reader: Callable[[str], str] = getpass,
    line_reader: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    api_factory: Callable[[str], TelegramBotClient] = TelegramBotApi,
    key_verifier: Callable[[str, str], None] = verify_provider_api_key,
    pairing_code: str | None = None,
    maximum_polls: int = 12,
) -> TelegramPollingSettings:
    output("Reckoning setup")
    gateway_name = _choose_setup_option(
        "Choose a gateway:",
        "Gateway [1]: ",
        GATEWAY_SETUP_OPTIONS,
        line_reader=line_reader,
        output=output,
    )
    provider_name = _choose_setup_option(
        "Choose a provider:",
        "Provider [1]: ",
        PROVIDER_SETUP_OPTIONS,
        line_reader=line_reader,
        output=output,
    )
    store: ProviderCredentialStore | None = None
    if provider_name == "fake":
        output(
            "Fake uses deterministic local replies, so it does not need an API key."
        )
    else:
        store = _enter_provider_credentials(
            provider_name,
            secret_reader=secret_reader,
            key_verifier=key_verifier,
        )
        output(f"{_PROVIDER_DISPLAY_NAMES[provider_name]} verified the API key.")
    settings = setup_telegram_polling(
        config_path=config_path,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        api_factory=api_factory,
        pairing_code=pairing_code,
        maximum_polls=maximum_polls,
        gateway_name=gateway_name,
        provider_name=provider_name,
    )
    if store is not None:
        store.save(credentials_path)
        output(
            f"Provider credentials saved to {credentials_path} "
            "with owner-only permissions."
        )
    return settings


def _enter_provider_credentials(
    provider_name: str,
    *,
    secret_reader: Callable[[str], str],
    key_verifier: Callable[[str, str], None],
) -> ProviderCredentialStore:
    display_name = _PROVIDER_DISPLAY_NAMES[provider_name]
    api_key = secret_reader(
        f"Paste the {display_name} API key (input is hidden): "
    ).strip()
    if not api_key:
        raise ValueError(f"A {display_name} API key is required.")
    key_verifier(provider_name, api_key)
    store = ProviderCredentialStore()
    store.set_key(provider_name, api_key, make_default=True)
    return store
