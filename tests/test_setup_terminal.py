"""Renderer-level tests for behavior the command seam cannot prove."""

from __future__ import annotations

import pytest

from reckoning.setup_terminal import NonInteractiveUI, PlainTextUI
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
