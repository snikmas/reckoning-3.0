from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from reckoning.config import DEFAULT_PROVIDER_CREDENTIALS, ProviderCredentialStore
from reckoning.operations import (
    PASSPHRASE_ENV,
    OperationError,
    TransferKind,
    create_transfer,
    diagnose,
    migrate_transfer,
    release_readiness,
    restore_transfer,
)
from reckoning.presentation import (
    error_output,
    render_doctor,
    render_help,
    render_release_readiness,
)
from reckoning.provider_adapters import (
    ProviderVerificationError,
    probe_local_endpoint,
)
from reckoning.provider_registry import available_providers, find_provider
from reckoning.setup_copy import LOCALE
from reckoning.setup_terminal import (
    InteractiveUI,
    NonInteractiveUI,
    PlainTextUI,
)
from reckoning.setup_workflow import (
    SetupInputError,
    SetupOutcome,
    SetupPaths,
    SetupServices,
    SetupWorkflow,
)
from reckoning.telegram import (
    DEFAULT_TELEGRAM_CONFIG,
    TelegramBotApiError,
)
from reckoning.telegram import main as run_gateway
from reckoning.web import main as run_web

COMMAND_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Run",
        (
            ("reckoning", "Run the web interface."),
            (
                "reckoning gateway",
                "Run every configured channel (Telegram today).",
            ),
        ),
    ),
    (
        "Setup",
        (
            (
                "reckoning setup",
                "Guided setup and the installation status hub.",
            ),
            ("reckoning provider", "Manage providers and the primary model."),
            ("reckoning persona", "Manage desired-self personas."),
            ("reckoning channel", "Set up messaging connectors (Telegram)."),
            (
                "reckoning doctor",
                "Report installation health and how to fix problems.",
            ),
            (
                "reckoning reset",
                "Remove installation state after an explicit preview.",
            ),
        ),
    ),
    (
        "Data",
        (
            ("reckoning backup", "Create an encrypted recovery archive."),
            ("reckoning restore", "Restore into a clean installation."),
            ("reckoning export", "Create an encrypted transfer export."),
            (
                "reckoning migrate",
                "Migrate an encrypted transfer to the current format.",
            ),
        ),
    ),
)

_COMMAND_DESCRIPTIONS = {
    "doctor": "Inspect local operational state and print fix pointers.",
    "backup": "Create an encrypted recovery archive.",
    "restore": "Restore into a clean installation.",
    "export": "Create an encrypted transfer export.",
    "migrate": "Migrate an encrypted transfer to the current format.",
}

_PROVIDER_IDS = tuple(item.id for item in available_providers())


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (["-h"], ["--help"]):
        print_help()
        return 0
    if not arguments or arguments[0].startswith("-"):
        run_web(arguments)
        return 0
    command, rest = arguments[0], arguments[1:]
    if command == "setup":
        return _run_setup(rest)
    if command == "gateway":
        return run_gateway(rest, prog="reckoning gateway")
    if command == "provider":
        return _run_section("provider", rest)
    if command == "persona":
        return _run_section("persona", rest)
    if command == "channel":
        return _run_section("connectors", rest, prog="reckoning channel")
    if command == "reset":
        return _run_reset(rest)
    if command in _COMMAND_DESCRIPTIONS:
        return _run_operation(command, rest)
    print(f"reckoning: unknown command '{command}'", file=sys.stderr)
    print("Run 'reckoning --help' for the command list.", file=sys.stderr)
    return 2


def print_help() -> None:
    render_help(COMMAND_GROUPS)


def _setup_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Guided setup and maintenance: Quick or Custom setup on a fresh "
            "machine; the status view, verification, and repair on a "
            "configured one."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
        help="Local data directory for the instance.",
    )
    parser.add_argument(
        "--server-data-dir",
        type=Path,
        help="Personal-server storage root; required outside local placement.",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_PROVIDER_CREDENTIALS,
        help="Owner-only provider credential store.",
    )
    parser.add_argument(
        "--telegram-config",
        type=Path,
        default=DEFAULT_TELEGRAM_CONFIG,
        help="Telegram connector state file.",
    )
    parser.add_argument(
        "--draft-path",
        type=Path,
        default=Path.home() / ".config" / "reckoning" / "setup-draft.json",
        help="Non-secret resumable setup draft.",
    )
    parser.add_argument("--mode", choices=("quick", "custom"))
    parser.add_argument(
        "--placement",
        choices=("local", "personal-server", "hybrid"),
        help="Placement policy for private and confirmed state.",
    )
    parser.add_argument(
        "--persona",
        choices=("simon", "steady"),
        help="Desired-self persona preset; author originals interactively.",
    )
    parser.add_argument(
        "--provider",
        choices=_PROVIDER_IDS,
        help="Primary provider; secrets arrive via environment variables only.",
    )
    parser.add_argument("--model", help="Model ID for the primary provider.")
    parser.add_argument(
        "--base-url", help="Endpoint URL for custom providers."
    )
    parser.add_argument(
        "--profile",
        choices=("skip", "guided", "import"),
        help="Profile onboarding choice.",
    )
    parser.add_argument(
        "--profile-file",
        type=Path,
        help="UTF-8 Markdown profile for --profile import.",
    )
    parser.add_argument(
        "--first-message",
        help="The first conversation message for non-interactive setup.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Answer every prompt from flags and environment variables; "
            "missing values fail instead of prompting."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the setup result as stable JSON.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Force the numbered plain-text interface.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print tracebacks for unexpected errors; secrets stay redacted.",
    )
    return parser


def _setup_paths(arguments: argparse.Namespace) -> SetupPaths:
    return SetupPaths(
        data_dir=arguments.data_dir,
        server_data_dir=arguments.server_data_dir,
        credentials_path=arguments.credentials,
        telegram_config_path=arguments.telegram_config,
        draft_path=arguments.draft_path,
    )


def _non_interactive_answers(
    arguments: argparse.Namespace, paths: SetupPaths
) -> dict[str, str]:
    answers = {
        "persona-accept": "y",
        "profile": arguments.profile or "skip",
        "connectors": "skip",
        "first-message": arguments.first_message
        or "Setup check: confirm that Reckoning answers.",
        "first-message-action": "accept",
        "review-confirm": "y",
        "provider-verify-consent": "y",
    }
    if arguments.profile == "import":
        if arguments.profile_file is None:
            raise OperationError("--profile import requires --profile-file")
        answers["profile-import-path"] = str(arguments.profile_file)
        answers["profile-import-remove"] = ""
    provider = arguments.provider or "fake"
    if provider not in ("fake", "ollama", "lmstudio", "vllm", "llamacpp"):
        if provider == "custom":
            if arguments.base_url is None:
                raise OperationError(
                    "--base-url is required for --provider custom in "
                    "non-interactive mode"
                )
            answers["provider-base-url"] = arguments.base_url
            answers["provider-custom-key"] = ""
        else:
            definition = find_provider(provider)
            env_name = next(
                (
                    name
                    for name in definition.env_names
                    if os.environ.get(name, "").strip()
                ),
                None,
            )
            saved = ProviderCredentialStore.load(
                paths.credentials_path
            ).credential_for(provider)
            if env_name is not None:
                answers["provider-key-source"] = "env-ref"
            elif saved is not None:
                answers["provider-key-source"] = "saved"
            else:
                names = ", ".join(definition.env_names)
                raise OperationError(
                    f"{names or 'an API key'} is required for --provider "
                    f"{provider} in non-interactive mode; secrets are accepted "
                    "via environment variables only, never command-line flags"
                )
    if arguments.model:
        answers["provider-model"] = "manual"
        answers["provider-model-manual"] = arguments.model
    elif provider == "custom" or provider in (
        "ollama",
        "lmstudio",
        "vllm",
        "llamacpp",
    ):
        answers["provider-model"] = "manual"
        if not arguments.model:
            raise OperationError(
                f"--model is required for --provider {provider} in "
                "non-interactive mode"
            )
    else:
        answers["provider-model"] = "recommended"
    return answers


def _run_setup(rest: Sequence[str]) -> int:
    parser = _setup_parser("reckoning setup")
    arguments = parser.parse_args(rest)
    paths = _setup_paths(arguments)
    services = SetupServices(environ=dict(os.environ), probe=probe_local_endpoint)
    preselected = {
        key: value
        for key, value in (
            ("mode", arguments.mode),
            ("placement", arguments.placement),
            ("persona", arguments.persona),
            ("provider", arguments.provider),
        )
        if value
    }
    if arguments.non_interactive:
        preselected.setdefault("mode", "quick")
        preselected.setdefault("persona", "simon")
        preselected.setdefault("provider", "fake")
    if arguments.server_data_dir is not None:
        preselected["server-data-dir"] = str(arguments.server_data_dir)
    lines: list[str] = []
    try:
        ui: PlainTextUI | NonInteractiveUI
        if arguments.non_interactive:
            answers = _non_interactive_answers(arguments, paths)
            ui = NonInteractiveUI(answers, output=lines.append)
        elif arguments.plain or not sys.stdin.isatty():
            ui = PlainTextUI(output=lines.append)
        else:
            ui = InteractiveUI(no_color=arguments.no_color, output=lines.append)
        workflow = SetupWorkflow(
            paths=paths,
            ui=ui,
            services=services,
            preselected=preselected,
        )
        outcome = workflow.run()
    except (SetupInputError, OperationError, TelegramBotApiError, ValueError) as error:
        _report_failure(arguments, str(error))
        return 2
    except ProviderVerificationError as error:
        _report_failure(arguments, str(error))
        return 2
    except Exception as error:  # unexpected: concise by default
        _report_failure(
            arguments,
            f"unexpected failure: {error}",
            unexpected=error,
        )
        return 2
    for line in lines:
        print(line)
    if arguments.json:
        print(_outcome_json(outcome))
    return 0 if outcome.status in ("activated", "managed") else 1


def _report_failure(
    arguments: argparse.Namespace,
    message: str,
    *,
    unexpected: Exception | None = None,
) -> None:
    print(f"setup failed: {message}", file=sys.stderr)
    print("next: fix the cause and rerun; nothing was activated.", file=sys.stderr)
    if arguments.debug and unexpected is not None:
        traceback.print_exception(unexpected)


def _outcome_json(outcome: SetupOutcome) -> str:
    return json.dumps(
        {
            "locale": LOCALE,
            "status": outcome.status,
            "provider": outcome.provider_id,
            "model": outcome.model,
            "demo": outcome.demo,
            "persona": outcome.persona_id,
            "telegram": outcome.telegram_status,
            "data_dir": str(outcome.data_dir) if outcome.data_dir else None,
        },
        sort_keys=True,
    )


def _run_section(section: str, rest: Sequence[str], prog: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=prog or f"reckoning {section}",
        description=f"Manage the {section} section of the installation.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
    )
    parser.add_argument(
        "--credentials", type=Path, default=DEFAULT_PROVIDER_CREDENTIALS
    )
    parser.add_argument(
        "--telegram-config", type=Path, default=DEFAULT_TELEGRAM_CONFIG
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the section state without changing anything.",
    )
    arguments = parser.parse_args(rest)
    if arguments.list:
        return _list_section(section, arguments)
    paths = SetupPaths(
        data_dir=arguments.data_dir,
        credentials_path=arguments.credentials,
        telegram_config_path=arguments.telegram_config,
    )
    ui: object
    if not sys.stdin.isatty():
        ui = PlainTextUI()
    else:
        ui = InteractiveUI()
    try:
        workflow = SetupWorkflow(
            paths=paths,
            ui=ui,  # type: ignore[arg-type]
            services=SetupServices(environ=dict(os.environ)),
        )
        workflow.manage_section(section)
    except (SetupInputError, OperationError, ValueError) as error:
        parser.exit(2, f"reckoning {section}: {error}\n")
    return 0


def _list_section(section: str, arguments: argparse.Namespace) -> int:
    if section == "provider":
        store = ProviderCredentialStore.load(arguments.credentials)
        if not store.providers:
            print("no provider credentials stored")
            return 0
        for name, entry in store.providers.items():
            state = "verified" if entry.verified else "inactive (unverified)"
            default = " (default)" if name == store.default_provider else ""
            print(f"{name}: {state}{default}")
        return 0
    if section == "persona":
        from reckoning.personas import JsonFilePersonaRepository, PersonaService

        service = PersonaService(
            JsonFilePersonaRepository(arguments.data_dir / "personas.json")
        )
        active = None
        try:
            active = service.active().definition.id
        except (KeyError, LookupError):
            pass
        for item in (*service.list_defaults(), *service.list_authored()):
            marker = " (active)" if item.id == active else ""
            print(f"{item.id}: {item.name}{marker}")
        return 0
    from reckoning.telegram import telegram_connector_status

    print(f"telegram: {telegram_connector_status(arguments.telegram_config)}")
    return 0


def _run_reset(rest: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="reckoning reset",
        description=(
            "Remove installation state after previewing the exact targets. "
            "Reset is isolated from normal repair on purpose."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
    )
    parser.add_argument(
        "--credentials", type=Path, default=DEFAULT_PROVIDER_CREDENTIALS
    )
    parser.add_argument(
        "--telegram-config", type=Path, default=DEFAULT_TELEGRAM_CONFIG
    )
    parser.add_argument(
        "--draft-path",
        type=Path,
        default=Path.home() / ".config" / "reckoning" / "setup-draft.json",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the previewed removal without an interactive prompt.",
    )
    arguments = parser.parse_args(rest)
    targets = [
        path
        for path in (
            arguments.data_dir,
            arguments.credentials,
            arguments.telegram_config,
            arguments.draft_path,
        )
        if path.exists()
    ]
    if not targets:
        print("reset: nothing to remove")
        return 0
    print("reset will remove exactly:")
    for target in targets:
        print(f"  - {target}")
    if not arguments.yes:
        answer = input("Type 'yes' to confirm: ").strip()
        if answer != "yes":
            print("reset: aborted; nothing was removed")
            return 1
    import shutil

    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    print("reset: removed the previewed targets")
    return 0


def _run_operation(command: str, rest: Sequence[str]) -> int:
    parser = _operation_parser(command)
    arguments = parser.parse_args(rest)
    try:
        if command == "doctor":
            if arguments.release_evidence is not None:
                return _print_release_readiness(arguments.release_evidence)
            render_doctor(
                diagnose(
                    arguments.data_dir,
                    server_data_dir=arguments.server_data_dir,
                )
            )
            if arguments.ping:
                return _ping_provider(arguments.credentials)
            return 0
        if command in {"backup", "export"}:
            count = create_transfer(
                arguments.data_dir,
                arguments.output,
                _passphrase(),
                kind=cast(TransferKind, command),
                server_data_dir=arguments.server_data_dir,
            )
            print(
                f"encrypted {command} created: {arguments.output} ({count} state files)"
            )
            return 0
        if command == "restore":
            count = restore_transfer(
                arguments.input,
                arguments.data_dir,
                _passphrase(),
                server_data_dir=arguments.server_data_dir,
            )
            print(f"restore complete: {count} state files restored")
            return 0
        before, after, count = migrate_transfer(
            arguments.input, arguments.output, _passphrase()
        )
        print(
            f"migration complete: transfer format {before} -> {after}; "
            f"{count} state files validated"
        )
        return 0
    except (OperationError, OSError) as error:
        print(f"{command} failed: {error}", file=sys.stderr)
        return 2


def _operation_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"reckoning {command}",
        description=_COMMAND_DESCRIPTIONS[command],
    )
    if command == "doctor":
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=Path.home() / ".local" / "state" / "reckoning",
        )
        parser.add_argument("--server-data-dir", type=Path)
        parser.add_argument(
            "--credentials", type=Path, default=DEFAULT_PROVIDER_CREDENTIALS
        )
        parser.add_argument(
            "--release-evidence",
            type=Path,
            help="Report public-release gates from an evidence file.",
        )
        parser.add_argument(
            "--ping",
            action="store_true",
            help=(
                "Opt in to a live provider check; the default report makes "
                "no network calls."
            ),
        )
    elif command in {"backup", "export"}:
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=Path.home() / ".local" / "state" / "reckoning",
        )
        parser.add_argument("--server-data-dir", type=Path)
        parser.add_argument("--output", type=Path, required=True)
    elif command == "restore":
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=Path.home() / ".local" / "state" / "reckoning",
        )
        parser.add_argument("--server-data-dir", type=Path)
    else:
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
    return parser


def _ping_provider(credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS) -> int:
    store = ProviderCredentialStore.load(credentials_path)
    provider_name = store.default_provider
    if provider_name is None:
        print("provider ping: skipped; the fake provider needs no network check.")
        return 0
    api_key = store.api_key_for(provider_name)
    if api_key is None:
        print(
            f"provider ping: no saved key for {provider_name}; fix: run reckoning setup"
        )
        return 2
    from reckoning.providers import (
        ProviderKeyVerificationError,
        verify_provider_api_key,
    )

    try:
        verify_provider_api_key(provider_name, api_key)
    except (ProviderKeyVerificationError, ValueError) as error:
        print(f"provider ping: {error} fix: run reckoning setup")
        return 2
    print(f"provider ping: {provider_name} verified the saved API key.")
    return 0


def _passphrase() -> str:
    value = os.environ.get(PASSPHRASE_ENV)
    if value is None:
        raise OperationError(
            f"set {PASSPHRASE_ENV}; passphrases are not accepted as command arguments"
        )
    return value


def _print_release_readiness(evidence_path: Path) -> int:
    report = release_readiness(evidence_path)
    if report.ready:
        render_release_readiness(
            True, "READY: every required public single-user release gate has evidence"
        )
        return 0
    render_release_readiness(
        False,
        "BLOCKED: missing required evidence for " + ", ".join(report.missing_gates),
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
