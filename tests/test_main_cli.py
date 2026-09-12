from __future__ import annotations

import io
import os
import subprocess
import sys
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import reckoning.command as command_module
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


def test_bare_reckoning_runs_terminal_and_web_is_an_explicit_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        command_module,
        "run_terminal",
        lambda arguments: calls.append(("terminal", tuple(arguments))) or 0,
        raising=False,
    )
    monkeypatch.setattr(
        command_module,
        "run_web",
        lambda arguments: calls.append(("web", tuple(arguments))) or 0,
    )

    assert main([]) == 0
    assert main(["web", "--port", "8123"]) == 0
    assert calls == [("terminal", ()), ("web", ("--port", "8123"))]


def test_gateway_records_truthful_runtime_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning.runtime_status import runtime_path

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    observed: dict[str, bool] = {}

    def fake_gateway(arguments: object, *, prog: str) -> int:
        observed["recorded"] = runtime_path(data_dir).exists()
        return 0

    monkeypatch.setattr(command_module, "run_gateway", fake_gateway)
    returncode = main(["gateway", "--data-dir", str(data_dir)])

    assert returncode == 0
    assert observed["recorded"] is True
    assert not runtime_path(data_dir).exists()


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


def test_setup_help_has_one_guided_path_without_a_mode_flag() -> None:
    returncode, stdout, _ = run_dispatcher("setup", "--help")

    assert returncode == 0
    assert "[--mode " not in stdout
    assert "  --mode " not in stdout
    assert "Quick" not in stdout
    assert "Custom setup" not in stdout


def test_configuration_commands_have_one_home_under_setup() -> None:
    returncode, stdout, _ = run_dispatcher("--help")

    assert returncode == 0
    for obsolete in ("reckoning provider", "reckoning persona", "reckoning channel"):
        assert obsolete not in stdout

    for obsolete in ("provider", "persona", "channel"):
        returncode, _, stderr = run_dispatcher(obsolete)
        assert returncode == 2
        assert f"unknown command '{obsolete}'" in stderr


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
            "fake",
            "skip",
            "simon",
            "y",
            "skip",
            "continue",
            "I want to protect my study time.",
            "accept",
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
    assert "current section 1 of 5" in stdout
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
    assert payload["result"] == "complete"
    assert payload["provider"] == "fake"
    assert payload["demo"] is True


def test_non_interactive_json_reports_invalid_setup_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as jsonlib

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    returncode, stdout, stderr = run_dispatcher(
        "setup",
        "--non-interactive",
        "--json",
        "--provider",
        "deepseek",
        "--data-dir",
        str(tmp_path / "invalid-json-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 2
    assert stderr == ""
    payload = jsonlib.loads(stdout)
    assert payload["status"] == "invalid"
    assert payload["result"] == "invalid"
    assert "DEEPSEEK_API_KEY is required" in payload["error"]
    assert payload["activated"] is False


def test_json_reports_an_interrupted_setup_as_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as jsonlib

    answers = iter(("exit",))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    returncode, stdout, stderr = run_dispatcher(
        "setup",
        "--json",
        "--data-dir",
        str(tmp_path / "incomplete-json-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 1
    assert stderr == ""
    payload = jsonlib.loads(stdout.strip().splitlines()[-1])
    assert payload["status"] == "draft"
    assert payload["result"] == "incomplete"
    assert payload["demo"] is False


def test_command_line_secret_values_are_rejected_without_echoing_them(
    tmp_path: Path,
) -> None:
    secret = "sk-must-not-appear"

    returncode, stdout, stderr = run_dispatcher(
        "setup",
        "--non-interactive",
        "--api-key",
        secret,
        "--data-dir",
        str(tmp_path / "secret-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 2
    assert secret not in stdout
    assert secret not in stderr
    assert "--credential-env" in stderr


@pytest.mark.parametrize(
    ("provider_args", "expected"),
    (
        (("--credential-env", "CUSTOM_TOKEN"), "requires --provider"),
        (
            ("--provider", "deepseek", "--header-env", "X-Tenant=CUSTOM_TENANT"),
            "only valid with --provider custom",
        ),
    ),
)
def test_provider_specific_script_flags_reject_ambiguous_combinations(
    tmp_path: Path, provider_args: tuple[str, ...], expected: str
) -> None:
    returncode, _stdout, stderr = run_dispatcher(
        "setup",
        "--non-interactive",
        *provider_args,
        "--data-dir",
        str(tmp_path / "invalid-flags-instance"),
        *setup_paths(tmp_path),
    )

    assert returncode == 2
    assert expected in stderr
    assert not (tmp_path / "invalid-flags-instance").exists()


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
