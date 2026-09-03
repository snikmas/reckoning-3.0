from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from reckoning.application import ReckoningApplication, create_local_application
from reckoning.config import (
    DEFAULT_PROVIDER_CREDENTIALS,
    ProviderCredentialStore,
)
from reckoning.operations import OperationError, PlacementProfile, setup_instance
from reckoning.personas import DEFAULT_PERSONAS, PersonaDefinition
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

DEFAULT_DATA_DIR = Path.home() / ".local" / "state" / "reckoning"

PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "orcarouter": "ORCAROUTER_API_KEY",
}


class SetupIncompleteError(RuntimeError):
    """Setup finished without proving the core continuity loop."""


@dataclass(frozen=True)
class SetupResult:
    provider_name: str
    telegram: TelegramPollingSettings | None


@dataclass(frozen=True)
class SetupMenuOption:
    name: str
    label: str
    available: bool
    unavailable_message: str = ""


PLACEMENT_SETUP_OPTIONS = (
    SetupMenuOption(
        "local",
        "Local — private data stays on this device",
        True,
    ),
    SetupMenuOption(
        "personal-server",
        "Personal server — private and confirmed state lives on your server",
        True,
    ),
    SetupMenuOption(
        "hybrid",
        "Hybrid — private data local, approved remote sources on your server",
        True,
    ),
)

PERSONA_PRESET_OPTIONS = (
    SetupMenuOption("simon", "Simon — composed, direct, and demanding", True),
    SetupMenuOption("steady", "Steady — reflective, warm, and probing", True),
    SetupMenuOption(
        "original",
        "Original — author your own persona",
        True,
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

MANAGE_SETUP_OPTIONS = (
    SetupMenuOption("add", "Add a provider", True),
    SetupMenuOption("replace", "Replace a provider key", True),
    SetupMenuOption("remove", "Remove a provider", True),
    SetupMenuOption("default", "Change the default provider", True),
    SetupMenuOption("done", "Done", True),
)

VERIFICATION_RECOVERY_OPTIONS = (
    SetupMenuOption("retry", "Re-enter the API key", True),
    SetupMenuOption(
        "save",
        "Save the key anyway (the verification endpoint may be down)",
        True,
    ),
    SetupMenuOption("abort", "Abort setup without saving anything", True),
)

_PLACEMENT_REASONS = {
    "local": "private data stays on this device",
    "personal-server": "private and confirmed state lives on your personal server",
    "hybrid": "private data stays local; approved remote sources live on your server",
}


def _choose_setup_option(
    heading: str,
    prompt: str,
    options: tuple[SetupMenuOption, ...],
    *,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    default_index: int = 1,
) -> str:
    output(heading)
    for index, option in enumerate(options, start=1):
        output(f"  {index}. {option.label}")

    while True:
        raw_choice = line_reader(prompt).strip() or str(default_index)
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
    default: bool = False,
) -> bool:
    output(question)
    answer = line_reader("").strip().casefold()
    if not answer:
        return default
    return answer in ("y", "yes")


def setup_reckoning(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    server_data_dir: Path | None = None,
    placement: PlacementProfile | None = None,
    persona: str | None = None,
    provider: str | None = None,
    non_interactive: bool = False,
    config_path: Path = DEFAULT_TELEGRAM_CONFIG,
    credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    secret_reader: Callable[[str], str] = getpass,
    line_reader: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    api_factory: Callable[[str], TelegramBotClient] = TelegramBotApi,
    key_verifier: Callable[[str, str], None] = verify_provider_api_key,
    pairing_code: str | None = None,
    maximum_polls: int = 12,
    environ: Mapping[str, str] | None = None,
    application_factory: Callable[..., ReckoningApplication] = create_local_application,
) -> SetupResult:
    output("Reckoning setup")
    store = ProviderCredentialStore.load(credentials_path)
    if (data_dir / "instance.json").exists() or store.providers:
        if non_interactive:
            raise OperationError(
                "this installation is already configured; run reckoning setup "
                "interactively to manage providers and channels"
            )
        return _manage_installation(
            store,
            config_path=config_path,
            credentials_path=credentials_path,
            secret_reader=secret_reader,
            line_reader=line_reader,
            output=output,
            api_factory=api_factory,
            key_verifier=key_verifier,
            pairing_code=pairing_code,
            maximum_polls=maximum_polls,
        )

    if non_interactive:
        _preflight_non_interactive(
            placement,
            server_data_dir,
            persona,
            provider,
            environ,
        )
    chosen_placement, chosen_server_dir = _resolve_placement(
        placement,
        server_data_dir,
        non_interactive=non_interactive,
        line_reader=line_reader,
        output=output,
    )
    persona_definition = _resolve_persona(
        persona,
        non_interactive=non_interactive,
        line_reader=line_reader,
        output=output,
    )
    setup_instance(
        data_dir,
        chosen_placement,
        persona_definition,
        server_data_dir=chosen_server_dir,
    )
    output(
        f"Step 1 — instance: created at {data_dir} with {chosen_placement} "
        f"placement; {_PLACEMENT_REASONS[chosen_placement]}."
    )
    output(
        f"Step 2 — persona: {persona_definition.name} is the desired-self "
        "preset; fine-tuning lives in personas.json, not in this wizard."
    )

    provider_name = _resolve_provider(
        provider,
        store,
        non_interactive=non_interactive,
        environ=environ,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        key_verifier=key_verifier,
    )
    output(
        f"Step 3 — provider: {_provider_label(provider_name)} answers reckoning runs."
    )

    telegram = _run_channels_step(
        provider_name,
        non_interactive=non_interactive,
        config_path=config_path,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        api_factory=api_factory,
        pairing_code=pairing_code,
        maximum_polls=maximum_polls,
    )

    _run_proof(
        provider_name,
        store,
        output=output,
        application_factory=application_factory,
    )
    if store.providers:
        store.save(credentials_path)
        output(
            f"Provider credentials saved to {credentials_path} "
            "with owner-only permissions."
        )
    output(
        "Setup complete: the core continuity loop is proven. "
        "Run reckoning to start, or reckoning gateway for channels."
    )
    return SetupResult(provider_name=provider_name, telegram=telegram)


def _manage_installation(
    store: ProviderCredentialStore,
    *,
    config_path: Path,
    credentials_path: Path,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    api_factory: Callable[[str], TelegramBotClient],
    key_verifier: Callable[[str, str], None],
    pairing_code: str | None,
    maximum_polls: int,
) -> SetupResult:
    fake_default = _manage_providers(
        store,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        key_verifier=key_verifier,
    )
    provider_name = "fake" if fake_default else store.default_provider or "fake"
    telegram = _ensure_telegram_settings(
        provider_name,
        config_path=config_path,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        api_factory=api_factory,
        pairing_code=pairing_code,
        maximum_polls=maximum_polls,
    )
    store.save(credentials_path)
    output(
        f"Provider credentials saved to {credentials_path} with owner-only permissions."
    )
    return SetupResult(provider_name=provider_name, telegram=telegram)


def _resolve_placement(
    placement: PlacementProfile | None,
    server_data_dir: Path | None,
    *,
    non_interactive: bool,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> tuple[PlacementProfile, Path | None]:
    chosen = placement
    if chosen is None:
        if non_interactive:
            chosen = "local"
        else:
            chosen = cast(
                PlacementProfile,
                _choose_setup_option(
                    "Choose a placement policy:",
                    "Placement [1]: ",
                    PLACEMENT_SETUP_OPTIONS,
                    line_reader=line_reader,
                    output=output,
                ),
            )
    if chosen == "local":
        return chosen, server_data_dir
    if server_data_dir is not None:
        return chosen, server_data_dir
    if non_interactive:
        raise OperationError(
            f"--server-data-dir is required for {chosen} placement in "
            "non-interactive mode"
        )
    raw = line_reader("Personal-server data directory: ").strip()
    if not raw:
        raise ValueError(
            f"A personal-server data directory is required for {chosen} placement."
        )
    return chosen, Path(raw)


def _resolve_persona(
    persona: str | None,
    *,
    non_interactive: bool,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> PersonaDefinition:
    chosen = persona
    if chosen is None:
        if non_interactive:
            chosen = "simon"
        else:
            chosen = _choose_setup_option(
                "Choose a desired-self persona preset:",
                "Persona [1]: ",
                PERSONA_PRESET_OPTIONS,
                line_reader=line_reader,
                output=output,
            )
    if chosen != "original":
        return next(item for item in DEFAULT_PERSONAS if item.id == chosen)
    if non_interactive:
        raise OperationError(
            "--persona original is authored in the interactive wizard; "
            "non-interactive setup accepts simon or steady"
        )
    persona_id = line_reader("Persona id (lowercase slug, e.g. clear-eyed): ").strip()
    persona_name = line_reader("Persona display name: ").strip()
    definition = PersonaDefinition(
        id=persona_id,
        name=persona_name,
        voice="composed",
        directness="balanced",
        warmth="balanced",
        humor="none",
        challenge="probing",
        sensitive_topic_handling="calm",
    )
    output(
        "Original persona starts from balanced defaults; tune its style "
        "fields in personas.json."
    )
    return definition


def _preflight_non_interactive(
    placement: PlacementProfile | None,
    server_data_dir: Path | None,
    persona: str | None,
    provider: str | None,
    environ: Mapping[str, str] | None,
) -> None:
    """Fail on missing non-interactive values before anything is written."""
    chosen_placement = placement or "local"
    if chosen_placement != "local" and server_data_dir is None:
        raise OperationError(
            f"--server-data-dir is required for {chosen_placement} placement "
            "in non-interactive mode"
        )
    if persona == "original":
        raise OperationError(
            "--persona original is authored in the interactive wizard; "
            "non-interactive setup accepts simon or steady"
        )
    chosen_provider = provider or "fake"
    if chosen_provider != "fake":
        env_name = PROVIDER_KEY_ENV[chosen_provider]
        values = os.environ if environ is None else environ
        if not values.get(env_name, "").strip():
            raise OperationError(
                f"{env_name} is required for --provider {chosen_provider} in "
                "non-interactive mode; secrets are accepted via environment "
                "variables only, never command-line flags"
            )


def _resolve_provider(
    provider: str | None,
    store: ProviderCredentialStore,
    *,
    non_interactive: bool,
    environ: Mapping[str, str] | None,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    key_verifier: Callable[[str, str], None],
) -> str:
    if non_interactive:
        name = provider or "fake"
        if name == "fake":
            return "fake"
        env_name = PROVIDER_KEY_ENV[name]
        values = os.environ if environ is None else environ
        api_key = values.get(env_name, "").strip()
        if not api_key:
            raise OperationError(
                f"{env_name} is required for --provider {name} in "
                "non-interactive mode; secrets are accepted via environment "
                "variables only, never command-line flags"
            )
        store.set_key(name, api_key)
        return name
    if provider is not None:
        if provider == "fake":
            output(
                "Fake uses deterministic local replies, so it does not need an API key."
            )
            return "fake"
        api_key = _enter_provider_key(
            provider,
            secret_reader=secret_reader,
            key_verifier=key_verifier,
            line_reader=line_reader,
            output=output,
        )
        store.set_key(provider, api_key)
        return provider
    return _run_provider_loop(
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        key_verifier=key_verifier,
        store=store,
    )


def _run_channels_step(
    provider_name: str,
    *,
    non_interactive: bool,
    config_path: Path,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    api_factory: Callable[[str], TelegramBotClient],
    pairing_code: str | None,
    maximum_polls: int,
) -> TelegramPollingSettings | None:
    if non_interactive:
        output(
            "Step 4 — channels: skipped; unattended setup cannot pair "
            "Telegram. Re-run reckoning setup interactively to add it."
        )
        return None
    if not _confirm(
        "Set up the Telegram channel now? [Y/n]",
        line_reader=line_reader,
        output=output,
        default=True,
    ):
        output(
            "Step 4 — channels: skipped; re-run reckoning setup to add Telegram later."
        )
        return None
    settings = setup_telegram_polling(
        config_path=config_path,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        api_factory=api_factory,
        pairing_code=pairing_code,
        maximum_polls=maximum_polls,
        gateway_name="telegram",
        provider_name=provider_name,
    )
    output(
        f"Step 4 — channels: Telegram is paired as @{settings.bot_username}; "
        "reckoning gateway runs every configured channel."
    )
    return settings


def _run_proof(
    provider_name: str,
    store: ProviderCredentialStore,
    *,
    output: Callable[[str], None],
    application_factory: Callable[..., ReckoningApplication],
) -> None:
    try:
        with TemporaryDirectory(prefix="reckoning-setup-proof-") as temporary:
            state_path = Path(temporary) / "continuity.json"

            def build_application() -> ReckoningApplication:
                return application_factory(
                    state_path,
                    provider_name=provider_name,
                    orcarouter_api_key=store.api_key_for("orcarouter"),
                    deepseek_api_key=store.api_key_for("deepseek"),
                )

            application = build_application()
            decision = application.start_reckoning(
                "Protect one fixed commitment while keeping one smaller task alive."
            )
            output(
                f"Step 5 — proof 1/3: {_provider_label(provider_name)} "
                "answered a live round-trip."
            )
            confirmed = application.confirm_reckoning(decision.id)
            if confirmed.status != "confirmed":
                raise _proof_failure("the continuity record did not confirm")
            output("Step 5 — proof 2/3: a continuity record was created and confirmed.")
            resumed = build_application().resume_decision(decision.id)
            if resumed.decision.id != decision.id:
                raise _proof_failure("the record did not survive a restart")
            output(
                "Step 5 — proof 3/3: the store re-opened; the record "
                "survives a restart."
            )
    except SetupIncompleteError:
        raise
    except Exception as error:
        raise _proof_failure(str(error)) from error


def _proof_failure(detail: str) -> SetupIncompleteError:
    return SetupIncompleteError(
        f"Setup is incomplete: {detail}. Run reckoning doctor to find the problem."
    )


def _manage_providers(
    store: ProviderCredentialStore,
    *,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    key_verifier: Callable[[str, str], None],
) -> bool:
    """Manage stored providers; return True when the default became fake."""
    fake_default = False
    while True:
        configured = ", ".join(
            f"{_provider_label(name)}"
            f"{' (default)' if name == store.default_provider else ''}"
            for name in store.providers
        )
        output(f"Configured providers: {configured or 'none'}.")
        action = _choose_setup_option(
            "What do you want to change?",
            "Action [5]: ",
            MANAGE_SETUP_OPTIONS,
            default_index=5,
            line_reader=line_reader,
            output=output,
        )
        if action == "done":
            return fake_default
        if action == "add":
            chosen = _run_provider_loop(
                secret_reader=secret_reader,
                line_reader=line_reader,
                output=output,
                key_verifier=key_verifier,
                store=store,
                current_default=store.default_provider,
            )
            fake_default = chosen == "fake"
            continue
        if not store.providers:
            output("No providers are configured yet.")
            continue
        if action == "replace":
            provider_name = _choose_stored_provider(
                store,
                "Replace which provider's key?",
                line_reader=line_reader,
                output=output,
            )
            new_key = _enter_provider_key(
                provider_name,
                secret_reader=secret_reader,
                key_verifier=key_verifier,
                line_reader=line_reader,
                output=output,
                keep_current=store.api_key_for(provider_name),
            )
            store.set_key(provider_name, new_key)
        elif action == "remove":
            provider_name = _choose_stored_provider(
                store,
                "Remove which provider?",
                line_reader=line_reader,
                output=output,
            )
            was_default = provider_name == store.default_provider
            store.remove(provider_name)
            output(f"Removed {_provider_label(provider_name)}.")
            if was_default and store.providers:
                _choose_default_provider(
                    list(store.providers),
                    store,
                    line_reader=line_reader,
                    output=output,
                    current_default=store.default_provider,
                )
                fake_default = False
            elif not store.providers:
                output("No providers remain; the fake provider will be used.")
        elif action == "default":
            if len(store.providers) < 2:
                output("Only one provider is configured; it stays the default.")
                continue
            _choose_default_provider(
                list(store.providers),
                store,
                line_reader=line_reader,
                output=output,
                current_default=store.default_provider,
            )
            fake_default = False


def _choose_stored_provider(
    store: ProviderCredentialStore,
    heading: str,
    *,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    return _choose_setup_option(
        heading,
        "Provider [1]: ",
        tuple(
            SetupMenuOption(name, _provider_label(name), True)
            for name in store.providers
        ),
        line_reader=line_reader,
        output=output,
    )


def _ensure_telegram_settings(
    provider_name: str,
    *,
    config_path: Path,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    api_factory: Callable[[str], TelegramBotClient],
    pairing_code: str | None,
    maximum_polls: int,
) -> TelegramPollingSettings:
    if config_path.exists():
        settings = TelegramPollingSettings.load(config_path)
        if settings.provider_name == provider_name:
            return settings
        updated = replace(settings, provider_name=provider_name)
        updated.save(config_path)
        output(
            f"Default provider updated to {_provider_label(provider_name)} "
            f"in {config_path}."
        )
        return updated
    output("Telegram is not configured yet; finishing gateway setup.")
    return setup_telegram_polling(
        config_path=config_path,
        secret_reader=secret_reader,
        line_reader=line_reader,
        output=output,
        api_factory=api_factory,
        pairing_code=pairing_code,
        maximum_polls=maximum_polls,
        gateway_name="telegram",
        provider_name=provider_name,
    )


def _run_provider_loop(
    *,
    secret_reader: Callable[[str], str],
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    key_verifier: Callable[[str, str], None],
    store: ProviderCredentialStore,
    current_default: str | None = None,
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
                "Fake uses deterministic local replies, so it does not need an API key."
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
        current_default=current_default,
    )


def _choose_default_provider(
    configured: list[str],
    store: ProviderCredentialStore,
    *,
    line_reader: Callable[[str], str],
    output: Callable[[str], None],
    current_default: str | None = None,
) -> str:
    if len(configured) == 1:
        default_name = configured[0]
    else:
        options = tuple(
            SetupMenuOption(name, _provider_label(name), True) for name in configured
        )
        default_index = (
            configured.index(current_default) + 1
            if current_default in configured
            else 1
        )
        default_name = _choose_setup_option(
            "Which provider is the default?",
            f"Default provider [{default_index}]: ",
            options,
            default_index=default_index,
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
    keep_current: str | None = None,
) -> str:
    display_name = _PROVIDER_DISPLAY_NAMES[provider_name]
    if keep_current is None:
        prompt = f"Paste the {display_name} API key (input is hidden): "
    else:
        prompt = (
            f"Paste the new {display_name} API key "
            "(input is hidden; Enter keeps the current key): "
        )
    while True:
        api_key = secret_reader(prompt).strip()
        if not api_key:
            if keep_current is not None:
                output(f"Kept the current {display_name} API key.")
                return keep_current
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
