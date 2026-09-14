"""Issue #124 browser check: the web decision journey in real Chromium.

Drives one synthetic installation (fake provider, no network, no secrets)
through the repaired #124 contract with the reviewed shell:

1. Casual conversation creates no decision.
2. Propose renders a proposal with reasons, uncertainty, and next action,
   with exactly one confirmation control.
3. Direct editing produces an unconfirmed revision; the stale pre-correction
   control conflicts instead of confirming.
4. Confirmation binds to the displayed version.
5. After a full server restart, Home links reopen the saved decision with
   the corrected meaning and the actual evidence.
6. Keyboard-only completion of the propose step and a narrow (390px)
   viewport show no horizontal scroll.

Run with:  PYTHONPATH=src .venv/bin/python docs/reviews/2026-09-14-issue-124-browser-check.py
Exit code 0 means every phase passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


def start_server(data_dir: Path) -> subprocess.Popen:
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reckoning",
            "web",
            "--data-dir",
            str(data_dir),
            "--provider",
            "fake",
            "--port",
            str(PORT),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{BASE}/", timeout=1)
            return process
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("The web server exited during startup.")
            time.sleep(0.1)
    raise RuntimeError("The web server did not start.")


def stop_server(process: subprocess.Popen) -> None:
    process.terminate()
    process.wait(timeout=10)


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="reckoning-124-browser-"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "reckoning",
            "setup",
            "--non-interactive",
            "--data-dir",
            str(data_dir),
            "--placement",
            "local",
            "--persona",
            "simon",
            "--provider",
            "fake",
            "--profile",
            "skip",
            "--first-message",
            "Hello, Simon.",
            "--json",
        ],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        check=True,
        capture_output=True,
    )
    server = start_server(data_dir)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            page.goto(f"{BASE}/simon")
            check(
                "Hello, Simon." in page.content(),
                "setup conversation is visible on Simon",
            )

            # Phase 1: casual conversation creates no decision.
            page.get_by_label("Your message").fill("Just thinking out loud.")
            page.get_by_role("button", name="Send", exact=True).click()
            page.wait_for_url(f"{BASE}/simon")
            page.goto(f"{BASE}/")
            check(
                page.locator('a[href^="/decisions/"]').count() == 0,
                "casual conversation created no decision link",
            )

            # Phase 2: propose through the rendered composer.
            page.goto(f"{BASE}/simon")
            page.get_by_label("Your message").fill(
                "Exams, project, and job search all compete."
            )
            page.get_by_role("button", name="Propose").click()
            page.wait_for_url(f"{BASE}/decisions/*")
            decision_url = page.url
            check(
                page.get_by_text(
                    "The deadlines, consequences, and available time are not yet known."
                ).count()
                == 1,
                "proposal shows its uncertainty",
            )
            check(
                page.get_by_role("button", name="Confirm this version").count() == 1,
                "exactly one confirmation control",
            )
            check(
                page.get_by_text("Name the nearest irreversible consequence").count()
                == 1,
                "proposal shows the next action",
            )

            # Phase 3: direct editing; the stale control conflicts.
            page.get_by_label("Edit meaning").fill(
                "Mary intends to protect exam preparation."
            )
            page.get_by_role("button", name="Save correction").click()
            page.wait_for_url(decision_url)
            check(
                page.get_by_text("Mary intends to protect exam preparation.").count()
                >= 1,
                "correction is visible as an unconfirmed revision",
            )
            check(
                page.locator("text=Revision 2").count() == 1,
                "revision advanced to 2",
            )

            # Phase 4: the explicit control confirms the displayed version.
            page.get_by_role("button", name="Confirm this version").click()
            page.wait_for_url(decision_url)
            check(
                page.locator(".status-confirmed").first.is_visible(),
                "decision is confirmed",
            )

            # Phase 5: restart; Home links reopen the saved decision.
            stop_server(server)
            server = start_server(data_dir)
            page.goto(f"{BASE}/")
            link = page.locator('a[href^="/decisions/"]')
            check(link.count() == 1, "Home lists the saved decision as a link")
            link.first.click()
            page.wait_for_url(f"{BASE}/decisions/*")
            check(
                page.get_by_text("Mary intends to protect exam preparation.").count()
                >= 1,
                "reopened decision shows the corrected meaning",
            )
            check(
                page.get_by_text("direct user correction").count() == 1,
                "reopened decision shows the actual evidence",
            )

            # Phase 6a: keyboard-only propose on a fresh decision surface.
            page.goto(f"{BASE}/simon")
            composer = page.locator("#message")
            composer.focus()
            page.keyboard.type("Keyboard-only conflict description.")
            page.get_by_role("button", name="Propose").focus()
            page.keyboard.press("Enter")
            page.wait_for_url(f"{BASE}/decisions/*")
            check(
                "Keyboard-only conflict description." in page.content(),
                "keyboard-only propose completes",
            )

            # Phase 6b: narrow viewport without horizontal scroll.
            narrow = browser.new_page(viewport={"width": 390, "height": 844})
            narrow.goto(f"{BASE}/")
            overflow = narrow.evaluate(
                "document.scrollingElement.scrollWidth > document.scrollingElement.clientWidth"
            )
            check(not overflow, "390px viewport has no horizontal scroll")
            narrow.goto(page.url)
            overflow = narrow.evaluate(
                "document.scrollingElement.scrollWidth > document.scrollingElement.clientWidth"
            )
            check(not overflow, "decision page fits the narrow viewport")

            browser.close()
    finally:
        stop_server(server)
    print("All #124 browser checks passed.")


if __name__ == "__main__":
    main()
