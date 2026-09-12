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
from reckoning.json_store import read_json
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
    render_doctor,
    render_help,
    render_release_readiness,
)
from reckoning.provider_adapters import (
    ProviderVerificationError,
    probe_local_endpoint,
)
from reckoning.provider_registry import available_providers, find_provider
from reckoning.provider_validation import is_valid_env_name
from reckoning.runtime_status import record_gateway_runtime
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
from reckoning.terminal import main as run_terminal
from reckoning.web import main as run_web

COMMAND_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Run",
        (
            ("reckoning", "Start a Terminal conversation."),
            ("reckoning web", "Run the local Web interface."),
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
        return run_terminal(arguments)
    command, rest = arguments[0], arguments[1:]
    if command == "setup":
        return _run_setup(rest)
    if command == "web":
        run_web(rest)
        return 0
    if command == "gateway":
        return _run_gateway(rest)
    if command == "reset":
        return _run_reset(rest)
    if command in _COMMAND_DESCRIPTIONS:
        return _run_operation(command, rest)
    print(f"reckoning: unknown command '{command}'", file=sys.stderr)
    print("Run 'reckoning --help' for the command list.", file=sys.stderr)
    return 2


def print_help() -> None:
    render_help(COMMAND_GROUPS)


_DEFAULT_DATA_DIR = Path.home() / ".local" / "state" / "reckoning"

_GATEWAY_VALUE_OPTIONS = frozenset(
    (
        "--host",
        "--port",
        "--provider",
        "--model",
        "--base-url",
        "--data-dir",
        "--server-data-dir",
        "--telegram-config",
    )
)


def _gateway_command(rest: Sequence[str]) -> str:
    skip_value = False
    for token in rest:
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-"):
            if token in _GATEWAY_VALUE_OPTIONS:
                skip_value = True
            continue
        return token
    return "run"


def _gateway_data_dir(rest: Sequence[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA_DIR)
    try:
        known, _ = parser.parse_known_args(list(rest))
    except SystemExit:
        return _DEFAULT_DATA_DIR
    return known.data_dir


def _run_gateway(rest: Sequence[str]) -> int:
    if any(token in ("-h", "--help") for token in rest) or _gateway_command(
        rest
    ) not in ("run", "webhook"):
        return run_gateway(rest, prog="reckoning gateway")
    with record_gateway_runtime(_gateway_data_dir(rest)):
        return run_gateway(rest, prog="reckoning gateway")


def _setup_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Guided setup on a fresh machine; status, verification, and "
            "focused editing on a configured installation."
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
    parser.add_argument(
        "--credential-env",
        metavar="NAME",
        help="Environment variable name that holds the provider credential.",
    )
    parser.add_argument("--api-key", help=argparse.SUPPRESS)
    parser.add_argument("--model", help="Model ID for the primary provider.")
    parser.add_argument(
        "--base-url", help="Endpoint URL for custom providers."
    )
    parser.add_argument(
        "--protocol",
        choices=("openai-chat-completions",),
        help="Custom endpoint protocol under Advanced.",
    )
    parser.add_argument(
        "--context-window",
        type=int,
        help="Custom endpoint context size in tokens under Advanced.",
    )
    parser.add_argument(
        "--header-env",
        action="append",
        default=[],
        metavar="HEADER=ENV_VAR",
        help="Custom request header whose value comes from an environment variable.",
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
        "review-action": "continue",
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
            if not arguments.credential_env:
                answers["provider-custom-key"] = ""
            advanced = bool(
                arguments.protocol
                or arguments.context_window is not None
                or arguments.header_env
            )
            answers["provider-custom-advanced"] = "y" if advanced else "n"
            if advanced:
                answers["provider-custom-protocol"] = (
                    arguments.protocol or "openai-chat-completions"
                )
                answers["provider-custom-context"] = (
                    str(arguments.context_window)
                    if arguments.context_window is not None
                    else ""
                )
                answers["provider-custom-header-env"] = ",".join(
                    arguments.header_env
                )
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
            if arguments.credential_env:
                if not os.environ.get(arguments.credential_env, "").strip():
                    raise OperationError(
                        f"{arguments.credential_env} is not set for --provider "
                        f"{provider}"
                    )
            elif env_name is not None:
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
    if arguments.api_key is not None:
        parser.error(
            "--api-key is not supported because command-line values can leak; "
            "use --credential-env NAME"
        )
    if arguments.credential_env and arguments.provider is None:
        parser.error("--credential-env requires --provider")
    if (
        arguments.protocol
        or arguments.context_window is not None
        or arguments.header_env
        or arguments.base_url
    ) and arguments.provider != "custom":
        parser.error(
            "--base-url, --protocol, --context-window, and --header-env are "
            "only valid with --provider custom"
        )
    paths = _setup_paths(arguments)
    services = SetupServices(environ=dict(os.environ), probe=probe_local_endpoint)
    preselected = {
        key: value
        for key, value in (
            ("placement", arguments.placement),
            ("persona", arguments.persona),
            ("provider", arguments.provider),
        )
        if value
    }
    if arguments.non_interactive:
        preselected.setdefault("persona", "simon")
        preselected.setdefault("provider", "fake")
    if arguments.server_data_dir is not None:
        preselected["server-data-dir"] = str(arguments.server_data_dir)
    if arguments.credential_env:
        if not is_valid_env_name(arguments.credential_env):
            parser.error("--credential-env must be an environment variable name")
        if arguments.provider in ("fake", "ollama", "lmstudio", "vllm", "llamacpp"):
            parser.error(
                f"--provider {arguments.provider} does not accept a credential"
            )
        preselected["credential-env"] = arguments.credential_env
    lines: list[str] = []
    # Interactive output must reach the terminal before setup waits for
    # input; buffer only when a stable JSON result must print afterwards.
    buffered = arguments.non_interactive or arguments.json
    output = lines.append if buffered else None
    try:
        ui: PlainTextUI | NonInteractiveUI
        if arguments.non_interactive:
            answers = _non_interactive_answers(arguments, paths)
            ui = NonInteractiveUI(answers, output=lines.append)
        elif arguments.plain or not sys.stdin.isatty():
            ui = PlainTextUI(output=output)
        else:
            ui = InteractiveUI(no_color=arguments.no_color, output=output)
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
    except Exception as error:  # noqa: BLE001 — top-level concise boundary
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
    if arguments.json:
        print(
            json.dumps(
                {
                    "activated": False,
                    "error": message,
                    "locale": LOCALE,
                    "next": "fix the cause and rerun; nothing was activated",
                    "result": "invalid",
                    "status": "invalid",
                },
                sort_keys=True,
            )
        )
        if arguments.debug and unexpected is not None:
            traceback.print_exception(unexpected)
        return
    print(f"setup failed: {message}", file=sys.stderr)
    print("next: fix the cause and rerun; nothing was activated.", file=sys.stderr)
    if arguments.debug and unexpected is not None:
        traceback.print_exception(unexpected)


def _outcome_json(outcome: SetupOutcome) -> str:
    result = "incomplete" if outcome.status == "draft" else "complete"
    return json.dumps(
        {
            "locale": LOCALE,
            "status": outcome.status,
            "result": result,
            "activated": outcome.status == "activated",
            "provider": outcome.provider_id,
            "model": outcome.model,
            "demo": outcome.demo,
            "persona": outcome.persona_id,
            "telegram": outcome.telegram_status,
            "data_dir": str(outcome.data_dir) if outcome.data_dir else None,
        },
        sort_keys=True,
    )


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
    parser.add_argument(
        "--include-credentials",
        action="store_true",
        help=(
            "Also remove the shared provider credential store. By default, "
            "verified credentials are preserved."
        ),
    )
    arguments = parser.parse_args(rest)
    try:
        server_root = _configured_server_root(arguments.data_dir)
    except (OperationError, RuntimeError) as error:
        parser.error(str(error))
    candidates = (
        arguments.data_dir,
        server_root,
        arguments.credentials if arguments.include_credentials else None,
        arguments.telegram_config,
        arguments.draft_path,
    )
    existing = [path for path in candidates if path is not None and path.exists()]
    targets: list[Path] = []
    for path in existing:
        resolved = path.expanduser().resolve()
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            parser.error(f"refusing to reset broad path: {resolved}")
        if any(resolved == known or resolved.is_relative_to(known) for known in targets):
            continue
        if any(known.is_relative_to(resolved) for known in targets):
            parser.error(f"refusing to reset overlapping broad path: {resolved}")
        targets.append(resolved)
    if not targets:
        print("reset: nothing to remove")
        if arguments.credentials.exists() and not arguments.include_credentials:
            print(f"provider credentials preserved: {arguments.credentials}")
        return 0
    print("reset will remove exactly:")
    for target in targets:
        print(f"  - {target}")
    if arguments.credentials.exists() and not arguments.include_credentials:
        print(f"provider credentials preserved: {arguments.credentials}")
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


def _configured_server_root(data_dir: Path) -> Path | None:
    instance_path = data_dir.expanduser().resolve() / "instance.json"
    if not instance_path.exists():
        return None
    instance = read_json(instance_path, default={})
    placement = instance.get("placement_profile")
    roots = instance.get("storage_roots")
    if placement == "local":
        return None
    if placement not in ("personal-server", "hybrid") or not isinstance(roots, dict):
        raise OperationError(
            "reset cannot determine the configured personal-server root"
        )
    raw_server = roots.get("server")
    if not isinstance(raw_server, str) or not raw_server:
        raise OperationError(
            "reset cannot determine the configured personal-server root"
        )
    server_root = Path(raw_server).expanduser()
    if not server_root.is_absolute():
        raise OperationError("the configured personal-server root is not absolute")
    resolved_data = data_dir.expanduser().resolve()
    resolved_server = server_root.resolve()
    if resolved_server.is_relative_to(resolved_data) or resolved_data.is_relative_to(
        resolved_server
    ):
        raise OperationError("configured reset roots overlap")
    return resolved_server


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
