from __future__ import annotations

from collections.abc import Callable

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def render_help(
    groups: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    *,
    console: Console | None = None,
) -> None:
    console = console or Console()
    console.print(
        "Reckoning — a local-first single-user continuity agent.",
        style="bold",
    )
    for group, commands in groups:
        table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
        table.add_column(style="cyan", no_wrap=True)
        table.add_column()
        for name, description in commands:
            table.add_row(name, description)
        console.print(Panel(table, title=group, border_style="blue"))
    console.print("Run 'reckoning <command> --help' for a command's flags.")


def render_doctor(lines: tuple[str, ...], *, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title="reckoning doctor", box=box.ROUNDED)
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Result")
    for line in lines:
        check, separator, result = line.partition(": ")
        if not separator:
            check, result = "report", line
        style = ""
        if check == "status":
            style = "green" if result == "healthy" else "bold red"
        table.add_row(check, result, style=style)
    console.print(table)


def render_release_readiness(
    ready: bool, message: str, *, console: Console | None = None
) -> None:
    console = console or Console()
    console.print(
        Panel(
            message,
            title="release readiness",
            border_style="green" if ready else "bold red",
        )
    )


def wizard_output(*, console: Console | None = None) -> Callable[[str], None]:
    """Render wizard lines through rich; menus and prompts stay plain text."""
    console = console or Console()

    def emit(text: str) -> None:
        if text == "Reckoning setup":
            console.print(Panel(text, border_style="blue"))
        elif text.startswith("Step "):
            console.print(text, style="green", markup=False, highlight=False)
        elif text.startswith("Setup complete"):
            console.print(text, style="bold green", markup=False, highlight=False)
        elif text.startswith("Setup is incomplete"):
            console.print(text, style="bold red", markup=False, highlight=False)
        else:
            console.print(text, markup=False, highlight=False)

    return emit


def error_output(*, console: Console | None = None) -> Callable[[str], None]:
    console = console or Console(stderr=True)

    def emit(text: str) -> None:
        console.print(text, style="bold red", markup=False, highlight=False)

    return emit
