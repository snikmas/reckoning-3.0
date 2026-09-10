from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Callable, Iterable
from urllib.parse import urlencode

from reckoning.application import (
    ApplicationDependencies,
    Message,
    ModelRequest,
    MessageRole,
    PlacementState,
    ReckoningApplication,
)
from reckoning.web import ReckoningWebApplication


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


class RecordingModel:
    def __init__(self, response: str) -> None:
        self._response = response
        self.requests: list[ModelRequest] = []

    def respond(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return self._response


class FakeConnectors:
    def available_names(self) -> tuple[str, ...]:
        return ("calendar",)


class RecordingStorage:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def append(
        self, role: MessageRole, content: str, created_at: datetime
    ) -> Message:
        message = Message(
            sequence=len(self.messages) + 1,
            role=role,
            content=content,
            created_at=created_at,
        )
        self.messages.append(message)
        return message

    def list_messages(self) -> tuple[Message, ...]:
        return tuple(self.messages)


def request(
    application: Callable,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
    *,
    cookie: str = "",
) -> tuple[str, dict[str, str], bytes]:
    body = urlencode(form or {}).encode("utf-8")
    captured_status = ""
    captured_headers: dict[str, str] = {}

    def start_response(
        status: str,
        headers: list[tuple[str, str]],
        exc_info: object | None = None,
    ) -> None:
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": BytesIO(body),
        "wsgi.errors": BytesIO(),
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "8000",
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    response: Iterable[bytes] = application(environ, start_response)
    return captured_status, captured_headers, b"".join(response)


def test_first_local_session_sends_message_through_application_boundary() -> None:
    instant = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
    model = RecordingModel("Compare the smallest testable version of each option.")
    storage = RecordingStorage()
    placement = PlacementState(
        processing_location="local",
        storage_location="local",
        local_node_available=True,
    )
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(instant),
            model=model,
            placement=placement,
            connectors=FakeConnectors(),
            storage=storage,
        )
    )
    web = ReckoningWebApplication(application)

    status, first_headers, first_session_page = request(web, "GET", "/")
    assert status == "200 OK"
    assert b">Simon</h1>" in first_session_page
    cookie = first_headers["Set-Cookie"].split(";", 1)[0]
    token_match = re.search(
        rb'name="_csrf_token"\s+value="([^"]+)"', first_session_page
    )
    assert token_match is not None
    csrf_token = token_match.group(1).decode()

    status, headers, _ = request(
        web,
        "POST",
        "/messages",
        {
            "message": "I need to choose between two implementation options.",
            "_csrf_token": csrf_token,
        },
        cookie=cookie,
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/"

    status, _, conversation_page = request(web, "GET", "/")
    assert status == "200 OK"
    assert b"I need to choose between two implementation options." in conversation_page
    assert b"Compare the smallest testable version of each option." in conversation_page

    assert len(model.requests) == 1
    assert model.requests[0].user_message == "I need to choose between two implementation options."
    assert model.requests[0].history == ()
    assert model.requests[0].requested_at == instant
    assert model.requests[0].placement == placement
    assert model.requests[0].available_connectors == ("calendar",)
    assert [message.role for message in storage.messages] == ["user", "assistant"]
    assert [message.content for message in storage.messages] == [
        "I need to choose between two implementation options.",
        "Compare the smallest testable version of each option.",
    ]
