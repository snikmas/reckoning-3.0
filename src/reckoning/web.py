from __future__ import annotations

import argparse
from html import escape
from ipaddress import ip_address
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
from wsgiref.types import StartResponse, WSGIEnvironment

from reckoning.application import Message, ReckoningApplication, create_local_application
from reckoning.config import DeepSeekSettings, OrcaRouterSettings
from reckoning.interfaces import (
    ControlView,
    ReckoningInterfaceApplication,
    create_local_interface_application,
)
from reckoning.operations import OperationError, load_installation_runtime


def validate_bind_host(host: str) -> str:
    """Keep the unauthenticated built-in server off public interfaces."""
    if host == "localhost":
        return host
    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(
            "the built-in server may bind only to a loopback address; use an "
            "SSH tunnel for remote access"
        )
    return host

class ReckoningWebApplication:
    """A thin WSGI adapter for the Reckoning application boundary."""

    def __init__(
        self,
        application: ReckoningApplication,
        *,
        interface_application: ReckoningInterfaceApplication | None = None,
    ) -> None:
        self._application = application
        self._interfaces = interface_application

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))

        if self._interfaces is not None:
            interface_response = self._handle_interface_request(
                method, path, environ, start_response
            )
            if interface_response is not None:
                return interface_response

        if method == "GET" and path == "/":
            return self._html_response(
                start_response,
                "200 OK",
                self._render_session(self._application.open_session()),
            )

        if method == "POST" and path == "/messages":
            try:
                self._application.send_message(self._read_message(environ))
            except (ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_session(
                        self._application.open_session(), error=str(error)
                    ),
                )
            start_response("303 See Other", [("Location", "/"), ("Content-Length", "0")])
            return [b""]

        body = b"Not found"
        start_response(
            "404 Not Found",
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    def _handle_interface_request(
        self,
        method: str,
        path: str,
        environ: WSGIEnvironment,
        start_response: StartResponse,
    ) -> list[bytes] | None:
        assert self._interfaces is not None
        if (
            method == "GET"
            and (path == "/control" or path.startswith("/control/"))
            and not self._interfaces.administration_allowed(
                str(environ.get("REMOTE_ADDR", "")),
                private_network_authenticated=environ.get(
                    "reckoning.private_network_authenticated"
                )
                is True,
                via_ssh_tunnel=environ.get("reckoning.ssh_tunnel") is True,
            )
        ):
            body = b"Administration requires a private access path."
            start_response(
                "403 Forbidden",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]
        if method == "GET" and path == "/":
            area = self._interfaces.landing_area()
            return self._html_response(
                start_response,
                "200 OK",
                self._render_interface_area(area),
            )
        if method == "GET" and path in {
            "/home",
            "/simon",
            "/plan",
            "/review",
            "/control",
        }:
            return self._html_response(
                start_response,
                "200 OK",
                self._render_interface_area(path.removeprefix("/")),
            )
        if method == "POST" and path == "/messages":
            try:
                self._interfaces.send_channel_message(
                    "web", self._read_message(environ)
                )
            except (ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_interface_area("simon", error=str(error)),
                )
            start_response("303 See Other", [("Location", "/"), ("Content-Length", "0")])
            return [b""]
        return None

    @staticmethod
    def _read_message(environ: WSGIEnvironment) -> str:
        content_length = int(str(environ.get("CONTENT_LENGTH") or "0"))
        if content_length > 65_536:
            raise ValueError("The message is too long.")

        input_stream = environ.get("wsgi.input")
        if input_stream is None or not hasattr(input_stream, "read"):
            raise ValueError("The request body is missing.")

        body = input_stream.read(content_length).decode("utf-8")
        fields = parse_qs(body, keep_blank_values=True)
        return fields.get("message", [""])[0]

    @staticmethod
    def _html_response(
        start_response: StartResponse, status: str, page: str
    ) -> list[bytes]:
        body = page.encode("utf-8")
        start_response(
            status,
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    @staticmethod
    def _render_session(messages: tuple[Message, ...], error: str | None = None) -> str:
        conversation = "".join(
            (
                '<article class="message">'
                f'<strong>{"You" if message.role == "user" else "Simon"}</strong>'
                f"<p>{escape(message.content)}</p>"
                "</article>"
            )
            for message in messages
        )
        if not conversation:
            conversation = '<p class="empty">Send Simon the first message.</p>'

        error_markup = f'<p class="error">{escape(error)}</p>' if error else ""
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Simon | Reckoning</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-monospace, monospace; }}
    body {{ background: #11131a; color: #e9edf5; margin: 0; }}
    main {{ margin: 10vh auto; max-width: 42rem; padding: 0 1.25rem; }}
    .panel {{ background: #191d28; border: 1px solid #353c4e; padding: 1.5rem; }}
    h1 {{ margin-top: 0; }}
    .message {{ border-top: 1px solid #353c4e; padding-top: 1rem; }}
    .message p {{ line-height: 1.6; white-space: pre-wrap; }}
    .empty {{ color: #aeb7c8; }}
    .error {{ color: #ff9d9d; }}
    label, textarea {{ display: block; width: 100%; }}
    textarea {{ background: #10121a; border: 1px solid #4c566f; box-sizing: border-box;
      color: inherit; font: inherit; margin: .5rem 0 1rem; min-height: 7rem; padding: .75rem; }}
    button {{ background: #d6ff5f; border: 0; color: #12150b; cursor: pointer;
      font: inherit; font-weight: 700; padding: .7rem 1rem; }}
  </style>
</head>
<body>
  <main>
    <section class="panel" aria-labelledby="simon-heading">
      <h1 id="simon-heading">Simon</h1>
      <div aria-live="polite">{conversation}</div>
      {error_markup}
      <form action="/messages" method="post">
        <label for="message">Your message</label>
        <textarea id="message" name="message" required autofocus></textarea>
        <button type="submit">Send</button>
      </form>
    </section>
  </main>
</body>
</html>"""

    def _render_interface_area(self, area: str, error: str | None = None) -> str:
        assert self._interfaces is not None
        visual_state = self._interfaces.visual_state()
        if area == "home":
            home = self._interfaces.home()
            content = f"""
              <header><p class="eyebrow">Command center</p><h1>Home</h1></header>
              <section class="priority" aria-labelledby="matters-now">
                <h2 id="matters-now">What matters now</h2>{self._list(home.matters_now)}
              </section>
              <div class="disclosures">
                <details><summary>What changed</summary>{self._list(home.changes)}</details>
                <details><summary>Needs your decision</summary>
                  {self._list(home.decisions)}</details>
                <details><summary>System health</summary><p>{escape(home.health)}</p>
                  <p>{escape(home.placement.notice)}</p></details>
              </div>
            """
        elif area == "control":
            content = self._render_control(self._interfaces.control())
        elif area == "simon":
            messages = self._interfaces.channel_session("web")
            conversation = "".join(
                '<article class="message">'
                f'<strong>{"You" if message.role == "user" else "Simon"}</strong>'
                f"<p>{escape(message.content)}</p></article>"
                for message in messages
            ) or '<p class="empty">Send Simon the first message.</p>'
            error_markup = f'<p class="error">{escape(error)}</p>' if error else ""
            content = f"""
              <header><p class="eyebrow">Conversation</p><h1>Simon</h1></header>
              <div aria-live="polite">{conversation}</div>{error_markup}
              <form action="/messages" method="post">
                <label for="message">Your message</label>
                <textarea id="message" name="message" required></textarea>
                <button type="submit">Send</button>
              </form>
            """
        elif area == "plan":
            content = (
                '<header><p class="eyebrow">Choose the work</p><h1>Plan</h1>'
                "</header><p>Shape direction, goals, and feasible commitments.</p>"
            )
        else:
            content = (
                '<header><p class="eyebrow">Learn from outcomes</p><h1>Review</h1>'
                "</header><p>Compare intentions with what happened and decide what "
                "changes.</p>"
            )

        task_areas = (
            ("home", "Home", "See your current situation"),
            ("simon", "Simon", "Think with Simon"),
            ("plan", "Plan", "Choose the work"),
            ("review", "Review", "Learn from outcomes"),
            ("control", "Control", "Inspect operations"),
        )
        navigation = "".join(
            f'<a href="/{"" if slug == "home" else slug}"'
            f'{" aria-current=\"page\"" if slug == area else ""}'
            f' data-task-area="{slug}"><strong>{label}</strong>'
            f'<span>{description}</span></a>'
            for slug, label, description in task_areas
        )
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(area.title())} | Reckoning</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-monospace, monospace; }}
    * {{ box-sizing: border-box; }}
    body {{ background: #0b0e14; color: #e8edf6; margin: 0; min-height: 100vh; }}
    .shell {{ display: grid; grid-template-columns: 15rem minmax(0, 1fr) 17rem;
      min-height: 100vh; }}
    nav {{ border-right: 1px solid #303846; display: flex; flex-direction: column;
      gap: .4rem; padding: 1.5rem 1rem; }}
    nav a {{ color: #b8c0cf; display: flex; flex-direction: column; justify-content: center;
      min-height: 44px; padding: .65rem .8rem; text-decoration: none; }}
    nav a span {{ font-size: .72rem; line-height: 1.3; margin-top: .2rem; }}
    nav a[aria-current] {{ background: #1a2130; border-left: 3px solid #d6ff5f;
      color: #fff; }}
    main {{ max-width: 58rem; padding: 3rem clamp(1rem, 4vw, 4rem); width: 100%; }}
    aside {{ border-left: 1px solid #303846; padding: 1.5rem; }}
    .eyebrow {{ color: #9aa6bb; letter-spacing: .12em; text-transform: uppercase; }}
    h1 {{ font-size: clamp(2rem, 5vw, 4rem); margin: 0 0 2rem; }}
    h2 {{ font-size: 1rem; text-transform: uppercase; }}
    .priority, details, .control-section {{ background: #141923; border: 1px solid #303846;
      margin-bottom: .8rem; padding: 1.1rem; }}
    .disclosures {{ display: grid; gap: .8rem;
      grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .disclosures details {{ margin-bottom: 0; min-width: 0; }}
    summary {{ cursor: pointer; font-weight: 700; }}
    .message {{ border-top: 1px solid #303846; padding-top: 1rem; }}
    label, textarea {{ display: block; width: 100%; }}
    textarea {{ background: #0b0e14; border: 1px solid #4d596d; color: inherit;
      font: inherit; margin: .5rem 0 1rem; min-height: 7rem; padding: .75rem; }}
    button {{ background: #d6ff5f; border: 0; color: #111; font: inherit;
      font-weight: 700; padding: .7rem 1rem; }}
    .error {{ color: #ff9d9d; }}
    .simon-core {{ align-items: center; display: grid; gap: 1rem; justify-items: center; }}
    .core-glyph {{ background: #171d29; border: 4px double #8f9db2; border-radius: 38%;
      box-shadow: inset 0 0 0 6px #0b0e14; display: grid; height: 7rem; place-items: center;
      position: relative; width: 7rem; }}
    .core-glyph::before {{ border: 2px solid #d6ff5f; border-radius: 50%; content: "";
      height: 2.2rem; width: 2.2rem; }}
    [data-simon-state="warning"] .core-glyph, [data-simon-state="degraded"] .core-glyph
      {{ border-color: #ff9d9d; }}
    [data-simon-state="approval"] .core-glyph {{ border-color: #ffd166; }}
    [data-simon-state="execution"] .core-glyph {{ border-color: #70d6ff; }}
    [data-simon-state="listening"] .core-glyph {{ border-color: #7bdcb5; }}
    [data-simon-state="reasoning"] .core-glyph {{ border-color: #c4a7ff; }}
    @media (max-width: 720px) {{
      .shell {{ display: block; }}
      nav {{ border-bottom: 1px solid #303846; border-right: 0; flex-direction: row;
        overflow-x: auto; padding: .75rem; }}
      nav a {{ flex: 0 0 auto; min-height: 44px; white-space: nowrap; }}
      nav a span {{ display: none; }}
      main {{ padding: 1.5rem 1rem; }}
      .disclosures {{ grid-template-columns: 1fr; }}
      aside {{ border-left: 0; border-top: 1px solid #303846; padding: 1rem; }}
      .simon-core {{ grid-template-columns: auto 1fr; justify-items: start; margin: 0; }}
      .core-glyph {{ height: 4rem; width: 4rem; }}
    }}
  </style>
</head>
<body data-area="{escape(area)}">
  <div class="shell">
    <nav aria-label="Task areas">{navigation}</nav>
    <main>{content}</main>
    <aside>
      <figure class="simon-core" role="img" aria-label="Simon, original mechanical core"
        data-simon-state="{visual_state}">
        <span class="core-glyph" aria-hidden="true"></span>
        <figcaption>Simon · {visual_state}</figcaption>
      </figure>
    </aside>
  </div>
</body>
</html>"""

    @classmethod
    def _render_control(cls, control: ControlView) -> str:
        runs = tuple(
            f"{item.id}: {item.summary} ({item.status})"
            for item in reversed(control.receipts)
        )
        sections = (
            ("Runs", runs),
            ("Evidence", control.evidence),
            ("Memory", control.memory),
            ("Permissions", control.permissions),
            ("Approvals", control.approvals),
            ("Routine health", control.routine_health),
            ("Connector health", control.connector_health),
            ("Suppressed findings", control.suppressed_findings),
            ("Tools", control.tools),
            ("Actions", control.actions),
        )
        details = "".join(
            f'<section class="control-section"><h2>{escape(title)}</h2>{cls._list(items)}</section>'
            for title, items in sections
        )
        failures = cls._list(tuple(item.summary for item in control.failures))
        return f"""
          <header><p class="eyebrow">Operational truth</p><h1>Control</h1></header>
          <section class="control-section"><h2>Costs</h2>
            <p>{control.cost_units} billable units</p></section>
          {details}
          <section id="failure-log" class="control-section"><h2>Failure log</h2>{failures}</section>
        """

    @staticmethod
    def _list(items: tuple[str, ...]) -> str:
        if not items:
            return '<p class="empty">None recorded.</p>'
        return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Reckoning's local web interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--provider", choices=("fake", "deepseek", "orcarouter"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
    )
    parser.add_argument(
        "--server-data-dir",
        type=Path,
        help="Configured personal-server storage root for server or hybrid placement.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()

    try:
        host = validate_bind_host(arguments.host)
        runtime = load_installation_runtime(
            arguments.data_dir,
            server_data_dir=arguments.server_data_dir,
        )
    except (OperationError, ValueError) as error:
        parser.error(str(error))

    orcarouter_settings = OrcaRouterSettings.load()
    deepseek_settings = DeepSeekSettings.load()
    provider_name = arguments.provider or (
        "orcarouter"
        if orcarouter_settings.api_key
        else "deepseek"
        if deepseek_settings.api_key
        else "fake"
    )
    settings = (
        deepseek_settings if provider_name == "deepseek" else orcarouter_settings
    )

    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        provider_name=provider_name,
        orcarouter_api_key=orcarouter_settings.api_key,
        deepseek_api_key=deepseek_settings.api_key,
        model_name=arguments.model or settings.model,
        base_url=arguments.base_url or settings.base_url,
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    web = ReckoningWebApplication(
        application,
        interface_application=create_local_interface_application(
            application,
            runtime.state_path("confirmed-state", "interfaces.json"),
            placement=runtime.interface_placement,
            connector_data_dir=runtime.root_for("approved-remote-sources"),
        ),
    )
    with make_server(host, arguments.port, web) as server:
        print(f"Simon is available at http://{host}:{arguments.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nReckoning stopped.")


if __name__ == "__main__":
    main()
