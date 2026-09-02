from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_reckoning(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    return subprocess.run(
        [sys.executable, "-m", "reckoning", *arguments],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def run_reckoning_telegram(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.argv[0] = 'reckoning-telegram'; "
                "from reckoning.telegram import main; main()"
            ),
            *arguments,
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_top_level_cli_exposes_product_setup() -> None:
    top_level_help = run_reckoning("--help")
    result = run_reckoning("setup", "--help")

    assert top_level_help.returncode == 0
    assert "reckoning setup" in top_level_help.stdout
    assert result.returncode == 0
    assert "usage: reckoning setup" in result.stdout
    assert "--telegram-config TELEGRAM_CONFIG" in result.stdout


def test_telegram_cli_rejects_product_setup() -> None:
    result = run_reckoning_telegram("setup")

    assert result.returncode == 2
    assert "invalid choice: 'setup'" in result.stderr
