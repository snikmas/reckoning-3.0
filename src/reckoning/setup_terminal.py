"""Terminal adapters for the setup workflow.

Three implementations share the SetupUI seam:

- ``InteractiveUI``: arrow-key menus with semantic colors on a real terminal.
- ``PlainTextUI``: numbered prompts for limited or non-interactive terminals.
- ``NonInteractiveUI``: answers from a mapping for scripts and CI.

All copy comes from setup_copy; meaning never depends on color or symbols.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from getpass import getpass
from typing import Any

from reckoning.setup_copy import BRAND_LINE, HELP_HINT
from reckoning.setup_workflow import (
    MenuOption,
    SetupBack,
    SetupExit,
    SetupInputError,
)

BACK_KEYWORDS = ("back", "b")
EXIT_KEYWORDS = ("exit", "quit", "q")


def _default_line_reader(prompt: str) -> str:
    return input(prompt)


def _default_secret_reader(prompt: str) -> str:
    return getpass(prompt)


def _default_output(text: str) -> None:
    # Flush so interactive output lands before the workflow blocks on input;
    # the arrow-key menu reads with os.read and never flushes stdio itself.
    print(text, flush=True)


class PlainTextUI:
    """The no-color, no-Unicode, numbered-prompt fallback."""

    def __init__(
        self,
        *,
        line_reader: Callable[[str], str] | None = None,
        secret_reader: Callable[[str], str] | None = None,
        output: Callable[[str], None] | None = None,
    ) -> None:
        self._read = line_reader or _default_line_reader
        self._read_secret = secret_reader or _default_secret_reader
        self._output = output or _default_output

    def banner(self) -> None:
        self._output(BRAND_LINE)

    def step(self, index: int, total: int, title: str) -> None:
        self._output(f"Step {index}/{total} — {title}")

    def info(self, text: str) -> None:
        self._output(text)

    def success(self, text: str) -> None:
        self._output(f"OK: {text}")

    def warning(self, text: str) -> None:
        self._output(f"Warning: {text}")

    def failure(self, text: str) -> None:
        self._output(f"Failed: {text}")

    def choose(
        self,
        key: str,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool = False,
        help_text: str | None = None,
    ) -> str:
        self._output(prompt)
        selectable: list[MenuOption] = []
        for option in options:
            if option.available and not option.dim:
                selectable.append(option)
                note = f" — {option.note}" if option.note else ""
                self._output(f"  {len(selectable)}. {option.label}{note}")
            else:
                note = f" — {option.note}" if option.note else ""
                self._output(f"  -. {option.label} (not selectable){note}")
        if allow_back:
            self._output("  Type 'back' to return to the previous step.")
        self._output(f"  Type 'exit' to save a draft and leave. {HELP_HINT}")
        while True:
            raw = self._read("> ").strip()
            lowered = raw.casefold()
            if lowered in BACK_KEYWORDS and allow_back:
                raise SetupBack
            if lowered in EXIT_KEYWORDS:
                raise SetupExit
            if lowered == "?":
                self._output(help_text or HELP_HINT)
                continue
            try:
                choice = int(raw)
            except ValueError:
                # Allow choosing by option id for scripted runs.
                match = next(
                    (item for item in selectable if item.id == lowered), None
                )
                if match is not None:
                    return match.id
                self._output(f"Enter a number from 1 to {len(selectable)}.")
                continue
            if choice < 1 or choice > len(selectable):
                self._output(f"Enter a number from 1 to {len(selectable)}.")
                continue
            return selectable[choice - 1].id

    def ask(
        self,
        key: str,
        prompt: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = True,
    ) -> str:
        reader = self._read_secret if secret else self._read
        suffix = f" [{default}]" if default else ""
        while True:
            raw = reader(f"{prompt}{suffix} ").strip()
            if raw.casefold() in BACK_KEYWORDS:
                raise SetupBack
            if raw.casefold() in EXIT_KEYWORDS:
                raise SetupExit
            value = raw or default
            if value or allow_empty:
                return value
            self._output("A value is required.")

    def confirm(self, key: str, question: str, *, default: bool = False) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        raw = self._read(f"{question} {suffix} ").strip().casefold()
        if not raw:
            return default
        return raw in ("y", "yes")


class InteractiveUI(PlainTextUI):
    """Arrow-key menus with semantic colors on capable terminals.

    Falls back to numbered prompts when stdin is not a TTY, when ANSI is
    unsupported, or when NO_COLOR is set.
    """

    def __init__(
        self,
        *,
        line_reader: Callable[[str], str] | None = None,
        secret_reader: Callable[[str], str] | None = None,
        output: Callable[[str], None] | None = None,
        stream: Any = None,
        no_color: bool = False,
    ) -> None:
        super().__init__(
            line_reader=line_reader, secret_reader=secret_reader, output=output
        )
        self._stream = stream if stream is not None else sys.stdin
        self._no_color = no_color or "NO_COLOR" in os.environ

    def _interactive_capable(self) -> bool:
        return (
            not self._no_color
            and hasattr(self._stream, "isatty")
            and bool(self._stream.isatty())
            and os.name == "posix"
        )

    def _color(self, text: str, code: str) -> str:
        if self._no_color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def banner(self) -> None:
        self._output(self._color(BRAND_LINE, "36"))  # cyan branding

    def success(self, text: str) -> None:
        self._output(self._color(f"OK: {text}", "32"))

    def warning(self, text: str) -> None:
        self._output(self._color(f"Warning: {text}", "33"))

    def failure(self, text: str) -> None:
        self._output(self._color(f"Failed: {text}", "31"))

    def choose(
        self,
        key: str,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool = False,
        help_text: str | None = None,
    ) -> str:
        if not self._interactive_capable():
            return super().choose(
                key,
                prompt,
                options,
                allow_back=allow_back,
                help_text=help_text,
            )
        return self._arrow_choose(
            prompt, options, allow_back=allow_back, help_text=help_text
        )

    def _arrow_choose(
        self,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool,
        help_text: str | None,
    ) -> str:
        import termios
        import tty

        entries: list[tuple[str, str, bool]] = [
            (option.id, option.label, option.available and not option.dim)
            for option in options
        ]
        if allow_back:
            entries.append(("__back__", "Back", True))
        entries.append(("__exit__", "Exit (save a draft)", True))
        selectable = [index for index, entry in enumerate(entries) if entry[2]]
        position = selectable[0]

        # Frame height: prompt + one line per entry + the hint line.
        frame_height = len(entries) + 2
        # Extra lines printed below the frame (help text); the next redraw
        # must travel up over them too.
        extra_lines = 0

        def render(*, redraw: bool) -> None:
            nonlocal extra_lines
            if redraw:
                # Cursor up to the top of the previous frame and clear
                # everything below, so re-renders replace instead of stack.
                up = frame_height + extra_lines
                extra_lines = 0
                self._output(f"\x1b[{up}A\x1b[J{prompt}")
            else:
                self._output(prompt)
            for index, (option_id, label, enabled) in enumerate(entries):
                if not enabled:
                    self._output(self._color(f"    {label}", "2"))
                elif index == position:
                    self._output(self._color(f"  > {label}", "36"))
                else:
                    self._output(f"    {label}")
            self._output(
                "Up/Down to move, Enter to select"
                + (", '?' for help" if help_text else "")
            )

        stream: Any = self._stream
        render(redraw=False)
        old_settings = termios.tcgetattr(stream)
        try:
            # Read keys immediately without disabling the terminal's output
            # newline handling. tty.setraw() changes both input and output,
            # which makes each redrawn line continue at the previous column.
            input_settings = termios.tcgetattr(stream)
            tty.cfmakeraw(input_settings)
            input_settings[1] = old_settings[1]
            termios.tcsetattr(stream, termios.TCSAFLUSH, input_settings)
            while True:
                char = os.read(stream.fileno(), 1)
                if char == b"\x1b":
                    rest = os.read(stream.fileno(), 2)
                    if rest == b"[A":  # up
                        current = selectable.index(position)
                        position = selectable[(current - 1) % len(selectable)]
                    elif rest == b"[B":  # down
                        current = selectable.index(position)
                        position = selectable[(current + 1) % len(selectable)]
                    render(redraw=True)
                elif char in (b"\r", b"\n"):
                    break
                elif char == b"?":
                    text = help_text or HELP_HINT
                    self._output("")
                    self._output(text)
                    extra_lines += text.count("\n") + 2
                elif char == b"\x03":  # Ctrl+C
                    raise SetupExit
        finally:
            termios.tcsetattr(stream, termios.TCSADRAIN, old_settings)
        chosen = entries[position][0]
        if chosen == "__back__":
            raise SetupBack
        if chosen == "__exit__":
            raise SetupExit
        self._output(f"Selected: {entries[position][1]}")
        return chosen


class NonInteractiveUI:
    """Answers from a mapping; missing required answers fail loudly."""

    def __init__(
        self,
        answers: Mapping[str, str],
        *,
        output: Callable[[str], None] | None = None,
    ) -> None:
        self._answers = answers
        self._output = output or _default_output

    def banner(self) -> None:
        self._output(BRAND_LINE)

    def step(self, index: int, total: int, title: str) -> None:
        self._output(f"Step {index}/{total} — {title}")

    def info(self, text: str) -> None:
        self._output(text)

    def success(self, text: str) -> None:
        self._output(f"OK: {text}")

    def warning(self, text: str) -> None:
        self._output(f"Warning: {text}")

    def failure(self, text: str) -> None:
        self._output(f"Failed: {text}")

    def choose(
        self,
        key: str,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool = False,
        help_text: str | None = None,
    ) -> str:
        self._output(prompt)
        if key not in self._answers:
            valid = ", ".join(option.id for option in options if option.available)
            raise SetupInputError(
                f"non-interactive setup needs an answer for '{key}' "
                f"(one of: {valid})"
            )
        value = self._answers[key].strip()
        available = {option.id for option in options if option.available}
        if value in available:
            return value
        # Some steps accept free-form ids (a discovered model, a persona id).
        if key in ("provider-model", "provider-model-pick", "persona-select"):
            return value
        raise SetupInputError(
            f"the answer for '{key}' is not selectable: {value!r} "
            f"(one of: {', '.join(sorted(available))})"
        )

    def ask(
        self,
        key: str,
        prompt: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = True,
    ) -> str:
        self._output(prompt)
        if key not in self._answers:
            if default or allow_empty:
                return default
            raise SetupInputError(
                f"non-interactive setup needs an answer for '{key}'"
            )
        value = self._answers[key].strip()
        if not value and not allow_empty and not default:
            raise SetupInputError(f"the answer for '{key}' cannot be empty")
        return value or default

    def confirm(self, key: str, question: str, *, default: bool = False) -> bool:
        self._output(question)
        if key not in self._answers:
            return default
        return self._answers[key].strip().casefold() in ("y", "yes", "true", "1")
