from __future__ import annotations

import io
import os
import subprocess
import sys
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from reckoning.command import main


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


def run_dispatcher(*arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            returncode = main(list(arguments))
        except SystemExit as error:
            returncode = int(error.code or 0)
    return returncode, stdout.getvalue(), stderr.getvalue()


def test_installed_package_exposes_exactly_one_binary() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["scripts"] == {"reckoning": "reckoning.command:main"}


def test_grouped_help_lists_the_whole_command_tree() -> None:
    returncode, stdout, _ = run_dispatcher("--help")

    assert returncode == 0
    assert stdout.index("Run") < stdout.index("Setup") < stdout.index("Data")
    run_section = stdout.split("Setup")[0]
    assert "reckoning gateway" in run_section
    setup_section = stdout.split("Setup")[1].split("Data")[0]
    assert "reckoning setup" in setup_section
    assert "reckoning doctor" in setup_section
    data_section = stdout.split("Data")[1]
    for command in ("backup", "restore", "export", "migrate"):
        assert f"reckoning {command}" in data_section


def test_subprocess_help_matches_the_dispatcher_seam() -> None:
    result = run_reckoning("--help")

    assert result.returncode == 0
    assert "Run" in result.stdout
    assert "reckoning gateway" in result.stdout
    assert "reckoning-ops" not in result.stdout
    assert "reckoning-telegram" not in result.stdout


def test_every_subcommand_accepts_help() -> None:
    for command in (
        "setup",
        "provider",
        "persona",
        "channel",
        "reset",
        "gateway",
        "doctor",
        "backup",
        "restore",
        "export",
        "migrate",
    ):
        returncode, stdout, _ = run_dispatcher(command, "--help")

        assert returncode == 0, command
        assert f"usage: reckoning {command}" in stdout


def test_gateway_help_describes_channels_not_a_telegram_command() -> None:
    returncode, stdout, _ = run_dispatcher("gateway", "--help")

    assert returncode == 0
    assert "every configured channel" in stdout


def test_unknown_commands_are_rejected() -> None:
    for unknown in ("reckoning-ops", "telegram", "diagnose", "ops"):
        returncode, _, stderr = run_dispatcher(unknown)

        assert returncode == 2, unknown
        assert f"unknown command '{unknown}'" in stderr
        assert "reckoning --help" in stderr


def test_help_renders_rich_panels() -> None:
    returncode, stdout, _ = run_dispatcher("--help")

    assert returncode == 0
    assert "╭" in stdout


def setup_paths(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--credentials",
        str(tmp_path / "provider.json"),
        "--telegram-config",
        str(tmp_path / "telegram.json"),
        "--draft-path",
        str(tmp_path / "setup-draft.json"),
    )


def test_the_interactive_wizard_uses_the_plain_text_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(
        (
            "quick",
            "simon",
            "y",
            "browse",
            "fake",
            "skip",
            "skip",
            "I want to protect my study time.",
            "accept",
            "y",
        )
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, _ = run_dispatcher(
        "setup",
        "--data-dir",
        str(tmp_path / "wizard-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 0
    assert "== RECKONING setup ==" in stdout
    assert "Step 1/6" in stdout
    assert "Demo mode" in stdout
    assert "Setup complete" in stdout


def test_non_interactive_setup_stays_plain_for_scripts(
    tmp_path: Path,
) -> None:
    returncode, stdout, _ = run_dispatcher(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(tmp_path / "scripted-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 0
    assert "╭" not in stdout
    assert "Setup complete" in stdout


def test_non_interactive_setup_reports_stable_json(tmp_path: Path) -> None:
    import json as jsonlib

    returncode, stdout, _ = run_dispatcher(
        "setup",
        "--non-interactive",
        "--json",
        "--data-dir",
        str(tmp_path / "json-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 0
    payload = jsonlib.loads(stdout.strip().splitlines()[-1])
    assert payload["status"] == "activated"
    assert payload["provider"] == "fake"
    assert payload["demo"] is True


def test_non_interactive_setup_requires_env_references_for_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    returncode, _, stderr = run_dispatcher(
        "setup",
        "--non-interactive",
        "--provider",
        "deepseek",
        "--data-dir",
        str(tmp_path / "env-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 2
    assert "DEEPSEEK_API_KEY is required" in stderr
    assert not (tmp_path / "env-instance").exists()


def test_gateway_rejects_product_setup() -> None:
    returncode, _, stderr = run_dispatcher("gateway", "setup")

    assert returncode == 2
    assert "invalid choice: 'setup'" in stderr
