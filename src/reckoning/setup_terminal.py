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
        self._output(
            f"Progress: {index - 1} complete; current section "
            f"{index} of {total} — {title}"
        )

    def info(self, text: str) -> None:
        self._output(text)

    def secondary(self, text: str) -> None:
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

    def step(self, index: int, total: int, title: str) -> None:
        progress = " ".join(
            "✓" if position < index else "◆" if position == index else "○"
            for position in range(1, total + 1)
        )
        self._output(
            f"{progress}  {self._color(title, '35')} "
            f"(section {index} of {total})"
        )

    def success(self, text: str) -> None:
        self._output(self._color(f"OK: {text}", "32"))

    def secondary(self, text: str) -> None:
        self._output(self._color(text, "2"))

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
        import select
        import termios
        import tty

        controls = (
            (("__back__", "Back"),) if allow_back else ()
        ) + (("__exit__", "Exit and save progress"),)
        query = ""
        selected_id: str = next(
            (
                option.id
                for option in options
                if option.available and not option.dim
            ),
            controls[0][0],
        )
        previous_height = 0

        def visible_options() -> tuple[MenuOption, ...]:
            if not query:
                return options
            needle = query.casefold()
            return tuple(
                option
                for option in options
                if needle in option.label.casefold()
                or needle in option.note.casefold()
            )

        def entries() -> tuple[tuple[str, str, bool, str, bool], ...]:
            option_entries = tuple(
                (
                    option.id,
                    option.label,
                    option.available and not option.dim,
                    option.note,
                    False,
                )
                for option in visible_options()
            )
            control_entries = tuple(
                (item_id, label, True, "", True) for item_id, label in controls
            )
            return option_entries + control_entries

        def select_first_available() -> None:
            nonlocal selected_id
            current = entries()
            if any(item[0] == selected_id and item[2] for item in current):
                return
            selected_id = next(item[0] for item in current if item[2])

        def render(*, redraw: bool) -> None:
            nonlocal previous_height
            current = entries()
            lines = [prompt]
            if query:
                lines.append(self._color(f"Filter: {query}", "2"))
            for item_id, label, enabled, note, control in current:
                if not enabled:
                    lines.append(self._color(f"    {label}", "2"))
                elif item_id == selected_id:
                    lines.append(self._color(f"  ◆ {label}", "35"))
                    if note:
                        lines.append(self._color(f"    {note}", "2"))
                elif control:
                    lines.append(self._color(f"  › {label}", "36"))
                else:
                    lines.append(f"  ○ {label}")
            supported = ["Up/Down to move"]
            if options:
                supported.append("type to filter")
            supported.append("Enter to select")
            if help_text:
                supported.append("'?' for help")
            supported.append("Esc to go back" if allow_back else "Esc to exit")
            lines.append(", ".join(supported))
            if redraw:
                self._output(f"\x1b[{previous_height}A\x1b[J{lines[0]}")
                for line in lines[1:]:
                    self._output(line)
            else:
                for line in lines:
                    self._output(line)
            previous_height = len(lines)

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
                    ready, _, _ = select.select([stream.fileno()], [], [], 0.03)
                    if not ready:
                        if allow_back:
                            raise SetupBack
                        raise SetupExit
                    rest = os.read(stream.fileno(), 2)
                    current = entries()
                    selectable = [item[0] for item in current if item[2]]
                    selected_index = selectable.index(selected_id)
                    if rest == b"[A":
                        selected_id = selectable[
                            (selected_index - 1) % len(selectable)
                        ]
                    elif rest == b"[B":
                        selected_id = selectable[
                            (selected_index + 1) % len(selectable)
                        ]
                    render(redraw=True)
                elif char in (b"\r", b"\n"):
                    break
                elif char == b"?":
                    text = help_text or HELP_HINT
                    self._output("")
                    self._output(text)
                    previous_height += text.count("\n") + 2
                elif char in (b"\x7f", b"\x08"):
                    if query:
                        query = query[:-1]
                        select_first_available()
                        render(redraw=True)
                elif char == b"\x03":  # Ctrl+C
                    raise SetupExit
                elif len(char) == 1 and 32 <= char[0] <= 126:
                    query += char.decode("ascii")
                    select_first_available()
                    render(redraw=True)
        finally:
            termios.tcsetattr(stream, termios.TCSADRAIN, old_settings)
        if selected_id == "__back__":
            raise SetupBack
        if selected_id == "__exit__":
            raise SetupExit
        chosen = next(item for item in entries() if item[0] == selected_id)
        self._output(f"Selected: {chosen[1]}")
        return selected_id


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

    def secondary(self, text: str) -> None:
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
