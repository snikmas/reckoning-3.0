from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from reckoning.application import create_local_application
from reckoning.config import DEFAULT_PROVIDER_CREDENTIALS, RuntimeProviderSettings
from reckoning.interfaces import create_local_interface_application
from reckoning.operations import OperationError, load_installation_runtime
from reckoning.provider_adapters import AdapterConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reckoning",
        description="Talk to your configured Reckoning agent in the terminal.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
    )
    parser.add_argument("--server-data-dir", type=Path)
    parser.add_argument(
        "--credentials", type=Path, default=DEFAULT_PROVIDER_CREDENTIALS
    )
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    line_reader: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        runtime = load_installation_runtime(
            arguments.data_dir,
            server_data_dir=arguments.server_data_dir,
        )
        provider = RuntimeProviderSettings.load(
            arguments.data_dir,
            credentials_path=arguments.credentials,
            provider_name=arguments.provider,
            model=arguments.model,
            base_url=arguments.base_url,
        )
    except (OperationError, ValueError) as error:
        parser.error(str(error))

    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        provider_name=provider.provider_name,
        provider_config=AdapterConfig(
            api_key=provider.api_key,
            model=provider.model,
            base_url=provider.base_url,
            protocol=provider.protocol,
            context_window=provider.context_window,
            headers=provider.headers,
        ),
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    interface = create_local_interface_application(
        application,
        runtime.state_path("confirmed-state", "interfaces.json"),
        placement=runtime.interface_placement,
        connector_data_dir=runtime.root_for("approved-remote-sources"),
    )

    output(
        f"Reckoning Terminal with {runtime.persona.name}. "
        "Type /exit to leave."
    )
    while True:
        try:
            text = line_reader("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            output("")
            break
        if text.casefold() in {"/exit", "/quit"}:
            break
        if not text:
            continue
        try:
            reply = interface.send_channel_message("terminal", text)
        except (RuntimeError, ValueError) as error:
            output(f"Could not answer: {error}")
            continue
        output(f"{runtime.persona.name}: {reply.text}")
    output("Reckoning stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
