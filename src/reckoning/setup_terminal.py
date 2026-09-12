"""Terminal adapters for the setup workflow: the Signal presentation.

Three implementations share the SetupUI seam:

- ``InteractiveUI``: the Signal renderer — arrow-key menus, semantic colors,
  progress dots, and searchable lists — on capable terminals.
- ``PlainTextUI``: numbered prompts for limited or non-interactive terminals.
- ``NonInteractiveUI``: answers from a mapping for scripts and CI.

Signal semantics: cyan marks the brand and the selected row, violet marks the
current section and the selected row's explanation, green marks verified
success, amber and red mark warnings and failures, and dim text carries
secondary copy. Color and symbols never carry meaning alone; every state also
has text.

Setup uses the normal terminal buffer and redraws only the active menu
region, so scrollback is preserved. All user-facing copy lives in
``setup_copy`` for later localization. Behavior is identical on Linux, macOS,
and WSL: any posix terminal with a TTY gets the interactive renderer, color
degrades from 256-color to basic ANSI to none (``NO_COLOR``), and box symbols
degrade to ASCII when the terminal encoding is not UTF-8.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from getpass import getpass
from typing import Any, Literal

from reckoning.setup_copy import (
    BRAND_LINE,
    BRAND_NAME,
    CONTROL_BACK,
    CONTROL_EXIT,
    CONTROL_FILTER,
    CONTROL_HELP,
    CONTROL_MOVE,
    CONTROL_SAVE_AND_EXIT,
    CONTROL_SELECT,
    FILTER_EMPTY,
    FILTER_LABEL,
    FILTER_PLACEHOLDER,
    HELP_HINT,
    MORE_ABOVE,
    MORE_BELOW,
    PLAIN_BACK_HINT,
    PLAIN_ENTER_NUMBER,
    PLAIN_EXIT_HINT,
    PLAIN_NOT_SELECTABLE,
    PLAIN_PROGRESS,
    PLAIN_VALUE_REQUIRED,
    PROGRESS_POSITION,
    SELECTED_ECHO,
    STATUS_FAILURE_PREFIX,
    STATUS_OK_PREFIX,
    STATUS_WARNING_PREFIX,
)
from reckoning.setup_workflow import (
    MenuOption,
    SetupBack,
    SetupExit,
    SetupInputError,
)

BACK_KEYWORDS = ("back", "b")
EXIT_KEYWORDS = ("exit", "quit", "q")

# Lists at or above this size gain the filter line and typed filtering;
# smaller decision menus stay arrow-only so the footer names only the
# controls the screen actually supports.
SEARCH_MIN_OPTIONS = 7

# Bounds for the scroll window over long lists; the actual size adapts to
# the terminal height between these limits.
MENU_MIN_ROWS = 5
MENU_MAX_ROWS = 12
MENU_RESERVED_LINES = 10

ColorMode = Literal["extended", "basic", "none"]
ColorRole = Literal[
    "brand", "navigation", "current", "verified", "warning", "failure", "muted"
]

_EXTENDED_CODES: dict[ColorRole, str] = {
    "brand": "38;5;45",
    "navigation": "38;5;45",
    "current": "38;5;141",
    "verified": "38;5;84",
    "warning": "38;5;221",
    "failure": "38;5;203",
    "muted": "38;5;246",
}
_BASIC_CODES: dict[ColorRole, str] = {
    "brand": "36",
    "navigation": "36",
    "current": "35",
    "verified": "32",
    "warning": "33",
    "failure": "31",
    "muted": "2",
}


def _detect_color_mode(no_color: bool) -> ColorMode:
    if no_color or "NO_COLOR" in os.environ:
        return "none"
    term = os.environ.get("TERM", "")
    colorterm = os.environ.get("COLORTERM", "").casefold()
    if "256color" in term or colorterm in ("truecolor", "24bit"):
        return "extended"
    return "basic"


def _detect_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").casefold()
    return "utf" in encoding


@dataclass(frozen=True)
class SignalTheme:
    """Semantic Signal colors and terminal-safe symbols with fallbacks."""

    mode: ColorMode
    unicode: bool

    def paint(self, role: ColorRole, text: str, *, bold: bool = False) -> str:
        if self.mode == "none" or not text:
            return text
        codes = _EXTENDED_CODES if self.mode == "extended" else _BASIC_CODES
        prefix = "1;" if bold and codes[role] != "2" else ""
        return f"\x1b[{prefix}{codes[role]}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        if self.mode == "none" or not text:
            return text
        return f"\x1b[1m{text}\x1b[0m"

    # --- symbols ------------------------------------------------------------

    @property
    def progress_done(self) -> str:
        return "●" if self.unicode else "*"

    @property
    def progress_current(self) -> str:
        return "●" if self.unicode else ">"

    @property
    def progress_remaining(self) -> str:
        return "○" if self.unicode else "-"

    @property
    def row_idle(self) -> str:
        return "○" if self.unicode else "-"

    @property
    def row_open(self) -> str:
        return "┌─" if self.unicode else ">"

    @property
    def row_detail(self) -> str:
        return "│" if self.unicode else "|"

    @property
    def row_close(self) -> str | None:
        return "└─" if self.unicode else None

    @property
    def scroll_above(self) -> str:
        return "↑" if self.unicode else "^"

    @property
    def scroll_below(self) -> str:
        return "↓" if self.unicode else "v"

    @property
    def key_move(self) -> str:
        return "↑↓" if self.unicode else "Up/Down"

    @property
    def rule(self) -> str:
        return "─" if self.unicode else "-"


class MenuSession:
    """The state and rendering of one interactive menu, without any IO.

    Pure transitions keep selection, filtering, and windowing testable
    without a terminal; ``InteractiveUI`` only reads keys and paints lines.
    """

    def __init__(
        self,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool,
        help_available: bool,
        searchable: bool | None = None,
    ) -> None:
        self.prompt = prompt
        self.options = options
        self.allow_back = allow_back
        self.help_available = help_available
        self.searchable = (
            len(options) >= SEARCH_MIN_OPTIONS if searchable is None else searchable
        )
        self.query = ""
        selectable = [option.id for option in options if self._selectable(option)]
        self.selected_id: str | None = selectable[0] if selectable else None

    @staticmethod
    def _selectable(option: MenuOption) -> bool:
        return option.available and not option.dim

    def visible_options(self) -> tuple[MenuOption, ...]:
        if not self.query:
            return self.options
        needle = self.query.casefold()
        return tuple(
            option
            for option in self.options
            if needle in option.label.casefold() or needle in option.note.casefold()
        )

    def move(self, delta: int) -> None:
        selectable = [
            option.id for option in self.visible_options() if self._selectable(option)
        ]
        if not selectable:
            self.selected_id = None
            return
        if self.selected_id in selectable:
            index = selectable.index(self.selected_id)
            self.selected_id = selectable[(index + delta) % len(selectable)]
        else:
            self.selected_id = selectable[0]

    def type_char(self, char: str) -> None:
        if not self.searchable:
            return
        self.query += char
        self._reset_selection()

    def backspace(self) -> None:
        if not self.searchable or not self.query:
            return
        self.query = self.query[:-1]
        self._reset_selection()

    def escape(self) -> Literal["cleared", "back", "exit"]:
        """Esc clears an active filter first, then navigates."""
        if self.searchable and self.query:
            self.query = ""
            self._reset_selection()
            return "cleared"
        return "back" if self.allow_back else "exit"

    def _reset_selection(self) -> None:
        selectable = [
            option.id for option in self.visible_options() if self._selectable(option)
        ]
        if self.selected_id not in selectable:
            self.selected_id = selectable[0] if selectable else None

    def footer_controls(self, theme: SignalTheme) -> list[str]:
        controls = [f"{theme.key_move} {CONTROL_MOVE}"]
        if self.searchable:
            controls.append(CONTROL_FILTER)
        controls.append(f"Enter {CONTROL_SELECT}")
        if self.help_available:
            controls.append(f"? {CONTROL_HELP}")
        controls.append(f"Esc {CONTROL_BACK if self.allow_back else CONTROL_EXIT}")
        controls.append(f"Ctrl+C {CONTROL_SAVE_AND_EXIT}")
        return controls

    def lines(self, theme: SignalTheme, *, width: int, max_rows: int) -> list[str]:
        visible = self.visible_options()
        lines = [theme.bold(self.prompt)]
        if self.searchable:
            if self.query:
                query_text = theme.paint("navigation", self.query)
            else:
                query_text = theme.paint("muted", FILTER_PLACEHOLDER)
            lines.append(f"  {theme.bold(FILTER_LABEL)}  {query_text}")
        lines.append("")

        selected_index = next(
            (
                index
                for index, option in enumerate(visible)
                if option.id == self.selected_id
            ),
            0,
        )
        max_rows = max(MENU_MIN_ROWS, max_rows)
        start = 0
        if len(visible) > max_rows:
            half = max_rows // 2
            start = min(max(0, selected_index - half), len(visible) - max_rows)
        window = visible[start : start + max_rows]

        if start > 0:
            lines.append(
                theme.paint(
                    "muted", f"  {theme.scroll_above} {MORE_ABOVE.format(count=start)}"
                )
            )
        for option in window:
            lines.extend(self._row_lines(theme, option, width=width))
        remaining = len(visible) - (start + len(window))
        if remaining > 0:
            lines.append(
                theme.paint(
                    "muted",
                    f"  {theme.scroll_below} {MORE_BELOW.format(count=remaining)}",
                )
            )
        if not visible:
            lines.append(theme.paint("muted", f"  {FILTER_EMPTY}"))

        lines.append("")
        lines.append(theme.paint("muted", "  " + "   ".join(self.footer_controls(theme))))
        return lines

    def _row_lines(
        self, theme: SignalTheme, option: MenuOption, *, width: int
    ) -> list[str]:
        if not self._selectable(option):
            note = f"  {option.note}" if option.note else ""
            return [theme.paint("muted", f"    {option.label}{note}")]
        if option.id != self.selected_id:
            return [f"  {theme.row_idle} {option.label}"]
        header = theme.paint(
            "navigation", f"  {theme.row_open} {option.label}", bold=True
        )
        rows = [header]
        if option.note:
            indent = f"  {theme.row_detail}  "
            for wrapped in textwrap.wrap(
                option.note, width=max(28, width - len(indent))
            ):
                rows.append(theme.paint("current", f"{indent}{wrapped}"))
        if theme.row_close is not None:
            rows.append(theme.paint("navigation", f"  {theme.row_close}"))
        return rows


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
            PLAIN_PROGRESS.format(done=index - 1, index=index, total=total, title=title)
        )

    def info(self, text: str) -> None:
        self._output(text)

    def secondary(self, text: str) -> None:
        self._output(text)

    def success(self, text: str) -> None:
        self._output(f"{STATUS_OK_PREFIX}: {text}")

    def warning(self, text: str) -> None:
        self._output(f"{STATUS_WARNING_PREFIX}: {text}")

    def failure(self, text: str) -> None:
        self._output(f"{STATUS_FAILURE_PREFIX}: {text}")

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
                self._output(f"  -. {option.label} {PLAIN_NOT_SELECTABLE}{note}")
        if allow_back:
            self._output(f"  {PLAIN_BACK_HINT}")
        self._output(f"  {PLAIN_EXIT_HINT} {HELP_HINT}")
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
                self._output(PLAIN_ENTER_NUMBER.format(count=len(selectable)))
                continue
            if choice < 1 or choice > len(selectable):
                self._output(PLAIN_ENTER_NUMBER.format(count=len(selectable)))
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
            self._output(PLAIN_VALUE_REQUIRED)

    def confirm(self, key: str, question: str, *, default: bool = False) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        raw = self._read(f"{question} {suffix} ").strip().casefold()
        if not raw:
            return default
        return raw in ("y", "yes")


class InteractiveUI(PlainTextUI):
    """The Signal renderer: arrow-key menus with semantic colors.

    Falls back to numbered prompts when stdin is not a TTY or the platform
    is not posix. ``NO_COLOR`` keeps the interactive menus but removes all
    color; a non-UTF-8 terminal encoding replaces box symbols with ASCII.
    """

    def __init__(
        self,
        *,
        line_reader: Callable[[str], str] | None = None,
        secret_reader: Callable[[str], str] | None = None,
        output: Callable[[str], None] | None = None,
        stream: Any = None,
        no_color: bool = False,
        theme: SignalTheme | None = None,
    ) -> None:
        super().__init__(
            line_reader=line_reader, secret_reader=secret_reader, output=output
        )
        self._stream = stream if stream is not None else sys.stdin
        self._theme = theme or SignalTheme(
            mode=_detect_color_mode(no_color), unicode=_detect_unicode()
        )

    def _interactive_capable(self) -> bool:
        return (
            hasattr(self._stream, "isatty")
            and bool(self._stream.isatty())
            and os.name == "posix"
        )

    @property
    def _width(self) -> int:
        return max(56, min(76, shutil.get_terminal_size((72, 24)).columns - 4))

    @property
    def _menu_rows(self) -> int:
        lines = shutil.get_terminal_size((72, 24)).lines
        return max(MENU_MIN_ROWS, min(MENU_MAX_ROWS, lines - MENU_RESERVED_LINES))

    def banner(self) -> None:
        self._output(self._theme.paint("brand", BRAND_LINE, bold=True))

    def step(self, index: int, total: int, title: str) -> None:
        theme = self._theme
        dots: list[str] = []
        for position in range(1, total + 1):
            if position < index:
                dots.append(theme.paint("verified", theme.progress_done))
            elif position == index:
                dots.append(theme.paint("current", theme.progress_current, bold=True))
            else:
                dots.append(theme.paint("muted", theme.progress_remaining))
        brand = theme.paint("brand", BRAND_NAME, bold=True)
        progress = " ".join(dots)
        rule = theme.paint("muted", theme.rule * self._width)
        position_text = theme.paint(
            "muted", PROGRESS_POSITION.format(index=index, total=total)
        )
        self._output("")
        self._output(rule)
        gap = " " * max(2, self._width - len(BRAND_NAME) - len(" ".join(dots)))
        self._output(f"{brand}{gap}{progress}")
        self._output("")
        self._output(f"{theme.bold(title)}  {position_text}")
        self._output("")

    def success(self, text: str) -> None:
        self._output(self._theme.paint("verified", f"{STATUS_OK_PREFIX}: {text}"))

    def secondary(self, text: str) -> None:
        self._output(self._theme.paint("muted", text))

    def warning(self, text: str) -> None:
        self._output(self._theme.paint("warning", f"{STATUS_WARNING_PREFIX}: {text}"))

    def failure(self, text: str) -> None:
        self._output(self._theme.paint("failure", f"{STATUS_FAILURE_PREFIX}: {text}"))

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

        session = MenuSession(
            prompt,
            options,
            allow_back=allow_back,
            help_available=help_text is not None,
        )
        previous_height = 0

        def render(*, redraw: bool) -> None:
            nonlocal previous_height
            lines = session.lines(
                self._theme, width=self._width, max_rows=self._menu_rows
            )
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
                        action = session.escape()
                        if action == "cleared":
                            render(redraw=True)
                            continue
                        if action == "back":
                            raise SetupBack
                        raise SetupExit
                    rest = os.read(stream.fileno(), 2)
                    if rest == b"[A":
                        session.move(-1)
                    elif rest == b"[B":
                        session.move(1)
                    render(redraw=True)
                elif char in (b"\r", b"\n"):
                    if session.selected_id is not None:
                        break
                elif char == b"?":
                    text = help_text or HELP_HINT
                    self._output("")
                    self._output(text)
                    previous_height += text.count("\n") + 2
                elif char in (b"\x7f", b"\x08"):
                    session.backspace()
                    render(redraw=True)
                elif char == b"\x03":  # Ctrl+C
                    raise SetupExit
                elif len(char) == 1 and 32 <= char[0] <= 126:
                    session.type_char(char.decode("ascii"))
                    render(redraw=True)
        finally:
            termios.tcsetattr(stream, termios.TCSADRAIN, old_settings)
        chosen = next(
            item for item in session.visible_options() if item.id == session.selected_id
        )
        self._output(SELECTED_ECHO.format(label=chosen.label))
        return chosen.id


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
        self._output(f"{STATUS_OK_PREFIX}: {text}")

    def warning(self, text: str) -> None:
        self._output(f"{STATUS_WARNING_PREFIX}: {text}")

    def failure(self, text: str) -> None:
        self._output(f"{STATUS_FAILURE_PREFIX}: {text}")

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
