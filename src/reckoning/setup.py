from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from reckoning.config import (
    DEFAULT_PROVIDER_CREDENTIALS,
    ProviderCredentialStore,
)
from reckoning.operations import OperationError
from reckoning.providers import (
    ProviderKeyVerificationError,
    verify_provider_api_key,
)
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

VERIFICATION_RECOVERY_OPTIONS = (
    SetupMenuOption("retry", "Re-enter the API key", True),
    SetupMenuOption(
        "save",
        "Save the key anyway (the verification endpoint may be down)",
        True,
    ),
    SetupMenuOption("abort", "Abort setup without saving anything", True),
)


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


def _confirm(
    question: str,
    *,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> bool:
    output(question)
    return line_reader("").strip().casefold() in ("y", "yes")


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
    store = ProviderCredentialStore()
    provider_name = _run_provider_loop(
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        key_verifier=key_verifier,
        store=store,
    )
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
    if store.providers:
        store.save(credentials_path)
        output(
            f"Provider credentials saved to {credentials_path} "
            "with owner-only permissions."
        )
    return settings


def _run_provider_loop(
    *,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    key_verifier: Callable[[str, str], None],
    store: ProviderCredentialStore,
) -> str:
    """Configure providers until declined; return the chosen default."""
    configured: list[str] = list(store.providers)
    while True:
        options = tuple(
            SetupMenuOption(
                option.name,
                f"{option.label} (configured)"
                if option.name in configured and option.name != "fake"
                else option.label,
                option.available,
                option.unavailable_message,
            )
            for option in PROVIDER_SETUP_OPTIONS
        )
        provider_name = _choose_setup_option(
            "Choose a provider:",
            "Provider [1]: ",
            options,
            line_reader=line_reader,
            output=output,
        )
        if provider_name == "fake":
            output(
                "Fake uses deterministic local replies, "
                "so it does not need an API key."
            )
        else:
            api_key = _enter_provider_key(
                provider_name,
                secret_reader=secret_reader,
                key_verifier=key_verifier,
                line_reader=line_reader,
                output=output,
            )
            store.set_key(provider_name, api_key)
        if provider_name not in configured:
            configured.append(provider_name)
        if not _confirm(
            "Add another provider? [y/N]",
            line_reader=line_reader,
            output=output,
        ):
            break
    return _choose_default_provider(
        configured,
        store,
        line_reader=line_reader,
        output=output,
    )


def _choose_default_provider(
    configured: list[str],
    store: ProviderCredentialStore,
    *,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    if len(configured) == 1:
        default_name = configured[0]
    else:
        options = tuple(
            SetupMenuOption(name, _provider_label(name), True)
            for name in configured
        )
        default_name = _choose_setup_option(
            "Which provider is the default?",
            "Default provider [1]: ",
            options,
            line_reader=line_reader,
            output=output,
        )
    if default_name in store.providers:
        store.default_provider = default_name
    return default_name


def _provider_label(provider_name: str) -> str:
    if provider_name == "fake":
        return "Fake (no API key)"
    return _PROVIDER_DISPLAY_NAMES[provider_name]


def _enter_provider_key(
    provider_name: str,
    *,
    secret_reader: Callable[[str], str],
    key_verifier: Callable[[str, str], None],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    display_name = _PROVIDER_DISPLAY_NAMES[provider_name]
    while True:
        api_key = secret_reader(
            f"Paste the {display_name} API key (input is hidden): "
        ).strip()
        if not api_key:
            raise ValueError(f"A {display_name} API key is required.")
        try:
            key_verifier(provider_name, api_key)
        except ProviderKeyVerificationError as error:
            output(str(error))
            action = _choose_setup_option(
                f"{display_name} could not verify the API key. What now?",
                "Recovery [1]: ",
                VERIFICATION_RECOVERY_OPTIONS,
                line_reader=line_reader,
                output=output,
            )
            if action == "retry":
                continue
            if action == "abort":
                raise OperationError(
                    "Setup aborted; no credentials or gateway config were saved."
                )
            output(
                f"Saved the {display_name} API key unverified; "
                "rerun setup to verify it later."
            )
            return api_key
        output(f"{display_name} verified the API key.")
        return api_key
