"""Renderer-level tests for behavior the command seam cannot prove."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from reckoning.setup_terminal import (
    InteractiveUI,
    MenuSession,
    NonInteractiveUI,
    PlainTextUI,
    SignalTheme,
)
from reckoning.setup_workflow import (
    MenuOption,
    SetupBack,
    SetupExit,
    SetupInputError,
)

NO_COLOR_UNICODE = SignalTheme(mode="none", unicode=True)
NO_COLOR_ASCII = SignalTheme(mode="none", unicode=False)


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

BIG_LIST = tuple(
    MenuOption(f"model-{index:02d}", f"model-{index:02d}", note="Served model")
    for index in range(1, 21)
)


def strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


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
    ui = InteractiveUI(output=outputs.append, theme=NO_COLOR_UNICODE)

    ui.step(3, 5, "Agent style")

    rendered = "\n".join(outputs)
    assert "RECKONING" in rendered
    assert "● ● ● ○ ○" in rendered
    assert "Agent style" in rendered
    assert "section 3 of 5" in rendered


def test_signal_progress_without_color_still_carries_position_in_text() -> None:
    outputs: list[str] = []
    ui = InteractiveUI(output=outputs.append, theme=NO_COLOR_ASCII)

    ui.step(1, 5, "AI provider and model")

    rendered = "\n".join(outputs)
    assert "\x1b[" not in rendered
    assert "* - - - -" not in rendered  # current section uses the pointer
    assert "> - - - -" in rendered
    assert "section 1 of 5" in rendered


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
        ui.choose("mode", "soon", OPTIONS)


def test_non_interactive_ui_uses_confirm_defaults() -> None:
    ui = NonInteractiveUI({}, output=lambda line: None)
    assert ui.confirm("unknown", "Continue?", default=True) is True
    assert ui.confirm("unknown", "Continue?", default=False) is False
    ui = NonInteractiveUI({"go": "yes"}, output=lambda line: None)
    assert ui.confirm("go", "Continue?", default=False) is True


# --- Signal menu session (pure rendering and transitions) --------------------


def render(session: MenuSession, theme: SignalTheme = NO_COLOR_UNICODE) -> str:
    return "\n".join(session.lines(theme, width=72, max_rows=8))


def test_selected_row_is_dominant_and_shows_its_explanation_nearby() -> None:
    options = (
        MenuOption("a", "Alpha", note="The first explanation."),
        MenuOption("b", "Beta", note="The second explanation."),
    )
    session = MenuSession("Pick:", options, allow_back=True, help_available=True)

    text = render(session)

    assert "┌─ Alpha" in text
    assert "│  The first explanation." in text
    assert "└─" in text
    assert "○ Beta" in text
    assert "The second explanation." not in text


def test_small_menus_are_arrow_only_and_the_footer_names_supported_controls() -> None:
    session = MenuSession("Pick:", OPTIONS, allow_back=True, help_available=True)

    controls = session.footer_controls(NO_COLOR_UNICODE)

    assert controls == [
        "↑↓ move",
        "Enter select",
        "? help",
        "Esc back",
        "Ctrl+C save & exit",
    ]
    assert session.searchable is False
    session.type_char("x")
    assert session.query == ""


def test_large_lists_are_searchable_and_footer_says_so() -> None:
    session = MenuSession("Pick:", BIG_LIST, allow_back=False, help_available=False)

    controls = session.footer_controls(NO_COLOR_UNICODE)

    assert session.searchable is True
    assert controls == [
        "↑↓ move",
        "type to filter",
        "Enter select",
        "Esc exit",
        "Ctrl+C save & exit",
    ]


def test_typed_filtering_updates_the_visible_choices() -> None:
    session = MenuSession("Pick:", BIG_LIST, allow_back=True, help_available=False)
    for char in "model-1":
        session.type_char(char)

    visible = session.visible_options()

    assert len(visible) == 10  # model-10 .. model-19
    assert all(option.label.startswith("model-1") for option in visible)
    text = render(session)
    assert "model-10" in text
    assert "model-01" not in text


def test_escape_clears_an_active_filter_before_navigating_back() -> None:
    session = MenuSession("Pick:", BIG_LIST, allow_back=True, help_available=False)
    session.type_char("2")

    assert session.escape() == "cleared"
    assert session.query == ""
    assert session.visible_options() == BIG_LIST
    assert session.escape() == "back"


def test_escape_exits_when_no_back_is_available() -> None:
    session = MenuSession("Pick:", OPTIONS, allow_back=False, help_available=False)
    assert session.escape() == "exit"


def test_empty_filter_shows_a_textual_no_match_state() -> None:
    session = MenuSession("Pick:", BIG_LIST, allow_back=True, help_available=False)
    for char in "zzz":
        session.type_char(char)

    text = render(session)

    assert session.selected_id is None
    assert "No matches; Backspace changes the filter." in text


def test_long_lists_scroll_and_report_choices_above_and_below() -> None:
    session = MenuSession("Pick:", BIG_LIST, allow_back=True, help_available=False)

    first_page = render(session)
    assert "12 more below" in first_page
    assert "more above" not in first_page
    assert "model-01" in first_page
    assert "model-20" not in first_page

    for _ in range(19):
        session.move(1)
    last_page = render(session)
    assert "12 more above" in last_page
    assert "more below" not in last_page
    assert "┌─ model-20" in last_page
    assert "model-01" not in last_page


def test_filter_placeholder_and_label_keep_filtering_discoverable() -> None:
    session = MenuSession("Pick:", BIG_LIST, allow_back=True, help_available=False)

    text = render(session)

    assert "Filter" in text
    assert "Start typing to filter" in text


def test_no_color_rendering_preserves_every_label_explanation_and_control() -> None:
    session = MenuSession(
        "Pick a model:",
        (MenuOption("a", "Alpha", note="Explained nearby."),) + BIG_LIST[:8],
        allow_back=True,
        help_available=True,
    )

    text = render(session)

    assert "\x1b[" not in text
    assert "Pick a model:" in text
    assert "Alpha" in text
    assert "Explained nearby." in text
    assert "Esc back" in text
    assert "Ctrl+C save & exit" in text


def test_ascii_fallback_replaces_box_symbols_but_keeps_the_structure() -> None:
    options = (MenuOption("a", "Alpha", note="Detail."), MenuOption("b", "Beta"))
    session = MenuSession("Pick:", options, allow_back=True, help_available=False)

    text = render(session, theme=NO_COLOR_ASCII)

    assert "> Alpha" in text
    assert "|  Detail." in text
    assert "- Beta" in text
    assert "┌" not in text and "└" not in text and "│" not in text
    assert "Up/Down move" in text


def test_unavailable_rows_stay_visible_but_muted_and_unselectable() -> None:
    session = MenuSession("Pick:", OPTIONS, allow_back=True, help_available=False)

    text = render(session)
    session.move(1)
    session.move(1)  # wraps past the unavailable row back to the first

    assert "A future option" in text
    assert session.selected_id == "one"


def test_extended_and_basic_themes_pick_distinct_signal_palettes() -> None:
    extended = SignalTheme(mode="extended", unicode=True)
    basic = SignalTheme(mode="basic", unicode=True)

    assert extended.paint("navigation", "x") == "\x1b[38;5;45mx\x1b[0m"
    assert extended.paint("current", "x") == "\x1b[38;5;141mx\x1b[0m"
    assert basic.paint("navigation", "x") == "\x1b[36mx\x1b[0m"
    assert basic.paint("current", "x") == "\x1b[35mx\x1b[0m"


def test_no_color_mode_disables_all_escape_sequences() -> None:
    theme = SignalTheme(mode="none", unicode=True)
    for role in ("brand", "navigation", "current", "verified", "warning", "failure"):
        assert theme.paint(role, "text", bold=True) == "text"  # type: ignore[arg-type]
    assert theme.bold("text") == "text"


# --- Controlled-terminal integration through a real pty ----------------------


def run_menu_in_pty(child: str, driver) -> bytes:
    import pty
    import termios

    master, slave = pty.openpty()
    original_settings = termios.tcgetattr(slave)
    environment = os.environ.copy()
    environment.pop("NO_COLOR", None)
    environment["TERM"] = "xterm-256color"
    environment["LC_ALL"] = "C.UTF-8"
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
        output = driver(master)
        assert process.wait(timeout=2) == 0
        assert termios.tcgetattr(slave) == original_settings
        return output
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        os.close(master)
        os.close(slave)


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


@pytest.mark.skipif(os.name != "posix", reason="arrow menus need a posix pty")
def test_arrow_menu_is_visible_redraws_in_place_and_restores_terminal() -> None:
    child = """
from reckoning.setup_terminal import InteractiveUI
from reckoning.setup_workflow import MenuOption, SetupExit

try:
    InteractiveUI().choose(
        "provider",
        "Choose an AI provider",
        (
            MenuOption("orcarouter", "OrcaRouter", note="One key, many models."),
            MenuOption("deepseek", "DeepSeek"),
            MenuOption("fake", "Demo - deterministic and offline"),
        ),
        allow_back=True,
        help_text="Filter providers by typing.",
    )
except SetupExit:
    print("EXIT")
"""
    collected = bytearray()

    def driver(master: int) -> bytes:
        # No input is sent until the complete first menu is visible.
        initial = read_until(master, b"Esc back")
        collected.extend(initial)
        assert b"Choose an AI provider" in initial
        assert "┌─ OrcaRouter".encode() in initial
        # The selected row's explanation renders nearby.
        assert b"One key, many models." in initial
        # The footer names only controls this screen supports.
        assert b"type to filter" not in initial
        assert b"Ctrl+C save & exit" in initial

        os.write(master, b"\x1b[B")
        redraw = read_until(master, b"Esc back")
        collected.extend(redraw)
        assert b"\x1b[J" in redraw
        assert "┌─ DeepSeek".encode() in redraw

        os.write(master, b"\x03")
        collected.extend(read_until(master, b"EXIT"))
        return bytes(collected)

    run_menu_in_pty(child, driver)


@pytest.mark.skipif(os.name != "posix", reason="arrow menus need a posix pty")
def test_arrow_menu_filters_while_typing_and_escape_goes_back() -> None:
    options = ",\n".join(
        f'            MenuOption("opt-{index:02d}", "Cloud model {index:02d}")'
        for index in range(1, 9)
    )
    child = f"""
from reckoning.setup_terminal import InteractiveUI
from reckoning.setup_workflow import MenuOption, SetupBack

try:
    InteractiveUI().choose(
        "provider-model-pick",
        "Choose a model",
        (
{options},
        ),
        allow_back=True,
        help_text="Filter models by typing.",
    )
except SetupBack:
    print("BACK")
"""
    collected = bytearray()

    def driver(master: int) -> bytes:
        initial = read_until(master, b"Esc back")
        collected.extend(initial)
        assert b"type to filter" in initial
        assert b"Start typing to filter" in initial

        for char in b"model 03":
            os.write(master, bytes((char,)))
            filtered = read_until(master, b"Esc back")
        collected.extend(filtered)
        frame = filtered.rsplit(b"\x1b[J", 1)[-1]
        assert b"Cloud model 03" in frame
        assert b"Cloud model 01" not in frame

        # The first Escape clears the filter; the second goes back.
        os.write(master, b"\x1b")
        cleared = read_until(master, b"Esc back")
        collected.extend(cleared)
        frame = cleared.rsplit(b"\x1b[J", 1)[-1]
        assert b"Cloud model 01" in frame
        assert b"Start typing to filter" in frame

        os.write(master, b"\x1b")
        collected.extend(read_until(master, b"BACK"))
        return bytes(collected)

    run_menu_in_pty(child, driver)
