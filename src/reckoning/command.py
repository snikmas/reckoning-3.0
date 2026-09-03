from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from getpass import getpass
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
    wizard_output,
)
from reckoning.providers import (
    ProviderKeyVerificationError,
    verify_provider_api_key,
)
from reckoning.setup import DEFAULT_DATA_DIR, SetupIncompleteError, setup_reckoning
from reckoning.telegram import TelegramBotApiError
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
                "First-run setup wizard; re-run to manage providers and channels.",
            ),
            (
                "reckoning doctor",
                "Report installation health and how to fix problems.",
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
    if command in _COMMAND_DESCRIPTIONS:
        return _run_operation(command, rest)
    print(f"reckoning: unknown command '{command}'", file=sys.stderr)
    print("Run 'reckoning --help' for the command list.", file=sys.stderr)
    return 2


def print_help() -> None:
    render_help(COMMAND_GROUPS)


def _run_setup(rest: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="reckoning setup",
        description=(
            "Run the first-run setup wizard, or manage providers and channels "
            "on an already configured installation."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Local data directory for the instance.",
    )
    parser.add_argument(
        "--server-data-dir",
        type=Path,
        help="Personal-server storage root; required outside local placement.",
    )
    parser.add_argument(
        "--placement",
        choices=("local", "personal-server", "hybrid"),
        help="Placement policy for private and confirmed state.",
    )
    parser.add_argument(
        "--persona",
        choices=("simon", "steady", "original"),
        help="Desired-self persona preset.",
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "deepseek", "orcarouter"),
        help="Model provider; secrets arrive via environment variables only.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Answer every prompt from flags and environment variables; "
            "missing values fail instead of prompting."
        ),
    )
    arguments = parser.parse_args(rest)
    try:
        setup_reckoning(
            data_dir=arguments.data_dir,
            server_data_dir=arguments.server_data_dir,
            placement=arguments.placement,
            persona=arguments.persona,
            provider=arguments.provider,
            non_interactive=arguments.non_interactive,
            output=print if arguments.non_interactive else wizard_output(),
            line_reader=input,
            secret_reader=getpass,
        )
    except SetupIncompleteError as error:
        error_output()(str(error))
        return 2
    except (
        OperationError,
        TelegramBotApiError,
        ProviderKeyVerificationError,
        ValueError,
    ) as error:
        parser.error(str(error))
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
                return _ping_provider()
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
        parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        parser.add_argument("--server-data-dir", type=Path)
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
        parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        parser.add_argument("--server-data-dir", type=Path)
        parser.add_argument("--output", type=Path, required=True)
    elif command == "restore":
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        parser.add_argument("--server-data-dir", type=Path)
    else:
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
    return parser


def _ping_provider() -> int:
    store = ProviderCredentialStore.load(DEFAULT_PROVIDER_CREDENTIALS)
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
    try:
        verify_provider_api_key(provider_name, api_key)
    except ProviderKeyVerificationError as error:
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
