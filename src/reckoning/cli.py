from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence

from reckoning.operations import (
    OperationError,
    PASSPHRASE_ENV,
    create_transfer,
    diagnose,
    migrate_transfer,
    release_readiness,
    restore_transfer,
    setup_instance,
)
from reckoning.personas import DEFAULT_PERSONAS, PersonaDefinition


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reckoning-ops",
        description=(
            "Setup, diagnosis, recovery, backup, export, and migration for one "
            "Reckoning instance."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="Create an encrypted recovery archive.")
    _transfer_source_arguments(backup)

    diagnosis = commands.add_parser("diagnose", help="Inspect local operational state.")
    diagnosis.add_argument("--data-dir", type=Path, default=_default_data_dir())
    diagnosis.add_argument(
        "--release-evidence",
        type=Path,
        help="Report public-release gates from an evidence file.",
    )

    export = commands.add_parser("export", help="Create an encrypted transfer export.")
    _transfer_source_arguments(export)

    migration = commands.add_parser("migrate", help="Migrate an encrypted transfer.")
    migration.add_argument("--input", type=Path, required=True)
    migration.add_argument("--output", type=Path, required=True)

    restore = commands.add_parser("restore", help="Restore into a clean installation.")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--data-dir", type=Path, default=_default_data_dir())

    setup = commands.add_parser("setup", help="Initialize a single-user installation.")
    setup.add_argument("--data-dir", type=Path, default=_default_data_dir())
    setup.add_argument(
        "--placement",
        choices=("local", "personal-server", "hybrid"),
        default="local",
    )
    setup.add_argument(
        "--persona",
        choices=("simon", "steady", "original"),
        default="simon",
        help="Select a default persona or author an original persona.",
    )
    setup.add_argument("--persona-id")
    setup.add_argument("--persona-name")
    setup.add_argument(
        "--voice",
        choices=("composed", "reflective", "formal", "candid", "energetic"),
        default="composed",
    )
    setup.add_argument(
        "--directness",
        choices=("gentle", "balanced", "direct", "severe"),
        default="balanced",
    )
    setup.add_argument(
        "--warmth",
        choices=("reserved", "balanced", "warm"),
        default="balanced",
    )
    setup.add_argument(
        "--humor", choices=("none", "dry", "light"), default="none"
    )
    setup.add_argument(
        "--challenge",
        choices=("supportive", "probing", "demanding", "uncomfortable"),
        default="probing",
    )
    setup.add_argument(
        "--sensitive-topic-handling",
        choices=("warm", "calm", "practical"),
        default="calm",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "setup":
            persona = _setup_persona(arguments)
            setup_instance(arguments.data_dir, arguments.placement, persona)
            print(_setup_explanation(arguments.placement, persona.name))
            return 0
        if arguments.command == "diagnose":
            if arguments.release_evidence is not None:
                return _print_release_readiness(arguments.release_evidence)
            print("\n".join(diagnose(arguments.data_dir)))
            return 0
        if arguments.command in {"backup", "export"}:
            count = create_transfer(
                arguments.data_dir,
                arguments.output,
                _passphrase(),
                kind=arguments.command,
            )
            print(
                f"encrypted {arguments.command} created: {arguments.output} "
                f"({count} state files)"
            )
            return 0
        if arguments.command == "restore":
            count = restore_transfer(
                arguments.input, arguments.data_dir, _passphrase()
            )
            print(f"restore complete: {count} state files restored")
            return 0
        if arguments.command == "migrate":
            before, after, count = migrate_transfer(
                arguments.input, arguments.output, _passphrase()
            )
            print(
                f"migration complete: transfer format {before} -> {after}; "
                f"{count} state files validated"
            )
            return 0
    except (OperationError, OSError) as error:
        print(f"{arguments.command} failed: {error}", file=sys.stderr)
        return 2
    parser.error("unsupported command")
    return 2


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "state" / "reckoning"


def _transfer_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    parser.add_argument("--output", type=Path, required=True)


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
        print(
            "READY: every required public single-user release gate has evidence"
        )
        return 0
    print(
        "BLOCKED: missing required evidence for "
        + ", ".join(report.missing_gates)
    )
    return 2


def _setup_persona(arguments: argparse.Namespace) -> PersonaDefinition:
    if arguments.persona != "original":
        return next(item for item in DEFAULT_PERSONAS if item.id == arguments.persona)
    if not arguments.persona_id or not arguments.persona_name:
        raise OperationError(
            "original persona setup requires --persona-id and --persona-name"
        )
    try:
        return PersonaDefinition(
            id=arguments.persona_id,
            name=arguments.persona_name,
            voice=arguments.voice,
            directness=arguments.directness,
            warmth=arguments.warmth,
            humor=arguments.humor,
            challenge=arguments.challenge,
            sensitive_topic_handling=arguments.sensitive_topic_handling,
        )
    except ValueError as error:
        raise OperationError(str(error)) from error


def _setup_explanation(placement: str, persona_name: str) -> str:
    return "\n".join(
        (
            f"Single-user Reckoning setup complete with {placement} placement.",
            f"The selected persona is {persona_name}.",
            "Private data stays local unless its source category is explicitly "
            "approved for remote placement.",
            "External writes require exact or standing permission. Lower layers "
            "cannot broaden that authority.",
            "Missing required context blocks work or enters visible limited mode.",
            "Operational access is limited to loopback, a private authenticated "
            "network, or an SSH tunnel.",
            "Create an encrypted backup with `reckoning-ops backup`. Restore it "
            "into a clean installation with `reckoning-ops restore`.",
            "The deterministic clean-install check completed the core continuity "
            "loop without a developer.",
            "This setup has no hosted tenancy and no public administration dashboard.",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
