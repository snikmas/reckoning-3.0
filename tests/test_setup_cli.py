"""Focused command and error-reporting tests at the CLI seam."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from reckoning.command import main
from reckoning.config import ProviderCredentialStore
from reckoning.operations import setup_instance
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


def test_setup_status_reports_provider_style_about_and_connectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = quick_setup(tmp_path)
    answers = iter(("exit",))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, stderr = run_cli(
        "setup", "--data-dir", str(data_dir), *cli_paths(tmp_path)
    )

    assert returncode == 0, stderr
    assert "Installation status" in stdout
    assert "Storage: local" in stdout
    assert "Agent style: Simon" in stdout
    assert "AI provider and model: fake (demo mode)" in stdout
    assert "Interfaces: Terminal ready; Web ready" in stdout
    assert "Telegram: Not configured" in stdout
    assert "Gateway: Stopped; start with `reckoning gateway`" in stdout


def test_setup_edits_the_agent_style_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = quick_setup(tmp_path)
    answers = iter(("edit", "persona", "select", "steady", "exit"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, stderr = run_cli(
        "setup", "--data-dir", str(data_dir), *cli_paths(tmp_path)
    )

    assert returncode == 0, stderr
    assert "Active persona: Steady" in stdout
    personas = json.loads((data_dir / "personas.json").read_text())
    assert personas["active_persona_id"] == "steady"
    # Editing the style did not disturb the installed provider.
    instance = json.loads((data_dir / "instance.json").read_text())
    assert instance["activation"]["provider"] == "fake"


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
    assert "provider credentials preserved" in stdout
    assert not data_dir.exists()
    assert (tmp_path / "provider.json").exists()


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


def test_reset_preserves_provider_credentials_unless_explicitly_included(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    setup_instance(data_dir, "local")
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-kept", verified=True)
    store.save(tmp_path / "provider.json")

    returncode, stdout, stderr = run_cli(
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

    assert returncode == 0, stderr
    assert "provider credentials preserved" in stdout
    assert ProviderCredentialStore.load(
        tmp_path / "provider.json"
    ).credential_for("deepseek") is not None


def test_reset_removes_the_configured_personal_server_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    server_dir = tmp_path / "server"
    setup_instance(
        data_dir,
        "personal-server",
        server_data_dir=server_dir,
    )

    returncode, stdout, stderr = run_cli(
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

    assert returncode == 0, stderr
    assert str(server_dir) in stdout
    assert not data_dir.exists()
    assert not server_dir.exists()


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
    answers = iter(("fake", "skip", "simon", "y", "skip", "exit"))
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
    assert draft["completed"] == ["provider", "connectors", "persona", "profile"]
    assert draft["next_step"] == "review"
    assert not (tmp_path / "paused").exists()


def test_interactive_setup_prints_each_step_before_waiting_for_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = io.StringIO()
    snapshots: list[str] = []
    answers = iter(("fake", "exit"))

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
    assert "Choose an AI provider" in snapshots[0]
    # The next step renders before setup waits again.
    assert len(snapshots[1]) > len(snapshots[0])
    assert "Ways to use Reckoning" in snapshots[1]


def test_setup_on_an_unconfigured_installation_starts_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(("exit",))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    returncode, stdout, stderr = run_cli(
        "setup",
        "--data-dir",
        str(tmp_path / "absent"),
        *cli_paths(tmp_path),
    )

    assert returncode == 1, stderr
    assert "Installation status" not in stdout
    assert "Choose an AI provider" in stdout
    assert (tmp_path / "setup-draft.json").exists()
