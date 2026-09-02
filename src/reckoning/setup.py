from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

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
    SetupMenuOption(
        "deepseek",
        "DeepSeek (API-key setup coming later)",
        False,
        "DeepSeek setup is not available yet.",
    ),
    SetupMenuOption(
        "orcarouter",
        "OrcaRouter (API-key setup coming later)",
        False,
        "OrcaRouter setup is not available yet.",
    ),
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


def setup_reckoning(
    *,
    config_path: Path = DEFAULT_TELEGRAM_CONFIG,
    secret_reader: Callable[[str], str] = getpass,
    line_reader: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    api_factory: Callable[[str], TelegramBotClient] = TelegramBotApi,
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
    output("Fake uses deterministic local replies, so it does not need an API key.")
    return setup_telegram_polling(
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
