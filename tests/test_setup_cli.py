"""Focused command and error-reporting tests at the CLI seam."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from reckoning.command import main
from reckoning.config import ProviderCredentialStore
from reckoning.setup_copy import BRAND_LINE


def run_cli(*arguments: str, stdin: io.StringIO | None = None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            returncode = main(list(arguments))
        except SystemExit as error:
            returncode = int(error.code or 0)
    return returncode, stdout.getvalue(), stderr.getvalue()


def cli_paths(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--credentials",
        str(tmp_path / "provider.json"),
        "--telegram-config",
        str(tmp_path / "telegram.json"),
        "--draft-path",
        str(tmp_path / "setup-draft.json"),
    )


def quick_setup(tmp_path: Path, name: str = "instance") -> Path:
    data_dir = tmp_path / name
    returncode, _stdout, stderr = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(data_dir),
        *cli_paths(tmp_path),
    )
    assert returncode == 0, stderr
    return data_dir


def test_provider_list_reports_verified_and_inactive_states(tmp_path: Path) -> None:
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-good", verified=True)
    store.set_key("orcarouter", "sk-pending", verified=False)
    store.save(tmp_path / "provider.json")

    returncode, stdout, _ = run_cli(
        "provider", "--list", "--credentials", str(tmp_path / "provider.json")
    )

    assert returncode == 0
    assert "deepseek: verified (default)" in stdout
    assert "orcarouter: inactive (unverified)" in stdout
    assert "sk-good" not in stdout
    assert "sk-pending" not in stdout


def test_persona_list_marks_the_active_persona(tmp_path: Path) -> None:
    data_dir = quick_setup(tmp_path)

    returncode, stdout, _ = run_cli(
        "persona", "--list", "--data-dir", str(data_dir)
    )

    assert returncode == 0
    assert "simon: Simon (active)" in stdout
    assert "steady: Steady" in stdout


def test_channel_list_reports_connector_status(tmp_path: Path) -> None:
    quick_setup(tmp_path)

    returncode, stdout, _ = run_cli(
        "channel", "--list", "--telegram-config", str(tmp_path / "telegram.json")
    )

    assert returncode == 0
    assert "telegram: not-configured" in stdout


def test_reset_previews_exact_targets_and_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = quick_setup(tmp_path)
    ProviderCredentialStore().save(tmp_path / "provider.json")

    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    returncode, stdout, _ = run_cli(
        "reset",
        "--data-dir",
        str(data_dir),
        "--credentials",
        str(tmp_path / "provider.json"),
        "--telegram-config",
        str(tmp_path / "telegram.json"),
        "--draft-path",
        str(tmp_path / "setup-draft.json"),
    )

    assert returncode == 1
    assert str(data_dir) in stdout
    assert "aborted" in stdout
    assert data_dir.exists()

    returncode, stdout, _ = run_cli(
        "reset",
        "--yes",
        "--data-dir",
        str(data_dir),
        "--credentials",
        str(tmp_path / "provider.json"),
        "--telegram-config",
        str(tmp_path / "telegram.json"),
        "--draft-path",
        str(tmp_path / "setup-draft.json"),
    )

    assert returncode == 0
    assert "removed the previewed targets" in stdout
    assert not data_dir.exists()
    assert not (tmp_path / "provider.json").exists()


def test_reset_on_an_empty_machine_is_a_noop(tmp_path: Path) -> None:
    returncode, stdout, _ = run_cli(
        "reset",
        "--yes",
        "--data-dir",
        str(tmp_path / "absent"),
        "--credentials",
        str(tmp_path / "absent.json"),
        "--telegram-config",
        str(tmp_path / "absent2.json"),
        "--draft-path",
        str(tmp_path / "absent3.json"),
    )
    assert returncode == 0
    assert "nothing to remove" in stdout


def test_setup_on_a_configured_installation_opens_the_status_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = quick_setup(tmp_path)
    answers = iter(("exit",))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, _ = run_cli(
        "setup", "--data-dir", str(data_dir), *cli_paths(tmp_path)
    )

    assert returncode == 0
    assert "Installation status" in stdout
    assert "Verify all" in stdout


def test_failures_are_concise_without_debug_and_traced_with_it(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "instance.json").mkdir()  # a directory where a file is expected

    returncode, _, stderr = run_cli(
        "setup", "--data-dir", str(broken), *cli_paths(tmp_path)
    )
    assert returncode == 2
    assert "setup failed:" in stderr
    assert "Traceback" not in stderr

    returncode, _, stderr = run_cli(
        "setup", "--debug", "--data-dir", str(broken), *cli_paths(tmp_path)
    )
    assert returncode == 2
    assert "Traceback" in stderr
    assert "setup failed:" in stderr


def test_an_interrupted_setup_exits_with_an_incomplete_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The local-runtime probe may find a real runtime on the dev machine;
    # answer the detection prompt before exiting at the provider catalog.
    answers = iter(("quick", "simon", "y", "n", "exit"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, _ = run_cli(
        "setup",
        "--data-dir",
        str(tmp_path / "paused"),
        *cli_paths(tmp_path),
    )

    assert returncode == 1
    assert "paused" in stdout
    draft = json.loads((tmp_path / "setup-draft.json").read_text())
    assert draft["completed"] == ["persona"]
    assert not (tmp_path / "paused").exists()


def test_interactive_setup_prints_each_step_before_waiting_for_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = io.StringIO()
    snapshots: list[str] = []
    answers = iter(("quick", "exit"))

    def fake_input(prompt: str) -> str:
        snapshots.append(stdout.getvalue())
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    with redirect_stdout(stdout):
        returncode = main(
            ["setup", "--data-dir", str(tmp_path / "paused"), *cli_paths(tmp_path)]
        )

    assert returncode == 1
    assert len(snapshots) == 2
    # The banner and the first menu are visible before the first key is read.
    assert BRAND_LINE in snapshots[0]
    assert "Quick Setup" in snapshots[0]
    # The next step renders before setup waits again.
    assert len(snapshots[1]) > len(snapshots[0])
    assert "persona" in snapshots[1].lower()


def test_the_persona_command_edits_only_the_persona_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = quick_setup(tmp_path)
    answers = iter(("select", "steady"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, _ = run_cli(
        "persona",
        "--data-dir",
        str(data_dir),
        "--credentials",
        str(tmp_path / "provider.json"),
        "--telegram-config",
        str(tmp_path / "telegram.json"),
    )

    assert returncode == 0
    assert "Active persona: Steady" in stdout
    personas = json.loads((data_dir / "personas.json").read_text())
    assert personas["active_persona_id"] == "steady"
    # Nothing else was touched.
    instance = json.loads((data_dir / "instance.json").read_text())
    assert instance["activation"]["provider"] == "fake"


def test_focused_commands_refuse_an_unconfigured_installation(
    tmp_path: Path,
) -> None:
    returncode, _, stderr = run_cli(
        "persona",
        "--data-dir",
        str(tmp_path / "absent"),
        "--credentials",
        str(tmp_path / "provider.json"),
    )
    assert returncode == 2
    assert "run reckoning setup first" in stderr
