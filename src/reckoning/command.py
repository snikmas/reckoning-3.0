from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from reckoning.operations import OperationError
from reckoning.setup import setup_reckoning
from reckoning.telegram import DEFAULT_TELEGRAM_CONFIG, TelegramBotApiError
from reckoning.web import build_parser as build_web_parser
from reckoning.web import main as run_web


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (["-h"], ["--help"]):
        parser = build_web_parser()
        parser.prog = "reckoning"
        parser.epilog = (
            "Run 'reckoning setup' to configure a gateway and model provider."
        )
        parser.print_help()
        return

    if arguments and arguments[0] == "setup":
        parser = argparse.ArgumentParser(
            prog="reckoning setup",
            description="Set up Reckoning's gateway and model provider.",
        )
        parser.add_argument(
            "--telegram-config",
            type=Path,
            default=DEFAULT_TELEGRAM_CONFIG,
            help="Local credential file used by Telegram polling.",
        )
        setup_arguments = parser.parse_args(arguments[1:])
        try:
            setup_reckoning(config_path=setup_arguments.telegram_config)
        except (OperationError, TelegramBotApiError, ValueError) as error:
            parser.error(str(error))
        return

    run_web()
