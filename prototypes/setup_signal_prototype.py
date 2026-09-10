#!/usr/bin/env python3
"""Throwaway Signal prototype for the full Reckoning terminal setup flow.

Question: does one focused, color-led terminal flow make setup understandable?

Run:
    python3 prototypes/setup_signal_prototype.py

This prototype makes no network requests, reads no files, stores no credentials,
and writes no setup state. All provider, Telegram, profile, and reply behavior is
simulated in memory.
"""

from __future__ import annotations

import argparse
import os
import select
import shutil
import sys
import termios
import textwrap
import tty
from dataclasses import dataclass, field


ESC = "\x1b"
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[38;5;45m"
VIOLET = "\x1b[38;5;141m"
GREEN = "\x1b[38;5;84m"
AMBER = "\x1b[38;5;221m"
RED = "\x1b[38;5;203m"
MUTED = "\x1b[38;5;246m"


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    description: str
    note: str = ""


@dataclass
class PrototypeState:
    provider: str = "OrcaRouter"
    model: str = "Auto"
    telegram: str = "Not configured"
    agent_style: str = "Simon"
    profile: str = "Skipped"
    profile_details: list[str] = field(default_factory=list)
    storage: str = "This machine"
    first_message: str = ""
    accepted: bool = False


class SignalPrototype:
    def __init__(self, *, color: bool = True) -> None:
        self.color = color
        self.state = PrototypeState()
        self.total_steps = 5

    def paint(self, text: str, *codes: str) -> str:
        if not self.color:
            return text
        return "".join(codes) + text + RESET

    @property
    def width(self) -> int:
        return max(56, min(76, shutil.get_terminal_size((72, 24)).columns - 4))

    def rule(self) -> str:
        return self.paint("─" * self.width, MUTED)

    def wrapped(self, text: str, *, indent: str = "", width: int | None = None) -> list[str]:
        available = width or self.width - len(indent)
        return textwrap.wrap(text, width=max(28, available), initial_indent=indent, subsequent_indent=indent)

    def progress(self, current: int) -> str:
        dots: list[str] = []
        for index in range(1, self.total_steps + 1):
            if index < current:
                dots.append(self.paint("●", GREEN))
            elif index == current:
                dots.append(self.paint("●", VIOLET, BOLD))
            else:
                dots.append(self.paint("○", MUTED))
        return " ".join(dots)

    def heading(self, current: int, title: str, intro: str = "") -> None:
        print()
        print(self.rule())
        gap = " " * max(2, self.width - len("RECKONING") - len("● ● ● ● ●"))
        print(self.paint("RECKONING", CYAN, BOLD) + gap + self.progress(current))
        print()
        print(self.paint(title, BOLD))
        if intro:
            for line in self.wrapped(intro):
                print(self.paint(line, MUTED))
        print()

    def status_line(self, label: str, value: str, *, state: str = "normal") -> str:
        symbol = "✓" if state == "ready" else "○"
        color = GREEN if state == "ready" else MUTED
        return f"{self.paint(symbol, color)}  {label:<14} {value}"

    def _read_key(self) -> str:
        if not sys.stdin.isatty():
            return input("> ").strip()
        descriptor = sys.stdin.fileno()
        previous = termios.tcgetattr(descriptor)
        try:
            tty.setraw(descriptor)
            first = os.read(descriptor, 1).decode("utf-8", errors="ignore")
            if first == "\x03":
                raise KeyboardInterrupt
            if first != ESC:
                return first
            ready, _, _ = select.select([descriptor], [], [], 0.05)
            if not ready:
                return "ESC"
            second = os.read(descriptor, 1).decode("utf-8", errors="ignore")
            if second != "[":
                return "ESC"
            third = os.read(descriptor, 1).decode("utf-8", errors="ignore")
            return {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}.get(third, "ESC")
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)

    def _clear_active(self, lines: int) -> None:
        if lines and sys.stdout.isatty():
            sys.stdout.write(f"\x1b[{lines}A\r\x1b[J")

    def _draw_menu(
        self,
        options: list[Option],
        selected: int,
        *,
        query: str,
        searchable: bool,
        allow_back: bool,
    ) -> int:
        lines: list[str] = []
        if searchable:
            search_value = query if query else "Start typing to filter"
            search_color = CYAN if query else MUTED
            lines.append(f"  {self.paint('Search', BOLD)}  {self.paint(search_value, search_color)}")
            lines.append("")
        for index, option in enumerate(options):
            suffix = f"  {self.paint(option.note, MUTED)}" if option.note else ""
            if index == selected:
                lines.append(self.paint(f"  ┌─ {option.label}", CYAN, BOLD) + suffix)
                detail = self.wrapped(option.description, indent="  │  ", width=self.width - 2)
                lines.extend(self.paint(line, VIOLET) for line in detail)
                lines.append(self.paint("  └─", CYAN))
            else:
                lines.append(f"     {option.label}{suffix}")
        lines.append("")
        controls = ["↑↓ navigate"]
        if searchable:
            controls.append("type search")
        controls.append("enter select")
        if allow_back:
            controls.append("esc back")
        controls.append("ctrl+c save & exit")
        lines.append(self.paint("  " + "   ".join(controls), MUTED))
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        return len(lines)

    def menu(
        self,
        options: list[Option],
        *,
        searchable: bool = False,
        allow_back: bool = True,
    ) -> str:
        if not sys.stdin.isatty():
            for index, option in enumerate(options, start=1):
                print(f"  {index}. {option.label} — {option.description}")
            if allow_back:
                print("  b. Back")
            while True:
                raw = input("> ").strip()
                if allow_back and raw.casefold() in {"b", "back"}:
                    return "__back__"
                if raw.isdigit() and 1 <= int(raw) <= len(options):
                    return options[int(raw) - 1].id

        selected = 0
        query = ""
        active_options = options
        drawn = 0
        while True:
            self._clear_active(drawn)
            drawn = self._draw_menu(
                active_options,
                selected,
                query=query,
                searchable=searchable,
                allow_back=allow_back,
            )
            key = self._read_key()
            if key == "UP":
                selected = (selected - 1) % len(active_options)
            elif key == "DOWN":
                selected = (selected + 1) % len(active_options)
            elif key in {"\r", "\n"}:
                print()
                return active_options[selected].id
            elif key == "ESC":
                if query:
                    query = ""
                    active_options = options
                    selected = 0
                elif allow_back:
                    print()
                    return "__back__"
            elif searchable and key in {"\x7f", "\b"}:
                query = query[:-1]
                active_options = self._filter(options, query)
                selected = 0
            elif searchable and key.isprintable() and not key.isspace():
                query += key
                active_options = self._filter(options, query)
                selected = 0

    @staticmethod
    def _filter(options: list[Option], query: str) -> list[Option]:
        if not query:
            return options
        lowered = query.casefold()
        matches = [
            option
            for option in options
            if lowered in f"{option.label} {option.description}".casefold()
        ]
        return matches or [Option("__none__", "No matches", "Backspace to change your search.")]

    def pause(self, prompt: str = "Press Enter to continue") -> None:
        print(self.paint(prompt, MUTED))
        if not sys.stdin.isatty():
            input()
            return
        while self._read_key() not in {"\r", "\n"}:
            pass

    def welcome(self) -> None:
        print()
        print(self.paint("◆", VIOLET, BOLD), self.paint("RECKONING", CYAN, BOLD))
        print()
        print(self.paint("Set up your personal agent.", BOLD))
        print()
        for line in self.wrapped(
            "Connect an AI provider, choose how Reckoning works with you, "
            "and start a real conversation."
        ):
            print(self.paint(line, MUTED))
        print()
        print(self.paint("Prototype only. Nothing is connected, read, or saved.", AMBER))
        print()
        self.pause("Press Enter to begin")

    def provider_flow(self) -> bool:
        self.heading(
            1,
            "Connect an AI provider",
            "Choose the AI service Reckoning will use. Only working providers appear here.",
        )
        choice = self.menu(
            [
                Option("orcarouter", "OrcaRouter", "Use one API key to reach a broad model catalog.", "Recommended"),
                Option("deepseek", "DeepSeek", "Connect directly to DeepSeek with an API key."),
                Option("demo", "Try without an API key", "Use Reckoning's deterministic Demo response.", "Demo"),
            ],
            searchable=True,
        )
        if choice == "__back__":
            return False
        if choice == "__none__":
            return self.provider_flow()
        self.state.provider = {
            "orcarouter": "OrcaRouter",
            "deepseek": "DeepSeek",
            "demo": "Demo",
        }[choice]
        print(self.paint("  More providers coming soon", MUTED))
        print()
        if choice != "demo":
            print(self.paint(f"Connect {self.state.provider}", BOLD))
            print(self.paint("Prototype: no credential is requested or stored.", AMBER))
            print()
            self.pause("Press Enter to simulate a private connection check")
            print(self.paint("✓ Connected", GREEN, BOLD))
            print(self.paint("  A fixed non-personal request was simulated.", MUTED))
        else:
            print(self.paint("✓ Demo is ready", GREEN, BOLD))
            print(self.paint("  No network connection will be used.", MUTED))
        self.pause()
        return self.model_flow()

    def model_flow(self) -> bool:
        self.heading(1, "Choose a model", f"Provider  {self.state.provider}")
        if self.state.provider == "OrcaRouter":
            options = [
                Option("Auto", "Auto", "Let OrcaRouter choose a suitable available model.", "Recommended"),
                Option("DeepSeek V3.2", "DeepSeek V3.2", "A reasoning-capable model available through the selected provider."),
                Option("Claude Sonnet", "Claude Sonnet", "A balanced model available through the selected provider."),
                Option("Gemini Flash", "Gemini Flash", "A faster model available through the selected provider."),
            ]
        elif self.state.provider == "DeepSeek":
            options = [
                Option("deepseek-chat", "deepseek-chat", "The recommended general-purpose DeepSeek model.", "Recommended"),
                Option("deepseek-reasoner", "deepseek-reasoner", "A model for requests that benefit from more reasoning."),
            ]
        else:
            options = [Option("Built-in Demo", "Built-in Demo", "A deterministic offline response.", "Demo")]
        choice = self.menu(options, searchable=True)
        if choice == "__back__":
            return False
        if choice == "__none__":
            return self.model_flow()
        self.state.model = choice
        return True

    def ways_flow(self) -> bool:
        self.heading(
            2,
            "Ways to use Reckoning",
            "Terminal and Web are ready after setup. Telegram is optional.",
        )
        print(self.status_line("Terminal", "Ready after setup", state="ready"))
        print(self.status_line("Web", "Run `reckoning web`", state="ready"))
        telegram_state = self.state.telegram
        telegram_ready = telegram_state == "Ready"
        print(self.status_line("Telegram", telegram_state, state="ready" if telegram_ready else "normal"))
        print()
        choice = self.menu(
            [
                Option("telegram", "Set up Telegram", "Verify a private bot and pair its owner chat."),
                Option("continue", "Continue", "Keep Telegram as it is and move to Agent style."),
            ]
        )
        if choice == "__back__":
            return False
        if choice == "telegram":
            print(self.paint("Connect Telegram", BOLD))
            print(self.paint("Prototype: bot verification and private pairing are simulated.", AMBER))
            print()
            self.pause("Press Enter to simulate verification and pairing")
            self.state.telegram = "Ready"
            print(self.paint("✓ Telegram is ready", GREEN, BOLD))
            print(self.paint("  The Gateway is not running. Start it later with `reckoning gateway`.", MUTED))
            print()
            self.pause()
        print(self.paint("  More messaging apps coming soon", MUTED))
        return True

    def style_flow(self) -> bool:
        self.heading(3, "Agent style", "How should Reckoning work with you?")
        choice = self.menu(
            [
                Option("Simon", "Simon", "Direct, composed, and demanding."),
                Option("Steady", "Steady", "Warm, reflective, and patient."),
                Option("Custom", "Create your own", "Start from a clear style and adjust it."),
            ]
        )
        if choice == "__back__":
            return False
        self.state.agent_style = choice
        if choice == "Custom":
            print(self.paint("Custom style", BOLD))
            print(self.paint("  The production flow will open the bounded style builder here.", MUTED))
            print(self.paint("  For this visual prototype, Custom uses balanced defaults.", MUTED))
            print()
            self.pause()
        return True

    def profile_flow(self) -> bool:
        self.heading(
            4,
            "About you",
            "Relevant context helps Reckoning fit your situation. You decide what to share, and every question is optional.",
        )
        choice = self.menu(
            [
                Option("guided", "Answer a few questions", "Build a small starting profile. Every answer is optional."),
                Option("starter", "Create a starter file", "Preview a Markdown file with headings and writing suggestions."),
                Option("import", "Import an existing file", "Preview extracted statements before anything is proposed."),
                Option("skip", "Skip for now", "Reckoning can learn later, with your permission."),
            ]
        )
        if choice == "__back__":
            return False
        if choice == "guided":
            self.guided_profile()
        elif choice == "starter":
            self.starter_profile()
        elif choice == "import":
            self.import_profile()
        else:
            self.state.profile = "Skipped"
            self.state.profile_details.clear()
        print(self.paint("  More context sources coming soon", MUTED))
        return True

    def guided_profile(self) -> None:
        print(self.paint("A few basics", BOLD))
        print(self.paint("Press Enter to skip any question.", MUTED))
        prompts = [
            "What should Reckoning call you?",
            "What are you working on right now?",
            "What matters most at the moment?",
            "How do you prefer to work?",
            "Is there anything Reckoning should avoid?",
        ]
        answers: list[str] = []
        for prompt in prompts:
            answer = input(f"{self.paint('›', CYAN)} {prompt}\n  ").strip()
            if answer:
                answers.append(answer)
        self.state.profile_details = answers
        self.state.profile = f"{len(answers)} proposed details" if answers else "Skipped"

    def starter_profile(self) -> None:
        print(self.paint("Starter profile preview", BOLD))
        print()
        for line in (
            "# About me",
            "# What I am working on",
            "# Goals",
            "# How I prefer to work",
            "# Boundaries",
        ):
            print(self.paint(f"  {line}", VIOLET if line.startswith("#") else MUTED))
        print()
        print(self.paint("Prototype: this preview is not written to disk.", AMBER))
        self.state.profile = "Starter file previewed"
        self.state.profile_details.clear()
        self.pause()

    def import_profile(self) -> None:
        print(self.paint("Import a profile", BOLD))
        print(self.paint("Prototype: no file path is requested and no file is read.", AMBER))
        print()
        print("  Parsed statements")
        print(self.paint("  ✓ Working on a personal software project", GREEN))
        print(self.paint("  ✓ Prefers direct and practical explanations", GREEN))
        print(self.paint("  ✓ Wants important assumptions made visible", GREEN))
        print()
        self.state.profile = "3 proposed details"
        self.state.profile_details = [
            "Working on a personal software project",
            "Prefers direct and practical explanations",
            "Wants important assumptions made visible",
        ]
        self.pause("Press Enter to keep this simulated preview")

    def storage_flow(self) -> None:
        self.heading(5, "Storage", "Choose where Reckoning keeps personal data.")
        choice = self.menu(
            [
                Option("This machine", "This machine", "Keep personal data on the machine running Reckoning.", "Recommended"),
                Option("Personal server", "Personal server", "Keep private state on a server you control."),
                Option("Split", "Split", "Keep private context here and approved remote sources on your server."),
            ]
        )
        if choice != "__back__":
            self.state.storage = choice

    def review_flow(self) -> bool:
        while True:
            self.heading(5, "Review", "Check what Reckoning will use. Select any row to change it.")
            rows = [
                Option("provider", f"AI provider      {self.state.provider}", "Change the provider and verify it again."),
                Option("model", f"Model            {self.state.model}", "Choose another model from the selected provider."),
                Option("style", f"Agent style      {self.state.agent_style}", "Change how Reckoning works with you."),
                Option("profile", f"About you        {self.state.profile}", "Change, review, or skip initial personal context."),
                Option("telegram", f"Telegram         {self.state.telegram}", "Configure or review the private Telegram connection."),
                Option("storage", f"Storage          {self.state.storage}", "Change where Reckoning keeps personal data."),
                Option("continue", "Continue to your first conversation", "Preview the first setup exchange with these choices."),
            ]
            choice = self.menu(rows)
            if choice == "__back__":
                return False
            if choice == "continue":
                return True
            if choice == "provider":
                self.provider_flow()
            elif choice == "model":
                self.model_flow()
            elif choice == "style":
                self.style_flow()
            elif choice == "profile":
                self.profile_flow()
            elif choice == "telegram":
                self.ways_flow()
            elif choice == "storage":
                self.storage_flow()

    def first_conversation(self) -> bool:
        while True:
            self.heading(5, "Your first conversation", "Write something you genuinely want help with.")
            print(self.paint("For example:", MUTED))
            print(self.paint("  “Help me decide what to work on today.”", MUTED))
            print(self.paint("  “I am stuck on a project and need a clear next step.”", MUTED))
            print()
            message = input(f"{self.paint('You ›', CYAN, BOLD)} ").strip()
            if not message:
                print(self.paint("Write a message, or press Ctrl+C to leave this prototype.", AMBER))
                continue
            self.state.first_message = message
            print()
            print(self.paint(f"{self.state.agent_style} ◆", VIOLET, BOLD))
            reply = self.simulated_reply()
            for line in self.wrapped(reply, indent="  "):
                print(line)
            print()
            choice = self.menu(
                [
                    Option("accept", "Yes, finish setup", "Keep this exchange and activate the reviewed setup."),
                    Option("retry", "Try another message", "Discard this exchange and ask something else."),
                    Option("style", "Change Agent style", "Return to Simon, Steady, or Custom."),
                    Option("provider", "Change AI provider", "Return to provider and model selection."),
                ]
            )
            if choice == "accept":
                self.state.accepted = True
                return True
            if choice == "retry" or choice == "__back__":
                continue
            if choice == "style":
                self.style_flow()
            elif choice == "provider":
                self.provider_flow()

    def simulated_reply(self) -> str:
        if self.state.agent_style == "Simon":
            return (
                "You are trying to improve the setup before adding more capability. "
                "Walk through this version once as a new user, then change the first "
                "screen that makes you hesitate."
            )
        if self.state.agent_style == "Steady":
            return (
                "Start with one pass through the flow. Notice where you slow down or "
                "feel uncertain, then revise that part before expanding the setup."
            )
        return (
            "Test the complete path once. Keep the parts that make the next action "
            "obvious and revise the first point where the flow becomes difficult."
        )

    def complete(self) -> None:
        print()
        print(self.rule())
        print(self.paint("✓ Reckoning is ready", GREEN, BOLD))
        print()
        print(f"  {'Terminal':<12} {self.paint('reckoning', CYAN)}")
        print(f"  {'Web':<12} {self.paint('reckoning web', CYAN)}")
        telegram = (
            "Configured, Gateway is not running"
            if self.state.telegram == "Ready"
            else "Not configured"
        )
        print(f"  {'Telegram':<12} {telegram}")
        if self.state.telegram == "Ready":
            print(self.paint("                Start with `reckoning gateway`", MUTED))
        print()
        print(self.paint("Prototype complete. Nothing was saved.", AMBER))
        print()

    def run(self) -> int:
        try:
            self.welcome()
            while not self.provider_flow():
                self.welcome()
            while not self.ways_flow():
                self.provider_flow()
            while not self.style_flow():
                self.ways_flow()
            while not self.profile_flow():
                self.style_flow()
            while not self.review_flow():
                self.profile_flow()
            if self.first_conversation():
                self.complete()
            return 0
        except KeyboardInterrupt:
            print()
            print(self.paint("Setup prototype closed. Nothing was saved.", AMBER))
            return 130

    def preview(self) -> None:
        self.heading(1, "Connect an AI provider", "Choose the AI service Reckoning will use.")
        self._draw_menu(
            [
                Option("orcarouter", "OrcaRouter", "Use one API key to reach a broad model catalog.", "Recommended"),
                Option("deepseek", "DeepSeek", "Connect directly to DeepSeek with an API key."),
                Option("demo", "Try without an API key", "Use Reckoning's deterministic Demo response.", "Demo"),
            ],
            0,
            query="",
            searchable=True,
            allow_back=True,
        )
        print(self.paint("  More providers coming soon", MUTED))
        print()
        self.heading(5, "Review", "Check what Reckoning will use.")
        for label, value in (
            ("AI provider", self.state.provider),
            ("Model", self.state.model),
            ("Agent style", self.state.agent_style),
            ("About you", self.state.profile),
            ("Telegram", self.state.telegram),
            ("Storage", self.state.storage),
        ):
            print(f"  {label:<14} {value}")
        print()
        print(self.paint("› Continue to your first conversation", CYAN, BOLD))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the throwaway Signal setup prototype.")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--preview", action="store_true", help="Print representative screens without interaction.")
    arguments = parser.parse_args()
    prototype = SignalPrototype(color=not arguments.no_color)
    if arguments.preview:
        prototype.preview()
        return 0
    return prototype.run()


if __name__ == "__main__":
    raise SystemExit(main())
