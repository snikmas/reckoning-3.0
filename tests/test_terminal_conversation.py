"""Acceptance test for the `reckoning` Terminal conversation.

The message must travel through the installed provider and persona and
persist through the interface repository, not just echo in the process.
"""

from __future__ import annotations

from pathlib import Path

from reckoning.command import main as command_main
from reckoning.interfaces import JsonFileInterfaceRepository
from reckoning.operations import load_installation_runtime
from reckoning.terminal import main as terminal_main


def _install(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    data_dir = tmp_path / "data"
    credentials = tmp_path / "provider.json"
    telegram = tmp_path / "telegram.json"
    draft = tmp_path / "setup-draft.json"
    returncode = command_main(
        [
            "setup",
            "--non-interactive",
            "--data-dir",
            str(data_dir),
            "--credentials",
            str(credentials),
            "--telegram-config",
            str(telegram),
            "--draft-path",
            str(draft),
        ]
    )
    assert returncode == 0
    return data_dir, credentials, telegram, draft


def test_terminal_message_uses_installed_persona_and_persists(
    tmp_path: Path,
) -> None:
    data_dir, credentials, _telegram, _draft = _install(tmp_path)
    replies = iter(["What should I focus on today?", "/exit"])
    outputs: list[str] = []

    returncode = terminal_main(
        ["--data-dir", str(data_dir), "--credentials", str(credentials)],
        line_reader=lambda prompt: next(replies),
        output=outputs.append,
    )

    assert returncode == 0
    assert any(line.startswith("Simon: ") for line in outputs)
    assert any("Reckoning stopped." in line for line in outputs)

    runtime = load_installation_runtime(data_dir)
    state = JsonFileInterfaceRepository(
        runtime.state_path("confirmed-state", "interfaces.json")
    ).load()
    terminal_sessions = [
        session for session in state.sessions if session.channel == "terminal"
    ]
    assert len(terminal_sessions) == 1
    contents = [
        (message.role, message.content)
        for message in terminal_sessions[0].messages
    ]
    assert contents[0] == ("user", "What should I focus on today?")
    assert contents[1][0] == "assistant"
    assert contents[1][1].strip()


def test_terminal_exits_without_a_durable_session_when_nothing_is_sent(
    tmp_path: Path,
) -> None:
    data_dir, credentials, _telegram, _draft = _install(tmp_path)
    replies = iter(["/exit"])
    outputs: list[str] = []

    returncode = terminal_main(
        ["--data-dir", str(data_dir), "--credentials", str(credentials)],
        line_reader=lambda prompt: next(replies),
        output=outputs.append,
    )

    assert returncode == 0
    runtime = load_installation_runtime(data_dir)
    state = JsonFileInterfaceRepository(
        runtime.state_path("confirmed-state", "interfaces.json")
    ).load()
    assert not any(session.channel == "terminal" for session in state.sessions)
