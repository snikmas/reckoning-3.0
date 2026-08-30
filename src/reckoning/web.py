from __future__ import annotations

import argparse
from html import escape
import os
from typing import Callable, Iterable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from reckoning.application import Message, ReckoningApplication, create_local_application

StartResponse = Callable[[str, list[tuple[str, str]]], None]


class ReckoningWebApplication:
    """A thin WSGI adapter for the Reckoning application boundary."""

    def __init__(self, application: ReckoningApplication) -> None:
        self._application = application

    def __call__(
        self, environ: dict[str, object], start_response: StartResponse
    ) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))

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

    @staticmethod
    def _read_message(environ: dict[str, object]) -> str:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Reckoning's local web interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--provider", choices=("fake", "deepseek"), default="fake")
    parser.add_argument("--model", default="deepseek-v4-flash")
    arguments = parser.parse_args()

    web = ReckoningWebApplication(
        create_local_application(
            provider_name=arguments.provider,
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
            deepseek_model=arguments.model,
        )
    )
    with make_server(arguments.host, arguments.port, web) as server:
        print(f"Simon is available at http://{arguments.host}:{arguments.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nReckoning stopped.")


if __name__ == "__main__":
    main()
