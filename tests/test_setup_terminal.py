"""Renderer-level tests for behavior the command seam cannot prove."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from reckoning.setup_terminal import InteractiveUI, NonInteractiveUI, PlainTextUI
from reckoning.setup_workflow import (
    MenuOption,
    SetupBack,
    SetupExit,
    SetupInputError,
)


def make_plain(lines: list[str]):
    outputs: list[str] = []
    secrets: list[str] = []
    ui = PlainTextUI(
        line_reader=lambda prompt: next(lines),
        secret_reader=lambda prompt: secrets.append(prompt) or "typed-secret",
        output=outputs.append,
    )
    return ui, outputs, secrets


OPTIONS = (
    MenuOption("one", "First option"),
    MenuOption("two", "Second option"),
    MenuOption("soon", "A future option", available=False, dim=True),
)


def test_plain_text_menus_are_numbered_and_hide_unavailable_entries() -> None:
    ui, outputs, _ = make_plain(iter(["2"]))

    chosen = ui.choose("k", "Pick one:", OPTIONS)

    assert chosen == "two"
    rendered = "\n".join(outputs)
    assert "1. First option" in rendered
    assert "2. Second option" in rendered
    assert "A future option (not selectable)" in rendered
    assert "3. A future option" not in rendered


def test_plain_text_menus_accept_ids_for_scripted_runs() -> None:
    ui, _, _ = make_plain(iter(["one"]))
    assert ui.choose("k", "Pick one:", OPTIONS) == "one"


def test_plain_text_back_and_exit_are_keywords() -> None:
    ui, _, _ = make_plain(iter(["back"]))
    with pytest.raises(SetupBack):
        ui.choose("k", "Pick one:", OPTIONS, allow_back=True)

    ui, _, _ = make_plain(iter(["exit"]))
    with pytest.raises(SetupExit):
        ui.choose("k", "Pick one:", OPTIONS)


def test_plain_text_help_is_contextual() -> None:
    ui, outputs, _ = make_plain(iter(["?", "1"]))
    chosen = ui.choose("k", "Pick one:", OPTIONS, help_text="Providers answer runs.")
    assert chosen == "one"
    assert "Providers answer runs." in "\n".join(outputs)


def test_plain_text_status_never_relies_on_color_or_symbols() -> None:
    ui, outputs, _ = make_plain(iter([]))
    ui.success("done")
    ui.warning("careful")
    ui.failure("broke")
    rendered = "\n".join(outputs)
    assert "OK: done" in rendered
    assert "Warning: careful" in rendered
    assert "Failed: broke" in rendered
    assert "\x1b[" not in rendered


def test_signal_progress_names_the_current_section_and_every_state() -> None:
    outputs: list[str] = []
    ui = InteractiveUI(output=outputs.append)

    ui.step(3, 5, "Agent style")

    rendered = "\n".join(outputs)
    assert "✓ ✓ ◆ ○ ○" in rendered
    assert "Agent style" in rendered
    assert "section 3 of 5" in rendered


def test_plain_progress_names_completion_without_requiring_symbols() -> None:
    outputs: list[str] = []
    ui = PlainTextUI(output=outputs.append)

    ui.step(3, 5, "Agent style")

    assert outputs == ["Progress: 2 complete; current section 3 of 5 — Agent style"]


def test_secret_prompts_use_the_hidden_reader() -> None:
    ui, _, secrets = make_plain(iter([]))
    value = ui.ask("k", "Paste the key: ", secret=True)
    assert value == "typed-secret"
    assert secrets == ["Paste the key:  "]


def test_required_answers_reprompt_until_given() -> None:
    ui, outputs, _ = make_plain(iter(["", "  ", "value"]))
    assert ui.ask("k", "Name: ", allow_empty=False) == "value"
    assert "A value is required." in "\n".join(outputs)


def test_non_interactive_ui_fails_loudly_on_missing_answers() -> None:
    ui = NonInteractiveUI({}, output=lambda line: None)
    with pytest.raises(SetupInputError, match="'mode'"):
        ui.choose("mode", "Mode?", OPTIONS)


def test_non_interactive_ui_rejects_unselectable_answers() -> None:
    ui = NonInteractiveUI({"mode": "soon"}, output=lambda line: None)
    with pytest.raises(SetupInputError, match="not selectable"):
        ui.choose("mode", "Mode?", OPTIONS)


def test_non_interactive_ui_uses_confirm_defaults() -> None:
    ui = NonInteractiveUI({}, output=lambda line: None)
    assert ui.confirm("unknown", "Continue?", default=True) is True
    assert ui.confirm("unknown", "Continue?", default=False) is False
    ui = NonInteractiveUI({"go": "yes"}, output=lambda line: None)
    assert ui.confirm("go", "Continue?", default=False) is True


@pytest.mark.skipif(os.name != "posix", reason="arrow menus need a posix pty")
def test_arrow_menu_is_visible_redraws_in_place_and_restores_terminal() -> None:
    import pty
    import termios

    child = """
from reckoning.setup_terminal import InteractiveUI
from reckoning.setup_workflow import MenuOption, SetupExit

try:
    InteractiveUI().choose(
        "provider",
        "Choose an AI provider",
        (
            MenuOption("orcarouter", "OrcaRouter"),
            MenuOption("deepseek", "DeepSeek"),
            MenuOption("fake", "Demo - deterministic and offline"),
        ),
        allow_back=True,
        help_text="Filter providers by typing.",
    )
except SetupExit:
    print("EXIT")
"""

    def read_until(fd: int, marker: bytes) -> bytes:
        output = bytearray()
        deadline = time.monotonic() + 2
        while marker not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"terminal output stopped at {bytes(output)!r}"
            readable, _, _ = select.select([fd], [], [], remaining)
            assert readable, f"terminal output stopped at {bytes(output)!r}"
            output.extend(os.read(fd, 4096))
        return bytes(output)

    master, slave = pty.openpty()
    original_settings = termios.tcgetattr(slave)
    environment = os.environ.copy()
    environment.pop("NO_COLOR", None)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=environment,
        close_fds=True,
    )
    try:
        # No input is sent until the complete first menu is visible.
        initial = read_until(master, b"Enter to select")
        assert b"Choose an AI provider" in initial
        assert "◆ OrcaRouter".encode() in initial
        assert process.poll() is None

        deadline = time.monotonic() + 2
        while termios.tcgetattr(slave)[3] & termios.ICANON:
            assert time.monotonic() < deadline, "terminal never entered raw mode"
            time.sleep(0.01)

        os.write(master, b"\x1b[B")
        redraw = read_until(master, b"Enter to select")
        assert b"\x1b[J" in redraw
        assert "◆ DeepSeek".encode() in redraw

        os.write(master, b"\x03")
        read_until(master, b"EXIT")
        assert process.wait(timeout=2) == 0
        assert termios.tcgetattr(slave) == original_settings
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(os.name != "posix", reason="arrow menus need a posix pty")
def test_arrow_menu_filters_while_typing_and_escape_goes_back() -> None:
    import pty
    import termios

    child = """
from reckoning.setup_terminal import InteractiveUI
from reckoning.setup_workflow import MenuOption, SetupBack

try:
    InteractiveUI().choose(
        "provider",
        "Choose an AI provider",
        (
            MenuOption("first", "First Cloud"),
            MenuOption("second", "Second Local"),
        ),
        allow_back=True,
        help_text="Filter providers by typing.",
    )
except SetupBack:
    print("BACK")
"""

    def read_until(fd: int, marker: bytes) -> bytes:
        output = bytearray()
        deadline = time.monotonic() + 2
        while marker not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"terminal output stopped at {bytes(output)!r}"
            readable, _, _ = select.select([fd], [], [], remaining)
            assert readable, f"terminal output stopped at {bytes(output)!r}"
            output.extend(os.read(fd, 4096))
        return bytes(output)

    master, slave = pty.openpty()
    original_settings = termios.tcgetattr(slave)
    environment = os.environ.copy()
    environment.pop("NO_COLOR", None)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=environment,
        close_fds=True,
    )
    try:
        initial = read_until(master, b"Esc to go back")
        assert b"First Cloud" in initial
        assert b"Second Local" in initial

        for char in b"se":
            os.write(master, bytes((char,)))
            read_until(master, b"Esc to go back")
        os.write(master, b"c")
        filtered = read_until(master, b"Esc to go back")
        assert b"Filter: sec" in filtered
        assert b"Second Local" in filtered
        assert b"First Cloud" not in filtered

        os.write(master, b"\x1b")
        finished = read_until(master, b"BACK")
        assert b"BACK" in finished
        assert process.wait(timeout=2) == 0
        assert termios.tcgetattr(slave) == original_settings
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        os.close(master)
        os.close(slave)
